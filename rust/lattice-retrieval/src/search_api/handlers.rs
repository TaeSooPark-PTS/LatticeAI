//! The thirteen `search.py` routes (minus KEEP_WORKER embeddings/rebuild).

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
use axum::body::Bytes;
use axum::extract::{RawQuery, State};
use axum::http::HeaderMap;
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_auth::OrderedMap;
use lattice_core::CoreError;
use lattice_core::LocalEmbeddingModel;
use serde_json::{json, Map, Value};
use std::sync::Arc;

use super::{
    engine_error, ok, optional, required, FieldSpec, Kind, Model, Query, RetrievalApiState,
    DEFAULT_IMAGE_FUSION_WEIGHT, IMAGE_FUSION_DISABLED, IMAGE_FUSION_GATE_DETAIL,
    IMAGE_FUSION_UNAVAILABLE, TEXT_IMAGE_FUSION_ENV,
};
use crate::service::Scope;
use crate::service::{graph_search, keyword_search, vector_search, GraphSearchOptions};
use crate::service_hybrid::{service_hybrid_search, ServiceHybridOptions, DEFAULT_HYBRID_WEIGHTS};

// ── the router ──────────────────────────────────────────────────────────────

/// `create_search_router(...)` — thirteen routes, same paths, same verbs.
pub fn router(state: Arc<RetrievalApiState>) -> Router {
    Router::new()
        .route("/api/search/hybrid", get(hybrid_get).post(hybrid_post))
        .route("/api/search/keyword", get(keyword_get).post(keyword_post))
        .route("/api/search/vector", get(vector_get).post(vector_post))
        .route("/api/search/graph", post(graph_search_post))
        .route("/api/search/image-query", get(image_query))
        .route("/api/graph", get(graph_view))
        .route("/api/graph/node", get(node_get).post(node_post))
        .route(
            "/api/graph/relationship",
            get(relationship_get).post(relationship_post),
        )
        .with_state(state)
}

/// `SearchRequest` — the base every POST body extends.
pub(crate) const SEARCH_REQUEST: &[FieldSpec] = &[
    required("query", Kind::Str(1)),
    optional("limit", Kind::Int),
];

pub(crate) const HYBRID_REQUEST: &[FieldSpec] = &[
    required("query", Kind::Str(1)),
    optional("limit", Kind::Int),
    optional("keyword_limit", Kind::Int),
    optional("vector_limit", Kind::Int),
    optional("graph_limit", Kind::Int),
    optional("weights", Kind::Object),
    optional("image_fusion", Kind::Bool),
];

const VECTOR_REQUEST: &[FieldSpec] = &[
    required("query", Kind::Str(1)),
    optional("limit", Kind::Int),
    optional("min_score", Kind::Float),
];

const GRAPH_SEARCH_REQUEST: &[FieldSpec] = &[
    required("query", Kind::Str(1)),
    optional("limit", Kind::Int),
    optional("expand_depth", Kind::Int),
];

const GRAPH_NODE_REQUEST: &[FieldSpec] = &[
    required("node_id", Kind::Str(1)),
    optional("include_neighbors", Kind::Bool),
    optional("depth", Kind::Int),
    optional("limit", Kind::Int),
];

pub(crate) const RELATIONSHIP_REQUEST: &[FieldSpec] = &[
    optional("query", Kind::OptStr),
    optional("node_id", Kind::OptStr),
    optional("relationship_type", Kind::OptStr),
    optional("limit", Kind::Int),
];

/// One blocking engine call on the store, with the guard already run.
async fn run<T, F>(state: &RetrievalApiState, work: F) -> Result<T, Response>
where
    T: Send + 'static,
    F: FnOnce(&rusqlite::Connection) -> Result<T, CoreError> + Send + 'static,
{
    let store = state.require_graph()?;
    store.read(work).await.map_err(engine_error)
}

// ── /api/search/hybrid ──────────────────────────────────────────────────────

