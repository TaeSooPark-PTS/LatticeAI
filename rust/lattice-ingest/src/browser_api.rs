//! `latticeai/api/browser.py` — native 127.0.0.1-gated fetch (WP-R6).
//!
//! Origin restrictions match Python byte for byte on the validation
//! branches the fixtures pin: scheme, credentials, private/loopback/
//! link-local/multicast hosts. The fetch itself is native; the graph
//! write is delegated to the worker (`POST /knowledge-graph/ingest` for
//! a fetched page, `POST /api/browser/ingest-current-tab` for a tab
//! payload that already has text).

#![allow(
    dead_code,
    unused_imports,
    unused_variables,
    unused_assignments,
    unused_mut,
    private_interfaces,
    clippy::result_large_err,
    clippy::needless_lifetimes,
    clippy::too_many_arguments,
    clippy::type_complexity,
    clippy::collapsible_if,
    clippy::needless_as_bytes,
    clippy::redundant_closure,
    clippy::needless_return,
    clippy::manual_clamp,
    clippy::ptr_arg,
    clippy::unnecessary_sort_by,
    clippy::result_unit_err,
    clippy::useless_vec,
    clippy::uninlined_format_args,
    clippy::manual_contains,
    clippy::needless_borrows_for_generic_args,
    clippy::implicit_clone,
    clippy::unnecessary_map_or,
    clippy::match_like_matches_macro,
    clippy::manual_range_contains,
    clippy::derivable_impls,
    clippy::needless_pass_by_ref_mut,
    clippy::redundant_guards,
    clippy::map_identity,
    clippy::iter_overeager_cloned,
    clippy::explicit_auto_deref,
    clippy::bool_comparison,
    clippy::nonminimal_bool,
    clippy::if_same_then_else,
    clippy::question_mark,
    clippy::single_char_pattern,
    clippy::manual_pattern_char_comparison,
    clippy::manual_is_ascii_check,
    clippy::repeat_once,
    clippy::unused_self,
    clippy::module_inception
)]
use std::net::{IpAddr, ToSocketAddrs};
use std::sync::Arc;
use std::time::Duration;

use axum::body::Bytes;
use axum::extract::State;
use axum::http::HeaderMap;
use axum::response::Response;
use axum::routing::post;
use axum::Router;
use lattice_auth::AuthState;
use lattice_core::db::RuntimeConfig;
use lattice_core::worker::WorkerSeamClient;
use serde_json::{json, Value};

use crate::local_files_api::http::{
    detail, http_error, language, ok, optional, required, FieldSpec, Kind, Model,
};

/// Every `(method, path)` this module mounts.
pub const MOUNTED: &[(&str, &str)] = &[
    ("POST", "/api/browser/read-url"),
    ("POST", "/api/browser/ingest-current-tab"),
];

/// `MAX_TAB_BYTES` / `MAX_URL_FETCH_BYTES`.
pub const MAX_TAB_BYTES: usize = 4 * 1024 * 1024;
/// `MAX_URL_LENGTH`.
pub const MAX_URL_LENGTH: usize = 8192;
/// `URL_FETCH_TIMEOUT`.
pub const URL_FETCH_TIMEOUT: Duration = Duration::from_secs(12);

const READ_URL: &[FieldSpec] = &[
    required("url", Kind::Str(0)),
    optional("workspace_id", Kind::OptStr),
];

const INGEST_TAB: &[FieldSpec] = &[
    required("url", Kind::Str(0)),
    optional("title", Kind::OptStr),
    optional("text", Kind::OptStr),
    optional("selected_text", Kind::OptStr),
    optional("html", Kind::OptStr),
    optional("captured_at", Kind::OptStr),
    optional("workspace_id", Kind::OptStr),
];

/// What the two browser routes need.
#[derive(Clone)]
pub struct BrowserState {
    auth: Arc<AuthState>,
    seam: Option<WorkerSeamClient>,
    #[allow(dead_code)]
    config: RuntimeConfig,
}

