//! Delegated knowledge-graph writes over the worker seam.

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

use super::GRAPH_MUTATE_PATH;
use crate::search_api::{
    engine_error, graph_disabled, http_error, language, ok, optional, Kind, Model, Query,
    RetrievalApiState,
};

const CURATE_NOISE_REQUEST: &[crate::search_api::FieldSpec] = &[
    optional("dry_run", Kind::Bool),
    optional("max_df_ratio", Kind::Float),
    optional("min_doc_frequency", Kind::Int),
    optional("min_corpus_docs", Kind::Int),
    optional("normalize_verbs", Kind::Bool),
    optional("max_removals", Kind::Int),
];

const PROMOTION_ACTION_REQUEST: &[crate::search_api::FieldSpec] = &[optional("ids", Kind::Array)];

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
pub(crate) fn seam_error(error: WorkerSeamError) -> Response {
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
    // `ids=None` applies/rejects every pending promotion; the seam's argument
    // whitelist accepts the key with a null just as the Python model does.
    let args = json!({ "ids": model.get("ids").cloned().unwrap_or(Value::Null) });
    match mutate(&state, lang, op, args).await {
        Ok(value) => ok(&value),
        Err(refusal) => refusal,
    }
}
