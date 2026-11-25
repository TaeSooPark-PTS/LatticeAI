use std::net::{IpAddr, Ipv4Addr, Ipv6Addr};
use std::time::Duration;

use serde_json::{json, Map, Value};

use crate::tools::args;
use crate::tools::sandbox::ToolError;

pub const MAX_WEB_BODY_BYTES: usize = 2 * 1024 * 1024;

pub const DEFAULT_MAX_CHARS: usize = 4_000;
pub const MIN_MAX_CHARS: usize = 200;
pub const MAX_MAX_CHARS: usize = 12_000;

pub const DEFAULT_MAX_RESULTS: usize = 5;
pub const MIN_MAX_RESULTS: usize = 1;
pub const MAX_MAX_RESULTS: usize = 10;

pub const MAX_REDIRECTS: usize = 5;

const WEB_TIMEOUT: Duration = Duration::from_secs(12);

mod html;
pub use html::{
    distill_html_to_text, parse_duckduckgo_html, sanitize_untrusted_web_text, DistilledPage,
    REDACTED_INJECTION_MARKER,
};
use html::{parse_custom_search_json, resolve_href};

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct ParsedHttpUrl {
    scheme: String,
    pub(super) authority: String,
    pub(super) host: String,
    pub(super) port: Option<u16>,
    pub(super) path_and_query: String,
}

impl ParsedHttpUrl {
    pub(super) fn origin(&self) -> String {
        format!("{}://{}", self.scheme, self.authority)
    }
}

pub(super) fn parse_http_url(raw: &str) -> Result<ParsedHttpUrl, ToolError> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return Err(ToolError::tool("URL is required."));
    }
    if trimmed
        .chars()
        .any(|ch| ch.is_control() || ch.is_whitespace())
    {
        return Err(ToolError::tool(
            "URL contains invalid whitespace or control characters.",
        ));
    }
    let Some((scheme_raw, after_scheme)) = trimmed.split_once("://") else {
        return Err(ToolError::tool(
            "Unsupported URL scheme: only http:// and https:// are allowed.",
        ));
    };
    let scheme = scheme_raw.to_ascii_lowercase();
    if scheme != "http" && scheme != "https" {
        return Err(ToolError::tool(format!(
            "Unsupported URL scheme '{scheme}': only http:// and https:// are allowed."
        )));
    }
    let authority_end = after_scheme
        .find(['/', '?', '#'])
        .unwrap_or(after_scheme.len());
    let authority = &after_scheme[..authority_end];
    let tail = &after_scheme[authority_end..];
    if authority.is_empty() {
        return Err(ToolError::tool("URL is missing a host."));
    }
    if authority.contains('@') {
        return Err(ToolError::tool("URLs with userinfo (@) are not allowed."));
    }

    let (host, port) = if authority.starts_with('[') {
        let Some(close_idx) = authority.find(']') else {
            return Err(ToolError::tool("Malformed IPv6 host in URL."));
        };
        let ipv6_host = &authority[1..close_idx];
        if ipv6_host.is_empty() {
            return Err(ToolError::tool("Empty IPv6 host in URL."));
        }
        let remainder = &authority[close_idx + 1..];
        let parsed_port = if remainder.is_empty() {
            None
        } else if let Some(port_str) = remainder.strip_prefix(':') {
            let p = port_str
                .parse::<u16>()
                .map_err(|_| ToolError::tool("Invalid port in URL."))?;
            if p == 0 {
                return Err(ToolError::tool("Invalid port 0 in URL."));
            }
            Some(p)
        } else {
            return Err(ToolError::tool("Malformed authority after IPv6 host."));
        };
        (ipv6_host.to_ascii_lowercase(), parsed_port)
    } else if let Some((h, p_str)) = authority.rsplit_once(':') {
        if h.contains(':') {
            return Err(ToolError::tool("Unbracketed IPv6 host in URL."));
        }
        if h.is_empty() {
            return Err(ToolError::tool("URL is missing a host."));
        }
        let p = p_str
            .parse::<u16>()
            .map_err(|_| ToolError::tool("Invalid port in URL."))?;
        if p == 0 {
            return Err(ToolError::tool("Invalid port 0 in URL."));
        }
        (h.to_ascii_lowercase(), Some(p))
    } else {
        (authority.to_ascii_lowercase(), None)
    };

    let normalized_host = host.trim_end_matches('.').to_string();
    if normalized_host.is_empty() {
        return Err(ToolError::tool("URL is missing a host."));
    }

    let path_and_query = if tail.is_empty() {
        "/".to_string()
    } else if let Some((before_frag, _)) = tail.split_once('#') {
        if before_frag.is_empty() {
            "/".to_string()
        } else {
            before_frag.to_string()
        }
    } else {
        tail.to_string()
    };

    Ok(ParsedHttpUrl {
        scheme,
        authority: authority.to_string(),
        host: normalized_host,
        port,
        path_and_query,
    })
}

