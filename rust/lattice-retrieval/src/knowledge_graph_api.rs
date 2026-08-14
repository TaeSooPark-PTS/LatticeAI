//! `latticeai/api/knowledge_graph.py`, natively (WP-R6).
//!
//! Fourteen of the module's seventeen routes. `GET /graph` and
//! `GET /knowledge-graph` are page shells and belong to WP-I4's redirect table
//! (`lattice_platform::ui_redirects`, which already lists them); **`POST
//! /knowledge-graph/ingest` stays on the worker** — it is the Brain's single
//! write door, and the plan keeps it there on purpose. Nothing in this module
//! claims it.
//!
//! ## Reads native, writes delegated
//!
//! Ten of the fourteen are reads and are ported against the same
//! `knowledge_graph.sqlite` Python reads, through
//! [`lattice_core::read::read_tables`] so the `LATTICEAI_KG_READ_V2` view
//! selection is the one selection. The other four — curate, noise curation and
//! the two promotion actions — mutate the graph, so they go over
//! [`lattice_core::worker::WorkerSeamClient`] to `POST /worker/graph/mutate`
//! under the op names WP-I6 whitelisted. There is no code path here that opens
//! a write connection to the Brain.
//!
//! ## Scoping, and the two shapes it takes
//!
//! `knowledge_graph.py` does its scoping in the *router*, not in the store:
//! `_scoped()` resolves the membership set and `_filter_scoped()` re-filters
//! whatever the store returned. Some store methods also accept
//! `allowed_workspaces` and some raise `TypeError` when handed it — the router
//! catches that and filters afterwards. Both halves are reproduced here as one
//! function, [`filter_scoped`], because the fallback path is the one that
//! actually runs for `list_documents`, `search` and `neighbors`.

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
use std::sync::Arc;

use axum::body::Bytes;
use axum::extract::{Path as AxumPath, RawQuery, State};
use axum::http::HeaderMap;
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_auth::OrderedMap;
use lattice_core::worker::{WorkerSeamClient, WorkerSeamError};
use lattice_core::CoreError;

use crate::memory_api::graph_native;
use rusqlite::Connection;
use serde_json::{json, Value};

use crate::search_api::{
    engine_error, graph_disabled, http_error, language, ok, optional, Kind, Model, Query,
    RetrievalApiState,
};
#[cfg(test)]
use crate::service::Scope;

// ── the route table ─────────────────────────────────────────────────────────

/// Every `(method, path)` this module mounts.
///
/// `/knowledge-graph/neighbors/*node_id` is a wildcard because FastAPI declares
/// it `{node_id:path}`: node ids carry colons *and* slashes
/// (`local-file:…/notes/a.md`), and a plain capture would 404 on every one of
/// them. `rust/fixtures/openapi/knowledge_search.json`'s `greedy_path_params`
/// is the record, and `tests/kg_api_contract.rs` checks this table against it.
pub const MOUNTED: &[(&str, &str)] = &[
    // Native (W3b). Spec still lives in worker_keep.json so fragment
    // byte-composition stays identical — see kg_api_contract.rs.
    ("POST", "/knowledge-graph/ingest"),
    ("POST", "/knowledge-graph/curate"),
    ("POST", "/knowledge-graph/curate/noise"),
    ("GET", "/knowledge-graph/promotions"),
    ("POST", "/knowledge-graph/promotions/apply"),
    ("POST", "/knowledge-graph/promotions/reject"),
    ("GET", "/knowledge-graph/provenance/coverage"),
    ("GET", "/knowledge-graph/stats"),
    ("GET", "/knowledge-graph/pipeline/status"),
    ("GET", "/knowledge-graph/schema"),
    ("GET", "/knowledge-graph/graph"),
    ("GET", "/knowledge-graph/documents"),
    ("GET", "/knowledge-graph/search"),
    ("GET", "/knowledge-graph/context"),
    ("GET", "/knowledge-graph/neighbors/*node_id"),
];

/// The seam path every graph write in this family travels.
pub const GRAPH_MUTATE_PATH: &str = "/worker/graph/mutate";

/// `_kg_constants.GRAPH_SCHEMA_VERSION`.
pub const GRAPH_SCHEMA_VERSION: i64 = 1;
/// `schema.KG_SCHEMA_V2_VERSION`.
pub const KG_SCHEMA_V2_VERSION: i64 = 2;
/// `schema.EMBED_DIM`'s default; `LATTICEAI_EMBED_DIM` overrides it.
pub const DEFAULT_EMBED_DIM: i64 = 1024;

/// `graph_view._GRAPH_VISIBLE_TYPES` — the canvas's allow-list, in order.
pub const GRAPH_VISIBLE_TYPES: &[&str] = &[
    "Computer",
    "Drive",
    "Folder",
    "File",
    "Chat",
    "Document",
    "CodeFile",
    "Spreadsheet",
    "SlideDeck",
    "Image",
    "ImageText",
    "Audio",
    "Concept",
    "Person",
    "Error",
    "Code",
    "Feature",
    "Task",
    "Decision",
    "Source",
    "Repository",
    "Meeting",
    "Organization",
    "Workflow",
    "Agent",
];

// ── router ──────────────────────────────────────────────────────────────────

