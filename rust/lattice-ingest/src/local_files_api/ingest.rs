//! Ingestion jobs, folder/obsidian/interop, and folder-watch routes.

use std::sync::Arc;

use axum::body::Bytes;
use axum::extract::{Path as AxumPath, RawQuery, State};
use axum::http::HeaderMap;
use axum::response::Response;
use lattice_auth::OrderedMap;
use lattice_core::CoreError;
use serde_json::{json, Value};

use super::http::{
    detail, http_error, http_error_with, language, ok, optional, required, FieldSpec, Kind, Model,
    Query,
};
use super::watch_bridge::{
    disable_watch, enable_watch, ensure_poller, read_watch_file, vault_watch_on,
};
use super::{forward, LocalFilesState};

const FOLDER_REQUEST: &[FieldSpec] = &[
    required("path", Kind::Str(0)),
    optional("recursive", Kind::Bool),
    optional("background", Kind::Bool),
    optional("workspace_id", Kind::OptStr),
    optional("approved", Kind::Bool),
    optional("approval_token", Kind::OptStr),
];

const OBSIDIAN_REQUEST: &[FieldSpec] = &[
    required("path", Kind::Str(0)),
    optional("workspace_id", Kind::OptStr),
    optional("dry_run", Kind::Bool),
    optional("approved", Kind::Bool),
    optional("approval_token", Kind::OptStr),
];

const INTEROP_REQUEST: &[FieldSpec] = &[
    required("source", Kind::Str(0)),
    required("path", Kind::Str(0)),
    optional("workspace_id", Kind::OptStr),
    optional("dry_run", Kind::Bool),
    optional("max_commits", Kind::Int),
    optional("approved", Kind::Bool),
    optional("approval_token", Kind::OptStr),
];

const WATCH_ENABLE_REQUEST: &[FieldSpec] = &[
    required("path", Kind::Str(0)),
    optional("recursive", Kind::Bool),
    optional("workspace_id", Kind::OptStr),
    optional("kind", Kind::OptStr),
    optional("approved", Kind::Bool),
    optional("approval_token", Kind::OptStr),
];

const GIT_UNAVAILABLE: &str = "reading a repository's history needs git on this machine and none was found; nothing was ingested";

pub(super) async fn jobs(
    State(state): State<Arc<LocalFilesState>>,
    headers: HeaderMap,
    RawQuery(raw): RawQuery,
) -> Response {
    if let Err(refusal) = state.require_user(&headers) {
        return refusal;
    }
    let lang = language(&headers);
    if let Err(refusal) = state.require_graph(lang) {
        return refusal;
    }
    let limit = match Query::parse(raw.as_deref()).int_or("limit", 20) {
        Ok(limit) => limit.clamp(1, 100),
        Err(refusal) => return refusal,
    };
    let store = state.store.clone().expect("require_graph");
    match store.read(move |conn| list_jobs(conn, limit)).await {
        Ok(jobs) => {
            let mut payload = OrderedMap::new();
            payload.insert("jobs", Value::Array(jobs));
            ok(&serde_json::to_value(payload).unwrap_or(Value::Null))
        }
        Err(_) => {
            let mut payload = OrderedMap::new();
            payload.insert("jobs", json!([]));
            ok(&serde_json::to_value(payload).unwrap_or(Value::Null))
        }
    }
}

pub(super) async fn job_detail(
    State(state): State<Arc<LocalFilesState>>,
    headers: HeaderMap,
    AxumPath(job_id): AxumPath<String>,
) -> Response {
    if let Err(refusal) = state.require_user(&headers) {
        return refusal;
    }
    let lang = language(&headers);
    let store = match state.require_graph(lang) {
        Ok(store) => store.clone(),
        Err(refusal) => return refusal,
    };
    match store.read(move |conn| load_job(conn, &job_id)).await {
        Ok(Some(job)) => ok(&job),
        Ok(None) => http_error(404, "ingestion.job_not_found", lang),
        Err(_) => http_error(404, "ingestion.job_not_found", lang),
    }
}

