use std::sync::Arc;

use axum::body::Bytes;
use axum::extract::State;
use axum::http::HeaderMap;
use axum::response::Response;

use crate::memory_api::graph_native;
use serde_json::{json, Value};

use crate::search_api::{graph_disabled, language, ok, optional, Kind, Model, RetrievalApiState};

const CURATE_NOISE_REQUEST: &[crate::search_api::FieldSpec] = &[
    optional("dry_run", Kind::Bool),
    optional("max_df_ratio", Kind::Float),
    optional("min_doc_frequency", Kind::Int),
    optional("min_corpus_docs", Kind::Int),
    optional("normalize_verbs", Kind::Bool),
    optional("max_removals", Kind::Int),
];

const PROMOTION_ACTION_REQUEST: &[crate::search_api::FieldSpec] = &[optional("ids", Kind::Array)];

async fn mutate(
    state: &RetrievalApiState,
    lang: &str,
    op: &str,
    args: Value,
) -> Result<Value, Response> {
    let Some(graph) = state.graph().cloned() else {
        let _ = lang;
        return Err(crate::search_api::detail(503, WRITE_ENGINE_UNCONFIGURED));
    };
    let op = op.to_string();
    tokio::task::spawn_blocking(move || graph_native::dispatch(&graph, &op, &args))
        .await
        .map_err(|error| crate::search_api::detail(500, &error.to_string()))?
        .map_err(|error| {
            crate::search_api::detail(graph_native::status_for(&error), &error.to_string())
        })
}

pub(crate) const WRITE_ENGINE_UNCONFIGURED: &str =
    "the knowledge-graph write engine is not configured on this host";

pub(crate) async fn curate(
    State(state): State<Arc<RetrievalApiState>>,
    headers: HeaderMap,
) -> Response {
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

pub(crate) async fn curate_noise(
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

pub(crate) async fn promotions_apply(
    State(state): State<Arc<RetrievalApiState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    promotion_action(state, headers, body, "apply_pending_promotions").await
}

pub(crate) async fn promotions_reject(
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
    let args = json!({ "ids": model.get("ids").cloned().unwrap_or(Value::Null) });
    match mutate(&state, lang, op, args).await {
        Ok(value) => ok(&value),
        Err(refusal) => refusal,
    }
}