impl std::fmt::Debug for BrowserState {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("BrowserState")
            .field("seam", &self.seam.as_ref().map(WorkerSeamClient::origin))
            .finish()
    }
}

impl BrowserState {
    /// Construct.
    pub fn new(auth: Arc<AuthState>, config: RuntimeConfig) -> Self {
        Self {
            auth,
            seam: None,
            config,
        }
    }

    /// Attach the worker the ingest write is delegated to.
    pub fn with_seam(mut self, seam: WorkerSeamClient) -> Self {
        self.seam = Some(seam);
        self
    }
}

/// The two browser routes.
pub fn router(state: Arc<BrowserState>) -> Router {
    Router::new()
        .route("/api/browser/read-url", post(read_url))
        .route("/api/browser/ingest-current-tab", post(ingest_current_tab))
        .with_state(state)
}

async fn read_url(
    State(state): State<Arc<BrowserState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let model = match Model::parse(&body, READ_URL) {
        Ok(model) => model,
        Err(refusal) => return refusal,
    };
    if let Err(refusal) = state.auth.require_user(&headers) {
        return refusal;
    }
    let lang = language(&headers);
    let Some(seam) = state.seam.as_ref() else {
        return http_error(503, "capture.ingestion_disabled", lang);
    };
    let url = match validate_http_url(model.str("url")) {
        Ok(url) => url,
        Err(message) => return detail(400, &message),
    };
    if let Err(message) = resolve_public(&url) {
        return detail(422, &message);
    }
    match fetch_url(&url).await {
        Ok((title, text)) => {
            if text.trim().is_empty() {
                return ok(&json!({
                    "status": "empty",
                    "source_type": "web_url",
                    "url": url,
                    "detail": "No readable text was extracted from the page.",
                    "capture_quality": thin_capture()
                }));
            }
            let forwarded = json!({
                "type": "note",
                "title": title,
                "content": text,
                "source": url,
                "metadata": {"source_type": "web_url"}
            });
            match forward(seam, &headers, "/knowledge-graph/ingest", &forwarded).await {
                Ok(mut payload) => {
                    if let Some(object) = payload.as_object_mut() {
                        object
                            .entry("capture_quality".to_string())
                            .or_insert_with(thin_capture);
                    }
                    ok(&payload)
                }
                Err(refusal) => refusal,
            }
        }
        Err(message) => detail(422, &message),
    }
}

async fn ingest_current_tab(
    State(state): State<Arc<BrowserState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let model = match Model::parse(&body, INGEST_TAB) {
        Ok(model) => model,
        Err(refusal) => return refusal,
    };
    if let Err(refusal) = state.auth.require_user(&headers) {
        return refusal;
    }
    let lang = language(&headers);
    let Some(seam) = state.seam.as_ref() else {
        return http_error(503, "capture.ingestion_disabled", lang);
    };
    let url = match validate_http_url(model.str("url")) {
        Ok(url) => url,
        Err(message) => return detail(400, &message),
    };
    let captured_bytes = [
        model.str("url"),
        model.str("title"),
        model.str("text"),
        model.str("selected_text"),
        model.str("html"),
        model.str("captured_at"),
        model
            .get("workspace_id")
            .and_then(Value::as_str)
            .unwrap_or(""),
    ]
    .iter()
    .map(|part| part.len())
    .sum::<usize>();
    if captured_bytes > MAX_TAB_BYTES {
        return http_error(413, "capture.payload_too_large", lang);
    }
    let mut text = model.str("text").trim().to_string();
    if text.is_empty() {
        if !model.str("html").is_empty() {
            let (_title, extracted) = extract_readable_text(model.str("html"));
            text = extracted;
        }
    }
    if text.is_empty() {
        text = model.str("selected_text").trim().to_string();
    }
    if text.is_empty() {
        return http_error(400, "capture.nothing_to_capture", lang);
    }
    let forwarded = json!({
        "url": url,
        "title": model.get("title").cloned().unwrap_or(Value::Null),
        "text": text,
        "selected_text": model.get("selected_text").cloned().unwrap_or(Value::Null),
        "html": model.get("html").cloned().unwrap_or(Value::Null),
        "captured_at": model.get("captured_at").cloned().unwrap_or(Value::Null),
        "workspace_id": model.get("workspace_id").cloned().unwrap_or(Value::Null),
    });
    match forward(
        seam,
        &headers,
        "/api/browser/ingest-current-tab",
        &forwarded,
    )
    .await
    {
        Ok(value) => ok(&value),
        Err(refusal) => refusal,
    }
}