async fn hybrid_get(
    State(state): State<Arc<RetrievalApiState>>,
    headers: HeaderMap,
    RawQuery(raw): RawQuery,
) -> Response {
    let query = Query::parse(raw.as_deref());
    let (q, limit, image_fusion) = match (
        query.require_str("q"),
        query.int_or("limit", 30),
        query.bool_or("image_fusion", false),
    ) {
        (Ok(q), Ok(limit), Ok(fusion)) => (q, limit, fusion),
        (Err(refusal), _, _) | (_, Err(refusal), _) | (_, _, Err(refusal)) => return refusal,
    };
    let (_identity, scope) = match state.guard(&headers) {
        Ok(pair) => pair,
        Err(refusal) => return refusal,
    };
    hybrid(&state, scope, q, limit, 30, 30, 30, None, image_fusion).await
}

async fn hybrid_post(
    State(state): State<Arc<RetrievalApiState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let model = match Model::parse(&body, HYBRID_REQUEST) {
        Ok(model) => model,
        Err(refusal) => return refusal,
    };
    let (_identity, scope) = match state.guard(&headers) {
        Ok(pair) => pair,
        Err(refusal) => return refusal,
    };
    // `weights=req.weights or DEFAULT_HYBRID_WEIGHTS`: the POST form *always*
    // pins the weights, so it never consults the retrieval policy and its
    // `query_class` is always null. The GET form passes no weights and does.
    // Reproduced, not repaired — the two verbs answer differently today.
    let weights = pinned_weights(model.get("weights"));
    hybrid(
        &state,
        scope,
        model.str("query").to_string(),
        model.int("limit", 30),
        model.int("keyword_limit", 30),
        model.int("vector_limit", 30),
        model.int("graph_limit", 30),
        Some(weights),
        model.bool("image_fusion", false),
    )
    .await
}

/// `req.weights or DEFAULT_HYBRID_WEIGHTS` — an empty map is falsy in Python.
pub(crate) fn pinned_weights(supplied: Option<&Value>) -> Map<String, Value> {
    match supplied.and_then(Value::as_object) {
        Some(object) if !object.is_empty() => object.clone(),
        _ => DEFAULT_HYBRID_WEIGHTS
            .iter()
            .map(|(channel, weight)| ((*channel).to_string(), json!(weight)))
            .collect(),
    }
}

#[allow(clippy::too_many_arguments)]
async fn hybrid(
    state: &RetrievalApiState,
    scope: Scope,
    query: String,
    limit: i64,
    keyword_limit: i64,
    vector_limit: i64,
    graph_limit: i64,
    weights: Option<Map<String, Value>>,
    image_fusion: bool,
) -> Response {
    let now = crate::routes::naive_local_now();
    let options = ServiceHybridOptions {
        limit,
        keyword_limit,
        vector_limit,
        graph_limit,
        weights,
        scope,
        now_secs: now,
    };
    let engine_query = query.clone();
    let outcome = run(state, move |conn| {
        let model = LocalEmbeddingModel::from_env();
        service_hybrid_search(conn, &model, &engine_query, &options)
    })
    .await;
    let mut report = match outcome {
        Ok(value) => value,
        Err(refusal) => return refusal,
    };
    // `_image_channel` returns None unless the caller asked, which is what
    // keeps an untouched response byte-identical to 11.1.0's.
    if image_fusion {
        if let Some(object) = report.as_object_mut() {
            object.insert(
                "multimodal".into(),
                json!({ "image_fusion": image_report() }),
            );
        }
    }
    ok(&report)
}

