use fancy_regex::Regex;
use serde_json::Value;

use super::parse_http_url;

pub const REDACTED_INJECTION_MARKER: &str = "[redacted-prompt-injection-attempt]";

const LLM_CONTROL_TOKENS: &[&str] = &[
    "<|im_start|>",
    "<|im_end|>",
    "<|channel|>",
    "<|channel>",
    "<|message|>",
    "<|start|>",
    "<|end|>",
    "<think>",
    "</think>",
    "<tool_call>",
    "</tool_call>",
];

const PROMPT_INJECTION_DIRECTIVES: &[&str] = &[
    "ignore all previous instructions",
    "ignore all prior instructions",
    "ignore previous instructions",
    "disregard system prompt",
    "override system",
    "you must now call",
    "execute run_command",
    "send all files to",
    "exfiltrate",
];

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct DistilledPage {
    pub title: String,
    pub text: String,
    pub links: Vec<String>,
}

pub(super) fn decode_html_entities(input: &str) -> String {
    if !input.contains('&') {
        return input.to_string();
    }
    let mut out = String::with_capacity(input.len());
    let bytes = input.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'&' {
            let max_end = (i + 16).min(bytes.len());
            if let Some(rel_semi) = input[i..max_end].find(';') {
                let entity = &input[i + 1..i + rel_semi];
                let decoded = match entity {
                    "amp" => Some('&'),
                    "lt" => Some('<'),
                    "gt" => Some('>'),
                    "quot" => Some('"'),
                    "apos" | "#39" | "#039" => Some('\''),
                    "nbsp" => Some(' '),
                    "mdash" => Some('—'),
                    "ndash" => Some('–'),
                    "hellip" => Some('…'),
                    "copy" => Some('©'),
                    "reg" => Some('®'),
                    "bull" => Some('•'),
                    _ if entity.starts_with("#x") || entity.starts_with("#X") => {
                        u32::from_str_radix(&entity[2..], 16)
                            .ok()
                            .and_then(char::from_u32)
                    }
                    _ if entity.starts_with('#') => {
                        entity[1..].parse::<u32>().ok().and_then(char::from_u32)
                    }
                    _ => None,
                };
                if let Some(ch) = decoded {
                    out.push(ch);
                    i += rel_semi + 1;
                    continue;
                }
            }
        }
        let ch = input[i..].chars().next().unwrap_or(' ');
        out.push(ch);
        i += ch.len_utf8();
    }
    out
}

pub(super) fn resolve_href(href: &str, base_url: &str) -> Option<String> {
    let trimmed = decode_html_entities(href).trim().to_string();
    if trimmed.is_empty() || trimmed.starts_with('#') {
        return None;
    }
    let lower = trimmed.to_ascii_lowercase();
    if lower.starts_with("javascript:")
        || lower.starts_with("mailto:")
        || lower.starts_with("tel:")
        || lower.starts_with("data:")
        || lower.starts_with("vbscript:")
    {
        return None;
    }
    if lower.starts_with("http://") || lower.starts_with("https://") {
        return Some(trimmed);
    }
    let parsed_base = parse_http_url(base_url).ok()?;
    if trimmed.starts_with("//") {
        return Some(format!("{}:{}", parsed_base.scheme, trimmed));
    }
    if trimmed.starts_with('/') {
        return Some(format!("{}{}", parsed_base.origin(), trimmed));
    }
    let base_path = parsed_base.path_and_query.split('?').next().unwrap_or("/");
    let dir = match base_path.rfind('/') {
        Some(idx) => &base_path[..=idx],
        None => "/",
    };
    Some(format!("{}{}{}", parsed_base.origin(), dir, trimmed))
}

fn extract_attr(tag_body: &str, attr_name: &str) -> Option<String> {
    let pattern = format!(
        r#"(?i)\b{}\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>"']+))"#,
        attr_name
    );
    let re = Regex::new(&pattern).ok()?;
    let caps = re.captures(tag_body).ok().flatten()?;
    caps.get(1)
        .or_else(|| caps.get(2))
        .or_else(|| caps.get(3))
        .map(|m| m.as_str().to_string())
}

