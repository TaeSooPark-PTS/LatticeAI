//! Todos, HTML inspect, preview, and file download tools.

use std::path::{Path, PathBuf};

use axum::extract::{Query, State};
use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
use axum::response::Response;
use lattice_agent::sandbox::{Workspace, MAX_FILE_BYTES};
use lattice_auth::OrderedMap;
use serde_json::{json, Value};

use crate::toolsurface::mcp::{json_status, localized, parse_json_object, require_user};

use super::{enforce, relative, resolve, tool_err, tool_ok, ToolsState};

pub(crate) async fn todo_read(State(state): State<ToolsState>, headers: HeaderMap) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let target = state.workspace.root().join(".lattice/todos.json");
    if !target.exists() {
        return tool_ok(
            &state.workspace,
            json!({"todos": [], "path": ".lattice/todos.json"}),
        );
    }
    let todos = std::fs::read_to_string(&target)
        .ok()
        .and_then(|t| serde_json::from_str::<Value>(&t).ok())
        .unwrap_or(json!([]));
    let todos = if todos.is_array() { todos } else { json!([]) };
    tool_ok(
        &state.workspace,
        json!({"todos": todos, "path": ".lattice/todos.json"}),
    )
}

pub(crate) async fn todo_write(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    let identity = match require_user(&state.auth, &headers) {
        Ok(id) => id,
        Err(r) => return r,
    };
    if let Err(r) = enforce("todo_write", &identity, false) {
        return r;
    }
    let parsed = if body.is_empty() {
        json!({})
    } else {
        match parse_json_object(&body) {
            Ok(v) => v,
            Err(r) => return r,
        }
    };
    let Some(todos) = parsed.get("todos").and_then(Value::as_array) else {
        return tool_err("todos must be a list.");
    };
    if todos.len() > 50 {
        return tool_err("Too many todos (max 50). Split into smaller batches.");
    }
    let mut cleaned = Vec::new();
    let mut in_progress = 0;
    for (idx, raw) in todos.iter().enumerate() {
        let Some(obj) = raw.as_object() else {
            return tool_err(&format!("Todo #{} is not an object.", idx + 1));
        };
        let content = obj
            .get("content")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim();
        if content.is_empty() {
            return tool_err(&format!("Todo #{} is missing 'content'.", idx + 1));
        }
        let status = obj
            .get("status")
            .and_then(Value::as_str)
            .unwrap_or("pending")
            .trim()
            .to_lowercase();
        if !matches!(status.as_str(), "pending" | "in_progress" | "completed") {
            return tool_err(&format!(
                "Todo #{} has invalid status '{status}'. Use one of ['completed', 'in_progress', 'pending'].",
                idx + 1
            ));
        }
        if status == "in_progress" {
            in_progress += 1;
        }
        let id = obj
            .get("id")
            .map(|v| match v {
                Value::String(s) => s.clone(),
                other => other.to_string(),
            })
            .unwrap_or_else(|| (idx + 1).to_string());
        cleaned.push(json!({
            "id": id,
            "content": content.chars().take(240).collect::<String>(),
            "status": status,
        }));
    }
    let target = state.workspace.root().join(".lattice/todos.json");
    if let Some(parent) = target.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let _ = std::fs::write(
        &target,
        serde_json::to_string_pretty(&cleaned).unwrap_or_else(|_| "[]".into()),
    );
    let warning = if in_progress > 1 {
        Value::String("More than one todo is in_progress; keep only one active at a time.".into())
    } else {
        Value::Null
    };
    tool_ok(
        &state.workspace,
        json!({"todos": cleaned, "path": ".lattice/todos.json", "warning": warning}),
    )
}

pub(crate) async fn clear_history(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    _body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let mut body = OrderedMap::new();
    body.insert("status", json!("cleared"));
    body.insert("removed", json!(0));
    body.insert("kept", json!(0));
    json_status(StatusCode::OK, &body)
}