pub(super) async fn job_resume(
    State(state): State<Arc<LocalFilesState>>,
    headers: HeaderMap,
    AxumPath(job_id): AxumPath<String>,
) -> Response {
    if let Err(refusal) = state.require_user(&headers) {
        return refusal;
    }
    let lang = language(&headers);
    let store = match state.require_graph(lang) {
        Ok(store) => store.clone(),
        Err(refusal) => return refusal,
    };
    let id = job_id.clone();
    let job = match store.read(move |conn| load_job(conn, &id)).await {
        Ok(Some(job)) => job,
        Ok(None) | Err(_) => return http_error(404, "ingestion.job_not_found", lang),
    };
    if job.get("status").and_then(Value::as_str) == Some("running") {
        let mut payload = OrderedMap::new();
        payload.insert("status", json!("already_running"));
        payload.insert("job_id", json!(job_id));
        payload.insert("job", job);
        return ok(&serde_json::to_value(payload).unwrap_or(Value::Null));
    }
    let remaining = remaining_of(&job);
    if remaining == 0 && job.get("status").and_then(Value::as_str) == Some("completed") {
        let mut payload = OrderedMap::new();
        payload.insert("status", json!("nothing_to_resume"));
        payload.insert("job_id", json!(job_id));
        payload.insert("job", job);
        return ok(&serde_json::to_value(payload).unwrap_or(Value::Null));
    }
    let seam = match state.require_seam(lang) {
        Ok(seam) => seam,
        Err(refusal) => return refusal,
    };
    match forward(
        seam,
        &headers,
        &format!("/api/ingestion/jobs/{job_id}/resume"),
        &json!({}),
    )
    .await
    {
        Ok(value) => ok(&value),
        Err(refusal) => refusal,
    }
}