/// The `image_fusion` block, for a runtime with no shared-space vision model.
///
/// Native retrieval has no text→image port at all, so the honest answer is
/// always the "could not run" one, and *which* reason it gives still follows
/// the gate: off (the default) reports the gate, on reports the missing model.
/// Nothing here ever claims `applied: true`.
pub(crate) fn image_report() -> Value {
    let enabled = matches!(
        std::env::var(TEXT_IMAGE_FUSION_ENV)
            .unwrap_or_default()
            .trim()
            .to_ascii_lowercase()
            .as_str(),
        "1" | "true" | "yes" | "on"
    );
    let mut report = OrderedMap::new();
    report.insert("requested", json!(true));
    report.insert("applied", json!(false));
    report.insert("weight", json!(DEFAULT_IMAGE_FUSION_WEIGHT));
    report.insert("candidates", json!(0));
    report.insert("fused", json!(0));
    report.insert(
        "detail",
        json!(if enabled {
            IMAGE_FUSION_UNAVAILABLE
        } else {
            IMAGE_FUSION_DISABLED
        }),
    );
    serde_json::to_value(report).unwrap_or(Value::Null)
}

// ── /api/search/keyword ─────────────────────────────────────────────────────

async fn keyword_get(
    State(state): State<Arc<RetrievalApiState>>,
    headers: HeaderMap,
    RawQuery(raw): RawQuery,
) -> Response {
    let query = Query::parse(raw.as_deref());
    let (q, limit) = match (query.require_str("q"), query.int_or("limit", 30)) {
        (Ok(q), Ok(limit)) => (q, limit),
        (Err(refusal), _) | (_, Err(refusal)) => return refusal,
    };
    let (_identity, scope) = match state.guard(&headers) {
        Ok(pair) => pair,
        Err(refusal) => return refusal,
    };
    keyword(&state, scope, q, limit).await
}

async fn keyword_post(
    State(state): State<Arc<RetrievalApiState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let model = match Model::parse(&body, SEARCH_REQUEST) {
        Ok(model) => model,
        Err(refusal) => return refusal,
    };
    let (_identity, scope) = match state.guard(&headers) {
        Ok(pair) => pair,
        Err(refusal) => return refusal,
    };
    keyword(
        &state,
        scope,
        model.str("query").to_string(),
        model.int("limit", 30),
    )
    .await
}

async fn keyword(state: &RetrievalApiState, scope: Scope, query: String, limit: i64) -> Response {
    match run(state, move |conn| {
        keyword_search(conn, &query, limit, &scope)
    })
    .await
    {
        Ok(value) => ok(&value),
        Err(refusal) => refusal,
    }
}

// ── /api/search/vector ──────────────────────────────────────────────────────

async fn vector_get(
    State(state): State<Arc<RetrievalApiState>>,
    headers: HeaderMap,
    RawQuery(raw): RawQuery,
) -> Response {
    let (_identity, scope) = match state.guard(&headers) {
        Ok(pair) => pair,
        Err(refusal) => return refusal,
    };
    let query = Query::parse(raw.as_deref());
    let (q, limit, min_score) = match (
        query.require_str("q"),
        query.int_or("limit", 30),
        query.float_or("min_score", 0.0),
    ) {
        (Ok(q), Ok(limit), Ok(min_score)) => (q, limit, min_score),
        (Err(refusal), _, _) | (_, Err(refusal), _) | (_, _, Err(refusal)) => return refusal,
    };
    vector(&state, scope, q, limit, min_score).await
}

async fn vector_post(
    State(state): State<Arc<RetrievalApiState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let (_identity, scope) = match state.guard(&headers) {
        Ok(pair) => pair,
        Err(refusal) => return refusal,
    };
    let model = match Model::parse(&body, VECTOR_REQUEST) {
        Ok(model) => model,
        Err(refusal) => return refusal,
    };
    vector(
        &state,
        scope,
        model.str("query").to_string(),
        model.int("limit", 30),
        model.float("min_score", 0.0),
    )
    .await
}

async fn vector(
    state: &RetrievalApiState,
    scope: Scope,
    query: String,
    limit: i64,
    min_score: f64,
) -> Response {
    match run(state, move |conn| {
        let model = LocalEmbeddingModel::from_env();
        vector_search(conn, &model, &query, limit, min_score, &scope)
    })
    .await
    {
        Ok(value) => ok(&value),
        Err(refusal) => refusal,
    }
}