pub(crate) async fn inspect_html(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    let path = parsed.get("path").and_then(Value::as_str).unwrap_or("");
    let target = match resolve(&state.workspace, path) {
        Ok(p) => p,
        Err(r) => return r,
    };
    if !target.exists() || !target.is_file() {
        return tool_err("HTML file does not exist.");
    }
    let ext = target
        .extension()
        .map(|e| e.to_string_lossy().to_lowercase())
        .unwrap_or_default();
    if ext != "html" && ext != "htm" {
        return tool_err("Path is not an HTML file.");
    }
    if target.metadata().map(|m| m.len()).unwrap_or(0) > MAX_FILE_BYTES {
        return tool_err("HTML file is too large to inspect.");
    }
    let content = match std::fs::read_to_string(&target) {
        Ok(text) => text,
        Err(_) => match std::fs::read(&target) {
            Ok(bytes) => String::from_utf8_lossy(&bytes).into_owned(),
            Err(_) => return tool_err("HTML file does not exist."),
        },
    };
    let rel = relative(&state.workspace, &target);
    tool_ok(&state.workspace, inspect_html_source(&rel, &content))
}

fn inspect_html_source(rel_path: &str, html: &str) -> Value {
    let mut title = String::new();
    let mut in_title = false;
    let mut links: Vec<String> = Vec::new();
    let mut scripts: Vec<String> = Vec::new();
    let mut stylesheets: Vec<String> = Vec::new();
    let mut images: Vec<String> = Vec::new();
    let mut forms: usize = 0;
    let mut headings: Vec<(String, String)> = Vec::new();
    let mut active_heading: Option<usize> = None;

    let mut rest = html;
    while let Some(lt) = rest.find('<') {
        let text_chunk = rest[..lt].trim();
        if !text_chunk.is_empty() {
            if in_title {
                title.push_str(text_chunk);
            } else if let Some(idx) = active_heading {
                if headings[idx].1.is_empty() {
                    headings[idx].1 = text_chunk.chars().take(120).collect();
                }
            }
        }
        let after_lt = &rest[lt + 1..];
        if after_lt.starts_with("!--") {
            if let Some(end_comment) = after_lt.find("-->") {
                rest = &after_lt[end_comment + 3..];
                continue;
            } else {
                break;
            }
        }
        let Some(gt) = after_lt.find('>') else {
            break;
        };
        let raw_tag = after_lt[..gt].trim();
        rest = &after_lt[gt + 1..];
        if raw_tag.is_empty() || raw_tag.starts_with('!') || raw_tag.starts_with('?') {
            continue;
        }
        if let Some(closing) = raw_tag.strip_prefix('/') {
            let tag_name = closing
                .split_whitespace()
                .next()
                .unwrap_or("")
                .to_ascii_lowercase();
            if tag_name == "title" {
                in_title = false;
            } else if matches!(tag_name.as_str(), "h1" | "h2" | "h3") {
                active_heading = None;
            }
            continue;
        }
        let raw_open = raw_tag.strip_suffix('/').unwrap_or(raw_tag).trim();
        let mut parts = raw_open.splitn(2, char::is_whitespace);
        let tag_name = parts.next().unwrap_or("").to_ascii_lowercase();
        let attr_str = parts.next().unwrap_or("");
        let attrs = parse_html_attrs(attr_str);

        match tag_name.as_str() {
            "title" => in_title = true,
            "a" => {
                if let Some(href) = attrs.get("href").filter(|v| !v.is_empty()) {
                    if links.len() < 50 {
                        links.push(href.clone());
                    }
                }
            }
            "script" => {
                if let Some(src) = attrs.get("src").filter(|v| !v.is_empty()) {
                    if scripts.len() < 50 {
                        scripts.push(src.clone());
                    }
                }
            }
            "link" => {
                let is_stylesheet = attrs
                    .get("rel")
                    .map(|rel| {
                        rel.split_whitespace()
                            .any(|tok| tok.eq_ignore_ascii_case("stylesheet"))
                    })
                    .unwrap_or(false);
                if is_stylesheet {
                    if let Some(href) = attrs.get("href").filter(|v| !v.is_empty()) {
                        if stylesheets.len() < 50 {
                            stylesheets.push(href.clone());
                        }
                    }
                }
            }
            "img" => {
                if let Some(src) = attrs.get("src").filter(|v| !v.is_empty()) {
                    if images.len() < 50 {
                        images.push(src.clone());
                    }
                }
            }
            "form" => forms += 1,
            "h1" | "h2" | "h3" => {
                headings.push((tag_name, String::new()));
                active_heading = Some(headings.len() - 1);
            }
            _ => {}
        }
    }

    let heading_values: Vec<Value> = headings
        .into_iter()
        .filter(|(_, text)| !text.is_empty())
        .take(30)
        .map(|(level, text)| json!({"level": level, "text": text}))
        .collect();

    json!({
        "path": rel_path,
        "title": title,
        "links": links,
        "scripts": scripts,
        "stylesheets": stylesheets,
        "images": images,
        "forms": forms,
        "headings": heading_values,
    })
}

