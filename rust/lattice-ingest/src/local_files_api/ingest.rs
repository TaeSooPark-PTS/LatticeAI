//! Ingestion jobs, folder/obsidian/interop, and folder-watch routes.

use std::sync::Arc;

use axum::body::Bytes;
use axum::extract::{Path as AxumPath, RawQuery, State};
use axum::http::HeaderMap;
use axum::response::Response;
use lattice_auth::OrderedMap;
use serde_json::{json, Value};

use super::http::{
    detail, http_error, http_error_with, language, ok, optional, required, FieldSpec, Kind, Model,
    Query,
};
use super::jobs_store::{bridge_status, list_jobs, load_job, remaining_of};
pub(super) use super::jobs_store::{next_job_id, now_stamp, write_job};
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

const FOLDER_PRUNE_REQUEST: &[FieldSpec] = &[
    required("path", Kind::Str(0)),
    optional("confirm", Kind::Bool),
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
    // `require_user`, not `require_local_user`: the trusted local owner is
    // the owner identity on a no-auth loopback install, same as every other
    // product route. A named session still wins when one is present.
    let user = match state.require_user(&headers) {
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
    let Some(graph) = state.graph().cloned() else {
        // No native writer: keep the old seam path for a mis-wired host.
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
        return match forward(seam, &headers, "/api/ingestion/folder", &forwarded).await {
            Ok(value) => ok(&value),
            Err(refusal) => refusal,
        };
    };
    let recursive = model.bool("recursive", true);
    let background = model.bool("background", false);
    let workspace_id = model
        .get("workspace_id")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_string);
    let owner = if user.is_empty() { None } else { Some(user) };
    match super::folder::ingest_folder_native(
        &state,
        graph,
        &path,
        recursive,
        background,
        owner.as_deref(),
        workspace_id.as_deref(),
    )
    .await
    {
        Ok(value) => ok(&value),
        Err(message) => detail(400, &message),
    }
}