fn is_cloud_metadata_host(host: &str) -> bool {
    matches!(
        host,
        "169.254.169.254"
            | "169.254.170.2"
            | "metadata.google.internal"
            | "metadata"
            | "metadata.internal"
            | "instance-data"
            | "100.100.100.200"
            | "fd00:ec2::254"
    ) || host.ends_with(".metadata.google.internal")
}

fn looks_like_obscured_ip_literal(host: &str) -> bool {
    if host.parse::<IpAddr>().is_ok() {
        return false;
    }
    if host.chars().all(|c| c.is_ascii_digit()) {
        return true;
    }
    if host.starts_with("0x") && host[2..].chars().all(|c| c.is_ascii_hexdigit()) {
        return true;
    }
    let parts: Vec<&str> = host.split('.').collect();
    parts.len() >= 2
        && parts.iter().all(|part| {
            !part.is_empty()
                && (part.chars().all(|c| c.is_ascii_digit())
                    || (part.starts_with("0x")
                        && part.len() > 2
                        && part[2..].chars().all(|c| c.is_ascii_hexdigit())))
        })
}

fn classify_ipv4(ip: Ipv4Addr, allow_loopback: bool) -> Result<bool, ToolError> {
    let octets = ip.octets();
    if octets == [169, 254, 169, 254]
        || octets == [169, 254, 170, 2]
        || octets == [100, 100, 100, 200]
    {
        return Err(ToolError::tool(format!(
            "Blocked cloud metadata IP address: {ip}"
        )));
    }
    if ip.is_loopback() {
        if allow_loopback {
            return Ok(true);
        }
        return Err(ToolError::tool(format!(
            "Blocked loopback address: {ip} (LATTICEAI_WEB_ALLOW_LOOPBACK is not enabled)."
        )));
    }
    if ip.is_unspecified() || octets[0] == 0 {
        return Err(ToolError::tool(format!(
            "Blocked unspecified IP address: {ip}"
        )));
    }
    if ip.is_link_local() {
        return Err(ToolError::tool(format!(
            "Blocked link-local IP address: {ip}"
        )));
    }
    if ip.is_private() {
        return Err(ToolError::tool(format!(
            "Blocked private RFC1918 IP address: {ip}"
        )));
    }
    if octets[0] == 100 && (octets[1] & 0b1100_0000) == 64 {
        return Err(ToolError::tool(format!(
            "Blocked shared/CGNAT IP address: {ip}"
        )));
    }
    if ip.is_broadcast() || ip.is_multicast() || ip.is_documentation() {
        return Err(ToolError::tool(format!(
            "Blocked reserved IP address: {ip}"
        )));
    }
    Ok(false)
}

fn classify_ipv6(ip: Ipv6Addr, allow_loopback: bool) -> Result<bool, ToolError> {
    if let Some(mapped_v4) = ip.to_ipv4_mapped() {
        return classify_ipv4(mapped_v4, allow_loopback);
    }
    if ip.is_loopback() {
        if allow_loopback {
            return Ok(true);
        }
        return Err(ToolError::tool(
            "Blocked loopback address: [::1] (LATTICEAI_WEB_ALLOW_LOOPBACK is not enabled).",
        ));
    }
    if ip.is_unspecified() || ip.is_multicast() {
        return Err(ToolError::tool(format!(
            "Blocked unspecified/multicast IPv6 address: {ip}"
        )));
    }
    let seg0 = ip.segments()[0];
    if (seg0 & 0xfe00) == 0xfc00 {
        return Err(ToolError::tool(format!(
            "Blocked unique-local private IPv6 address: {ip}"
        )));
    }
    if (seg0 & 0xffc0) == 0xfe80 {
        return Err(ToolError::tool(format!(
            "Blocked link-local IPv6 address: {ip}"
        )));
    }
    Ok(false)
}

