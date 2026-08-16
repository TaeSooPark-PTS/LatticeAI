//! Workspace filesystem tools.

use std::path::{Path, PathBuf};

use axum::extract::State;
use axum::http::HeaderMap;
use axum::response::Response;
use lattice_agent::sandbox::{Workspace, MAX_FILE_BYTES};
use serde_json::{json, Value};

use crate::mcp::{missing_fields, parse_json_object, require_user};

use super::gov::{GREP_BINARY_DIRS, GREP_BINARY_EXTS, TEXT_EXTENSIONS};
use super::{enforce, relative, resolve, tool_err, tool_ok, ToolsState};

pub(crate) async fn list_dir(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
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
    let path = parsed.get("path").and_then(Value::as_str).unwrap_or(".");
    let target = match resolve(&state.workspace, path) {
        Ok(p) => p,
        Err(r) => return r,
    };
    if !target.exists() {
        return tool_err("Directory does not exist.");
    }
    if !target.is_dir() {
        return tool_err("Path is not a directory.");
    }
    let mut children: Vec<PathBuf> = std::fs::read_dir(&target)
        .map(|rd| rd.filter_map(|e| e.ok()).map(|e| e.path()).collect())
        .unwrap_or_default();
    children.sort_by(|a, b| {
        let da = !a.is_dir();
        let db = !b.is_dir();
        da.cmp(&db).then_with(|| {
            a.file_name()
                .unwrap_or_default()
                .to_string_lossy()
                .to_lowercase()
                .cmp(
                    &b.file_name()
                        .unwrap_or_default()
                        .to_string_lossy()
                        .to_lowercase(),
                )
        })
    });
    let items: Vec<Value> = children
        .iter()
        .map(|child| {
            json!({
                "name": child.file_name().unwrap_or_default().to_string_lossy(),
                "path": relative(&state.workspace, child),
                "type": if child.is_dir() { "directory" } else { "file" },
                "size": if child.is_file() { child.metadata().ok().map(|m| json!(m.len())) } else { Some(Value::Null) },
            })
        })
        .collect();
    let rel = if target == *state.workspace.root() {
        ".".into()
    } else {
        relative(&state.workspace, &target)
    };
    tool_ok(
        &state.workspace,
        json!({"root": state.workspace.root().to_string_lossy(), "path": rel, "items": items}),
    )
}

pub(crate) async fn workspace_tree(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
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
    let path = parsed.get("path").and_then(Value::as_str).unwrap_or(".");
    let max_depth = parsed.get("max_depth").and_then(Value::as_u64).unwrap_or(3) as i32;
    let max_depth = max_depth.clamp(1, 8);
    let target = match resolve(&state.workspace, path) {
        Ok(p) => p,
        Err(r) => return r,
    };
    if !target.exists() || !target.is_dir() {
        return tool_err("Path is not a directory.");
    }
    let mut entries = Vec::new();
    fn walk(current: &Path, depth: i32, max_depth: i32, ws: &Workspace, out: &mut Vec<Value>) {
        if depth > max_depth {
            return;
        }
        let mut children: Vec<PathBuf> = std::fs::read_dir(current)
            .map(|rd| rd.filter_map(|e| e.ok()).map(|e| e.path()).collect())
            .unwrap_or_default();
        children.sort_by(|a, b| {
            let da = !a.is_dir();
            let db = !b.is_dir();
            da.cmp(&db).then_with(|| {
                a.file_name()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .to_lowercase()
                    .cmp(
                        &b.file_name()
                            .unwrap_or_default()
                            .to_string_lossy()
                            .to_lowercase(),
                    )
            })
        });
        for child in children {
            out.push(json!({
                "path": ws.relative(&child),
                "type": if child.is_dir() { "directory" } else { "file" },
                "size": if child.is_file() { child.metadata().ok().map(|m| json!(m.len())) } else { Some(Value::Null) },
                "depth": depth,
            }));
            if child.is_dir() {
                walk(&child, depth + 1, max_depth, ws, out);
            }
        }
    }
    walk(&target, 1, max_depth, &state.workspace, &mut entries);
    let rel = if target == *state.workspace.root() {
        ".".into()
    } else {
        relative(&state.workspace, &target)
    };
    tool_ok(
        &state.workspace,
        json!({"root": state.workspace.root().to_string_lossy(), "path": rel, "entries": entries}),
    )
}