fn parse_html_attrs(raw: &str) -> std::collections::BTreeMap<String, String> {
    let mut map = std::collections::BTreeMap::new();
    let bytes = raw.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        while i < bytes.len() && bytes[i].is_ascii_whitespace() {
            i += 1;
        }
        let key_start = i;
        while i < bytes.len() && !bytes[i].is_ascii_whitespace() && bytes[i] != b'=' {
            i += 1;
        }
        if i == key_start {
            break;
        }
        let key = raw[key_start..i].to_ascii_lowercase();
        while i < bytes.len() && bytes[i].is_ascii_whitespace() {
            i += 1;
        }
        if i >= bytes.len() || bytes[i] != b'=' {
            map.insert(key, String::new());
            continue;
        }
        i += 1; // skip '='
        while i < bytes.len() && bytes[i].is_ascii_whitespace() {
            i += 1;
        }
        if i >= bytes.len() {
            map.insert(key, String::new());
            break;
        }
        let quote = bytes[i];
        if quote == b'"' || quote == b'\'' {
            i += 1;
            let val_start = i;
            while i < bytes.len() && bytes[i] != quote {
                i += 1;
            }
            let val = raw[val_start..i].to_string();
            if i < bytes.len() {
                i += 1;
            }
            map.insert(key, val);
        } else {
            let val_start = i;
            while i < bytes.len() && !bytes[i].is_ascii_whitespace() {
                i += 1;
            }
            map.insert(key, raw[val_start..i].to_string());
        }
    }
    map
}

pub(crate) async fn preview_url(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    let path = parsed
        .get("path")
        .and_then(Value::as_str)
        .unwrap_or("index.html");
    let target = match resolve(&state.workspace, path) {
        Ok(p) => p,
        Err(r) => return r,
    };
    if !target.exists() || !target.is_file() {
        return tool_err("Preview file does not exist.");
    }
    let rel = relative(&state.workspace, &target);
    tool_ok(
        &state.workspace,
        json!({
            "path": rel,
            "local_url": format!("http://127.0.0.1:4825/agent-files/{rel}"),
            "note": "Use the server host or /web Telegram link host instead of 127.0.0.1 from a phone.",
        }),
    )
}

#[derive(Debug, serde::Deserialize, Default)]
pub(crate) struct DownloadQuery {
    path: Option<String>,
}

fn download_target(ws: &Workspace, raw: &str) -> Result<PathBuf, (StatusCode, &'static str)> {
    let rel = raw.trim_start_matches('/');
    // `Workspace::resolve` applies `..` the way Python's `Path.resolve(strict=False)`
    // does, so `../../etc/passwd` is a 403 (outside) rather than a 404 (the
    // lexical join still "starts with" the root).
    ws.resolve(rel)
        .map_err(|_| (StatusCode::FORBIDDEN, "tools.path_outside_workspace"))
}

fn content_disposition_attachment(filename: &str) -> Option<HeaderValue> {
    if filename.is_ascii() {
        return HeaderValue::from_str(&format!("attachment; filename=\"{filename}\"")).ok();
    }
    let ascii_fallback: String = filename
        .chars()
        .map(|c| {
            if c.is_ascii_graphic() && c != '"' && c != '\\' {
                c
            } else {
                '_'
            }
        })
        .collect();
    let mut encoded = String::new();
    for b in filename.as_bytes() {
        if b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.' | b'~') {
            encoded.push(*b as char);
        } else {
            encoded.push_str(&format!("%{b:02X}"));
        }
    }
    HeaderValue::from_str(&format!(
        "attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"
    ))
    .ok()
}

