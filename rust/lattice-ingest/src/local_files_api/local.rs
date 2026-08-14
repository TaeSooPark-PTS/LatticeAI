//! `/local/{list,read,serve,write}` and `GET /api/local-agent/status`.

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
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Instant;

use axum::body::Bytes;
use axum::extract::{RawQuery, State};
use axum::http::HeaderMap;
use axum::response::Response;
use lattice_auth::OrderedMap;
use serde_json::{json, Value};

use super::http::{http_error, language, ok, optional, required, FieldSpec, Kind, Model, Query};
use super::{expand_user, tool_error, tool_ok, LocalFilesState, APPROVAL_TTL_SECS};

const ACCESS_REQUEST: &[FieldSpec] = &[
    required("path", Kind::Str(0)),
    optional("approved", Kind::Bool),
    optional("approval_token", Kind::OptStr),
];

const WRITE_REQUEST: &[FieldSpec] = &[
    required("path", Kind::Str(0)),
    required("content", Kind::Str(0)),
    optional("approved", Kind::Bool),
    optional("approval_token", Kind::OptStr),
];

pub(super) async fn agent_status(
    State(state): State<Arc<LocalFilesState>>,
    headers: HeaderMap,
) -> Response {
    if let Err(refusal) = state.require_user(&headers) {
        return refusal;
    }
    let started = Instant::now();
    let mut errors: Vec<String> = Vec::new();

    let probe_dir = state.config.data_dir().to_path_buf();
    let fs_ok = match std::fs::create_dir_all(&probe_dir) {
        Ok(()) => {
            let name = format!(".local_agent_probe_{}", super::token_urlsafe(8));
            let path = probe_dir.join(name);
            let token = super::token_urlsafe(8);
            let ok = std::fs::write(&path, &token)
                .and_then(|_| std::fs::read_to_string(&path))
                .map(|read| read == token)
                .unwrap_or_else(|error| {
                    errors.push(format!("filesystem: {error}"));
                    false
                });
            let _ = std::fs::remove_file(&path);
            ok
        }
        Err(error) => {
            errors.push(format!("filesystem: {error}"));
            false
        }
    };

    let graph_reachable = match &state.store {
        Some(store) => match store
            .read(|conn| {
                conn.query_row("SELECT 1", [], |_| Ok(()))
                    .map_err(lattice_core::CoreError::from)
            })
            .await
        {
            Ok(()) => Some(true),
            Err(error) => {
                errors.push(format!("graph: {error}"));
                Some(false)
            }
        },
        None => None,
    };

    let sources = match &state.store {
        Some(store) => store
            .read(|conn| {
                let count: i64 = conn
                    .query_row("SELECT COUNT(*) FROM knowledge_sources", [], |row| {
                        row.get(0)
                    })
                    .unwrap_or(0);
                Ok::<_, lattice_core::CoreError>(count)
            })
            .await
            .unwrap_or_else(|error| {
                errors.push(format!("sources: {error}"));
                0
            }),
        None => 0,
    };

    let mode = if !fs_ok {
        "error"
    } else if graph_reachable == Some(false) {
        "degraded"
    } else {
        "online"
    };
    let latency_ms = (started.elapsed().as_secs_f64() * 1000.0 * 100.0).round() / 100.0;

    let mut agent = OrderedMap::new();
    agent.insert("id", json!("lattice-local-runtime"));
    agent.insert("name", json!("Lattice Local Agent"));
    agent.insert("kind", json!("on-device-runtime"));
    agent.insert("online", json!(mode == "online"));
    agent.insert("platform", json!(platform_string()));
    agent.insert("machine", json!(std::env::consts::ARCH));
    agent.insert("python", json!(null));

    let mut handshake = OrderedMap::new();
    handshake.insert("ok", json!(fs_ok && graph_reachable != Some(false)));
    handshake.insert("transport", json!("in-process"));
    handshake.insert("latency_ms", json!(latency_ms));
    handshake.insert(
        "detail",
        json!("Probed the in-process runtime (filesystem + graph); the local Lattice server is the on-device agent — no separate desktop process."),
    );

    let mut health = OrderedMap::new();
    health.insert("status", json!(mode));
    health.insert("filesystem_access", json!(fs_ok));
    health.insert("graph_reachable", json!(graph_reachable));
    health.insert("watcher_available", json!(false));

    let mut folders = OrderedMap::new();
    folders.insert("connected", json!(sources));
    folders.insert("watching", json!(0));

    let mut payload = OrderedMap::new();
    payload.insert("agent", serde_json::to_value(agent).unwrap_or(Value::Null));
    payload.insert("online", json!(mode == "online"));
    payload.insert("mode", json!(mode));
    payload.insert("version", json!(state.version));
    payload.insert("pid", json!(std::process::id()));
    payload.insert(
        "handshake",
        serde_json::to_value(handshake).unwrap_or(Value::Null),
    );
    payload.insert(
        "health",
        serde_json::to_value(health).unwrap_or(Value::Null),
    );
    payload.insert("filesystem_access", json!(fs_ok));
    payload.insert("watcher_available", json!(false));
    payload.insert("connected_folders", json!(sources));
    payload.insert("watched_folders", json!(0));
    payload.insert(
        "folders",
        serde_json::to_value(folders).unwrap_or(Value::Null),
    );
    payload.insert("watch", json!({"available": false, "active": {}}));
    payload.insert("sources", json!([]));
    payload.insert("last_seen", json!(super::naive_now()));
    payload.insert(
        "error",
        if errors.is_empty() {
            Value::Null
        } else {
            json!(errors.join("; "))
        },
    );
    ok(&serde_json::to_value(payload).unwrap_or(Value::Null))
}

