//! Knowledge garden and Obsidian vault tools.

use std::path::{Path, PathBuf};

use axum::extract::State;
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use lattice_agent::sandbox::MAX_FILE_BYTES;
use lattice_auth::{Identity, OrderedMap};
use serde_json::{json, Value};

use crate::mcp::{json_status, parse_json_object, requested_scope, require_user, sha256_hex};

use super::gov::KNOWLEDGE_FOLDERS;
use super::{tool_err, tool_ok, ToolExecError, ToolsState};

fn knowledge_root(brain: &Path, workspace_id: &str, email: &str) -> Result<PathBuf, Response> {
    let workspace = workspace_id.trim();
    let user = email.trim().to_lowercase();
    if workspace.is_empty() && user.is_empty() {
        return Ok(brain.to_path_buf());
    }
    if workspace.is_empty() || user.is_empty() {
        return Err(tool_err(
            "Knowledge tools require both workspace_id and user_email.",
        ));
    }
    Ok(brain
        .join(".lattice-scopes")
        .join(scope_digest("workspace", workspace))
        .join(scope_digest("user", &user)))
}

fn scope_digest(kind: &str, value: &str) -> String {
    let hex = sha256_hex(format!("{kind}\0{value}").as_bytes());
    format!("{kind}-{}", &hex[..24])
}

fn scope_of(
    state: &ToolsState,
    headers: &HeaderMap,
    identity: &Identity,
) -> Result<(String, String), Response> {
    if !state.require_auth {
        return Ok((String::new(), String::new()));
    }
    Ok((requested_scope(headers, None), identity.email.clone()))
}

pub(crate) async fn knowledge_save(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    let identity = match require_user(&state.auth, &headers) {
        Ok(id) => id,
        Err(r) => return r,
    };
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    let content = parsed.get("content").and_then(Value::as_str).unwrap_or("");
    if content.is_empty() {
        return tool_err("Knowledge content is required.");
    }
    let (ws, email) = match scope_of(&state, &headers, &identity) {
        Ok(s) => s,
        Err(r) => return r,
    };
    match save_note(
        &state,
        &ws,
        &email,
        content,
        parsed.get("title").and_then(Value::as_str),
        false,
    ) {
        Ok(v) => tool_ok(&state.workspace, v),
        Err(r) => r,
    }
}

fn save_note(
    state: &ToolsState,
    workspace_id: &str,
    email: &str,
    content: &str,
    title: Option<&str>,
    obsidian: bool,
) -> Result<Value, Response> {
    if content.len() as u64 > MAX_FILE_BYTES {
        return Err(tool_err("Knowledge content is too large."));
    }
    let root = knowledge_root(&state.brain_dir, workspace_id, email)?;
    let folder = "00_Raw";
    let target_dir = root.join(folder);
    let _ = std::fs::create_dir_all(&target_dir);
    let mut safe = title.map(str::to_string).unwrap_or_else(|| {
        content
            .trim()
            .lines()
            .next()
            .unwrap_or("note")
            .chars()
            .take(60)
            .collect()
    });
    if safe.is_empty() {
        safe = "note".into();
    }
    safe = safe
        .chars()
        .filter(|ch| ch.is_alphanumeric() || *ch == ' ' || *ch == '-' || *ch == '_')
        .collect();
    safe = safe.split_whitespace().collect::<Vec<_>>().join("_");
    if safe.is_empty() {
        safe = "note".into();
    }
    let mut target = target_dir.join(format!("{safe}.md"));
    let mut counter = 2;
    while target.exists() {
        target = target_dir.join(format!("{safe}_{counter}.md"));
        counter += 1;
    }
    let _ = std::fs::write(&target, content);
    let mut result = json!({
        "folder": folder,
        "filename": target.file_name().unwrap_or_default().to_string_lossy(),
        "path": target.to_string_lossy(),
    });
    if obsidian {
        result["vault_root"] = json!(root.to_string_lossy());
        result["obsidian_uri_hint"] =
            json!(format!("obsidian://open?path={}", target.to_string_lossy()));
    }
    Ok(result)
}