async fn forward(
    seam: &WorkerSeamClient,
    headers: &HeaderMap,
    path: &str,
    body: &Value,
) -> Result<Value, Response> {
    let mut client = seam.clone();
    if let Some(cookie) = headers.get(axum::http::header::COOKIE) {
        if let Ok(value) = cookie.to_str() {
            client = client.with_header("cookie", value);
        }
    }
    match client.post_json(path, body).await {
        Ok(value) => Ok(value),
        Err(error) => Err(crate::local_files_api::http::seam_error(error)),
    }
}

/// `_validate_http_url` / `_parse_http_url`.
pub fn validate_http_url(url: &str) -> Result<String, String> {
    let cleaned = url.trim();
    if cleaned.is_empty() {
        return Err("url is required.".into());
    }
    if cleaned.len() > MAX_URL_LENGTH {
        return Err("URL is too long.".into());
    }
    if cleaned.contains('\\') || cleaned.chars().any(|ch| ch < ' ' || ch == '\u{7f}') {
        return Err("Malformed URL.".into());
    }
    let parsed = url::Url::parse(cleaned).map_err(|_| "Malformed URL.".to_string())?;
    let scheme = parsed.scheme().to_ascii_lowercase();
    if scheme != "http" && scheme != "https" {
        return Err("Only http(s) URLs are supported.".into());
    }
    if parsed.host_str().is_none() {
        return Err("Malformed URL.".into());
    }
    if parsed.username() != "" || parsed.password().is_some() {
        return Err("URLs containing credentials are not supported.".into());
    }
    let hostname = parsed.host_str().unwrap_or_default();
    if hostname.contains('%') {
        return Err("Scoped IP addresses are not supported.".into());
    }
    Ok(cleaned.to_string())
}

/// Resolve the host and refuse every non-public address.
pub fn resolve_public(url: &str) -> Result<Vec<IpAddr>, String> {
    let parsed = url::Url::parse(url).map_err(|_| "Malformed URL.".to_string())?;
    let hostname = parsed.host_str().unwrap_or_default();
    if hostname == "localhost" || hostname.ends_with(".localhost") {
        return Err("Local and private network URLs are not allowed.".into());
    }
    if let Ok(ip) = hostname.parse::<IpAddr>() {
        if !is_public_ip(ip) {
            return Err("Local and private network URLs are not allowed.".into());
        }
        return Ok(vec![ip]);
    }
    let port = parsed.port_or_known_default().unwrap_or(80);
    let records = (hostname, port)
        .to_socket_addrs()
        .map_err(|_| format!("Could not resolve the page host: {hostname}."))?;
    let mut addresses = Vec::new();
    for record in records {
        let ip = record.ip();
        if !is_public_ip(ip) {
            return Err("Local and private network URLs are not allowed.".into());
        }
        if !addresses.contains(&ip) {
            addresses.push(ip);
        }
    }
    if addresses.is_empty() {
        return Err(format!("Could not resolve the page host: {hostname}."));
    }
    Ok(addresses)
}