pub fn validate_web_url(raw: &str) -> Result<String, ToolError> {
    let parsed = parse_http_url(raw)?;
    let host = parsed.host.as_str();

    if is_cloud_metadata_host(host) {
        return Err(ToolError::tool(format!(
            "Blocked cloud metadata endpoint: {host}"
        )));
    }

    if looks_like_obscured_ip_literal(host) {
        return Err(ToolError::tool(format!(
            "Blocked non-standard IP literal: {host}"
        )));
    }

    let allow_loopback = std::env::var("LATTICEAI_WEB_ALLOW_LOOPBACK")
        .unwrap_or_default()
        .trim()
        == "1";
    let offline_mode = std::env::var("LATTICEAI_NETWORK_MODE")
        .unwrap_or_default()
        .trim()
        .eq_ignore_ascii_case("offline");

    let is_loopback = if host == "localhost" || host.ends_with(".localhost") {
        if !allow_loopback {
            return Err(ToolError::tool(format!(
                "Blocked loopback host: {host} (LATTICEAI_WEB_ALLOW_LOOPBACK is not enabled)."
            )));
        }
        true
    } else if let Ok(ip) = host.parse::<IpAddr>() {
        match ip {
            IpAddr::V4(v4) => classify_ipv4(v4, allow_loopback)?,
            IpAddr::V6(v6) => classify_ipv6(v6, allow_loopback)?,
        }
    } else {
        false
    };

    if offline_mode {
        if !allow_loopback {
            return Err(ToolError::tool(
                "Web access is disabled because LATTICEAI_NETWORK_MODE is offline.",
            ));
        }
        if !is_loopback {
            return Err(ToolError::tool(format!(
                "External host '{host}' is blocked because LATTICEAI_NETWORK_MODE is offline."
            )));
        }
    }

    Ok(raw.trim().to_string())
}

fn read_clamped_usize(
    args: &Map<String, Value>,
    key: &str,
    default: usize,
    min: usize,
    max: usize,
) -> usize {
    let raw = match args.get(key) {
        Some(Value::Number(n)) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)),
        Some(Value::String(s)) => s.trim().parse::<i64>().ok(),
        _ => None,
    };
    match raw {
        Some(val) if val <= min as i64 => min,
        Some(val) if val >= max as i64 => max,
        Some(val) => val as usize,
        None => default,
    }
}

fn url_encode_query(input: &str) -> String {
    let mut encoded = String::with_capacity(input.len() * 2);
    for b in input.as_bytes() {
        match *b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                encoded.push(*b as char);
            }
            b' ' => encoded.push('+'),
            other => encoded.push_str(&format!("%{other:02X}")),
        }
    }
    encoded
}

struct FetchedResponse {
    final_url: String,
    status_code: u16,
    content_type: String,
    body_text: String,
    body_truncated: bool,
}

async fn get_with_validated_redirects(initial_url: &str) -> Result<FetchedResponse, ToolError> {
    let mut current_url = validate_web_url(initial_url)?;
    let client = reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(WEB_TIMEOUT)
        .build()
        .map_err(|err| ToolError::tool(format!("failed to build HTTP client: {err}")))?;

    for hop in 0..=MAX_REDIRECTS {
        let mut response = client
            .get(&current_url)
            .header("User-Agent", "LatticeAI-WebHarness/1.0")
            .header(
                "Accept",
                "text/html,application/xhtml+xml,application/json,text/plain;q=0.9,*/*;q=0.8",
            )
            .send()
            .await
            .map_err(|err| ToolError::tool(format!("web request failed: {err}")))?;

        let status = response.status();
        if status.is_redirection() {
            if hop == MAX_REDIRECTS {
                return Err(ToolError::tool(format!(
                    "Too many redirects (exceeded {MAX_REDIRECTS})."
                )));
            }
            let location = response
                .headers()
                .get(reqwest::header::LOCATION)
                .and_then(|v| v.to_str().ok())
                .ok_or_else(|| ToolError::tool("Redirect response is missing Location header."))?;
            let resolved = resolve_href(location, &current_url)
                .ok_or_else(|| ToolError::tool(format!("Invalid redirect Location: {location}")))?;
            current_url = validate_web_url(&resolved)?;
            continue;
        }

        let content_type = response
            .headers()
            .get(reqwest::header::CONTENT_TYPE)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("text/plain")
            .to_string();

        let mut buf: Vec<u8> = Vec::new();
        let mut body_truncated = false;
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|err| ToolError::tool(format!("failed reading response body: {err}")))?
        {
            let remaining = MAX_WEB_BODY_BYTES.saturating_sub(buf.len());
            if chunk.len() > remaining {
                buf.extend_from_slice(&chunk[..remaining]);
                body_truncated = true;
                break;
            }
            buf.extend_from_slice(&chunk);
        }

        return Ok(FetchedResponse {
            final_url: current_url,
            status_code: status.as_u16(),
            content_type,
            body_text: String::from_utf8_lossy(&buf).into_owned(),
            body_truncated,
        });
    }

    Err(ToolError::tool("Too many redirects."))
}

