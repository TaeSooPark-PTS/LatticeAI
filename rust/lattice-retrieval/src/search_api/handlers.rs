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

use super::temporal_params::{
    include_historical_field, ranking_from_model, ranking_from_query, since_field, sort_by_field,
    until_field,
};
use super::{
    engine_error, ok, optional, required, FieldSpec, Kind, Model, Query, RetrievalApiState,
    DEFAULT_IMAGE_FUSION_WEIGHT, IMAGE_FUSION_DISABLED, IMAGE_FUSION_GATE_DETAIL,
    IMAGE_FUSION_UNAVAILABLE, TEXT_IMAGE_FUSION_ENV,
};
use crate::service::Scope;
use crate::service::{
    graph_search, keyword_search, vector_search_aligned_service, GraphSearchOptions,
};
use crate::service_hybrid::{service_hybrid_search, ServiceHybridOptions, DEFAULT_HYBRID_WEIGHTS};
use crate::temporal::{
    prepare_ranked, ranked_search, search_text_for, TemporalPlan, TemporalRanking,
};

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

pub(crate) const SEARCH_REQUEST: &[FieldSpec] = &[
    required("query", Kind::Str(1)),
    optional("limit", Kind::Int),
    optional("mode", Kind::OptStr),
    optional("bm25f", Kind::Bool),
    sort_by_field(),
    include_historical_field(),
    since_field(),
    until_field(),
];

pub(crate) const HYBRID_REQUEST: &[FieldSpec] = &[
    required("query", Kind::Str(1)),
    optional("limit", Kind::Int),
    optional("keyword_limit", Kind::Int),
    optional("vector_limit", Kind::Int),
    optional("graph_limit", Kind::Int),
    optional("weights", Kind::Object),
    optional("image_fusion", Kind::Bool),
    optional("mode", Kind::OptStr),
    optional("fusion_strategy", Kind::OptStr),
    optional("bm25f", Kind::Bool),
    optional("expand_depth", Kind::Int),
    sort_by_field(),
    include_historical_field(),
    since_field(),
    until_field(),
];

const VECTOR_REQUEST: &[FieldSpec] = &[
    required("query", Kind::Str(1)),
    optional("limit", Kind::Int),
    optional("min_score", Kind::Float),
    sort_by_field(),
    include_historical_field(),
    since_field(),
    until_field(),
];

const GRAPH_SEARCH_REQUEST: &[FieldSpec] = &[
    required("query", Kind::Str(1)),
    optional("limit", Kind::Int),
    optional("expand_depth", Kind::Int),
    sort_by_field(),
    include_historical_field(),
    since_field(),
    until_field(),
];

const GRAPH_NODE_REQUEST: &[FieldSpec] = &[
    required("node_id", Kind::Str(1)),
    optional("include_neighbors", Kind::Bool),
    optional("include_reasoning", Kind::Bool),
    optional("depth", Kind::Int),
    optional("limit", Kind::Int),
];

pub(crate) const RELATIONSHIP_REQUEST: &[FieldSpec] = &[
    optional("query", Kind::OptStr),
    optional("node_id", Kind::OptStr),
    optional("relationship_type", Kind::OptStr),
    optional("limit", Kind::Int),
];

async fn run<T, F>(state: &RetrievalApiState, work: F) -> Result<T, Response>
where
    T: Send + 'static,
    F: FnOnce(&rusqlite::Connection) -> Result<T, CoreError> + Send + 'static,
{
    let store = state.require_graph()?;
    store.read(work).await.map_err(engine_error)
}

struct HybridRequest {
    query: String,
    limit: i64,
    keyword_limit: i64,
    vector_limit: i64,
    graph_limit: i64,
    expand_depth: i64,
    weights: Option<Map<String, Value>>,
    image_fusion: bool,
    ranking: TemporalRanking,
}

