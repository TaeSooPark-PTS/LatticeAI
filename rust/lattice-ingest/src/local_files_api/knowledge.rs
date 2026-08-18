//! The eight `/knowledge-graph/local/*` routes.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use axum::body::Bytes;
use axum::extract::{RawQuery, State};
use axum::http::HeaderMap;
use axum::response::Response;
use lattice_auth::OrderedMap;
use lattice_core::pytext::safe_loads;
use lattice_core::CoreError;
use serde_json::{json, Value};

use super::http::{detail, language, ok, optional, required, FieldSpec, Kind, Model, Query};
use super::{expand_user, forward, LocalFilesState};

const TREE_REQUEST: &[FieldSpec] = &[
    required("path", Kind::Str(0)),
    optional("max_items", Kind::Int),
    optional("approved", Kind::Bool),
    optional("approval_token", Kind::OptStr),
];

const AUDIT_REQUEST: &[FieldSpec] = &[
    required("path", Kind::Str(0)),
    optional("include_ocr", Kind::Bool),
    optional("max_files", Kind::Int),
    optional("approved", Kind::Bool),
    optional("approval_token", Kind::OptStr),
];

const INDEX_REQUEST: &[FieldSpec] = &[
    required("path", Kind::Str(0)),
    optional("include_ocr", Kind::Bool),
    optional("watch_enabled", Kind::Bool),
    optional("max_files", Kind::Int),
    optional("consent", Kind::Object),
    optional("approved", Kind::Bool),
    optional("approval_token", Kind::OptStr),
];

const WATCH_STOP_REQUEST: &[FieldSpec] = &[required("source_id", Kind::Str(0))];

pub(super) async fn roots(
    State(state): State<Arc<LocalFilesState>>,
    headers: HeaderMap,
) -> Response {
    if let Err(refusal) = state.require_user(&headers) {
        return refusal;
    }
    let lang = language(&headers);
    if let Err(refusal) = state.require_graph(lang) {
        return refusal;
    }
    ok(&discover_roots())
}

pub(super) async fn sources(
    State(state): State<Arc<LocalFilesState>>,
    headers: HeaderMap,
) -> Response {
    if let Err(refusal) = state.require_user(&headers) {
        return refusal;
    }
    let lang = language(&headers);
    let store = match state.require_graph(lang) {
        Ok(store) => store.clone(),
        Err(refusal) => return refusal,
    };
    match store.read(load_sources).await {
        Ok(mut payload) => {
            if let Some(Value::Array(list)) = payload.get_mut("sources") {
                for source in list {
                    if let Some(object) = source.as_object_mut() {
                        object.insert("watch_active".into(), json!(false));
                        object.insert("watch_status".into(), Value::Null);
                    }
                }
            }
            if let Some(object) = payload.as_object_mut() {
                object.insert("watch".into(), json!({"available": false, "active": {}}));
            }
            ok(&payload)
        }
        Err(error) => detail(500, &error.to_string()),
    }
}

pub(super) async fn health(
    State(state): State<Arc<LocalFilesState>>,
    headers: HeaderMap,
    RawQuery(raw): RawQuery,
) -> Response {
    if let Err(refusal) = state.require_user(&headers) {
        return refusal;
    }
    let lang = language(&headers);
    let store = match state.require_graph(lang) {
        Ok(store) => store.clone(),
        Err(refusal) => return refusal,
    };
    let samples = match Query::parse(raw.as_deref()).int_or("error_samples", 3) {
        Ok(value) => value.clamp(0, 20),
        Err(refusal) => return refusal,
    };
    match store.read(move |conn| source_health(conn, samples)).await {
        Ok(mut payload) => {
            if let Some(Value::Array(folders)) = payload.get_mut("folders") {
                for folder in folders {
                    if let Some(object) = folder.as_object_mut() {
                        object.insert("watch_active".into(), json!(false));
                        let root = object
                            .get("root_path")
                            .and_then(Value::as_str)
                            .unwrap_or_default()
                            .to_string();
                        let deleted = if root.is_empty() {
                            Vec::new()
                        } else {
                            super::prune::deleted_files(&store, Path::new(&root))
                        };
                        if let Some(Value::Object(files)) = object.get_mut("files") {
                            files.insert("deleted".into(), json!(deleted.len()));
                        }
                        object.insert("deleted".into(), json!(deleted));
                    }
                }
            }
            attach_watch_deletions(&state, &mut payload);
            if let Some(object) = payload.as_object_mut() {
                object.insert("watch".into(), json!({"available": false, "active": {}}));
                object.insert(
                    "vector_freshness_global".into(),
                    json!({"status": "unavailable", "detail": "index status lives on lattice-jobs", "pending_items": 0, "total_items": 0}),
                );
            }
            ok(&payload)
        }
        Err(error) => detail(500, &error.to_string()),
    }
}