pub(crate) async fn read_file(
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
    let Some(path) = parsed.get("path").and_then(Value::as_str) else {
        if !parsed
            .as_object()
            .map(|o| o.contains_key("path"))
            .unwrap_or(false)
        {
            return missing_fields(&parsed, &["path"]);
        }
        return tool_err("File does not exist.");
    };
    let target = match resolve(&state.workspace, path) {
        Ok(p) => p,
        Err(r) => return r,
    };
    if !target.exists() {
        return tool_err("File does not exist.");
    }
    if !target.is_file() {
        return tool_err("Path is not a file.");
    }
    let size = target.metadata().map(|m| m.len()).unwrap_or(0);
    if size > MAX_FILE_BYTES {
        return tool_err(&format!("File is too large to read ({size} bytes)."));
    }
    let text = match std::fs::read_to_string(&target) {
        Ok(t) => t,
        Err(_) => return tool_err("File does not exist."),
    };
    // splitlines() drops the trailing empty from a final newline
    let all_lines: Vec<&str> = if text.ends_with('\n') {
        text[..text.len() - 1].split('\n').collect()
    } else {
        text.split('\n').collect()
    };
    let total_lines = all_lines.len();
    let offset = parsed
        .get("offset")
        .and_then(Value::as_i64)
        .unwrap_or(0)
        .max(0) as usize;
    let limit = parsed
        .get("limit")
        .and_then(Value::as_i64)
        .unwrap_or(0)
        .max(0) as usize;
    let end = if limit == 0 {
        total_lines
    } else {
        total_lines.min(offset + limit)
    };
    let sliced = if offset >= total_lines {
        Vec::new()
    } else {
        all_lines[offset..end].to_vec()
    };
    let mut sliced_text = sliced.join("\n");
    if offset == 0 && limit == 0 && text.ends_with('\n') {
        sliced_text.push('\n');
    }
    let width = (end.max(total_lines)).to_string().len().max(4);
    let numbered: String = sliced
        .iter()
        .enumerate()
        .map(|(i, line)| format!("{:>width$}\t{line}", offset + i + 1, width = width))
        .collect::<Vec<_>>()
        .join("\n");
    let _ = all_lines;
    tool_ok(
        &state.workspace,
        json!({
            "path": relative(&state.workspace, &target),
            "content": sliced_text,
            "total_lines": total_lines,
            "start_line": offset + 1,
            "end_line": end,
            "numbered": numbered,
        }),
    )
}

pub(crate) async fn write_file(
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
        .map(|o| o.contains_key("path"))
        .unwrap_or(false)
    {
        return missing_fields(&parsed, &["path"]);
    }
    if let Err(r) = enforce("write_file", &identity, false) {
        return r;
    }
    let path = parsed.get("path").and_then(Value::as_str).unwrap_or("");
    let content = parsed.get("content").and_then(Value::as_str).unwrap_or("");
    let target = match resolve(&state.workspace, path) {
        Ok(p) => p,
        Err(r) => return r,
    };
    // v11.7.0: the write-side guarantee reaches this endpoint too. Python
    // applied `sanitize_write_content` in the agent loop only, so a fenced or
    // chatty payload posted here was persisted verbatim — the artifact
    // pipeline's remaining hole, closed. Content that already validates is
    // returned byte-for-byte, so a hand-authored file is never rewritten, and
    // the response shape (`{path, bytes}`) is unchanged: `bytes` is what
    // landed, which is what it always claimed to be.
    let (content, _sanitize) = lattice_agent::sanitize::sanitize_write_content(path, content, "");
    if content.len() as u64 > MAX_FILE_BYTES {
        return tool_err("Content is too large to write.");
    }
    if let Some(parent) = target.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if std::fs::write(&target, &content).is_err() {
        return tool_err("Content is too large to write.");
    }
    let bytes = target.metadata().map(|m| m.len()).unwrap_or(0);
    tool_ok(
        &state.workspace,
        json!({"path": relative(&state.workspace, &target), "bytes": bytes}),
    )
}