fn wants_graphrag(bm25f: bool, mode: &str) -> bool {
    bm25f
        || matches!(
            mode.trim().to_ascii_lowercase().as_str(),
            "graphrag" | "bm25_graph_rrf" | "bm25_vector_graph_rrf" | "bm25f"
        )
}

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
    let ranking = match ranking_from_query(&query) {
        Ok(ranking) => ranking,
        Err(refusal) => return refusal,
    };
    let mode = query.str_or("mode", "");
    let bm25f = query.bool_or("bm25f", false).unwrap_or(false);
    let (_identity, scope) = match state.guard(&headers) {
        Ok(pair) => pair,
        Err(refusal) => return refusal,
    };
    let graphrag = wants_graphrag(bm25f, &mode);
    let request = HybridRequest {
        query: q,
        limit,
        keyword_limit: 30,
        vector_limit: 30,
        graph_limit: 30,
        expand_depth: 2,
        weights: None,
        image_fusion,
        ranking,
    };
    if graphrag {
        return hybrid_graphrag(&state, scope, request).await;
    }
    hybrid(&state, scope, request).await
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
    let ranking = match ranking_from_model(&model) {
        Ok(ranking) => ranking,
        Err(refusal) => return refusal,
    };
    let (_identity, scope) = match state.guard(&headers) {
        Ok(pair) => pair,
        Err(refusal) => return refusal,
    };
    let mode = model
        .opt_str("mode")
        .or_else(|| model.opt_str("fusion_strategy"))
        .unwrap_or_default();
    let graphrag = wants_graphrag(model.bool("bm25f", false), mode);
    let request = HybridRequest {
        query: model.str("query").to_string(),
        limit: model.int("limit", 30),
        keyword_limit: model.int("keyword_limit", 30),
        vector_limit: model.int("vector_limit", 30),
        graph_limit: model.int("graph_limit", 30),
        expand_depth: model.int("expand_depth", 2),
        weights: (!graphrag).then(|| pinned_weights(model.get("weights"))),
        image_fusion: model.bool("image_fusion", false),
        ranking,
    };
    if graphrag {
        return hybrid_graphrag(&state, scope, request).await;
    }
    hybrid(&state, scope, request).await
}

async fn finish_ranked(
    state: &RetrievalApiState,
    outcome: Result<(Value, TemporalPlan), Response>,
    limit: i64,
    image_fusion: bool,
) -> Response {
    let (mut report, mut plan) = match outcome {
        Ok(pair) => pair,
        Err(refusal) => return refusal,
    };
    crate::rerank::rerank_report(
        state.seam(),
        plan.search_query(),
        &mut report,
        &crate::rerank::RerankConfig::from_env(),
    )
    .await;
    plan.apply_finalize(&mut report, limit.clamp(1, 100) as usize);
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

async fn hybrid_graphrag(
    state: &RetrievalApiState,
    scope: Scope,
    request: HybridRequest,
) -> Response {
    let store = match state.require_graph() {
        Ok(store) => Arc::clone(store),
        Err(refusal) => return refusal,
    };
    let now = crate::routes::naive_local_now();
    let search_text = search_text_for(&request.query, now, &request.ranking);
    let effective = crate::query_embed::effective_graphrag_query(&search_text);
    let resolution =
        crate::query_embed::resolve_query_embedding(&store, state.seam(), &effective).await;
    let graph_config = crate::bm25::GraphRagConfig {
        max_hops: request.expand_depth.clamp(1, 2) as usize,
        ..Default::default()
    };
    let HybridRequest {
        query,
        limit,
        keyword_limit,
        vector_limit,
        graph_limit,
        image_fusion,
        ranking,
        ..
    } = request;
    let degraded = resolution.degraded_reason;
    let query_vector = resolution.query_vector;
    let outcome = run(state, move |conn| {
        prepare_ranked(
            conn,
            &query,
            now,
            &ranking.clone().with_scope(scope.clone()),
            limit,
            true,
            |call| {
                let options = crate::bm25::GraphRagOptions {
                    limit: call.fetch_limit,
                    bm25_limit: keyword_limit.max(call.fetch_limit),
                    vector_limit: vector_limit.max(call.fetch_limit),
                    graph_limit: graph_limit.max(call.fetch_limit),
                    graph_config,
                    scope,
                    now_secs: now,
                    include_historical: call.include_historical,
                    query_embedding: query_vector,
                    recency: Some(call.decay),
                    ..crate::bm25::GraphRagOptions::default()
                };
                let model = LocalEmbeddingModel::from_env();
                let mut out = crate::bm25::hybrid_bm25_vector_graph_search(
                    conn,
                    &model,
                    &call.search_query,
                    &options,
                )?;
                if let (Some(reason), Some(object)) = (degraded.as_ref(), out.as_object_mut()) {
                    object.insert("vector_provider_status".into(), json!("degraded"));
                    object.insert("vector_degraded_reason".into(), json!(reason));
                }
                Ok(out)
            },
        )
    })
    .await;
    finish_ranked(state, outcome, limit, image_fusion).await
}

pub(crate) fn pinned_weights(supplied: Option<&Value>) -> Map<String, Value> {
    match supplied.and_then(Value::as_object) {
        Some(object) if !object.is_empty() => object.clone(),
        _ => DEFAULT_HYBRID_WEIGHTS
            .iter()
            .map(|(channel, weight)| ((*channel).to_string(), json!(weight)))
            .collect(),
    }
}

async fn hybrid(state: &RetrievalApiState, scope: Scope, request: HybridRequest) -> Response {
    let store = match state.require_graph() {
        Ok(store) => Arc::clone(store),
        Err(refusal) => return refusal,
    };
    let now = crate::routes::naive_local_now();
    let search_text = search_text_for(&request.query, now, &request.ranking);
    let effective =
        crate::query_embed::effective_hybrid_query(&search_text, request.weights.is_some());
    let resolution =
        crate::query_embed::resolve_query_embedding(&store, state.seam(), &effective).await;
    let HybridRequest {
        query,
        limit,
        keyword_limit,
        vector_limit,
        graph_limit,
        weights,
        image_fusion,
        ranking,
        ..
    } = request;
    let outcome = run(state, move |conn| {
        prepare_ranked(
            conn,
            &query,
            now,
            &ranking.clone().with_scope(scope.clone()),
            limit,
            true,
            |call| {
                let widen = |requested: i64| requested.max(call.fetch_limit).clamp(1, 100);
                let options = ServiceHybridOptions {
                    limit: call.fetch_limit,
                    keyword_limit: widen(keyword_limit),
                    vector_limit: widen(vector_limit),
                    graph_limit: widen(graph_limit),
                    weights,
                    scope,
                    now_secs: now,
                    query_embedding: resolution.query_vector,
                    semantic_degraded: resolution.degraded_reason,
                    recency: Some(call.decay),
                    include_historical: call.include_historical,
                    ..ServiceHybridOptions::default()
                };
                let model = LocalEmbeddingModel::from_env();
                service_hybrid_search(conn, &model, &call.search_query, &options)
            },
        )
    })
    .await;
    finish_ranked(state, outcome, limit, image_fusion).await
}

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
    let ranking = match ranking_from_query(&query) {
        Ok(ranking) => ranking,
        Err(refusal) => return refusal,
    };
    let (_identity, scope) = match state.guard(&headers) {
        Ok(pair) => pair,
        Err(refusal) => return refusal,
    };
    keyword(&state, scope, q, limit, ranking).await
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
    let ranking = match ranking_from_model(&model) {
        Ok(ranking) => ranking,
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
        ranking,
    )
    .await
}