fn strip_html_comments_and_raw_blocks(html: &str) -> String {
    let mut current = html.to_string();
    let patterns = [
        r"(?is)<!--.*?-->",
        r"(?is)<script\b[^>]*>.*?</script>",
        r"(?is)<style\b[^>]*>.*?</style>",
        r"(?is)<noscript\b[^>]*>.*?</noscript>",
        r"(?is)<svg\b[^>]*>.*?</svg>",
        r"(?is)<nav\b[^>]*>.*?</nav>",
        r"(?is)<footer\b[^>]*>.*?</footer>",
        r"(?is)<iframe\b[^>]*>.*?</iframe>",
    ];
    for pat in patterns {
        if let Ok(re) = Regex::new(pat) {
            current = re.replace_all(&current, " ").into_owned();
        }
    }
    current
}

fn collapse_whitespace_line(s: &str) -> String {
    s.split_whitespace().collect::<Vec<_>>().join(" ")
}

pub fn distill_html_to_text(html: &str, base_url: &str) -> DistilledPage {
    let cleaned_html = strip_html_comments_and_raw_blocks(html);

    let mut title = String::new();
    if let Ok(title_re) = Regex::new(r"(?is)<title\b[^>]*>(.*?)</title>") {
        if let Ok(Some(caps)) = title_re.captures(&cleaned_html) {
            if let Some(m) = caps.get(1) {
                title = collapse_whitespace_line(&decode_html_entities(m.as_str()));
            }
        }
    }

    let mut links: Vec<String> = Vec::new();
    if let Ok(link_re) = Regex::new(r"(?is)<a\b([^>]*)>(.*?)</a>") {
        let mut pos = 0;
        while links.len() < 15 && pos < cleaned_html.len() {
            let Ok(Some(caps)) = link_re.captures_from_pos(&cleaned_html, pos) else {
                break;
            };
            let Some(full) = caps.get(0) else {
                break;
            };
            pos = full.end();
            let attrs = caps.get(1).map(|m| m.as_str()).unwrap_or("");
            let inner = caps.get(2).map(|m| m.as_str()).unwrap_or("");
            let Some(href) = extract_attr(attrs, "href") else {
                continue;
            };
            let Some(resolved) = resolve_href(&href, base_url) else {
                continue;
            };
            let plain_inner = strip_all_tags(inner);
            let link_text = collapse_whitespace_line(&decode_html_entities(&plain_inner));
            let label = if link_text.is_empty() {
                resolved.clone()
            } else {
                link_text
            };
            let formatted = format!("[{label}]({resolved})");
            if !links.contains(&formatted) {
                links.push(formatted);
            }
        }
    }

    let body_html = if let Ok(head_re) = Regex::new(r"(?is)<head\b[^>]*>.*?</head>") {
        head_re.replace_all(&cleaned_html, "\n").into_owned()
    } else {
        cleaned_html
    };

    let mut out = String::with_capacity(body_html.len());
    let mut in_tr = false;
    let mut cell_in_row = 0usize;
    let mut idx = 0;
    let bytes = body_html.as_bytes();

    while idx < bytes.len() {
        if bytes[idx] == b'<' {
            if let Some(rel_close) = body_html[idx..].find('>') {
                let tag_content = body_html[idx + 1..idx + rel_close].trim();
                let is_closing = tag_content.starts_with('/');
                let tag_body = if is_closing {
                    tag_content[1..].trim()
                } else {
                    tag_content.trim_end_matches('/').trim()
                };
                let tag_name = tag_body
                    .split(|c: char| c.is_whitespace() || c == '/')
                    .next()
                    .unwrap_or("")
                    .to_ascii_lowercase();

                match (is_closing, tag_name.as_str()) {
                    (false, "h1") => out.push_str("\n\n# "),
                    (false, "h2") => out.push_str("\n\n## "),
                    (false, "h3") => out.push_str("\n\n### "),
                    (false, "h4" | "h5" | "h6") => out.push_str("\n\n#### "),
                    (true, "h1" | "h2" | "h3" | "h4" | "h5" | "h6") => out.push_str("\n\n"),
                    (false, "li") => out.push_str("\n- "),
                    (true, "li") => out.push('\n'),
                    (false, "tr") => {
                        in_tr = true;
                        cell_in_row = 0;
                        out.push_str("\n| ");
                    }
                    (false, "td" | "th") => {
                        if in_tr && cell_in_row > 0 {
                            out.push_str(" | ");
                        }
                        cell_in_row += 1;
                    }
                    (true, "tr") => {
                        if in_tr {
                            out.push_str(" |\n");
                        }
                        in_tr = false;
                        cell_in_row = 0;
                    }
                    (false, "p" | "article" | "section" | "main" | "blockquote" | "pre") => {
                        out.push_str("\n\n")
                    }
                    (true, "p" | "article" | "section" | "main" | "blockquote" | "pre") => {
                        out.push_str("\n\n")
                    }
                    (false, "div" | "header" | "ul" | "ol" | "table" | "br" | "hr") => {
                        out.push('\n')
                    }
                    (true, "div" | "header" | "ul" | "ol" | "table") => out.push('\n'),
                    _ => {}
                }
                idx += rel_close + 1;
                continue;
            }
        }

        let next_tag = body_html[idx..].find('<').unwrap_or(body_html.len() - idx);
        let text_chunk = &body_html[idx..idx + next_tag];
        let decoded = decode_html_entities(text_chunk);
        for ch in decoded.chars() {
            if ch == '\n' || ch == '\r' || ch == '\t' {
                out.push(' ');
            } else {
                out.push(ch);
            }
        }
        idx += next_tag;
    }

    let mut lines: Vec<String> = Vec::new();
    let mut blank_streak = 0usize;
    for raw_line in out.lines() {
        let collapsed = collapse_whitespace_line(raw_line);
        if collapsed.is_empty() {
            blank_streak += 1;
            if blank_streak == 1 && !lines.is_empty() {
                lines.push(String::new());
            }
        } else {
            blank_streak = 0;
            lines.push(collapsed);
        }
    }
    while lines.last().is_some_and(|l| l.is_empty()) {
        lines.pop();
    }

    DistilledPage {
        title,
        text: lines.join("\n"),
        links,
    }
}