pub(crate) async fn download(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    Query(q): Query<DownloadQuery>,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let path = q.path.unwrap_or_default();
    let decoded = urlencoding_decode(&path);
    let target = match download_target(&state.workspace, &decoded) {
        Ok(p) => p,
        Err((_, id)) => return localized(403, id, &headers),
    };
    if !target.exists() || !target.is_file() {
        return localized(404, "common.file_not_found", &headers);
    }
    let bytes = std::fs::read(&target).unwrap_or_default();
    let name = target
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_else(|| "download".into());
    let mut response = Response::new(axum::body::Body::from(bytes));
    *response.status_mut() = StatusCode::OK;
    response.headers_mut().insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("application/octet-stream"),
    );
    if let Some(v) = content_disposition_attachment(&name) {
        response
            .headers_mut()
            .insert(header::CONTENT_DISPOSITION, v);
    }
    response
}

pub(crate) async fn download_zip(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    Query(q): Query<DownloadQuery>,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let path = q.path.unwrap_or_default();
    let decoded = urlencoding_decode(&path);
    let target = match download_target(&state.workspace, &decoded) {
        Ok(p) => p,
        Err((_, id)) => return localized(403, id, &headers),
    };
    if !target.exists() || !target.is_dir() {
        return localized(404, "tools.directory_not_found", &headers);
    }
    let filename = format!(
        "{}.zip",
        target.file_name().unwrap_or_default().to_string_lossy()
    );
    let payload = match zip_dir_store(&target) {
        Ok(p) => p,
        Err(msg) => return tool_err(&msg),
    };
    let mut response = Response::new(axum::body::Body::from(payload));
    *response.status_mut() = StatusCode::OK;
    response.headers_mut().insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("application/zip"),
    );
    if let Some(v) = content_disposition_attachment(&filename) {
        response
            .headers_mut()
            .insert(header::CONTENT_DISPOSITION, v);
    }
    response
}