pub(super) async fn folder(
    State(state): State<Arc<LocalFilesState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let model = match Model::parse(&body, FOLDER_REQUEST) {
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
    let path = model.str("path").trim().to_string();
    if path.is_empty() {
        return http_error(400, "common.path_required", lang);
    }
    if !model.bool("approved", false) {
        return ok(&state.permissions.probe(&path, "read", &user, ""));
    }
    if let Err(refusal) = state.permissions.require(
        model.get("approval_token").and_then(Value::as_str),
        &path,
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
    let forwarded = json!({
        "path": path,
        "recursive": model.bool("recursive", true),
        "background": model.bool("background", false),
        "workspace_id": model.get("workspace_id").cloned().unwrap_or(Value::Null),
        "approved": true,
        "approval_token": model.get("approval_token").cloned().unwrap_or(Value::Null),
    });
    match forward(seam, &headers, "/api/ingestion/folder", &forwarded).await {
        Ok(value) => ok(&value),
        Err(refusal) => refusal,
    }
}

pub(super) async fn obsidian(
    State(state): State<Arc<LocalFilesState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let model = match Model::parse(&body, OBSIDIAN_REQUEST) {
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
    let path = model.str("path").trim().to_string();
    if path.is_empty() {
        return http_error(400, "ingestion.vault_path_required", lang);
    }
    if !model.bool("approved", false) {
        return ok(&state.permissions.probe(&path, "read", &user, ""));
    }
    if let Err(refusal) = state.permissions.require(
        model.get("approval_token").and_then(Value::as_str),
        &path,
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
    let forwarded = json!({
        "path": path,
        "workspace_id": model.get("workspace_id").cloned().unwrap_or(Value::Null),
        "dry_run": model.bool("dry_run", false),
        "approved": true,
        "approval_token": model.get("approval_token").cloned().unwrap_or(Value::Null),
    });
    match forward(seam, &headers, "/api/ingestion/obsidian", &forwarded).await {
        Ok(value) => ok(&value),
        Err(refusal) => refusal,
    }
}

pub(super) async fn interop_status(
    State(state): State<Arc<LocalFilesState>>,
    headers: HeaderMap,
) -> Response {
    if let Err(refusal) = state.require_user(&headers) {
        return refusal;
    }
    ok(&bridge_status())
}

pub(super) async fn interop_ingest(
    State(state): State<Arc<LocalFilesState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let model = match Model::parse(&body, INTEROP_REQUEST) {
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
    let path = model.str("path").trim().to_string();
    if path.is_empty() {
        return http_error(400, "ingestion.interop_path_required", lang);
    }
    let source = model.str("source");
    if !matches!(source, "notion" | "git" | "mail") {
        return http_error_with(
            400,
            "ingestion.interop_unknown_source",
            lang,
            &[("source", source)],
        );
    }
    if !model.bool("approved", false) {
        return ok(&state.permissions.probe(&path, "read", &user, ""));
    }
    if let Err(refusal) = state.permissions.require(
        model.get("approval_token").and_then(Value::as_str),
        &path,
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
        "/api/ingestion/interop",
        &serde_json::from_slice(&body).unwrap_or(Value::Null),
    )
    .await
    {
        Ok(value) => ok(&value),
        Err(refusal) => refusal,
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
    ok(&read_watch_file(state.config.data_dir()))
}

pub(super) async fn watch_enable(
    State(state): State<Arc<LocalFilesState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let model = match Model::parse(&body, WATCH_ENABLE_REQUEST) {
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
    let path = model.str("path").trim().to_string();
    if path.is_empty() {
        return http_error(400, "common.path_required", lang);
    }
    if !model.bool("approved", false) {
        return ok(&state.permissions.probe(&path, "read", &user, ""));
    }
    if let Err(refusal) = state.permissions.require(
        model.get("approval_token").and_then(Value::as_str),
        &path,
        "read",
        &user,
        "",
    ) {
        return refusal;
    }
    let kind = model
        .get("kind")
        .and_then(Value::as_str)
        .unwrap_or("folder");
    if kind == "vault" && !vault_watch_on() {
        return detail(
            403,
            "vault watch is off by default; turn it on to let an external vault re-sync in the background (LATTICEAI_VAULT_WATCH)",
        );
    }
    let enabled = enable_watch(
        &state,
        &path,
        kind,
        model.bool("recursive", true),
        &user,
        model.get("workspace_id").and_then(Value::as_str),
    );
    // The declaration is persisted and its baseline taken; start delivering.
    ensure_poller(&state);
    ok(&enabled)
}

pub(super) async fn watch_disable(
    State(state): State<Arc<LocalFilesState>>,
    headers: HeaderMap,
    RawQuery(raw): RawQuery,
) -> Response {
    if let Err(refusal) = state.require_user(&headers) {
        return refusal;
    }
    let lang = language(&headers);
    if let Err(refusal) = state.require_graph(lang) {
        return refusal;
    }
    let query = Query::parse(raw.as_deref());
    let watch_id = query.raw("watch_id").unwrap_or("").to_string();
    let path = query.raw("path").unwrap_or("").to_string();
    if watch_id.is_empty() && path.is_empty() {
        return http_error(400, "ingestion.watch_selector_required", lang);
    }
    match disable_watch(state.config.data_dir(), &watch_id, &path) {
        Some(result) => ok(&result),
        None => http_error(404, "ingestion.watch_not_found", lang),
    }
}

fn list_jobs(conn: &rusqlite::Connection, limit: i64) -> Result<Vec<Value>, CoreError> {
    let mut statement = match conn.prepare(
        "SELECT job_id, status, total, processed, failed, errors_json, created_at, updated_at \
         FROM ingestion_jobs ORDER BY created_at DESC, job_id DESC LIMIT ?",
    ) {
        Ok(statement) => statement,
        Err(_) => return Ok(Vec::new()),
    };
    let rows = statement.query_map([limit], job_from_row)?;
    Ok(rows.filter_map(Result::ok).collect())
}

fn load_job(conn: &rusqlite::Connection, job_id: &str) -> Result<Option<Value>, CoreError> {
    let mut statement = match conn.prepare(
        "SELECT job_id, status, total, processed, failed, errors_json, created_at, updated_at, \
                items_json, done_indices_json \
         FROM ingestion_jobs WHERE job_id=?",
    ) {
        Ok(statement) => statement,
        Err(_) => return Ok(None),
    };
    let mut rows = statement.query([job_id])?;
    let Some(row) = rows.next()? else {
        return Ok(None);
    };
    Ok(Some(job_from_row(row)?))
}

fn job_from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<Value> {
    let mut job = OrderedMap::new();
    job.insert("job_id", json!(row.get::<_, String>(0)?));
    job.insert("status", json!(row.get::<_, String>(1)?));
    job.insert("total", json!(row.get::<_, i64>(2).unwrap_or(0)));
    job.insert("processed", json!(row.get::<_, i64>(3).unwrap_or(0)));
    job.insert("failed", json!(row.get::<_, i64>(4).unwrap_or(0)));
    let errors: Value = row
        .get::<_, Option<String>>(5)
        .ok()
        .flatten()
        .and_then(|raw| serde_json::from_str(&raw).ok())
        .unwrap_or_else(|| json!([]));
    job.insert("errors", errors);
    job.insert(
        "created_at",
        json!(row.get::<_, Option<String>>(6)?.unwrap_or_default()),
    );
    job.insert(
        "updated_at",
        json!(row.get::<_, Option<String>>(7)?.unwrap_or_default()),
    );
    Ok(serde_json::to_value(job).unwrap_or(Value::Null))
}

fn remaining_of(job: &Value) -> i64 {
    let total = job.get("total").and_then(Value::as_i64).unwrap_or(0);
    let processed = job.get("processed").and_then(Value::as_i64).unwrap_or(0);
    (total - processed).max(0)
}

fn bridge_status() -> Value {
    let git = which_git();
    json!({
        "sources": {
            "notion": {
                "available": true,
                "accepts": ["directory", ".zip"],
                "detail": "a Notion export you downloaded — never the Notion API"
            },
            "git": {
                "available": git,
                "accepts": ["repository directory"],
                "detail": if git { Value::Null } else { json!(GIT_UNAVAILABLE) }
            },
            "mail": {
                "available": true,
                "accepts": [".eml", ".ics", "a folder of either"],
                "detail": "local files only; connecting a live mailbox or system calendar is deliberately out of scope"
            }
        }
    })
}

fn which_git() -> bool {
    std::process::Command::new("git")
        .arg("--version")
        .output()
        .map(|out| out.status.success())
        .unwrap_or(false)
}

/// `POST /upload/document` — parse via the worker, write via GraphWriter.
pub(super) async fn upload_document(
    State(state): State<Arc<LocalFilesState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if let Err(refusal) = state.require_user(&headers) {
        return refusal;
    }
    let lang = language(&headers);
    let Some(graph) = state.graph().cloned() else {
        return http_error(503, "capture.ingestion_disabled", lang);
    };
    let (filename, bytes) = super::enrich::unwrap_upload(&headers, &body);
    let mime = super::enrich::mime_hint(&filename);
    let parsed = if super::enrich::needs_parse(&filename, &bytes) {
        super::enrich::parse_via_seam(state.seam(), &filename, &bytes).await
    } else {
        None
    };
    let text = if super::enrich::needs_parse(&filename, &bytes) {
        parsed
            .as_ref()
            .map(super::enrich::parsed_text)
            .unwrap_or_default()
    } else {
        std::str::from_utf8(&bytes).unwrap_or("").to_string()
    };
    let extract_text = if text.trim().is_empty() {
        String::new()
    } else {
        format!("{filename}\n{text}")
    };
    let extracted = super::enrich::extract_via_seam(state.seam(), &extract_text, "document").await;
    let embedding = super::enrich::embed_via_seam(state.seam(), &extract_text).await;
    let mut chunks =
        super::enrich::chunk_pieces_for(&text, &filename, mime.as_deref().unwrap_or(""));
    let chunk_texts: Vec<String> = chunks.iter().map(|piece| piece.text.clone()).collect();
    let chunk_batch = super::enrich::embed_texts_via_seam(state.seam(), &chunk_texts).await;
    let chunk_agrees = match chunk_batch.as_ref() {
        Some((model_id, dim, _)) => super::enrich::model_agrees(&graph, model_id, *dim),
        None => true,
    };
    chunks = super::enrich::attach_chunk_embeddings(chunks, chunk_batch, chunk_agrees);
    let mut extracted_map = serde_json::Map::new();
    if !text.is_empty() {
        extracted_map.insert("content".into(), json!(text));
    }
    if let Some(parsed) = parsed {
        for (key, value) in parsed {
            if key != "content" && key != "filename" {
                extracted_map.entry(key).or_insert(value);
            }
        }
    }
    let tmp = std::env::temp_dir().join(format!(
        "lattice-upload-{}",
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0)
    ));
    if let Err(error) = std::fs::write(&tmp, &bytes) {
        return detail(500, &error.to_string());
    }
    let request = lattice_core::graph_write::types::IngestFileRequest {
        path: tmp.clone(),
        original_filename: Some(filename),
        mime_type: mime,
        extracted: extracted_map,
        chunks,
        concepts: extracted.concepts,
        triples: extracted.triples,
        semantic: extracted.semantic,
        embedding,
        ..Default::default()
    };
    let outcome = match tokio::task::spawn_blocking(move || graph.ingest_file(&request)).await {
        Ok(Ok(outcome)) => outcome,
        Ok(Err(error)) => {
            let _ = std::fs::remove_file(&tmp);
            return detail(500, &error.to_string());
        }
        Err(error) => {
            let _ = std::fs::remove_file(&tmp);
            return detail(500, &error.to_string());
        }
    };
    let _ = std::fs::remove_file(&tmp);
    ok(&outcome.to_json())
}