async fn keyword(
    state: &RetrievalApiState,
    scope: Scope,
    query: String,
    limit: i64,
    ranking: TemporalRanking,
) -> Response {
    let now = crate::routes::naive_local_now();
    match run(state, move |conn| {
        ranked_search(
            conn,
            &query,
            now,
            &ranking.clone().with_scope(scope.clone()),
            limit,
            false,
            |call| keyword_search(conn, &call.search_query, call.fetch_limit, &scope),
        )
    })
    .await
    {
        Ok(value) => ok(&value),
        Err(refusal) => refusal,
    }
}

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
    let ranking = match ranking_from_query(&query) {
        Ok(ranking) => ranking,
        Err(refusal) => return refusal,
    };
    vector(&state, scope, q, limit, min_score, ranking).await
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
    let ranking = match ranking_from_model(&model) {
        Ok(ranking) => ranking,
        Err(refusal) => return refusal,
    };
    vector(
        &state,
        scope,
        model.str("query").to_string(),
        model.int("limit", 30),
        model.float("min_score", 0.0),
        ranking,
    )
    .await
}

async fn vector(
    state: &RetrievalApiState,
    scope: Scope,
    query: String,
    limit: i64,
    min_score: f64,
    ranking: TemporalRanking,
) -> Response {
    let store = match state.require_graph() {
        Ok(store) => Arc::clone(store),
        Err(refusal) => return refusal,
    };
    let now = crate::routes::naive_local_now();
    let search_text = search_text_for(&query, now, &ranking);
    let resolution =
        crate::query_embed::resolve_query_embedding(&store, state.seam(), &search_text).await;
    match run(state, move |conn| {
        if let Some(reason) = resolution.degraded_reason.as_ref() {
            return Ok(json!({
                "query": query,
                "mode": "vector",
                "provider_status": "degraded",
                "degraded_reason": reason,
                "matches": [],
            }));
        }
        ranked_search(
            conn,
            &query,
            now,
            &ranking.clone().with_scope(scope.clone()),
            limit,
            false,
            |call| {
                let model = LocalEmbeddingModel::from_env();
                let query_input = match resolution.query_vector.as_ref() {
                    Some(ovr) => crate::vector::VectorQueryInput::Precomputed {
                        model_id: &ovr.model_id,
                        vector: &ovr.vector,
                        is_hash_fallback: ovr.is_hash_fallback,
                    },
                    None => crate::vector::VectorQueryInput::Model(&model),
                };
                vector_search_aligned_service(
                    conn,
                    query_input,
                    &call.search_query,
                    call.fetch_limit,
                    min_score,
                    &scope,
                    call.include_historical,
                )
            },
        )
    })
    .await
    {
        Ok(value) => ok(&value),
        Err(refusal) => refusal,
    }
}

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
    let ranking = match ranking_from_model(&model) {
        Ok(ranking) => ranking,
        Err(refusal) => return refusal,
    };
    let query = model.str("query").to_string();
    let limit = model.int("limit", 30);
    let expand_depth = model.int("expand_depth", 1);
    let now = crate::routes::naive_local_now();
    match run(&state, move |conn| {
        ranked_search(
            conn,
            &query,
            now,
            &ranking.clone().with_scope(scope.clone()),
            limit,
            false,
            |call| {
                let options = GraphSearchOptions {
                    limit: call.fetch_limit,
                    expand_depth,
                    scope,
                    ..GraphSearchOptions::default()
                };
                graph_search(conn, &call.search_query, &options)
            },
        )
    })
    .await
    {
        Ok(value) => ok(&value),
        Err(refusal) => refusal,
    }
}

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
    gate.insert("source", json!(if enabled { "env" } else { "default" }));
    gate.insert("detail", json!(IMAGE_FUSION_GATE_DETAIL));

    let mut payload = OrderedMap::new();
    payload.insert("available", json!(false));
    payload.insert("gate", serde_json::to_value(gate).unwrap_or(Value::Null));
    payload.insert("detail", json!(IMAGE_FUSION_UNAVAILABLE));
    ok(&serde_json::to_value(payload).unwrap_or(Value::Null))
}

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
    let (node_id, include_neighbors, include_reasoning, depth, limit) = match (
        query.require_str("node_id"),
        query.bool_or("include_neighbors", true),
        query.bool_or("include_reasoning", false),
        query.int_or("depth", 1),
        query.int_or("limit", 100),
    ) {
        (Ok(node_id), Ok(neighbors), Ok(reasoning), Ok(depth), Ok(limit)) => {
            (node_id, neighbors, reasoning, depth, limit)
        }
        (Err(refusal), _, _, _, _)
        | (_, Err(refusal), _, _, _)
        | (_, _, Err(refusal), _, _)
        | (_, _, _, Err(refusal), _)
        | (_, _, _, _, Err(refusal)) => return refusal,
    };
    node(
        &state,
        scope,
        node_id,
        include_neighbors,
        include_reasoning,
        depth,
        limit,
    )
    .await
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
        model.bool("include_reasoning", false),
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
    include_reasoning: bool,
    depth: i64,
    limit: i64,
) -> Response {
    match run(state, move |conn| {
        let node = crate::knowledge_graph_api::get_node(conn, &node_id, &scope)?;
        let mut payload = OrderedMap::new();
        payload.insert("node", node);
        if include_neighbors || include_reasoning {
            let effective_depth = if include_reasoning {
                depth.max(2)
            } else {
                depth
            };
            let neighborhood = crate::graph_reads::traverse(
                conn,
                &node_id,
                &crate::graph_reads::TraverseOptions {
                    depth: effective_depth,
                    limit,
                    allowed_workspaces: scope.allowed_workspaces.clone(),
                    include_legacy_global: scope.include_legacy_global,
                },
            )?;
            if include_reasoning {
                let reasoning =
                    crate::neighborhood_synthesis::synthesize_neighborhood(&node_id, &neighborhood);
                payload.insert("reasoning", reasoning);
            }
            if include_neighbors {
                payload.insert("neighborhood", neighborhood);
            }
        }
        Ok(serde_json::to_value(payload).unwrap_or(Value::Null))
    })
    .await
    {
        Ok(value) => ok(&value),
        Err(refusal) => refusal,
    }
}

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