// ── /api/search/graph ───────────────────────────────────────────────────────

async fn graph_search_post(
    State(state): State<Arc<RetrievalApiState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let (_identity, scope) = match state.guard(&headers) {
        Ok(pair) => pair,
        Err(refusal) => return refusal,
    };
    let model = match Model::parse(&body, GRAPH_SEARCH_REQUEST) {
        Ok(model) => model,
        Err(refusal) => return refusal,
    };
    let query = model.str("query").to_string();
    let options = GraphSearchOptions {
        limit: model.int("limit", 30),
        expand_depth: model.int("expand_depth", 1),
        scope,
    };
    match run(&state, move |conn| graph_search(conn, &query, &options)).await {
        Ok(value) => ok(&value),
        Err(refusal) => refusal,
    }
}

// ── /api/search/image-query ─────────────────────────────────────────────────

async fn image_query(State(state): State<Arc<RetrievalApiState>>, headers: HeaderMap) -> Response {
    if let Err(refusal) = state.auth.require_user(&headers) {
        return refusal;
    }
    let enabled = matches!(
        std::env::var(TEXT_IMAGE_FUSION_ENV)
            .unwrap_or_default()
            .trim()
            .to_ascii_lowercase()
            .as_str(),
        "1" | "true" | "yes" | "on"
    );
    let mut gate = OrderedMap::new();
    gate.insert("name", json!("text_image_fusion"));
    gate.insert("flag", json!(TEXT_IMAGE_FUSION_ENV));
    gate.insert("enabled", json!(enabled));
    gate.insert("default", json!(false));
    // `FeatureGate.source()`: "env" once the variable carries a word the gate
    // recognises, "default" otherwise. Python answers "resolver" here because
    // the feature-toggle service binds one; that service is WP-R2's, and this
    // module reports what it can actually see rather than claiming a resolver.
    gate.insert("source", json!(if enabled { "env" } else { "default" }));
    gate.insert("detail", json!(IMAGE_FUSION_GATE_DETAIL));

    let mut payload = OrderedMap::new();
    payload.insert("available", json!(false));
    payload.insert("gate", serde_json::to_value(gate).unwrap_or(Value::Null));
    payload.insert("detail", json!(IMAGE_FUSION_UNAVAILABLE));
    ok(&serde_json::to_value(payload).unwrap_or(Value::Null))
}

// ── /api/graph ──────────────────────────────────────────────────────────────

async fn graph_view(
    State(state): State<Arc<RetrievalApiState>>,
    headers: HeaderMap,
    RawQuery(raw): RawQuery,
) -> Response {
    let (_identity, scope) = match state.guard(&headers) {
        Ok(pair) => pair,
        Err(refusal) => return refusal,
    };
    let query = Query::parse(raw.as_deref());
    let limit = match query.int_or("limit", 300) {
        Ok(limit) => limit,
        Err(refusal) => return refusal,
    };
    let now = crate::routes::naive_local_now();
    match run(state.as_ref(), move |conn| {
        crate::knowledge_graph_api::graph_view(conn, limit, &scope, now)
    })
    .await
    {
        Ok(value) => ok(&value),
        Err(refusal) => refusal,
    }
}

// ── /api/graph/node ─────────────────────────────────────────────────────────

async fn node_get(
    State(state): State<Arc<RetrievalApiState>>,
    headers: HeaderMap,
    RawQuery(raw): RawQuery,
) -> Response {
    let (_identity, scope) = match state.guard(&headers) {
        Ok(pair) => pair,
        Err(refusal) => return refusal,
    };
    let query = Query::parse(raw.as_deref());
    let (node_id, include_neighbors, depth, limit) = match (
        query.require_str("node_id"),
        query.bool_or("include_neighbors", true),
        query.int_or("depth", 1),
        query.int_or("limit", 100),
    ) {
        (Ok(node_id), Ok(neighbors), Ok(depth), Ok(limit)) => (node_id, neighbors, depth, limit),
        (Err(refusal), _, _, _)
        | (_, Err(refusal), _, _)
        | (_, _, Err(refusal), _)
        | (_, _, _, Err(refusal)) => return refusal,
    };
    node(&state, scope, node_id, include_neighbors, depth, limit).await
}