pub(crate) fn run_knowledge_search(
    state: &ToolsState,
    identity: &Identity,
    headers: &HeaderMap,
    args: &Value,
) -> Result<Value, ToolExecError> {
    if !args
        .as_object()
        .map(|o| o.contains_key("query"))
        .unwrap_or(false)
    {
        return Err(ToolExecError::Missing("query"));
    }
    let query = args.get("query").and_then(Value::as_str).unwrap_or("");
    if query.is_empty() {
        return Err(ToolExecError::Message("Query is required.".into()));
    }
    let (ws, email) = scope_of(state, headers, identity)
        .map_err(|_| ToolExecError::Message("Knowledge search failed.".into()))?;
    search_notes(
        state,
        &ws,
        &email,
        query,
        args.get("max_results").and_then(Value::as_u64).unwrap_or(5) as usize,
        false,
    )
    .map_err(|_| ToolExecError::Message("Knowledge search failed.".into()))
}

pub(crate) async fn knowledge_search(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    let identity = match require_user(&state.auth, &headers) {
        Ok(id) => id,
        Err(r) => return r,
    };
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    match run_knowledge_search(&state, &identity, &headers, &parsed) {
        Ok(v) => tool_ok(&state.workspace, v),
        Err(error) => error.into_response(&parsed),
    }
}

fn search_notes(
    state: &ToolsState,
    workspace_id: &str,
    email: &str,
    query: &str,
    max_results: usize,
    obsidian: bool,
) -> Result<Value, Response> {
    let root = knowledge_root(&state.brain_dir, workspace_id, email)?;
    let max_results = max_results.clamp(1, 20);
    let ql = query.to_lowercase();
    let mut results = Vec::new();
    fn walk(dir: &Path, root: &Path, ql: &str, max: usize, out: &mut Vec<Value>) {
        if out.len() >= max {
            return;
        }
        let Ok(rd) = std::fs::read_dir(dir) else {
            return;
        };
        for e in rd.filter_map(|e| e.ok()) {
            if out.len() >= max {
                return;
            }
            let path = e.path();
            if path.is_dir() {
                walk(&path, root, ql, max, out);
                continue;
            }
            if path.extension().and_then(|x| x.to_str()) != Some("md") {
                continue;
            }
            let Ok(content) = std::fs::read_to_string(&path) else {
                continue;
            };
            if content.to_lowercase().contains(ql)
                || path
                    .file_name()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .to_lowercase()
                    .contains(ql)
            {
                out.push(json!({
                    "path": path.to_string_lossy(),
                    "relative_path": path.strip_prefix(root).unwrap_or(&path).to_string_lossy(),
                    "preview": content.chars().take(500).collect::<String>(),
                }));
            }
        }
    }
    walk(&root, &root, &ql, max_results, &mut results);
    let mut result = json!({"query": query, "results": results});
    if obsidian {
        result["vault_root"] = json!(root.to_string_lossy());
    }
    Ok(result)
}

pub(crate) fn run_knowledge_tree(
    state: &ToolsState,
    identity: &Identity,
    headers: &HeaderMap,
) -> Result<Value, ToolExecError> {
    let (ws, email) = scope_of(state, headers, identity)
        .map_err(|_| ToolExecError::Message("Knowledge tree failed.".into()))?;
    tree_notes(state, &ws, &email)
        .map_err(|_| ToolExecError::Message("Knowledge tree failed.".into()))
}

pub(crate) async fn knowledge_tree(
    State(state): State<ToolsState>,
    headers: HeaderMap,
) -> Response {
    let identity = match require_user(&state.auth, &headers) {
        Ok(id) => id,
        Err(r) => return r,
    };
    match run_knowledge_tree(&state, &identity, &headers) {
        Ok(v) => tool_ok(&state.workspace, v),
        Err(error) => error.into_response(&json!({})),
    }
}

