//! Todos, HTML inspect, preview, and file download tools.

use std::path::{Path, PathBuf};

use axum::extract::{Query, State};
use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
use axum::response::Response;
use lattice_agent::sandbox::Workspace;
use lattice_auth::OrderedMap;
use serde_json::{json, Value};

use crate::mcp::{json_status, localized, parse_json_object, require_user};

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
    tool_err("HTML file does not exist.")
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
    if let Ok(v) = HeaderValue::from_str(&format!("attachment; filename=\"{name}\"")) {
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
    if let Ok(v) = HeaderValue::from_str(&format!("attachment; filename=\"{filename}\"")) {
        response
            .headers_mut()
            .insert(header::CONTENT_DISPOSITION, v);
    }
    response
}

fn urlencoding_decode(s: &str) -> String {
    let mut out = String::new();
    let bytes = s.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            let hex = &s[i + 1..i + 3];
            if let Ok(v) = u8::from_str_radix(hex, 16) {
                out.push(v as char);
                i += 3;
                continue;
            }
        }
        out.push(bytes[i] as char);
        i += 1;
    }
    out
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