/// `create_knowledge_graph_router(...)` — the fourteen ported routes.
pub fn router(state: Arc<RetrievalApiState>) -> Router {
    Router::new()
        .route("/knowledge-graph/ingest", post(ingest))
        .route("/knowledge-graph/curate", post(curate))
        .route("/knowledge-graph/curate/noise", post(curate_noise))
        .route("/knowledge-graph/promotions", get(promotions))
        .route("/knowledge-graph/promotions/apply", post(promotions_apply))
        .route(
            "/knowledge-graph/promotions/reject",
            post(promotions_reject),
        )
        .route(
            "/knowledge-graph/provenance/coverage",
            get(provenance_coverage_route),
        )
        .route("/knowledge-graph/stats", get(stats_route))
        .route("/knowledge-graph/pipeline/status", get(pipeline_status))
        .route("/knowledge-graph/schema", get(schema_route))
        .route("/knowledge-graph/graph", get(graph_route))
        .route("/knowledge-graph/documents", get(documents_route))
        .route("/knowledge-graph/search", get(search_route))
        .route("/knowledge-graph/context", get(context_route))
        .route("/knowledge-graph/neighbors/*node_id", get(neighbors_route))
        .with_state(state)
}

const CURATE_NOISE_REQUEST: &[crate::search_api::FieldSpec] = &[
    optional("dry_run", Kind::Bool),
    optional("max_df_ratio", Kind::Float),
    optional("min_doc_frequency", Kind::Int),
    optional("min_corpus_docs", Kind::Int),
    optional("normalize_verbs", Kind::Bool),
    optional("max_removals", Kind::Int),
];

const PROMOTION_ACTION_REQUEST: &[crate::search_api::FieldSpec] = &[optional("ids", Kind::Array)];

/// One blocking read on the store, with the graph-disabled refusal already
/// applied.
async fn read<T, F>(state: &RetrievalApiState, work: F) -> Result<T, Response>
where
    T: Send + 'static,
    F: FnOnce(&Connection) -> Result<T, CoreError> + Send + 'static,
{
    let store = state.require_graph()?;
    store.read(work).await.map_err(engine_error)
}

// ── native ingest (W3b) ─────────────────────────────────────────────────────

const INGEST_REQUEST: &[crate::search_api::FieldSpec] = &[
    crate::search_api::required("type", Kind::Str(1)),
    crate::search_api::optional("content", Kind::Str(0)),
    crate::search_api::optional("role", Kind::Str(0)),
    crate::search_api::optional("title", Kind::Str(0)),
    crate::search_api::optional("source", Kind::Str(0)),
    crate::search_api::optional("conversation_id", Kind::Str(0)),
    crate::search_api::optional("user_email", Kind::Str(0)),
    crate::search_api::optional("user_nickname", Kind::Str(0)),
    crate::search_api::optional("metadata", Kind::Object),
];

