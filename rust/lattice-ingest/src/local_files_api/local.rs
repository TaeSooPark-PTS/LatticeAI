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
use super::{expand_user, tool_error, tool_ok, LocalFilesState};

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
    let (fs_ok, fs_err) =
        tokio::task::spawn_blocking(move || match std::fs::create_dir_all(&probe_dir) {
            Ok(()) => {
                let name = format!(".local_agent_probe_{}", super::token_urlsafe(8));
                let path = probe_dir.join(name);
                let token = super::token_urlsafe(8);
                let res = std::fs::write(&path, &token)
                    .and_then(|_| std::fs::read_to_string(&path))
                    .map(|read| read == token);
                let _ = std::fs::remove_file(&path);
                match res {
                    Ok(ok) => (ok, None),
                    Err(error) => (false, Some(format!("filesystem: {error}"))),
                }
            }
            Err(error) => (false, Some(format!("filesystem: {error}"))),
        })
        .await
        .unwrap_or_else(|error| (false, Some(format!("filesystem task: {error}"))));
    if let Some(err) = fs_err {
        errors.push(err);
    }

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
    let path_owned = path.to_string();
    let res = tokio::task::spawn_blocking(move || list_dir(&path_owned))
        .await
        .map_err(|err| format!("디렉터리 조회 작업 실패: {err}"))
        .and_then(|inner| inner);
    match res {
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
    let path_owned = path.to_string();
    let res = tokio::task::spawn_blocking(move || read_file(&path_owned))
        .await
        .map_err(|err| format!("파일 읽기 작업 실패: {err}"))
        .and_then(|inner| inner);
    match res {
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
    let lang = language(&headers);
    let expanded = expand_user(&path);
    enum ServeOutcome {
        Forbidden,
        NotFound,
        Ok(Vec<u8>, &'static str),
    }
    let outcome = tokio::task::spawn_blocking(move || {
        let target = PathBuf::from(expanded);
        let target = std::fs::canonicalize(&target).unwrap_or(target);
        let slash = target.to_string_lossy().replace('\\', "/");
        for prefix in super::WRITE_BLOCKED_PREFIXES {
            if slash == prefix.trim_end_matches('/') || slash.starts_with(prefix) {
                return ServeOutcome::Forbidden;
            }
        }
        if !target.is_file() {
            return ServeOutcome::NotFound;
        }
        match std::fs::read(&target) {
            Ok(bytes) => ServeOutcome::Ok(bytes, mime_of(&target)),
            Err(_) => ServeOutcome::NotFound,
        }
    })
    .await
    .unwrap_or(ServeOutcome::NotFound);

    match outcome {
        ServeOutcome::Forbidden => http_error(403, "common.forbidden", lang),
        ServeOutcome::NotFound => http_error(404, "common.file_not_found", lang),
        ServeOutcome::Ok(bytes, mime) => {
            let mut response = Response::new(axum::body::Body::from(bytes));
            *response.status_mut() = axum::http::StatusCode::OK;
            response.headers_mut().insert(
                axum::http::header::CONTENT_TYPE,
                mime.parse().unwrap_or_else(|_| {
                    axum::http::HeaderValue::from_static("application/octet-stream")
                }),
            );
            response.headers_mut().insert(
                axum::http::header::HeaderName::from_static("x-content-type-options"),
                axum::http::HeaderValue::from_static("nosniff"),
            );
            response.headers_mut().insert(
                axum::http::header::CONTENT_SECURITY_POLICY,
                axum::http::HeaderValue::from_static(
                    "default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'",
                ),
            );
            response
        }
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
    let path_owned = path.to_string();
    let content_owned = content.to_string();
    let res = tokio::task::spawn_blocking(move || write_file(&path_owned, &content_owned))
        .await
        .map_err(|err| format!("파일 쓰기 작업 실패: {err}"))
        .and_then(|inner| inner);
    match res {
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

const READ_SENSITIVE_SEGMENTS: &[&str] = &[
    "/.ssh/",
    "/.aws/",
    "/.gnupg/",
    "/etc/shadow",
    "/etc/sudoers",
];

fn is_sensitive_read_path(path: &std::path::Path) -> bool {
    let slash = path.to_string_lossy().replace('\\', "/");
    let lower = slash.to_ascii_lowercase();
    if READ_SENSITIVE_SEGMENTS
        .iter()
        .any(|seg| lower.contains(seg) || lower.ends_with(seg.trim_end_matches('/')))
    {
        return true;
    }
    if let Some(name) = path.file_name().and_then(|n| n.to_str()) {
        let nl = name.to_ascii_lowercase();
        if nl == ".env" || nl.starts_with(".env.") || nl == "id_rsa" || nl == "id_ed25519" {
            return true;
        }
    }
    false
}

fn canonicalize_soft(path: &std::path::Path) -> PathBuf {
    if let Ok(c) = std::fs::canonicalize(path) {
        return c;
    }
    let mut missing = Vec::new();
    let mut cur = path.to_path_buf();
    while !cur.exists() {
        let Some(name) = cur.file_name() else {
            break;
        };
        missing.push(name.to_os_string());
        if !cur.pop() {
            break;
        }
    }
    let mut base = std::fs::canonicalize(&cur).unwrap_or(cur);
    for seg in missing.into_iter().rev() {
        base.push(seg);
    }
    base
}

fn read_file(path: &str) -> Result<Value, String> {
    const MAX: u64 = 512_000;
    let target = canonicalize_soft(&PathBuf::from(expand_user(path)));
    if is_sensitive_read_path(&target) {
        return Err("보안상 민감한 자격 증명 경로는 읽을 수 없습니다.".into());
    }
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
    let target = canonicalize_soft(&PathBuf::from(expand_user(path)));
    if is_sensitive_read_path(&target) {
        return Err("차단된 민감한 자격 증명 경로에는 쓸 수 없습니다.".into());
    }
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
        "webp" => "image/webp",
        "gif" => "image/gif",
        "pdf" => "application/pdf",
        _ => "application/octet-stream",
    }
}