fn tree_notes(state: &ToolsState, workspace_id: &str, email: &str) -> Result<Value, Response> {
    let root = knowledge_root(&state.brain_dir, workspace_id, email)?;
    let mut entries = Vec::new();
    for folder in KNOWLEDGE_FOLDERS {
        let dir = root.join(folder);
        let _ = std::fs::create_dir_all(&dir);
        let mut files: Vec<PathBuf> = walkdir_md(&dir);
        files.sort();
        for file in files {
            entries.push(json!({
                "folder": folder,
                "relative_path": file.strip_prefix(&root).unwrap_or(&file).to_string_lossy(),
                "size": file.metadata().map(|m| m.len()).unwrap_or(0),
            }));
        }
    }
    Ok(json!({"root": root.to_string_lossy(), "entries": entries}))
}

fn walkdir_md(dir: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    fn rec(dir: &Path, out: &mut Vec<PathBuf>) {
        let Ok(rd) = std::fs::read_dir(dir) else {
            return;
        };
        for e in rd.filter_map(|e| e.ok()) {
            let p = e.path();
            if p.is_dir() {
                rec(&p, out);
            } else if p.extension().and_then(|x| x.to_str()) == Some("md") {
                out.push(p);
            }
        }
    }
    rec(dir, &mut out);
    out
}

pub(crate) async fn obsidian_save(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    let identity = match require_user(&state.auth, &headers) {
        Ok(id) => id,
        Err(r) => return r,
    };
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    let content = parsed.get("content").and_then(Value::as_str).unwrap_or("");
    if content.is_empty() {
        return tool_err("Knowledge content is required.");
    }
    let (ws, email) = match scope_of(&state, &headers, &identity) {
        Ok(s) => s,
        Err(r) => return r,
    };
    match save_note(
        &state,
        &ws,
        &email,
        content,
        parsed.get("title").and_then(Value::as_str),
        true,
    ) {
        Ok(v) => tool_ok(&state.workspace, v),
        Err(r) => r,
    }
}

pub(crate) async fn obsidian_search(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    let identity = match require_user(&state.auth, &headers) {
        Ok(id) => id,
        Err(r) => return r,
    };
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    let query = parsed.get("query").and_then(Value::as_str).unwrap_or("");
    let (ws, email) = match scope_of(&state, &headers, &identity) {
        Ok(s) => s,
        Err(r) => return r,
    };
    match search_notes(&state, &ws, &email, query, 5, true) {
        Ok(v) => tool_ok(&state.workspace, v),
        Err(r) => r,
    }
}

pub(crate) async fn obsidian_tree(State(state): State<ToolsState>, headers: HeaderMap) -> Response {
    let identity = match require_user(&state.auth, &headers) {
        Ok(id) => id,
        Err(r) => return r,
    };
    let (ws, email) = match scope_of(&state, &headers, &identity) {
        Ok(s) => s,
        Err(r) => return r,
    };
    match tree_notes(&state, &ws, &email) {
        Ok(v) => tool_ok(&state.workspace, v),
        Err(r) => r,
    }
}

pub(crate) async fn obsidian_status(
    State(state): State<ToolsState>,
    headers: HeaderMap,
) -> Response {
    let identity = match require_user(&state.auth, &headers) {
        Ok(id) => id,
        Err(r) => return r,
    };
    let (ws, email) = match scope_of(&state, &headers, &identity) {
        Ok(s) => s,
        Err(r) => return r,
    };
    let root = match knowledge_root(&state.brain_dir, &ws, &email) {
        Ok(p) => p,
        Err(r) => return r,
    };
    let folders: Vec<String> = if root.exists() {
        std::fs::read_dir(&root)
            .map(|rd| {
                rd.filter_map(|e| e.ok())
                    .filter(|e| e.path().is_dir())
                    .filter_map(|e| e.file_name().into_string().ok())
                    .collect()
            })
            .unwrap_or_default()
    } else {
        Vec::new()
    };
    let ocr = which("tesseract");
    let mut body = OrderedMap::new();
    body.insert("status", json!("ok"));
    body.insert("vault_root", json!(root.to_string_lossy()));
    body.insert("folders", json!(folders));
    body.insert("ocr_engine", json!(ocr));
    json_status(StatusCode::OK, &body)
}

fn which(name: &str) -> Option<String> {
    let path = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path) {
        let candidate = dir.join(name);
        if candidate.is_file() {
            return Some(candidate.to_string_lossy().into_owned());
        }
    }
    None
}