fn strip_all_tags(fragment: &str) -> String {
    let mut out = String::with_capacity(fragment.len());
    let mut in_tag = false;
    for ch in fragment.chars() {
        match ch {
            '<' => in_tag = true,
            '>' => {
                in_tag = false;
                out.push(' ');
            }
            _ if !in_tag => out.push(ch),
            _ => {}
        }
    }
    out
}

pub fn sanitize_untrusted_web_text(text: &str) -> (String, usize) {
    let mut current = text.to_string();
    let mut count = 0usize;

    for token in LLM_CONTROL_TOKENS {
        let pattern = format!("(?i){}", fancy_regex::escape(token));
        if let Ok(re) = Regex::new(&pattern) {
            let matches = re.find_iter(&current).count();
            if matches > 0 {
                count += matches;
                current = re.replace_all(&current, "").into_owned();
            }
        }
    }

    for directive in PROMPT_INJECTION_DIRECTIVES {
        let pattern = format!("(?i){}", fancy_regex::escape(directive));
        if let Ok(re) = Regex::new(&pattern) {
            let matches = re.find_iter(&current).count();
            if matches > 0 {
                count += matches;
                current = re
                    .replace_all(&current, REDACTED_INJECTION_MARKER)
                    .into_owned();
            }
        }
    }

    (current, count)
}