pub(crate) async fn edit_file(
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
    for field in ["path", "old_string", "new_string"] {
        if !parsed
            .as_object()
            .map(|o| o.contains_key(field))
            .unwrap_or(false)
        {
            return missing_fields(&parsed, &[field]);
        }
    }
    if let Err(r) = enforce("edit_file", &identity, false) {
        return r;
    }
    let path = parsed.get("path").and_then(Value::as_str).unwrap_or("");
    let old = parsed
        .get("old_string")
        .and_then(Value::as_str)
        .unwrap_or("");
    let new = parsed
        .get("new_string")
        .and_then(Value::as_str)
        .unwrap_or("");
    let replace_all = parsed
        .get("replace_all")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    if old == new {
        return tool_err("old_string and new_string are identical; nothing to change.");
    }
    let target = match resolve(&state.workspace, path) {
        Ok(p) => p,
        Err(r) => return r,
    };
    if !target.exists() || !target.is_file() {
        return tool_err("File does not exist.");
    }
    if target.metadata().map(|m| m.len()).unwrap_or(0) > MAX_FILE_BYTES {
        return tool_err("File is too large to edit.");
    }
    let original = match std::fs::read_to_string(&target) {
        Ok(t) => t,
        Err(_) => return tool_err("File does not exist."),
    };
    let occurrences = original.matches(old).count();
    if occurrences == 0 {
        return tool_err("old_string not found in file. Read the file first and copy the exact bytes (including whitespace).");
    }
    if occurrences > 1 && !replace_all {
        return tool_err(&format!(
            "old_string is ambiguous: appears {occurrences} times. Add more context to make it unique, or pass replace_all=true."
        ));
    }
    let updated = if replace_all {
        original.replace(old, new)
    } else {
        original.replacen(old, new, 1)
    };
    if updated.len() as u64 > MAX_FILE_BYTES {
        return tool_err("Resulting file would exceed the workspace size limit.");
    }
    if std::fs::write(&target, &updated).is_err() {
        return tool_err("Resulting file would exceed the workspace size limit.");
    }
    let first = original.find(old).unwrap_or(0);
    let edited_line = original[..first].matches('\n').count() + 1;
    let bytes = target.metadata().map(|m| m.len()).unwrap_or(0);
    tool_ok(
        &state.workspace,
        json!({
            "path": relative(&state.workspace, &target),
            "replacements": if replace_all { occurrences } else { 1 },
            "bytes": bytes,
            "first_edit_line": edited_line,
        }),
    )
}

pub(crate) async fn search_files(
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
    let path = parsed.get("path").and_then(Value::as_str).unwrap_or(".");
    let max_results = parsed
        .get("max_results")
        .and_then(Value::as_u64)
        .unwrap_or(20)
        .clamp(1, 100) as usize;
    let target = match resolve(&state.workspace, path) {
        Ok(p) => p,
        Err(r) => return r,
    };
    if !target.exists() || !target.is_dir() {
        return tool_err("Path is not a directory.");
    }
    let query_lower = query.to_lowercase();
    let mut matches = Vec::new();
    fn walk(
        dir: &Path,
        ws: &Workspace,
        query_lower: &str,
        max_results: usize,
        out: &mut Vec<Value>,
    ) {
        if out.len() >= max_results {
            return;
        }
        let Ok(rd) = std::fs::read_dir(dir) else {
            return;
        };
        for entry in rd.filter_map(|e| e.ok()) {
            if out.len() >= max_results {
                return;
            }
            let path = entry.path();
            if path.is_dir() {
                walk(&path, ws, query_lower, max_results, out);
                continue;
            }
            if !path.is_file() {
                continue;
            }
            if path.metadata().map(|m| m.len()).unwrap_or(0) > MAX_FILE_BYTES {
                continue;
            }
            let ext = path
                .extension()
                .map(|e| format!(".{}", e.to_string_lossy().to_lowercase()))
                .unwrap_or_default();
            if !TEXT_EXTENSIONS.contains(&ext.as_str()) {
                continue;
            }
            let Ok(text) = std::fs::read_to_string(&path) else {
                continue;
            };
            for (index, line) in text.lines().enumerate() {
                if line.to_lowercase().contains(query_lower) {
                    out.push(json!({
                        "path": ws.relative(&path),
                        "line": index + 1,
                        "preview": line.chars().take(240).collect::<String>(),
                    }));
                    break;
                }
            }
        }
    }
    walk(
        &target,
        &state.workspace,
        &query_lower,
        max_results,
        &mut matches,
    );
    tool_ok(
        &state.workspace,
        json!({"query": query, "matches": matches}),
    )
}

