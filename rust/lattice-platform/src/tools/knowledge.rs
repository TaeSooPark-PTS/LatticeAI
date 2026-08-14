//! Knowledge garden and Obsidian vault tools.

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
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use axum::extract::{Query, State};
use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_agent::sandbox::{Workspace, MAX_FILE_BYTES};
use lattice_agent::{command, is_circuit_breaker};
use lattice_auth::{AuthState, Identity, OrderedMap};
use serde_json::{json, Value};

use crate::mcp::{
    detail, json_status, json_text, localized, missing_fields, parse_json_object, requested_scope,
    require_admin, require_user, sha256_hex,
};

use super::gov::KNOWLEDGE_FOLDERS;
use super::{tool_err, tool_ok, ToolsState};

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
    if content.as_bytes().len() as u64 > MAX_FILE_BYTES {
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
    if !parsed
        .as_object()
        .map(|o| o.contains_key("query"))
        .unwrap_or(false)
    {
        return missing_fields(&parsed, &["query"]);
    }
    let query = parsed.get("query").and_then(Value::as_str).unwrap_or("");
    if query.is_empty() {
        return tool_err("Query is required.");
    }
    let (ws, email) = match scope_of(&state, &headers, &identity) {
        Ok(s) => s,
        Err(r) => return r,
    };
    match search_notes(
        &state,
        &ws,
        &email,
        query,
        parsed
            .get("max_results")
            .and_then(Value::as_u64)
            .unwrap_or(5) as usize,
        false,
    ) {
        Ok(v) => tool_ok(&state.workspace, v),
        Err(r) => r,
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

pub(crate) async fn knowledge_tree(
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
    match tree_notes(&state, &ws, &email) {
        Ok(v) => tool_ok(&state.workspace, v),
        Err(r) => r,
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