fn platform_string() -> String {
    format!("{}-{}", std::env::consts::OS, std::env::consts::ARCH)
}

pub(super) async fn list_get(
    State(state): State<Arc<LocalFilesState>>,
    headers: HeaderMap,
    RawQuery(raw): RawQuery,
) -> Response {
    let path = match Query::parse(raw.as_deref()).require_str("path") {
        Ok(path) => path,
        Err(refusal) => return refusal,
    };
    let user = match state.require_local_user(&headers) {
        Ok(user) => user,
        Err(refusal) => return refusal,
    };
    ok(&state.permissions.probe(&path, "list", &user, ""))
}

pub(super) async fn list_post(
    State(state): State<Arc<LocalFilesState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let model = match Model::parse(&body, ACCESS_REQUEST) {
        Ok(model) => model,
        Err(refusal) => return refusal,
    };
    let user = match state.require_local_user(&headers) {
        Ok(user) => user,
        Err(refusal) => return refusal,
    };
    let path = model.str("path");
    if !model.bool("approved", false) {
        return ok(&state.permissions.probe(path, "list", &user, ""));
    }
    if let Err(refusal) = state.permissions.require(
        model.get("approval_token").and_then(Value::as_str),
        path,
        "list",
        &user,
        "",
    ) {
        return refusal;
    }
    match list_dir(path) {
        Ok(result) => tool_ok(&state.agent_root, result),
        Err(message) => tool_error(&message),
    }
}

pub(super) async fn read_post(
    State(state): State<Arc<LocalFilesState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let model = match Model::parse(&body, ACCESS_REQUEST) {
        Ok(model) => model,
        Err(refusal) => return refusal,
    };
    let user = match state.require_local_user(&headers) {
        Ok(user) => user,
        Err(refusal) => return refusal,
    };
    let path = model.str("path");
    if !model.bool("approved", false) {
        return ok(&state.permissions.probe(path, "read", &user, ""));
    }
    if let Err(refusal) = state.permissions.require(
        model.get("approval_token").and_then(Value::as_str),
        path,
        "read",
        &user,
        "",
    ) {
        return refusal;
    }
    match read_file(path) {
        Ok(result) => tool_ok(&state.agent_root, result),
        Err(message) => tool_error(&message),
    }
}

pub(super) async fn serve(
    State(state): State<Arc<LocalFilesState>>,
    headers: HeaderMap,
    RawQuery(raw): RawQuery,
) -> Response {
    let query = Query::parse(raw.as_deref());
    let path = match query.require_str("path") {
        Ok(path) => path,
        Err(refusal) => return refusal,
    };
    let user = match state.require_local_user(&headers) {
        Ok(user) => user,
        Err(refusal) => return refusal,
    };
    let token = query.raw("approval_token").map(str::to_string);
    if let Err(refusal) = state
        .permissions
        .require(token.as_deref(), &path, "read", &user, "")
    {
        return refusal;
    }
    let target = PathBuf::from(expand_user(&path));
    let target = std::fs::canonicalize(&target).unwrap_or(target);
    if !target.is_file() {
        return http_error(404, "common.file_not_found", language(&headers));
    }
    match std::fs::read(&target) {
        Ok(bytes) => {
            let mime = mime_of(&target);
            let mut response = Response::new(axum::body::Body::from(bytes));
            *response.status_mut() = axum::http::StatusCode::OK;
            response.headers_mut().insert(
                axum::http::header::CONTENT_TYPE,
                mime.parse()
                    .unwrap_or_else(|_| "application/octet-stream".parse().expect("mime")),
            );
            response
        }
        Err(_) => http_error(404, "common.file_not_found", language(&headers)),
    }
}