pub(super) async fn watch_status(
    State(state): State<Arc<LocalFilesState>>,
    headers: HeaderMap,
) -> Response {
    if let Err(refusal) = state.require_user(&headers) {
        return refusal;
    }
    let lang = language(&headers);
    if let Err(refusal) = state.require_graph(lang) {
        return refusal;
    }
    ok(&json!({
        "available": false,
        "active": {},
        "error": "watcher unavailable"
    }))
}

pub(super) async fn watch_stop(
    State(state): State<Arc<LocalFilesState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if let Err(refusal) = state.require_user(&headers) {
        return refusal;
    }
    let model = match Model::parse(&body, WATCH_STOP_REQUEST) {
        Ok(model) => model,
        Err(refusal) => return refusal,
    };
    let lang = language(&headers);
    if let Err(refusal) = state.require_graph(lang) {
        return refusal;
    }
    let source_id = model.str("source_id").trim().to_string();
    if source_id.is_empty() {
        return detail(404, "source_id required");
    }
    // One writer: `GraphWriter::set_local_source_watch`. The
    // `/worker/graph/mutate` delegation that used to stand behind this was
    // retired with the Python write door in v11.6.0, so a store without a
    // native writer has nowhere to send the change and says so.
    let Some(graph) = state.graph().cloned() else {
        return detail(404, &format!("knowledge source not found: {source_id}"));
    };
    let sid = source_id.clone();
    match tokio::task::spawn_blocking(move || graph.set_local_source_watch(&sid, false)).await {
        Ok(Ok(_)) => ok(&json!({
            "status": "ok",
            "watch": {"stopped": false, "source_id": source_id}
        })),
        Ok(Err(error)) => {
            let message = error.to_string();
            if message.contains("not found") {
                detail(404, &format!("knowledge source not found: {source_id}"))
            } else {
                detail(500, &message)
            }
        }
        Err(error) => detail(500, &error.to_string()),
    }
}

pub(super) async fn tree(
    State(state): State<Arc<LocalFilesState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let model = match Model::parse(&body, TREE_REQUEST) {
        Ok(model) => model,
        Err(refusal) => return refusal,
    };
    let user = match state.require_local_user(&headers) {
        Ok(user) => user,
        Err(refusal) => return refusal,
    };
    let lang = language(&headers);
    if let Err(refusal) = state.require_graph(lang) {
        return refusal;
    }
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
    match preview_tree(path, model.int("max_items", 200)) {
        Ok(value) => ok(&value),
        Err(message) => detail(400, &message),
    }
}

pub(super) async fn audit(
    State(state): State<Arc<LocalFilesState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let model = match Model::parse(&body, AUDIT_REQUEST) {
        Ok(model) => model,
        Err(refusal) => return refusal,
    };
    let user = match state.require_local_user(&headers) {
        Ok(user) => user,
        Err(refusal) => return refusal,
    };
    let lang = language(&headers);
    if let Err(refusal) = state.require_graph(lang) {
        return refusal;
    }
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
    match audit_folder(path, model.int("max_files", 50_000)) {
        Ok(value) => ok(&value),
        Err(message) => detail(400, &message),
    }
}

pub(super) async fn index(
    State(state): State<Arc<LocalFilesState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let model = match Model::parse(&body, INDEX_REQUEST) {
        Ok(model) => model,
        Err(refusal) => return refusal,
    };
    let user = match state.require_local_user(&headers) {
        Ok(user) => user,
        Err(refusal) => return refusal,
    };
    let lang = language(&headers);
    if let Err(refusal) = state.require_graph(lang) {
        return refusal;
    }
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
    let seam = match state.require_seam(lang) {
        Ok(seam) => seam,
        Err(refusal) => return refusal,
    };
    match forward(
        seam,
        &headers,
        "/knowledge-graph/local/index",
        &serde_json::from_slice(&body).unwrap_or(Value::Null),
    )
    .await
    {
        Ok(value) => ok(&value),
        Err(refusal) => refusal,
    }
}