async fn node_post(
    State(state): State<Arc<RetrievalApiState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let (_identity, scope) = match state.guard(&headers) {
        Ok(pair) => pair,
        Err(refusal) => return refusal,
    };
    let model = match Model::parse(&body, GRAPH_NODE_REQUEST) {
        Ok(model) => model,
        Err(refusal) => return refusal,
    };
    node(
        &state,
        scope,
        model.str("node_id").to_string(),
        model.bool("include_neighbors", true),
        model.int("depth", 1),
        model.int("limit", 100),
    )
    .await
}

async fn node(
    state: &RetrievalApiState,
    scope: Scope,
    node_id: String,
    include_neighbors: bool,
    depth: i64,
    limit: i64,
) -> Response {
    match run(state, move |conn| {
        let node = crate::knowledge_graph_api::get_node(conn, &node_id, &scope)?;
        let mut payload = OrderedMap::new();
        payload.insert("node", node);
        if include_neighbors {
            let neighborhood = crate::graph_reads::traverse(
                conn,
                &node_id,
                &crate::graph_reads::TraverseOptions {
                    depth,
                    limit,
                    allowed_workspaces: scope.allowed_workspaces.clone(),
                    include_legacy_global: scope.include_legacy_global,
                },
            )?;
            payload.insert("neighborhood", neighborhood);
        }
        Ok(serde_json::to_value(payload).unwrap_or(Value::Null))
    })
    .await
    {
        Ok(value) => ok(&value),
        Err(refusal) => refusal,
    }
}

// ── /api/graph/relationship ─────────────────────────────────────────────────

async fn relationship_get(
    State(state): State<Arc<RetrievalApiState>>,
    headers: HeaderMap,
    RawQuery(raw): RawQuery,
) -> Response {
    let (_identity, scope) = match state.guard(&headers) {
        Ok(pair) => pair,
        Err(refusal) => return refusal,
    };
    let query = Query::parse(raw.as_deref());
    let limit = match query.int_or("limit", 30) {
        Ok(limit) => limit,
        Err(refusal) => return refusal,
    };
    relationships(
        &state,
        scope,
        query.str_or("q", ""),
        query.str_or("node_id", ""),
        query.str_or("relationship_type", ""),
        limit,
    )
    .await
}

async fn relationship_post(
    State(state): State<Arc<RetrievalApiState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let (_identity, scope) = match state.guard(&headers) {
        Ok(pair) => pair,
        Err(refusal) => return refusal,
    };
    let model = match Model::parse(&body, RELATIONSHIP_REQUEST) {
        Ok(model) => model,
        Err(refusal) => return refusal,
    };
    relationships(
        &state,
        scope,
        model.str("query").to_string(),
        model.str("node_id").to_string(),
        model.str("relationship_type").to_string(),
        model.int("limit", 30),
    )
    .await
}

async fn relationships(
    state: &RetrievalApiState,
    scope: Scope,
    query: String,
    node_id: String,
    relationship_type: String,
    limit: i64,
) -> Response {
    let request = crate::graph_reads::RelationshipQuery {
        query,
        node_id,
        relationship_type,
        limit,
        allowed_workspaces: scope.allowed_workspaces,
        include_legacy_global: scope.include_legacy_global,
    };
    match run(state, move |conn| {
        crate::graph_reads::relationship_search(conn, &request)
    })
    .await
    {
        Ok(value) => ok(&value),
        Err(refusal) => refusal,
    }
}