pub(super) async fn write_post(
    State(state): State<Arc<LocalFilesState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let model = match Model::parse(&body, WRITE_REQUEST) {
        Ok(model) => model,
        Err(refusal) => return refusal,
    };
    let user = match state.require_local_user(&headers) {
        Ok(user) => user,
        Err(refusal) => return refusal,
    };
    let path = model.str("path");
    let content = model.str("content");
    if !model.bool("approved", false) {
        if let Err(refusal) = super::ensure_write_allowed(&super::LocalApprovals::normalize(path)) {
            return refusal;
        }
        return ok(&state.permissions.probe(path, "write", &user, content));
    }
    if let Err(refusal) = state.permissions.require(
        model.get("approval_token").and_then(Value::as_str),
        path,
        "write",
        &user,
        content,
    ) {
        return refusal;
    }
    match write_file(path, content) {
        Ok(result) => tool_ok(&state.agent_root, result),
        Err(message) => tool_error(&message),
    }
}

fn list_dir(path: &str) -> Result<Value, String> {
    let target = PathBuf::from(expand_user(path));
    let target = std::fs::canonicalize(&target).unwrap_or(target);
    if !target.exists() {
        return Err(format!("경로가 존재하지 않습니다: {path}"));
    }
    if !target.is_dir() {
        return Err(format!("폴더가 아닙니다: {path}"));
    }
    let mut children: Vec<_> = std::fs::read_dir(&target)
        .map_err(|error| format!("접근 권한 없음: {error}"))?
        .filter_map(Result::ok)
        .collect();
    children.sort_by_key(|entry| {
        let is_dir = entry.path().is_dir();
        (!is_dir, entry.file_name().to_string_lossy().to_lowercase())
    });
    let mut items = Vec::new();
    for entry in children {
        let child = entry.path();
        let meta = entry.metadata().ok();
        let is_dir = child.is_dir();
        let mut item = OrderedMap::new();
        item.insert(
            "name",
            json!(child
                .file_name()
                .map(|n| n.to_string_lossy())
                .unwrap_or_default()),
        );
        item.insert("path", json!(child.display().to_string()));
        item.insert("type", json!(if is_dir { "directory" } else { "file" }));
        item.insert(
            "size",
            if is_dir {
                Value::Null
            } else {
                json!(meta.map(|m| m.len()).unwrap_or(0))
            },
        );
        items.push(serde_json::to_value(item).unwrap_or(Value::Null));
    }
    let mut payload = OrderedMap::new();
    payload.insert("path", json!(target.display().to_string()));
    payload.insert("items", Value::Array(items));
    Ok(serde_json::to_value(payload).unwrap_or(Value::Null))
}

fn read_file(path: &str) -> Result<Value, String> {
    const MAX: u64 = 512_000;
    let target = PathBuf::from(expand_user(path));
    let target = std::fs::canonicalize(&target).unwrap_or(target);
    if !target.exists() {
        return Err(format!("파일이 존재하지 않습니다: {path}"));
    }
    if !target.is_file() {
        return Err(format!("파일이 아닙니다: {path}"));
    }
    let size = target.metadata().map(|m| m.len()).unwrap_or(0);
    if size > MAX {
        return Err(format!(
            "파일이 너무 큽니다 ({size} bytes). 최대 {MAX} bytes."
        ));
    }
    let content =
        std::fs::read_to_string(&target).map_err(|error| format!("파일 읽기 실패: {error}"))?;
    let mut payload = OrderedMap::new();
    payload.insert("path", json!(target.display().to_string()));
    payload.insert("size", json!(size));
    payload.insert("content", json!(content));
    Ok(serde_json::to_value(payload).unwrap_or(Value::Null))
}

fn write_file(path: &str, content: &str) -> Result<Value, String> {
    const MAX: usize = 512_000;
    let target = PathBuf::from(expand_user(path));
    let target = std::fs::canonicalize(&target).unwrap_or(target);
    let slash = target.to_string_lossy().replace('\\', "/");
    for prefix in super::WRITE_BLOCKED_PREFIXES {
        if slash == prefix.trim_end_matches('/') || slash.starts_with(prefix) {
            return Err("차단된 시스템 경로에는 쓸 수 없습니다.".into());
        }
    }
    if content.len() > MAX {
        return Err("내용이 너무 큽니다.".into());
    }
    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent).map_err(|error| format!("쓰기 권한 없음: {error}"))?;
    }
    std::fs::write(&target, content).map_err(|error| format!("쓰기 권한 없음: {error}"))?;
    let bytes = target.metadata().map(|m| m.len()).unwrap_or(0);
    let mut payload = OrderedMap::new();
    payload.insert("path", json!(target.display().to_string()));
    payload.insert("bytes", json!(bytes));
    Ok(serde_json::to_value(payload).unwrap_or(Value::Null))
}

fn mime_of(path: &std::path::Path) -> &'static str {
    match path
        .extension()
        .and_then(|ext| ext.to_str())
        .unwrap_or("")
        .to_ascii_lowercase()
        .as_str()
    {
        "md" | "txt" | "py" | "rs" | "json" | "toml" => "text/plain; charset=utf-8",
        "html" => "text/html; charset=utf-8",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "pdf" => "application/pdf",
        _ => "application/octet-stream",
    }
}

#[allow(dead_code)]
pub(super) const _TTL: u64 = APPROVAL_TTL_SECS;