fn is_html_content(content_type: &str, body: &str) -> bool {
    let ct_lower = content_type.to_ascii_lowercase();
    if ct_lower.contains("text/html") || ct_lower.contains("application/xhtml") {
        return true;
    }
    if ct_lower.contains("application/json") || ct_lower.contains("text/plain") {
        return false;
    }
    let sample = body.trim_start();
    sample.len() >= 5
        && (sample[..sample.len().min(64)]
            .to_ascii_lowercase()
            .starts_with("<!doctype html")
            || sample[..sample.len().min(64)]
                .to_ascii_lowercase()
                .starts_with("<html"))
}

pub async fn web_fetch(args: &Map<String, Value>) -> Result<Value, ToolError> {
    let raw_url = args::required_str(args, "url")?;
    let url = raw_url.trim().to_string();
    if url.is_empty() {
        return Err(ToolError::tool("URL is required."));
    }
    let max_chars = read_clamped_usize(
        args,
        "max_chars",
        DEFAULT_MAX_CHARS,
        MIN_MAX_CHARS,
        MAX_MAX_CHARS,
    );

    let validated_url = validate_web_url(&url)?;
    let fetched = get_with_validated_redirects(&validated_url).await?;

    let (title, full_text, links, redacted_count) =
        if is_html_content(&fetched.content_type, &fetched.body_text) {
            let distilled = distill_html_to_text(&fetched.body_text, &fetched.final_url);
            let (clean_title, r_title) = sanitize_untrusted_web_text(&distilled.title);
            let (clean_text, r_text) = sanitize_untrusted_web_text(&distilled.text);
            (clean_title, clean_text, distilled.links, r_title + r_text)
        } else {
            let (clean_text, r_text) = sanitize_untrusted_web_text(&fetched.body_text);
            (String::new(), clean_text, Vec::new(), r_text)
        };

    let total_chars = full_text.chars().count();
    let truncated = total_chars > max_chars || fetched.body_truncated;
    let content_excerpt: String = full_text.chars().take(max_chars).collect();
    let char_count = content_excerpt.chars().count();

    Ok(json!({
        "url": validated_url,
        "final_url": fetched.final_url,
        "status_code": fetched.status_code,
        "content_type": fetched.content_type,
        "title": title,
        "content_excerpt": content_excerpt,
        "links": links,
        "char_count": char_count,
        "truncated": truncated,
        "injection_signals_redacted": redacted_count,
        "untrusted_web_content": true,
    }))
}

pub async fn web_search(args: &Map<String, Value>) -> Result<Value, ToolError> {
    let raw_query = args::required_str(args, "query")?;
    let query = raw_query.trim().to_string();
    if query.is_empty() {
        return Err(ToolError::tool("Search query is required."));
    }
    let max_results = read_clamped_usize(
        args,
        "max_results",
        DEFAULT_MAX_RESULTS,
        MIN_MAX_RESULTS,
        MAX_MAX_RESULTS,
    );

    let custom_endpoint = std::env::var("LATTICEAI_WEB_SEARCH_ENDPOINT")
        .unwrap_or_default()
        .trim()
        .to_string();

    let (provider, raw_items) = if !custom_endpoint.is_empty() {
        let validated_base = validate_web_url(&custom_endpoint)?;
        let sep = if validated_base.contains('?') {
            '&'
        } else {
            '?'
        };
        let target_url = format!(
            "{validated_base}{sep}q={}&max_results={max_results}",
            url_encode_query(&query)
        );
        let fetched = get_with_validated_redirects(&target_url).await?;
        let parsed_json: Value = serde_json::from_str(&fetched.body_text).map_err(|err| {
            ToolError::tool(format!("failed to parse web_search JSON response: {err}"))
        })?;
        (
            "custom_endpoint",
            parse_custom_search_json(&parsed_json, max_results),
        )
    } else {
        let ddg_url = format!(
            "https://html.duckduckgo.com/html/?q={}",
            url_encode_query(&query)
        );
        let fetched = get_with_validated_redirects(&ddg_url).await?;
        (
            "duckduckgo_html",
            parse_duckduckgo_html(&fetched.body_text, max_results),
        )
    };

    let mut results: Vec<Value> = Vec::with_capacity(raw_items.len());
    for (idx, (raw_title, url, raw_snippet)) in raw_items.into_iter().take(max_results).enumerate()
    {
        let rank = idx + 1;
        let (clean_title, _) = sanitize_untrusted_web_text(&raw_title);
        let (clean_snippet, _) = sanitize_untrusted_web_text(&raw_snippet);
        results.push(json!({
            "rank": rank,
            "citation_id": format!("web:{rank}"),
            "title": clean_title,
            "url": url,
            "snippet": clean_snippet,
        }));
    }

    Ok(json!({
        "query": query,
        "provider": provider,
        "count": results.len(),
        "results": results,
    }))
}

#[cfg(test)]
mod tests;