pub(super) async fn folder_prune(
    State(state): State<Arc<LocalFilesState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let model = match Model::parse(&body, FOLDER_PRUNE_REQUEST) {
        Ok(model) => model,
        Err(refusal) => return refusal,
    };
    if let Err(refusal) = state.require_user(&headers) {
        return refusal;
    }
    let lang = language(&headers);
    if let Err(refusal) = state.require_graph(lang) {
        return refusal;
    }
    let path = model.str("path").trim().to_string();
    if path.is_empty() {
        return http_error(400, "common.path_required", lang);
    }
    let Some(graph) = state.graph().cloned() else {
        return http_error(503, "capture.ingestion_disabled", lang);
    };
    let confirm = model.bool("confirm", false);
    match super::prune::prune_deleted(&graph, std::path::Path::new(&path), confirm) {
        Ok(value) => ok(&value),
        Err(message) => detail(400, &message),
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
    let dry_run = model.bool("dry_run", false);
    let workspace_id = model
        .get("workspace_id")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_string);
    let owner = if user.is_empty() { None } else { Some(user) };
    let Some(graph) = state.graph().cloned() else {
        return http_error(503, "capture.ingestion_disabled", lang);
    };
    match super::interop::ingest_obsidian_native(
        &state,
        graph,
        &path,
        dry_run,
        owner.as_deref(),
        workspace_id.as_deref(),
    )
    .await
    {
        Ok(value) => ok(&value),
        Err(message) => detail(400, &message),
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
    let dry_run = model.bool("dry_run", false);
    let workspace_id = model
        .get("workspace_id")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_string);
    let owner = if user.is_empty() { None } else { Some(user) };
    let Some(graph) = state.graph().cloned() else {
        return http_error(503, "capture.ingestion_disabled", lang);
    };
    match super::interop::ingest_interop_native(
        &state,
        graph,
        source,
        &path,
        dry_run,
        owner.as_deref(),
        workspace_id.as_deref(),
    )
    .await
    {
        Ok(value) => ok(&value),
        Err(message) => detail(400, &message),
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
    let needs_parse = super::enrich::needs_parse(&filename, &bytes);
    let parsed = if needs_parse {
        super::enrich::parse_via_seam(state.seam(), &filename, &bytes).await
    } else {
        None
    };
    let parse_failed = needs_parse && parsed.is_none();
    let text = if needs_parse {
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
    let (extracted, extract_ok) =
        super::enrich::extract_via_seam_status(state.seam(), &extract_text, "document").await;
    let doc_embed = if extract_text.trim().is_empty() {
        None
    } else {
        Some(
            super::enrich::embed_texts_with_contract(
                state.seam(),
                std::slice::from_ref(&extract_text),
            )
            .await,
        )
    };
    let embedding = super::enrich::first_vector_from_batch(
        doc_embed
            .as_ref()
            .and_then(super::enrich::SeamEmbedOutcome::batch),
    );
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
    let structure_plan = crate::document_structure::extract_document_structure(
        &filename,
        mime.as_deref().unwrap_or(""),
        &text,
        &extracted_map,
        None,
    );
    let mut chunks =
        super::enrich::chunk_pieces_for(&text, &filename, mime.as_deref().unwrap_or(""));
    chunks.extend(structure_plan.table_chunks.clone());
    let chunk_texts: Vec<String> = chunks.iter().map(|piece| piece.text.clone()).collect();
    let chunk_embed = if chunk_texts.is_empty() {
        None
    } else {
        Some(super::enrich::embed_texts_with_contract(state.seam(), &chunk_texts).await)
    };
    chunks = super::enrich::attach_chunk_embeddings_from_batch(
        chunks,
        chunk_embed
            .as_ref()
            .and_then(super::enrich::SeamEmbedOutcome::batch),
        true,
    );
    static UPLOAD_TMP_SEQ: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);
    let tmp_seq = UPLOAD_TMP_SEQ.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let tmp = std::env::temp_dir().join(format!(
        "lattice-upload-{}-{}-{tmp_seq}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0)
    ));
    if let Err(error) = std::fs::write(&tmp, &bytes) {
        return detail(500, &error.to_string());
    }
    let has_supplied_vectors =
        embedding.is_some() || chunks.iter().any(|piece| piece.embedding.is_some());
    let incoming_contract = chunk_embed
        .as_ref()
        .and_then(super::enrich::SeamEmbedOutcome::contract)
        .or_else(|| {
            doc_embed
                .as_ref()
                .and_then(super::enrich::SeamEmbedOutcome::contract)
        })
        .cloned();
    let chunks_for_overlay = chunks.clone();
    let request = lattice_core::graph_write::types::IngestFileRequest {
        path: tmp.clone(),
        original_filename: Some(filename),
        mime_type: mime,
        extracted: extracted_map,
        structure: structure_plan.structure.clone(),
        structure_nodes: structure_plan.structure_nodes.clone(),
        chunks,
        concepts: extracted.concepts,
        triples: extracted.triples,
        semantic: extracted.semantic,
        embedding,
        ..Default::default()
    };
    let (outcome, reindex_required, active_contract, overlay_warning) =
        match tokio::task::spawn_blocking(move || {
            let prior_contract = graph.current_index_contract().ok().flatten();
            let reindex = prior_contract
                .as_ref()
                .zip(incoming_contract.as_ref())
                .is_some_and(|(prev, next)| !prev.is_compatible_with(next));
            let outcome = graph.ingest_file(&request)?;
            let overlay_warning =
                match crate::document_structure::persist_structure_and_multimodal_overlay(
                    &graph,
                    &outcome.node_id,
                    &chunks_for_overlay,
                    &outcome.chunk_ids,
                    &structure_plan,
                    None,
                    None,
                ) {
                    Ok(_) => None,
                    Err(err) => {
                        eprintln!(
                            "[lattice-ingest] structure overlay persist failed for {}: {err}",
                            outcome.node_id
                        );
                        Some(err.to_string())
                    }
                };
            let active = graph
                .current_index_contract()
                .ok()
                .flatten()
                .or(incoming_contract);
            Ok::<_, lattice_core::CoreError>((outcome, reindex, active, overlay_warning))
        })
        .await
        {
            Ok(Ok(res)) => res,
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
    let telemetry = super::enrich::build_enrichment_telemetry(
        parse_failed,
        extract_ok,
        doc_embed.as_ref(),
        chunk_embed.as_ref(),
        reindex_required,
        has_supplied_vectors || !parse_failed,
        active_contract,
    );
    let mut payload = outcome.to_json();
    if let Some(object) = payload.as_object_mut() {
        telemetry.apply_to_json(object);
        if let Some(warning) = overlay_warning {
            object.insert("structure_overlay_warning".into(), json!(warning));
        }
    }
    ok(&payload)
}