fn discover_roots() -> Value {
    let os_type = if cfg!(target_os = "macos") {
        "macos"
    } else if cfg!(target_os = "windows") {
        "windows"
    } else {
        "linux"
    };
    let home = std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .unwrap_or_else(|_| "/".into());
    let computer = hostname();
    let mut roots = Vec::new();
    let mut seen = std::collections::BTreeSet::new();
    let mut add = |label: &str, path: PathBuf, kind: &str, recommended: bool| {
        let resolved = std::fs::canonicalize(&path).unwrap_or(path);
        if !resolved.exists() {
            return;
        }
        let key = resolved.display().to_string();
        if !seen.insert(key.clone()) {
            return;
        }
        roots.push(json!({
            "id": format!("{kind}:{}", super::sha256_hex(key.as_bytes()).chars().take(12).collect::<String>()),
            "label": label,
            "path": key,
            "kind": kind,
            "recommended": recommended,
            "warning": Value::Null,
        }));
    };
    add("홈", PathBuf::from(&home), "home", true);
    for (name, label) in [
        ("Documents", "문서"),
        ("Desktop", "데스크탑"),
        ("Downloads", "다운로드"),
        ("Pictures", "사진"),
        ("Projects", "프로젝트"),
    ] {
        add(
            label,
            PathBuf::from(&home).join(name),
            &name.to_ascii_lowercase(),
            true,
        );
    }
    json!({
        "os_type": os_type,
        "computer": computer,
        "roots": roots,
        "privacy_notice": "처음에는 드라이브와 폴더 구조만 확인하며, 파일 내용은 사용자가 동의한 뒤에만 읽습니다."
    })
}

fn hostname() -> String {
    std::process::Command::new("hostname")
        .output()
        .ok()
        .and_then(|out| String::from_utf8(out.stdout).ok())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "local".into())
}

fn load_sources(conn: &rusqlite::Connection) -> Result<Value, CoreError> {
    let mut statement = match conn.prepare(
        "SELECT id, root_path, os_type, drive_id, label, status, include_ocr, \
                watch_enabled, consent_json, created_at, updated_at, last_scanned_at \
         FROM knowledge_sources ORDER BY updated_at DESC, id ASC",
    ) {
        Ok(statement) => statement,
        Err(_) => return Ok(json!({"sources": []})),
    };
    let rows = statement.query_map([], |row| {
        let consent = safe_loads(row.get::<_, Option<String>>(8)?.as_deref());
        Ok(json!({
            "id": row.get::<_, String>(0)?,
            "root_path": row.get::<_, Option<String>>(1)?.unwrap_or_default(),
            "os_type": row.get::<_, Option<String>>(2)?.unwrap_or_default(),
            "drive_id": row.get::<_, Option<String>>(3)?.unwrap_or_default(),
            "label": row.get::<_, Option<String>>(4)?.unwrap_or_default(),
            "status": row.get::<_, Option<String>>(5)?.unwrap_or_default(),
            "include_ocr": row.get::<_, i64>(6).unwrap_or(0) != 0,
            "watch_enabled": row.get::<_, i64>(7).unwrap_or(0) != 0,
            "consent": Value::Object(consent),
            "created_at": row.get::<_, Option<String>>(9)?.unwrap_or_default(),
            "updated_at": row.get::<_, Option<String>>(10)?.unwrap_or_default(),
            "last_scanned_at": row.get::<_, Option<String>>(11)?.unwrap_or_default(),
        }))
    })?;
    let mut sources: Vec<Value> = rows.filter_map(Result::ok).collect();
    let mut counts: std::collections::BTreeMap<String, serde_json::Map<String, Value>> =
        std::collections::BTreeMap::new();
    if let Ok(mut statement) = conn.prepare(
        "SELECT source_id, status, COUNT(*) FROM local_file_index GROUP BY source_id, status",
    ) {
        if let Ok(rows) = statement.query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, i64>(2)?,
            ))
        }) {
            for (source_id, status, count) in rows.filter_map(Result::ok) {
                counts
                    .entry(source_id)
                    .or_default()
                    .insert(status, json!(count));
            }
        }
    }
    for source in &mut sources {
        let id = source.get("id").and_then(Value::as_str).unwrap_or_default();
        let status = counts
            .get(id)
            .cloned()
            .map(Value::Object)
            .unwrap_or_else(|| json!({}));
        if let Some(object) = source.as_object_mut() {
            object.insert("file_status".into(), status);
        }
    }
    Ok(json!({"sources": sources}))
}