fn urlencoding_decode(s: &str) -> String {
    let mut out = Vec::with_capacity(s.len());
    let bytes = s.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            if let Ok(hex) = std::str::from_utf8(&bytes[i + 1..i + 3]) {
                if let Ok(v) = u8::from_str_radix(hex, 16) {
                    out.push(v);
                    i += 3;
                    continue;
                }
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8(out).unwrap_or_else(|e| String::from_utf8_lossy(e.as_bytes()).into_owned())
}

fn zip_dir_store(dir: &Path) -> Result<Vec<u8>, String> {
    // Minimal ZIP (store). Leading magic PK\x03\x04 matches the fixture.
    let mut files: Vec<(String, Vec<u8>)> = Vec::new();
    fn collect(root: &Path, dir: &Path, out: &mut Vec<(String, Vec<u8>)>) {
        let Ok(rd) = std::fs::read_dir(dir) else {
            return;
        };
        let mut entries: Vec<_> = rd.filter_map(|e| e.ok()).collect();
        entries.sort_by_key(|e| e.file_name());
        for e in entries {
            let path = e.path();
            if path.is_symlink() {
                continue;
            }
            if path.is_dir() {
                collect(root, &path, out);
            } else if path.is_file() {
                if let Ok(bytes) = std::fs::read(&path) {
                    if let Ok(rel) = path.strip_prefix(root.parent().unwrap_or(root)) {
                        out.push((rel.to_string_lossy().replace('\\', "/"), bytes));
                    }
                }
            }
        }
    }
    collect(dir, dir, &mut files);
    let mut out = Vec::new();
    let mut centrals = Vec::new();
    for (name, data) in &files {
        let name_bytes = name.as_bytes();
        let offset = out.len() as u32;
        out.extend_from_slice(&0x0403_4b50u32.to_le_bytes());
        out.extend_from_slice(&20u16.to_le_bytes());
        out.extend_from_slice(&0u16.to_le_bytes());
        out.extend_from_slice(&0u16.to_le_bytes());
        out.extend_from_slice(&0u16.to_le_bytes());
        out.extend_from_slice(&0u16.to_le_bytes());
        let crc = crc32(data);
        out.extend_from_slice(&crc.to_le_bytes());
        out.extend_from_slice(&(data.len() as u32).to_le_bytes());
        out.extend_from_slice(&(data.len() as u32).to_le_bytes());
        out.extend_from_slice(&(name_bytes.len() as u16).to_le_bytes());
        out.extend_from_slice(&0u16.to_le_bytes());
        out.extend_from_slice(name_bytes);
        out.extend_from_slice(data);
        let mut c = Vec::new();
        c.extend_from_slice(&0x0201_4b50u32.to_le_bytes());
        c.extend_from_slice(&20u16.to_le_bytes());
        c.extend_from_slice(&20u16.to_le_bytes());
        c.extend_from_slice(&0u16.to_le_bytes());
        c.extend_from_slice(&0u16.to_le_bytes());
        c.extend_from_slice(&0u16.to_le_bytes());
        c.extend_from_slice(&0u16.to_le_bytes());
        c.extend_from_slice(&crc.to_le_bytes());
        c.extend_from_slice(&(data.len() as u32).to_le_bytes());
        c.extend_from_slice(&(data.len() as u32).to_le_bytes());
        c.extend_from_slice(&(name_bytes.len() as u16).to_le_bytes());
        c.extend_from_slice(&0u16.to_le_bytes());
        c.extend_from_slice(&0u16.to_le_bytes());
        c.extend_from_slice(&0u16.to_le_bytes());
        c.extend_from_slice(&0u16.to_le_bytes());
        c.extend_from_slice(&0u32.to_le_bytes());
        c.extend_from_slice(&offset.to_le_bytes());
        c.extend_from_slice(name_bytes);
        centrals.push(c);
    }
    let cd_start = out.len() as u32;
    for c in &centrals {
        out.extend_from_slice(c);
    }
    let cd_size = out.len() as u32 - cd_start;
    out.extend_from_slice(&0x0605_4b50u32.to_le_bytes());
    out.extend_from_slice(&0u16.to_le_bytes());
    out.extend_from_slice(&0u16.to_le_bytes());
    out.extend_from_slice(&(files.len() as u16).to_le_bytes());
    out.extend_from_slice(&(files.len() as u16).to_le_bytes());
    out.extend_from_slice(&cd_size.to_le_bytes());
    out.extend_from_slice(&cd_start.to_le_bytes());
    out.extend_from_slice(&0u16.to_le_bytes());
    Ok(out)
}

fn crc32(data: &[u8]) -> u32 {
    let mut crc = 0xFFFF_FFFFu32;
    for &b in data {
        crc ^= b as u32;
        for _ in 0..8 {
            crc = if crc & 1 != 0 {
                (crc >> 1) ^ 0xEDB8_8320
            } else {
                crc >> 1
            };
        }
    }
    !crc
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn urlencoding_decode_preserves_utf8_and_decodes_percent_escapes() {
        assert_eq!(urlencoding_decode("out/페이지.html"), "out/페이지.html");
        assert_eq!(
            urlencoding_decode("out/%ED%8E%98%EC%9D%B4%EC%A7%80.html"),
            "out/페이지.html"
        );
        assert_eq!(urlencoding_decode("index.html"), "index.html");
    }

    #[test]
    fn content_disposition_supports_ascii_and_utf8_filenames() {
        let ascii = content_disposition_attachment("index.html").expect("ascii");
        assert_eq!(
            ascii.to_str().expect("str"),
            "attachment; filename=\"index.html\""
        );
        let utf8 = content_disposition_attachment("페이지.html").expect("utf8");
        let s = utf8.to_str().expect("str");
        assert!(s.contains("filename*=UTF-8''%ED%8E%98%EC%9D%B4%EC%A7%80.html"));
    }

    #[test]
    fn inspect_html_source_extracts_title_headings_and_assets() {
        let html = r#"<!DOCTYPE html>
<html>
<head>
  <title>Lattice Demo</title>
  <link rel="stylesheet preload" href="styles/main.css">
  <script src="app.js"></script>
</head>
<body>
  <h1>Welcome to LatticeAI</h1>
  <h2>Interactive Dashboard</h2>
  <a href="https://example.com">Docs</a>
  <img src="logo.png" alt="Logo" />
  <form><input type="text" /></form>
</body>
</html>"#;
        let report = inspect_html_source("index.html", html);
        assert_eq!(report["path"], json!("index.html"));
        assert_eq!(report["title"], json!("Lattice Demo"));
        assert_eq!(report["links"], json!(["https://example.com"]));
        assert_eq!(report["scripts"], json!(["app.js"]));
        assert_eq!(report["stylesheets"], json!(["styles/main.css"]));
        assert_eq!(report["images"], json!(["logo.png"]));
        assert_eq!(report["forms"], json!(1));
        assert_eq!(
            report["headings"],
            json!([
                {"level": "h1", "text": "Welcome to LatticeAI"},
                {"level": "h2", "text": "Interactive Dashboard"}
            ])
        );
    }
}