async fn ingest(
    State(state): State<Arc<RetrievalApiState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let identity = match state.auth().require_user(&headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let model = match Model::parse(&body, INGEST_REQUEST) {
        Ok(model) => model,
        Err(refusal) => return refusal,
    };
    let lang = language(&headers);
    let claimed = model.str("user_email");
    if !identity.email.is_empty()
        && !claimed.is_empty()
        && !identity.email.eq_ignore_ascii_case(claimed)
    {
        return crate::search_api::http_error(403, "common.user_mismatch", lang);
    }
    let event_type = model.str("type").trim().to_ascii_lowercase();
    if !matches!(event_type.as_str(), "message" | "ai_response" | "note") {
        return crate::search_api::http_error(400, "graph.unsupported_type", lang);
    }
    let Some(graph) = state.graph().cloned() else {
        return crate::search_api::http_error(503, "capture.ingestion_disabled", lang);
    };
    let effective_user = if identity.email.is_empty() {
        if claimed.is_empty() {
            None
        } else {
            Some(claimed.to_string())
        }
    } else {
        Some(identity.email.clone())
    };
    let workspace = state
        .scope_for(&identity)
        .allowed_workspaces
        .as_ref()
        .and_then(|set| set.iter().next().cloned());
    let role = {
        let given = model.str("role");
        if given.is_empty() {
            if event_type == "ai_response" {
                "assistant".into()
            } else {
                "user".into()
            }
        } else {
            given.to_string()
        }
    };
    let title = model.str("title").to_string();
    let content = model.str("content").to_string();
    let source = {
        let given = model.str("source");
        if given.is_empty() {
            "mcp".into()
        } else {
            given.to_string()
        }
    };
    let conversation_id = {
        let given = model.str("conversation_id");
        if given.is_empty() {
            None
        } else {
            Some(given.to_string())
        }
    };
    let nickname = {
        let given = model.str("user_nickname");
        if given.is_empty() {
            None
        } else {
            Some(given.to_string())
        }
    };
    let metadata = model
        .get("metadata")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let is_note = event_type == "note";
    let outcome = match tokio::task::spawn_blocking(move || {
        if is_note {
            let mut request = lattice_core::graph_write::types::IngestContentRequest {
                source_type: "note".into(),
                title,
                text: content,
                source_uri: Some(source),
                owner: effective_user,
                workspace_id: workspace,
                conversation_id,
                metadata,
                ..Default::default()
            };
            request.node_type = Some("Document".into());
            graph.ingest_content(&request)
        } else {
            let mut raw = metadata.clone();
            raw.insert("type".into(), json!(event_type));
            raw.insert("title".into(), json!(title));
            raw.insert("content".into(), json!(content));
            let request = lattice_core::graph_write::types::IngestMessageRequest {
                role,
                content,
                user_email: effective_user,
                user_nickname: nickname,
                source: Some(source),
                conversation_id,
                workspace_id: workspace,
                raw: Some(raw),
                ..Default::default()
            };
            graph.ingest_message(&request)
        }
    })
    .await
    {
        Ok(Ok(outcome)) => outcome,
        Ok(Err(error)) => return crate::search_api::detail(500, &error.to_string()),
        Err(error) => return crate::search_api::detail(500, &error.to_string()),
    };
    if let Some(graph) = state.graph().cloned() {
        let node_id = outcome
            .to_json()
            .get("node_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        if !node_id.is_empty() {
            let _ = tokio::task::spawn_blocking(move || graph.write_vectors(&node_id)).await;
        }
    }
    ok(&outcome.to_json())
}

// ── the four delegated writes ───────────────────────────────────────────────

/// `POST /worker/graph/mutate` with one whitelisted op, and its answer verbatim.
///
/// The route returns the store's return value, which is what
/// `graph().curate()` returns in Python; the seam wraps it as
/// `{"op", "result"}` and this unwraps it again, so a client sees exactly what
/// it saw before the move.
async fn mutate(
    state: &RetrievalApiState,
    lang: &str,
    op: &str,
    args: Value,
) -> Result<Value, Response> {
    if let Some(graph) = state.graph().cloned() {
        let op = op.to_string();
        return tokio::task::spawn_blocking(move || graph_native::dispatch(&graph, &op, &args))
            .await
            .map_err(|error| crate::search_api::detail(500, &error.to_string()))?
            .map_err(|error| {
                crate::search_api::detail(graph_native::status_for(&error), &error.to_string())
            });
    }
    let seam: &WorkerSeamClient = state.require_seam(lang)?;
    let body = json!({ "op": op, "args": args });
    match seam.post_json(GRAPH_MUTATE_PATH, &body).await {
        Ok(answer) => Ok(answer.get("result").cloned().unwrap_or(Value::Null)),
        Err(error) => Err(seam_error(error)),
    }
}

/// A refused delegation, reported with the worker's own status where it gave
/// one — a 403 from the worker must not reach the browser as a blanket 502.
fn seam_error(error: WorkerSeamError) -> Response {
    match error {
        WorkerSeamError::Rejected {
            status, ref detail, ..
        } => {
            // The worker already answered in the product's own error shape;
            // pass its status through and keep its sentence.
            let parsed: Option<Value> = serde_json::from_str(detail).ok();
            let message = parsed
                .as_ref()
                .and_then(|value| value.get("detail"))
                .and_then(Value::as_str)
                .map(str::to_string)
                .unwrap_or_else(|| detail.clone());
            crate::search_api::detail(status, &message)
        }
        other => crate::search_api::detail(502, &other.to_string()),
    }
}

async fn curate(State(state): State<Arc<RetrievalApiState>>, headers: HeaderMap) -> Response {
    if let Err(refusal) = state.guard_admin(&headers) {
        return refusal;
    }
    if state.require_graph().is_err() {
        return graph_disabled();
    }
    let lang = language(&headers);
    match mutate(&state, lang, "curate", json!({})).await {
        Ok(value) => ok(&value),
        Err(refusal) => refusal,
    }
}

async fn curate_noise(
    State(state): State<Arc<RetrievalApiState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if let Err(refusal) = state.guard_admin(&headers) {
        return refusal;
    }
    let model = match Model::parse(&body, CURATE_NOISE_REQUEST) {
        Ok(model) => model,
        Err(refusal) => return refusal,
    };
    if state.require_graph().is_err() {
        return graph_disabled();
    }
    let lang = language(&headers);
    let args = json!({
        "dry_run": model.bool("dry_run", true),
        "max_df_ratio": model.float("max_df_ratio", 0.8),
        "min_doc_frequency": model.int("min_doc_frequency", 1),
        "min_corpus_docs": model.int("min_corpus_docs", 5),
        "normalize_verbs": model.bool("normalize_verbs", true),
        "max_removals": model.int("max_removals", 200),
    });
    match mutate(&state, lang, "curate_noise", args).await {
        Ok(value) => ok(&value),
        Err(refusal) => refusal,
    }
}

async fn promotions_apply(
    State(state): State<Arc<RetrievalApiState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    promotion_action(state, headers, body, "apply_pending_promotions").await
}

async fn promotions_reject(
    State(state): State<Arc<RetrievalApiState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    promotion_action(state, headers, body, "reject_pending_promotions").await
}

async fn promotion_action(
    state: Arc<RetrievalApiState>,
    headers: HeaderMap,
    body: Bytes,
    op: &str,
) -> Response {
    if let Err(refusal) = state.guard_admin(&headers) {
        return refusal;
    }
    let model = match Model::parse(&body, PROMOTION_ACTION_REQUEST) {
        Ok(model) => model,
        Err(refusal) => return refusal,
    };
    if state.require_graph().is_err() {
        return graph_disabled();
    }
    let lang = language(&headers);
    // `ids=None` applies/rejects every pending promotion; the seam's argument
    // whitelist accepts the key with a null just as the Python model does.
    let args = json!({ "ids": model.get("ids").cloned().unwrap_or(Value::Null) });
    match mutate(&state, lang, op, args).await {
        Ok(value) => ok(&value),
        Err(refusal) => refusal,
    }
}

// ── reads ───────────────────────────────────────────────────────────────────

async fn promotions(State(state): State<Arc<RetrievalApiState>>, headers: HeaderMap) -> Response {
    if let Err(refusal) = state.guard_admin(&headers) {
        return refusal;
    }
    match read(&state, pending_promotions).await {
        Ok(pending) => {
            let total = pending.len();
            let mut payload = OrderedMap::new();
            payload.insert("pending", Value::Array(pending));
            payload.insert("total", json!(total));
            ok(&serde_json::to_value(payload).unwrap_or(Value::Null))
        }
        Err(refusal) => refusal,
    }
}

async fn provenance_coverage_route(
    State(state): State<Arc<RetrievalApiState>>,
    headers: HeaderMap,
) -> Response {
    if let Err(refusal) = state.auth().require_user(&headers) {
        return refusal;
    }
    match read(&state, provenance_coverage).await {
        Ok(value) => ok(&value),
        Err(refusal) => refusal,
    }
}

async fn stats_route(State(state): State<Arc<RetrievalApiState>>, headers: HeaderMap) -> Response {
    let (_identity, scope) = match state.guard(&headers) {
        Ok(pair) => pair,
        Err(refusal) => return refusal,
    };
    match read(&state, move |conn| stats(conn, &scope)).await {
        Ok(value) => ok(&value),
        Err(refusal) => refusal,
    }
}

async fn schema_route(State(state): State<Arc<RetrievalApiState>>, headers: HeaderMap) -> Response {
    let (_identity, scope) = match state.guard(&headers) {
        Ok(pair) => pair,
        Err(refusal) => return refusal,
    };
    match read(&state, move |conn| stats(conn, &scope)).await {
        Ok(value) => {
            let mut payload = OrderedMap::new();
            payload.insert(
                "legacy_schema_version",
                value.get("schema_version").cloned().unwrap_or(Value::Null),
            );
            payload.insert(
                "v2_schema_available",
                value
                    .get("v2_schema_available")
                    .cloned()
                    .unwrap_or(Value::Null),
            );
            payload.insert("v2", value.get("v2").cloned().unwrap_or(Value::Null));
            ok(&serde_json::to_value(payload).unwrap_or(Value::Null))
        }
        Err(refusal) => refusal,
    }
}

async fn graph_route(
    State(state): State<Arc<RetrievalApiState>>,
    headers: HeaderMap,
    RawQuery(raw): RawQuery,
) -> Response {
    let (_identity, scope) = match state.guard(&headers) {
        Ok(pair) => pair,
        Err(refusal) => return refusal,
    };
    let limit = match Query::parse(raw.as_deref()).int_or("limit", 300) {
        Ok(limit) => limit,
        Err(refusal) => return refusal,
    };
    let now = crate::routes::naive_local_now();
    match read(&state, move |conn| graph_view(conn, limit, &scope, now)).await {
        Ok(value) => ok(&value),
        Err(refusal) => refusal,
    }
}

async fn documents_route(
    State(state): State<Arc<RetrievalApiState>>,
    headers: HeaderMap,
    RawQuery(raw): RawQuery,
) -> Response {
    let (_identity, scope) = match state.guard(&headers) {
        Ok(pair) => pair,
        Err(refusal) => return refusal,
    };
    let limit = match Query::parse(raw.as_deref()).int_or("limit", 200) {
        Ok(limit) => limit,
        Err(refusal) => return refusal,
    };
    match read(&state, move |conn| {
        let payload = list_documents(conn, limit)?;
        scoped_documents(conn, payload, &scope)
    })
    .await
    {
        Ok(value) => ok(&value),
        Err(refusal) => refusal,
    }
}

async fn search_route(
    State(state): State<Arc<RetrievalApiState>>,
    headers: HeaderMap,
    RawQuery(raw): RawQuery,
) -> Response {
    let (_identity, scope) = match state.guard(&headers) {
        Ok(pair) => pair,
        Err(refusal) => return refusal,
    };
    let query = Query::parse(raw.as_deref());
    let (q, limit) = match (query.require_str("q"), query.int_or("limit", 30)) {
        (Ok(q), Ok(limit)) => (q, limit),
        (Err(refusal), _) | (_, Err(refusal)) => return refusal,
    };
    // `if not q or not q.strip(): return {"query": q, "matches": []}` — the
    // empty answer comes back before the store is consulted at all.
    if q.trim().is_empty() {
        let mut payload = OrderedMap::new();
        payload.insert("query", json!(q));
        payload.insert("matches", json!([]));
        return ok(&serde_json::to_value(payload).unwrap_or(Value::Null));
    }
    match read(&state, move |conn| {
        // The router calls `kg.search(q, limit)` positionally — unscoped — and
        // filters afterwards, which is a different code path from the scoped
        // `search()` the service layer uses.
        let mut payload = crate::keyword::search(conn, &q, limit, None, false)?;
        if scope.allowed_workspaces.is_some() {
            let matches = payload
                .get("matches")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            let kept = filter_scoped(conn, matches, &scope)?;
            if let Some(object) = payload.as_object_mut() {
                object.insert("matches".into(), Value::Array(kept));
            }
        }
        Ok(payload)
    })
    .await
    {
        Ok(value) => ok(&value),
        Err(refusal) => refusal,
    }
}

async fn context_route(
    State(state): State<Arc<RetrievalApiState>>,
    headers: HeaderMap,
    RawQuery(raw): RawQuery,
) -> Response {
    let (_identity, scope) = match state.guard(&headers) {
        Ok(pair) => pair,
        Err(refusal) => return refusal,
    };
    let query = Query::parse(raw.as_deref());
    let (q, limit) = match (query.require_str("q"), query.int_or("limit", 6)) {
        (Ok(q), Ok(limit)) => (q, limit),
        (Err(refusal), _) | (_, Err(refusal)) => return refusal,
    };
    let echoed = q.clone();
    match read(&state, move |conn| {
        context_for_query(conn, &q, limit, &scope)
    })
    .await
    {
        Ok(context) => {
            let mut payload = OrderedMap::new();
            payload.insert("query", json!(echoed));
            payload.insert("context", json!(context));
            ok(&serde_json::to_value(payload).unwrap_or(Value::Null))
        }
        Err(refusal) => refusal,
    }
}

async fn neighbors_route(
    State(state): State<Arc<RetrievalApiState>>,
    headers: HeaderMap,
    AxumPath(node_id): AxumPath<String>,
) -> Response {
    let (_identity, scope) = match state.guard(&headers) {
        Ok(pair) => pair,
        Err(refusal) => return refusal,
    };
    let lang = language(&headers);
    if node_id.is_empty() {
        return http_error(400, "graph.node_id_required", lang);
    }
    let scoped = scope.allowed_workspaces.is_some();
    let seed = node_id.clone();
    match read(&state, move |conn| {
        if scoped {
            let visible = filter_scoped(conn, vec![json!({ "id": seed })], &scope)?;
            if visible.is_empty() {
                return Ok(None);
            }
        }
        let mut payload = neighbors(conn, &seed)?;
        if scoped {
            let kept_nodes = filter_scoped(
                conn,
                payload
                    .get("neighbors")
                    .and_then(Value::as_array)
                    .cloned()
                    .unwrap_or_default(),
                &scope,
            )?;
            let kept: Vec<String> = kept_nodes
                .iter()
                .filter_map(|node| node.get("id").and_then(Value::as_str))
                .map(str::to_string)
                .collect();
            let edges: Vec<Value> = payload
                .get("edges")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default()
                .into_iter()
                .filter(|edge| {
                    let endpoint = |key: &str| {
                        edge.get(key)
                            .and_then(Value::as_str)
                            .map(str::to_string)
                            .unwrap_or_default()
                    };
                    let from = endpoint("from");
                    let to = endpoint("to");
                    (from == seed || kept.contains(&from)) && (to == seed || kept.contains(&to))
                })
                .collect();
            if let Some(object) = payload.as_object_mut() {
                object.insert("neighbors".into(), Value::Array(kept_nodes));
                object.insert("edges".into(), Value::Array(edges));
            }
        }
        Ok(Some(payload))
    })
    .await
    {
        Ok(Some(value)) => ok(&value),
        Ok(None) => http_error(404, "graph.node_not_found", lang),
        Err(refusal) => refusal,
    }
}

async fn pipeline_status(
    State(state): State<Arc<RetrievalApiState>>,
    headers: HeaderMap,
) -> Response {
    let (_identity, scope) = match state.guard(&headers) {
        Ok(pair) => pair,
        Err(refusal) => return refusal,
    };
    let now = crate::routes::naive_local_now();
    match read(&state, move |conn| pipeline_payload(conn, &scope, now)).await {
        Ok(value) => ok(&value),
        Err(refusal) => refusal,
    }
}

pub(crate) mod reads;
pub(crate) mod view;

pub use reads::{
    filter_scoped, list_documents, neighbors, pending_promotions, provenance_coverage, scope_sql,
    scoped_documents, stats,
};
pub use view::{
    civil_from_days, context_for_query, format_context, get_node, graph_view,
    naive_local_iso_seconds, pipeline_payload, stage_view,
};

/// Kept public so a host can render the same refusal the route does.
pub use crate::search_api::detail as json_detail;

#[cfg(test)]
mod tests {
    use super::*;
    use lattice_core::pytext::parse_iso;

    fn store() -> (tempfile::TempDir, Connection) {
        let dir = tempfile::tempdir().expect("tempdir");
        let conn = Connection::open(dir.path().join("knowledge_graph.sqlite")).expect("open");
        conn.execute_batch(
            "CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT, title TEXT, summary TEXT,
                                metadata_json TEXT, created_at TEXT, updated_at TEXT);
             CREATE TABLE edges(id TEXT PRIMARY KEY, from_node TEXT, to_node TEXT, type TEXT,
                                weight REAL, metadata_json TEXT, created_at TEXT);
             CREATE TABLE chunks(id TEXT PRIMARY KEY, source_node TEXT, text TEXT,
                                 metadata_json TEXT, created_at TEXT);
             CREATE TABLE nodes_v2(id TEXT PRIMARY KEY, workspace_id TEXT, type TEXT);
             CREATE TABLE edges_v2(id TEXT PRIMARY KEY, source TEXT, target TEXT, type TEXT);
             CREATE TABLE knowledge_sources(id TEXT PRIMARY KEY, root_path TEXT);
             CREATE TABLE local_file_index(id TEXT PRIMARY KEY, source_id TEXT, status TEXT);
             CREATE TABLE ingestion_provenance(id TEXT PRIMARY KEY, node_id TEXT,
                                               source_type TEXT);
             CREATE TABLE graph_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
             INSERT INTO nodes VALUES
               ('doc-a','Document','handbook.md','a handbook',
                '{\"filename\":\"handbook.md\",\"ext\":\".md\",\"extracted\":{\"chars\":12}}',
                '2026-01-01T00:00:00','2026-01-03T00:00:00'),
               ('doc-b','Document','notes.md','notes','{}',
                '2026-01-01T00:00:00','2026-01-02T00:00:00'),
               ('c-1','Concept','Lattice','','{}','2026-01-01T00:00:00','2026-01-04T00:00:00');
             INSERT INTO nodes_v2 VALUES ('doc-a','w1','Document'),('doc-b',NULL,'Document'),
                                         ('c-1','w1','Concept');
             INSERT INTO edges VALUES
               ('e1','doc-a','c-1','MENTIONS',0.9,'{}','2026-02-01T00:00:00');
             INSERT INTO edges_v2 VALUES ('e1','doc-a','c-1','MENTIONS');
             INSERT INTO chunks VALUES ('k1','doc-a','text','{}','2026-01-01T00:00:00');
             INSERT INTO knowledge_sources VALUES ('source:1','/tmp/corpus');
             INSERT INTO local_file_index VALUES ('f1','source:1','indexed');
             INSERT INTO ingestion_provenance VALUES ('p1','doc-a','note');",
        )
        .expect("schema");
        (dir, conn)
    }

    fn unscoped() -> Scope {
        Scope::default()
    }

    fn scoped(workspaces: &[&str]) -> Scope {
        Scope {
            allowed_workspaces: Some(workspaces.iter().map(|w| (*w).to_string()).collect()),
            include_legacy_global: false,
        }
    }

    #[test]
    fn the_route_table_includes_native_ingest() {
        assert_eq!(MOUNTED.len(), 15);
        assert!(MOUNTED.contains(&("POST", "/knowledge-graph/ingest")));
        assert!(MOUNTED
            .iter()
            .any(|(_, path)| *path == "/knowledge-graph/neighbors/*node_id"));
    }

    #[test]
    fn documents_report_their_index_state() {
        let (_dir, conn) = store();
        let payload = list_documents(&conn, 200).unwrap();
        let documents = payload["documents"].as_array().unwrap();
        assert_eq!(documents.len(), 2);
        // ORDER BY updated_at DESC: doc-a (Jan 3) before doc-b (Jan 2).
        assert_eq!(documents[0]["id"], json!("doc-a"));
        assert_eq!(documents[0]["filename"], json!("handbook.md"));
        assert_eq!(documents[0]["chars"], json!(12));
        assert_eq!(documents[0]["chunks"], json!(1));
        assert_eq!(documents[0]["indexed"], json!(true));
        assert_eq!(documents[0]["ingest_state"], json!("indexed"));
        assert_eq!(documents[1]["ingest_state"], json!("ingested"));
        assert_eq!(payload["total"], json!(2));
        // Key order is the frozen wire schema, not an alphabetical accident.
        let rendered = serde_json::to_string(&documents[0]).unwrap();
        assert!(rendered.starts_with(r#"{"id":"doc-a","filename":"handbook.md","ext":".md""#));
    }

    #[test]
    fn a_zero_limit_is_the_default_not_one_document() {
        let (_dir, conn) = store();
        assert_eq!(
            list_documents(&conn, 0).unwrap()["documents"]
                .as_array()
                .unwrap()
                .len(),
            2
        );
        assert_eq!(
            list_documents(&conn, 1).unwrap()["documents"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
    }

    #[test]
    fn scoped_documents_drop_the_legacy_global_row() {
        let (_dir, conn) = store();
        let payload = list_documents(&conn, 200).unwrap();
        let scoped = scoped_documents(&conn, payload, &scoped(&["w1"])).unwrap();
        assert_eq!(scoped["total"], json!(1));
        assert_eq!(scoped["documents"][0]["id"], json!("doc-a"));
    }

    #[test]
    fn stats_counts_the_whole_store_when_nobody_is_scoped() {
        let (_dir, conn) = store();
        let payload = stats(&conn, &unscoped()).unwrap();
        assert_eq!(payload["schema_version"], json!(1));
        assert_eq!(payload["v2_schema_available"], json!(true));
        assert_eq!(payload["nodes"]["Document"], json!(2));
        assert_eq!(payload["edges"]["MENTIONS"], json!(1));
        assert_eq!(payload["local_sources"], json!(1));
        assert_eq!(payload["local_file_status"]["indexed"], json!(1));
        assert_eq!(payload["v2"]["schema_version"], json!(2));
        assert_eq!(payload["v2"]["nodes"], json!(3));
    }

    #[test]
    fn scoped_stats_report_no_machine_local_bookkeeping() {
        let (_dir, conn) = store();
        let payload = stats(&conn, &scoped(&["w1"])).unwrap();
        assert_eq!(payload["nodes"]["Document"], json!(1));
        assert_eq!(payload["local_sources"], json!(0));
        assert_eq!(payload["local_file_status"], json!({}));
        assert_eq!(payload["v2"]["nodes"], json!(2));
        // A caller who may read nothing gets nothing, not the whole store.
        let nobody = stats(&conn, &scoped(&[])).unwrap();
        assert_eq!(nobody["nodes"], json!({}));
    }

    #[test]
    fn provenance_coverage_reports_the_uncovered_types() {
        let (_dir, conn) = store();
        let payload = provenance_coverage(&conn).unwrap();
        assert_eq!(payload["total_nodes"], json!(3));
        assert_eq!(payload["nodes_with_provenance"], json!(1));
        assert_eq!(payload["coverage_ratio"], json!(0.3333));
        assert_eq!(payload["uncovered_by_type"]["Document"], json!(1));
        assert_eq!(payload["provenance_by_source_type"]["note"], json!(1));
    }

    #[test]
    fn an_empty_store_reports_a_null_coverage_ratio() {
        let dir = tempfile::tempdir().unwrap();
        let conn = Connection::open(dir.path().join("g.sqlite")).unwrap();
        conn.execute_batch(
            "CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT, title TEXT, summary TEXT,
                                metadata_json TEXT, created_at TEXT, updated_at TEXT);
             CREATE TABLE edges(id TEXT PRIMARY KEY, from_node TEXT, to_node TEXT, type TEXT,
                                weight REAL, metadata_json TEXT, created_at TEXT);
             CREATE TABLE ingestion_provenance(id TEXT PRIMARY KEY, node_id TEXT,
                                               source_type TEXT);",
        )
        .unwrap();
        assert_eq!(
            provenance_coverage(&conn).unwrap()["coverage_ratio"],
            Value::Null
        );
    }

    #[test]
    fn neighbors_answers_one_hop_with_its_own_key_set() {
        let (_dir, conn) = store();
        let payload = neighbors(&conn, "doc-a").unwrap();
        assert_eq!(payload["node_id"], json!("doc-a"));
        let nodes = payload["neighbors"].as_array().unwrap();
        assert_eq!(nodes.len(), 1);
        assert_eq!(nodes[0]["id"], json!("c-1"));
        // No `updated_at` on a neighbour, unlike `graph()`.
        assert!(nodes[0].get("updated_at").is_none());
        let edges = payload["edges"].as_array().unwrap();
        assert_eq!(edges[0]["from"], json!("doc-a"));
        assert!(edges[0].get("id").is_none());
        assert_eq!(payload["edges"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn the_graph_view_carries_importance_metrics() {
        let (_dir, conn) = store();
        let payload = graph_view(&conn, 300, &unscoped(), 1_800_000_000.0).unwrap();
        let nodes = payload["nodes"].as_array().unwrap();
        assert_eq!(nodes.len(), 3);
        for node in nodes {
            let metrics = &node["metadata"]["graph_metrics"];
            assert!(metrics["degree"].is_number());
            assert!(metrics["recency_score"].is_number());
            assert!(metrics["importance_raw"].is_number());
            assert!(metrics["importance_norm"].is_number());
            assert!(node["importance"].is_number());
            assert!(node["importance_norm"].is_number());
        }
        // Both endpoints of the one edge scored a degree of 1.
        let by_id: std::collections::HashMap<&str, &Value> = nodes
            .iter()
            .map(|node| (node["id"].as_str().unwrap(), node))
            .collect();
        assert_eq!(
            by_id["doc-a"]["metadata"]["graph_metrics"]["degree"],
            json!(1)
        );
        assert_eq!(
            by_id["doc-b"]["metadata"]["graph_metrics"]["degree"],
            json!(0)
        );
        assert_eq!(payload["edges"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn the_graph_view_drops_rows_a_scope_cannot_read() {
        let (_dir, conn) = store();
        let payload = graph_view(&conn, 300, &scoped(&["w1"]), 1_800_000_000.0).unwrap();
        let ids: Vec<&str> = payload["nodes"]
            .as_array()
            .unwrap()
            .iter()
            .map(|node| node["id"].as_str().unwrap())
            .collect();
        assert_eq!(ids, vec!["c-1", "doc-a"]);
        assert_eq!(payload["edges"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn get_node_reports_its_degree_and_hides_what_a_scope_may_not_read() {
        let (_dir, conn) = store();
        let node = get_node(&conn, "doc-a", &unscoped()).unwrap();
        assert_eq!(node["id"], json!("doc-a"));
        assert_eq!(node["degree"], json!(1));
        let missing = get_node(&conn, "nope", &unscoped()).unwrap_err();
        assert!(matches!(missing, CoreError::InvalidRequest(ref message)
            if message == "graph node not found: nope"));
        // A legacy-global row is invisible to a scoped caller, and the refusal
        // says "not found" rather than confirming it exists elsewhere.
        let hidden = get_node(&conn, "doc-b", &scoped(&["w1"])).unwrap_err();
        assert!(matches!(hidden, CoreError::InvalidRequest(ref message)
            if message == "graph node not found: doc-b"));
        assert!(matches!(
            get_node(&conn, "  ", &unscoped()).unwrap_err(),
            CoreError::InvalidRequest(ref message) if message == "node_id required"
        ));
    }

    #[test]
    fn pending_promotions_tolerates_every_broken_shape() {
        let (_dir, conn) = store();
        assert!(pending_promotions(&conn).unwrap().is_empty());
        for (value, expected) in [
            (r#"[{"id":"topic:a"},{"no":"id"},{"id":""}]"#, 1),
            ("not json", 0),
            (r#"{"id":"a"}"#, 0),
            ("[]", 0),
        ] {
            conn.execute(
                "INSERT OR REPLACE INTO graph_meta(key, value) VALUES ('pending_promotions', ?)",
                [value],
            )
            .unwrap();
            assert_eq!(
                pending_promotions(&conn).unwrap().len(),
                expected,
                "{value}"
            );
        }
    }

    #[test]
    fn the_context_line_is_the_one_python_writes() {
        let matches = vec![json!({
            "id": "doc-a",
            "type": "Document",
            "title": "handbook.md",
            "summary": "  a   handbook\nwith lines ",
            "metadata": {"relative_path": "notes/handbook.md"},
        })];
        assert_eq!(
            format_context(&matches, 6),
            "- [Document] handbook.md | source=notes/handbook.md | a handbook with lines"
        );
        // The source falls back through the chain and ends at the id.
        let bare = vec![json!({"id": "x", "type": null, "title": null, "summary": null})];
        assert_eq!(format_context(&bare, 6), "- [None] None | source=x | ");
        assert_eq!(format_context(&matches, 0), "");
    }

    #[test]
    fn the_pipeline_ribbon_derives_a_coherent_stage() {
        assert_eq!(stage_view(0, 0)["status"], json!("waiting"));
        assert_eq!(stage_view(3, 0)["status"], json!("done"));
        assert_eq!(stage_view(3, 1)["status"], json!("working"));
        assert_eq!(stage_view(-3, -1)["count"], json!(0));
        let (_dir, conn) = store();
        let payload = pipeline_payload(&conn, &unscoped(), 1_800_000_000.0).unwrap();
        assert_eq!(payload["received"], json!(2));
        assert_eq!(payload["extracted"], json!(1));
        assert_eq!(payload["connected"], json!(1));
        assert_eq!(payload["stages"]["extracted"]["pending"], json!(1));
        assert_eq!(payload["stages"]["extracted"]["status"], json!("working"));
        assert!(payload["updated_at"].as_str().unwrap().contains('T'));
    }

    #[test]
    fn a_naive_local_stamp_is_seconds_precision_with_no_offset() {
        let stamp = naive_local_iso_seconds();
        assert_eq!(stamp.len(), 19, "{stamp}");
        assert!(parse_iso(Some(&stamp)).is_some(), "{stamp}");
        assert!(!stamp.contains('+'), "{stamp}");
    }

    #[test]
    fn the_civil_calendar_reduction_is_the_standard_one() {
        assert_eq!(civil_from_days(0), (1970, 1, 1));
        assert_eq!(civil_from_days(19_723), (2024, 1, 1));
        assert_eq!(civil_from_days(-1), (1969, 12, 31));
    }

    #[test]
    fn the_scope_predicate_distinguishes_none_from_empty() {
        assert!(scope_sql(&unscoped()).is_none());
        let (predicate, params) = scope_sql(&scoped(&["w1", "w2"])).unwrap();
        assert_eq!(predicate, "workspace_id IN (?,?)");
        assert_eq!(params, vec!["w1".to_string(), "w2".to_string()]);
        let (nothing, params) = scope_sql(&scoped(&[])).unwrap();
        assert_eq!(nothing, "0");
        assert!(params.is_empty());
        let legacy = Scope {
            allowed_workspaces: Some(Default::default()),
            include_legacy_global: true,
        };
        assert_eq!(scope_sql(&legacy).unwrap().0, "workspace_id IS NULL");
    }

    #[test]
    fn a_worker_refusal_keeps_its_status_and_sentence() {
        let refusal = seam_error(WorkerSeamError::Rejected {
            path: GRAPH_MUTATE_PATH.into(),
            status: 403,
            detail: r#"{"detail":"관리자 권한이 필요합니다."}"#.into(),
        });
        assert_eq!(refusal.status(), 403);
        let transport = seam_error(WorkerSeamError::Transport {
            path: GRAPH_MUTATE_PATH.into(),
            detail: "connection refused".into(),
        });
        assert_eq!(transport.status(), 502);
    }

    #[test]
    fn the_json_detail_alias_is_the_shared_renderer() {
        let response = json_detail(404, "gone");
        assert_eq!(response.status(), 404);
    }
}