fn source_health(conn: &rusqlite::Connection, samples: i64) -> Result<Value, CoreError> {
    let payload = load_sources(conn)?;
    let sources = payload
        .get("sources")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let mut folders = Vec::new();
    for source in &sources {
        let id = source.get("id").and_then(Value::as_str).unwrap_or_default();
        let counts = source
            .get("file_status")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();
        let total: i64 = counts.values().filter_map(Value::as_i64).sum();
        let indexed = counts.get("indexed").and_then(Value::as_i64).unwrap_or(0);
        let failed = counts.get("failed").and_then(Value::as_i64).unwrap_or(0)
            + counts.get("error").and_then(Value::as_i64).unwrap_or(0);
        let skipped = counts.get("skipped").and_then(Value::as_i64).unwrap_or(0);
        let mut recent = Vec::new();
        if samples > 0 {
            if let Ok(mut statement) = conn.prepare(
                "SELECT relative_path, status, error_message, last_scanned_at \
                 FROM local_file_index \
                 WHERE source_id=? AND error_message IS NOT NULL AND error_message<>'' \
                 ORDER BY last_scanned_at DESC LIMIT ?",
            ) {
                if let Ok(rows) = statement.query_map(rusqlite::params![id, samples], |row| {
                    Ok(json!({
                        "path": row.get::<_, Option<String>>(0)?.unwrap_or_default(),
                        "status": row.get::<_, Option<String>>(1)?.unwrap_or_default(),
                        "detail": row.get::<_, Option<String>>(2)?.unwrap_or_default().chars().take(300).collect::<String>(),
                        "at": row.get::<_, Option<String>>(3)?.unwrap_or_default(),
                    }))
                }) {
                    recent = rows.filter_map(Result::ok).collect();
                }
            }
        }
        folders.push(json!({
            "id": source.get("id").cloned().unwrap_or(Value::Null),
            "label": source.get("label").cloned().or_else(|| source.get("root_path").cloned()).unwrap_or(Value::Null),
            "root_path": source.get("root_path").cloned().unwrap_or(Value::Null),
            "status": source.get("status").cloned().unwrap_or(Value::Null),
            "watch_enabled": source.get("watch_enabled").cloned().unwrap_or(json!(false)),
            "last_scanned_at": source.get("last_scanned_at").cloned().unwrap_or(Value::Null),
            "files": {
                "total": total,
                "indexed": indexed,
                "failed": failed,
                "skipped": skipped,
                "pending": (total - indexed - failed - skipped).max(0)
            },
            "coverage": if total > 0 { json!(((indexed as f64 / total as f64) * 10_000.0).round() / 10_000.0) } else { Value::Null },
            "recent_errors": recent
        }));
    }
    Ok(json!({"folders": folders, "count": folders.len()}))
}

fn preview_tree(path: &str, max_items: i64) -> Result<Value, String> {
    let root = PathBuf::from(expand_user(path));
    let root = std::fs::canonicalize(&root).unwrap_or(root);
    if !root.exists() {
        return Err(format!("경로가 존재하지 않습니다: {path}"));
    }
    if !root.is_dir() {
        return Err(format!("폴더가 아닙니다: {path}"));
    }
    let max_items = max_items.clamp(1, 1000) as usize;
    let mut children: Vec<_> = std::fs::read_dir(&root)
        .map_err(|error| format!("접근 권한 없음: {error}"))?
        .filter_map(Result::ok)
        .collect();
    children.sort_by_key(|entry| {
        let is_dir = entry.path().is_dir();
        (!is_dir, entry.file_name().to_string_lossy().to_lowercase())
    });
    let mut items = Vec::new();
    for entry in children.into_iter().take(max_items) {
        let child = entry.path();
        let is_dir = child.is_dir();
        let meta = entry.metadata().ok();
        items.push(json!({
            "name": child.file_name().map(|n| n.to_string_lossy()).unwrap_or_default(),
            "path": child.display().to_string(),
            "type": if is_dir { "directory" } else { "file" },
            "extension": if is_dir { String::new() } else { child.extension().map(|e| format!(".{}", e.to_string_lossy().to_ascii_lowercase())).unwrap_or_default() },
            "size_bytes": if is_dir { Value::Null } else { json!(meta.map(|m| m.len()).unwrap_or(0)) },
        }));
    }
    Ok(json!({
        "path": root.display().to_string(),
        "items": items,
        "privacy_notice": "현재 단계에서는 파일 내용을 읽지 않고, 폴더와 파일의 이름/크기/수정일만 확인합니다."
    }))
}