fn is_public_ip(address: IpAddr) -> bool {
    match address {
        IpAddr::V4(ip) => {
            !(ip.is_loopback()
                || ip.is_private()
                || ip.is_link_local()
                || ip.is_multicast()
                || ip.is_unspecified()
                || ip.is_broadcast()
                || ip.octets()[0] == 0
                || (ip.octets()[0] & 0xf0) == 0xf0)
        }
        IpAddr::V6(ip) => {
            !(ip.is_loopback()
                || ip.is_multicast()
                || ip.is_unspecified()
                || ip.is_unique_local()
                || (ip.segments()[0] & 0xffc0) == 0xfe80)
        }
    }
}

async fn fetch_url(url: &str) -> Result<(String, String), String> {
    let client = reqwest::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(URL_FETCH_TIMEOUT)
        .build()
        .map_err(|error| format!("Could not reach the page: {error}"))?;
    let response = client
        .get(url)
        .header(
            "Accept",
            "text/html, text/plain;q=0.9, application/xhtml+xml;q=0.8",
        )
        .header("Accept-Encoding", "identity")
        .header("Connection", "close")
        .header(
            "User-Agent",
            format!(
                "LatticeAI-local/{} (+local-first knowledge graph)",
                env!("CARGO_PKG_VERSION")
            ),
        )
        .send()
        .await
        .map_err(|error| format!("Could not reach the page: {error}"))?;
    let status = response.status().as_u16();
    if matches!(status, 401 | 403) {
        return Err(format!(
            "The page is login-required or blocked (HTTP {status})."
        ));
    }
    if status >= 400 {
        return Err(format!("The page returned HTTP {status}."));
    }
    let bytes = response
        .bytes()
        .await
        .map_err(|error| format!("Could not reach the page: {error}"))?;
    if bytes.len() > MAX_TAB_BYTES {
        return Err("The page is too large to ingest.".into());
    }
    let body = String::from_utf8_lossy(&bytes);
    Ok(extract_readable_text(&body))
}

/// Best-effort HTML title + readable text. Never panics.
pub fn extract_readable_text(html: &str) -> (String, String) {
    let mut title = String::new();
    let mut chunks: Vec<String> = Vec::new();
    let mut skip = 0u32;
    let mut in_title = false;
    let mut rest = html;
    while let Some(start) = rest.find('<') {
        let text = &rest[..start];
        push_text(&mut chunks, &mut title, text, skip, in_title);
        rest = &rest[start + 1..];
        let Some(end) = rest.find('>') else {
            break;
        };
        let tag_raw = &rest[..end];
        rest = &rest[end + 1..];
        // Split on `/` only *after* the optional closer so `</title>` still
        // names `title`. Splitting first made the first token empty and left
        // `in_title` stuck on for the rest of the document.
        let closing = tag_raw.starts_with('/');
        let name_src = if closing { &tag_raw[1..] } else { tag_raw };
        let name = name_src
            .split(|ch: char| ch.is_whitespace() || ch == '/')
            .next()
            .unwrap_or("")
            .to_ascii_lowercase();
        if matches!(
            name.as_str(),
            "script" | "style" | "noscript" | "template" | "svg" | "head"
        ) {
            if closing {
                skip = skip.saturating_sub(1);
            } else {
                skip += 1;
            }
        }
        if name == "title" {
            in_title = !closing;
        }
        if closing
            && matches!(
                name.as_str(),
                "p" | "div" | "br" | "li" | "h1" | "h2" | "h3" | "h4" | "section" | "article"
            )
        {
            chunks.push("\n".into());
        }
    }
    push_text(&mut chunks, &mut title, rest, skip, in_title);
    let raw = chunks.join(" ");
    let lines: Vec<String> = raw
        .replace('\r', "")
        .split('\n')
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .map(str::to_string)
        .collect();
    (title.trim().to_string(), lines.join("\n"))
}

fn push_text(chunks: &mut Vec<String>, title: &mut String, text: &str, skip: u32, in_title: bool) {
    if in_title {
        title.push_str(text);
    }
    if skip == 0 {
        let trimmed = text.trim();
        if !trimmed.is_empty() {
            chunks.push(trimmed.to_string());
        }
    }
}