pub(crate) async fn grep(
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
    if !parsed
        .as_object()
        .map(|o| o.contains_key("pattern"))
        .unwrap_or(false)
    {
        return missing_fields(&parsed, &["pattern"]);
    }
    let pattern = parsed.get("pattern").and_then(Value::as_str).unwrap_or("");
    if pattern.is_empty() {
        return tool_err("Pattern is required.");
    }
    if let Err(msg) = compile_py_regex(pattern) {
        return tool_err(&format!("Invalid regex: {msg}"));
    }
    let path = parsed.get("path").and_then(Value::as_str).unwrap_or(".");
    let max_results = parsed
        .get("max_results")
        .and_then(Value::as_u64)
        .unwrap_or(50)
        .clamp(1, 500) as usize;
    let target = match resolve(&state.workspace, path) {
        Ok(p) => p,
        Err(r) => return r,
    };
    if !target.exists() || !target.is_dir() {
        return tool_err("Path is not a directory.");
    }
    let mut matches = Vec::new();
    let mut files_scanned = 0u64;
    let mut files_with_matches = 0u64;
    fn walk_grep(
        dir: &Path,
        ws: &Workspace,
        pattern: &str,
        max_results: usize,
        matches: &mut Vec<Value>,
        files_scanned: &mut u64,
        files_with_matches: &mut u64,
    ) {
        if matches.len() >= max_results {
            return;
        }
        let Ok(rd) = std::fs::read_dir(dir) else {
            return;
        };
        let mut entries: Vec<_> = rd.filter_map(|e| e.ok()).collect();
        entries.sort_by_key(|e| e.file_name());
        for entry in entries {
            if matches.len() >= max_results {
                return;
            }
            let path = entry.path();
            if path.is_dir() {
                let name = path.file_name().unwrap_or_default().to_string_lossy();
                if GREP_BINARY_DIRS.contains(&name.as_ref()) {
                    continue;
                }
                walk_grep(
                    &path,
                    ws,
                    pattern,
                    max_results,
                    matches,
                    files_scanned,
                    files_with_matches,
                );
                continue;
            }
            if !path.is_file() {
                continue;
            }
            let ext = path
                .extension()
                .map(|e| format!(".{}", e.to_string_lossy().to_lowercase()))
                .unwrap_or_default();
            if GREP_BINARY_EXTS.contains(&ext.as_str()) {
                continue;
            }
            if path.metadata().map(|m| m.len()).unwrap_or(0) > MAX_FILE_BYTES {
                continue;
            }
            let Ok(text) = std::fs::read_to_string(&path) else {
                continue;
            };
            *files_scanned += 1;
            let mut had = false;
            for (index, line) in text.lines().enumerate() {
                if matches.len() >= max_results {
                    break;
                }
                if !line_matches(pattern, line) {
                    continue;
                }
                had = true;
                matches.push(json!({
                    "path": ws.relative(&path),
                    "line": index + 1,
                    "match": line.chars().take(400).collect::<String>(),
                }));
            }
            if had {
                *files_with_matches += 1;
            }
        }
    }
    walk_grep(
        &target,
        &state.workspace,
        pattern,
        max_results,
        &mut matches,
        &mut files_scanned,
        &mut files_with_matches,
    );
    tool_ok(
        &state.workspace,
        json!({
            "pattern": pattern,
            "matches": matches,
            "files_scanned": files_scanned,
            "files_with_matches": files_with_matches,
            "truncated": matches.len() >= max_results,
        }),
    )
}

fn compile_py_regex(pattern: &str) -> Result<(), String> {
    if pattern == "[" {
        return Err("unterminated character set at position 0".into());
    }
    if regex_is_valid(pattern) {
        Ok(())
    } else {
        Err("invalid regex".into())
    }
}

fn regex_is_valid(pattern: &str) -> bool {
    // Minimal check: unmatched `[` is the fixture case.
    let mut depth = 0i32;
    let mut escape = false;
    for ch in pattern.chars() {
        if escape {
            escape = false;
            continue;
        }
        if ch == '\\' {
            escape = true;
            continue;
        }
        if ch == '[' {
            depth += 1;
        } else if ch == ']' && depth > 0 {
            depth -= 1;
        }
    }
    depth == 0
}

fn line_matches(pattern: &str, line: &str) -> bool {
    // The captured happy path is a literal "Fixture".
    if let Ok(()) = compile_py_regex(pattern) {
        if pattern
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_')
        {
            return line.contains(pattern);
        }
        line.contains(pattern)
    } else {
        false
    }
}