fn percent_decode(input: &str) -> String {
    let bytes = input.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            if let Ok(hex_str) = std::str::from_utf8(&bytes[i + 1..i + 3]) {
                if let Ok(val) = u8::from_str_radix(hex_str, 16) {
                    out.push(val);
                    i += 3;
                    continue;
                }
            }
        } else if bytes[i] == b'+' {
            out.push(b' ');
            i += 1;
            continue;
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn unwrap_ddg_url(href: &str) -> String {
    let decoded_href = decode_html_entities(href).trim().to_string();
    if let Some(q_idx) = decoded_href.find('?') {
        let query = &decoded_href[q_idx + 1..];
        for pair in query.split('&') {
            if let Some(val) = pair.strip_prefix("uddg=") {
                let target = percent_decode(val);
                if target.starts_with("http://") || target.starts_with("https://") {
                    return target;
                }
            }
        }
    }
    if decoded_href.starts_with("//") {
        return format!("https:{decoded_href}");
    }
    decoded_href
}

pub fn parse_duckduckgo_html(html: &str, max_results: usize) -> Vec<(String, String, String)> {
    let cleaned = strip_html_comments_and_raw_blocks(html);
    let mut items: Vec<(String, String, String)> = Vec::new();

    let Ok(anchor_re) =
        Regex::new(r#"(?is)<a\b[^>]*class\s*=\s*["'][^"']*result__a[^"']*["'][^>]*>(.*?)</a>"#)
    else {
        return items;
    };
    let snippet_re = Regex::new(
        r#"(?is)<(?:a|div|span)\b[^>]*class\s*=\s*["'][^"']*result__snippet[^"']*["'][^>]*>(.*?)</(?:a|div|span)>"#,
    )
    .ok();

    let mut snippets: Vec<String> = Vec::new();
    if let Some(ref s_re) = snippet_re {
        let mut pos = 0;
        while pos < cleaned.len() {
            let Ok(Some(caps)) = s_re.captures_from_pos(&cleaned, pos) else {
                break;
            };
            let Some(full) = caps.get(0) else {
                break;
            };
            pos = full.end();
            let raw_snip = caps.get(1).map(|m| m.as_str()).unwrap_or("");
            let text_snip =
                collapse_whitespace_line(&decode_html_entities(&strip_all_tags(raw_snip)));
            snippets.push(text_snip);
        }
    }

    let mut pos = 0;
    let mut idx = 0usize;
    while items.len() < max_results && pos < cleaned.len() {
        let Ok(Some(caps)) = anchor_re.captures_from_pos(&cleaned, pos) else {
            break;
        };
        let Some(full) = caps.get(0) else {
            break;
        };
        pos = full.end();
        let full_tag = full.as_str();
        let inner = caps.get(1).map(|m| m.as_str()).unwrap_or("");
        let Some(href) = extract_attr(full_tag, "href") else {
            continue;
        };
        let url = unwrap_ddg_url(&href);
        if !url.starts_with("http://") && !url.starts_with("https://") {
            continue;
        }
        let title = collapse_whitespace_line(&decode_html_entities(&strip_all_tags(inner)));
        let snippet = snippets.get(idx).cloned().unwrap_or_default();
        idx += 1;
        items.push((title, url, snippet));
    }

    items
}

pub(super) fn parse_custom_search_json(
    value: &Value,
    max_results: usize,
) -> Vec<(String, String, String)> {
    let list = match value {
        Value::Array(arr) => Some(arr.as_slice()),
        Value::Object(obj) => obj
            .get("results")
            .or_else(|| obj.get("items"))
            .and_then(Value::as_array)
            .map(Vec::as_slice),
        _ => None,
    };
    let Some(entries) = list else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for entry in entries.iter().take(max_results) {
        let Some(obj) = entry.as_object() else {
            continue;
        };
        let title = obj
            .get("title")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim()
            .to_string();
        let url = obj
            .get("url")
            .or_else(|| obj.get("href"))
            .or_else(|| obj.get("link"))
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim()
            .to_string();
        let snippet = obj
            .get("snippet")
            .or_else(|| obj.get("description"))
            .or_else(|| obj.get("content"))
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim()
            .to_string();
        if url.is_empty() && title.is_empty() {
            continue;
        }
        out.push((title, url, snippet));
    }
    out
}