fn attach_watch_deletions(state: &LocalFilesState, payload: &mut Value) {
    let watch = super::watch_bridge::read_watch_file(state.config.data_dir());
    let watches = watch
        .get("watches")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let existing: std::collections::HashSet<String> = payload
        .get("folders")
        .and_then(Value::as_array)
        .map(|folders| {
            folders
                .iter()
                .filter_map(|folder| {
                    folder
                        .get("root_path")
                        .and_then(Value::as_str)
                        .map(str::to_string)
                })
                .collect()
        })
        .unwrap_or_default();
    let mut extra = Vec::new();
    for entry in &watches {
        let path = entry
            .get("path")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let deleted = entry
            .get("last_result")
            .and_then(|result| result.get("deleted"))
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        if deleted.is_empty() || existing.contains(path) {
            continue;
        }
        extra.push(json!({
            "id": entry.get("id").cloned().unwrap_or(Value::Null),
            "label": path,
            "root_path": path,
            "status": "watching",
            "watch_active": true,
            "files": {
                "total": 0,
                "indexed": 0,
                "failed": 0,
                "skipped": 0,
                "pending": 0,
                "deleted": deleted.len()
            },
            "coverage": Value::Null,
            "recent_errors": [],
            "deleted": deleted,
        }));
    }
    let folder_count = if let Some(Value::Array(folders)) = payload.get_mut("folders") {
        folders.extend(extra);
        folders.len()
    } else {
        0
    };
    if let Some(object) = payload.as_object_mut() {
        object.insert("count".into(), json!(folder_count));
    }
    let pending: usize = payload
        .get("folders")
        .and_then(Value::as_array)
        .map(|folders| {
            folders
                .iter()
                .map(|folder| {
                    folder
                        .get("deleted")
                        .and_then(Value::as_array)
                        .map(Vec::len)
                        .unwrap_or(0)
                })
                .sum()
        })
        .unwrap_or(0);
    if let Some(object) = payload.as_object_mut() {
        object.insert("pending_deletions".into(), json!(pending));
    }
}

fn audit_folder(path: &str, max_files: i64) -> Result<Value, String> {
    let root = PathBuf::from(expand_user(path));
    let root = std::fs::canonicalize(&root).unwrap_or(root);
    if !root.exists() {
        return Err(format!("경로가 존재하지 않습니다: {path}"));
    }
    if !root.is_dir() {
        return Err(format!("폴더가 아닙니다: {path}"));
    }
    let mut files = Vec::new();
    walk(
        &root,
        &root,
        max_files.clamp(1, 50_000) as usize,
        &mut files,
    );
    let mut by_status = OrderedMap::new();
    by_status.insert("allowed", json!(files.len()));
    Ok(json!({
        "path": root.display().to_string(),
        "files": files,
        "by_status": serde_json::to_value(by_status).unwrap_or(Value::Null),
        "consent_required": {
            "knowledge_source": true,
            "image_ocr": false,
            "watch": true,
            "sensitive_files_default_excluded": true
        }
    }))
}

fn walk(root: &Path, current: &Path, cap: usize, out: &mut Vec<Value>) {
    if out.len() >= cap {
        return;
    }
    let Ok(entries) = std::fs::read_dir(current) else {
        return;
    };
    let mut children: Vec<_> = entries.filter_map(Result::ok).collect();
    children.sort_by_key(|entry| entry.file_name().to_string_lossy().to_lowercase());
    for entry in children {
        if out.len() >= cap {
            break;
        }
        let path = entry.path();
        if path.is_dir() {
            walk(root, &path, cap, out);
        } else {
            out.push(json!({
                "name": path.file_name().map(|n| n.to_string_lossy()).unwrap_or_default(),
                "path": path.display().to_string(),
                "relative_path": path.strip_prefix(root).unwrap_or(&path).display().to_string(),
            }));
        }
    }
}