fn thin_capture() -> Value {
    json!({
        "status": "thin",
        "reason": "no extracted text",
        "reason_codes": ["no_extracted_text"],
        "suggestions": [],
        "score": null,
        "level": null
    })
}

// `url` crate may not be a dep. Parse with a tiny splitter instead if needed.
mod url {
    pub struct Url {
        pub scheme: String,
        pub host: Option<String>,
        pub port: Option<u16>,
        pub user: String,
        pub password: Option<String>,
    }
    impl Url {
        pub fn parse(raw: &str) -> Result<Self, ()> {
            let (scheme, rest) = raw.split_once("://").ok_or(())?;
            if scheme.is_empty() {
                return Err(());
            }
            let authority = rest.split(['/', '?', '#']).next().unwrap_or(rest);
            if authority.is_empty() {
                return Err(());
            }
            let (userinfo, hostport) = match authority.rsplit_once('@') {
                Some((userinfo, hostport)) => (Some(userinfo), hostport),
                None => (None, authority),
            };
            let (user, password) = match userinfo {
                Some(info) => match info.split_once(':') {
                    Some((u, p)) => (u.to_string(), Some(p.to_string())),
                    None => (info.to_string(), Some(String::new())),
                },
                None => (String::new(), None),
            };
            let (host, port) = if hostport.starts_with('[') {
                let end = hostport.find(']').ok_or(())?;
                let host = hostport[1..end].to_string();
                let port = hostport[end + 1..]
                    .strip_prefix(':')
                    .and_then(|p| p.parse().ok());
                (host, port)
            } else {
                match hostport.rsplit_once(':') {
                    Some((h, p)) if !h.is_empty() && p.chars().all(|c| c.is_ascii_digit()) => {
                        (h.to_string(), p.parse().ok())
                    }
                    _ => (hostport.to_string(), None),
                }
            };
            if host.is_empty() {
                return Err(());
            }
            Ok(Self {
                scheme: scheme.to_string(),
                host: Some(host),
                port,
                user,
                password,
            })
        }
        pub fn scheme(&self) -> &str {
            &self.scheme
        }
        pub fn host_str(&self) -> Option<&str> {
            self.host.as_deref()
        }
        pub fn username(&self) -> &str {
            &self.user
        }
        pub fn password(&self) -> Option<&str> {
            self.password.as_deref()
        }
        pub fn port_or_known_default(&self) -> Option<u16> {
            self.port
                .or(match self.scheme.to_ascii_lowercase().as_str() {
                    "https" => Some(443),
                    "http" => Some(80),
                    _ => None,
                })
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_route_table_is_the_two_browser_routes() {
        assert_eq!(MOUNTED.len(), 2);
    }

    #[test]
    fn only_http_schemes_are_accepted() {
        assert!(validate_http_url("ftp://example.com").is_err());
        assert!(validate_http_url("https://example.com/a").is_ok());
        assert!(validate_http_url("http://user:pass@example.com").is_err());
        assert!(validate_http_url("").unwrap_err().contains("required"));
    }

    #[test]
    fn private_hosts_are_refused() {
        assert!(resolve_public("http://127.0.0.1/").is_err());
        assert!(resolve_public("http://10.0.0.1/").is_err());
        assert!(resolve_public("http://192.168.1.1/").is_err());
        assert!(resolve_public("http://localhost/").is_err());
        assert!(resolve_public("http://[::1]/").is_err());
    }

    #[test]
    fn html_extraction_drops_script_and_keeps_title() {
        let (title, text) = extract_readable_text(
            "<html><head><title>Hi</title><script>x()</script></head><body><p>Hello</p><div>World</div></body></html>",
        );
        assert_eq!(title, "Hi");
        assert!(text.contains("Hello"));
        assert!(text.contains("World"));
        assert!(!text.contains("x()"));
    }
}
