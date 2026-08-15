//! Native knowledge-graph writes: curation and the two promotion actions.

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

const CURATE_NOISE_REQUEST: &[crate::search_api::FieldSpec] = &[
    optional("dry_run", Kind::Bool),
    optional("max_df_ratio", Kind::Float),
    optional("min_doc_frequency", Kind::Int),
    optional("min_corpus_docs", Kind::Int),
    optional("normalize_verbs", Kind::Bool),
    optional("max_removals", Kind::Int),
];

const PROMOTION_ACTION_REQUEST: &[crate::search_api::FieldSpec] = &[optional("ids", Kind::Array)];

// ── the four native writes ──────────────────────────────────────────────────

/// One whitelisted op on the write engine, and its answer verbatim.
///
/// The route returns the store's return value, which is what
/// `graph().curate()` returned in Python — the worker seam that used to wrap
/// it as `{"op", "result"}` and unwrap it again is gone, so there is one
/// fewer shape to keep honest and a client still sees what it always saw.
///
/// `require_graph` in every caller has already refused a graph-less install
/// (404, `_require_graph()`'s sentence). This arm is the *mis-wired* one — a
/// store opened without a [`lattice_core::graph_write::GraphWriter`] — and it
/// says exactly that rather than inventing a delegate that no longer exists.
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

/// The one sentence a caller gets when the graph is on but no writer was bound.
///
/// Deliberately the same wording `memory_api::shared::BrainState::mutate_detailed`
/// answers with: it is the same mis-wiring, and two spellings of one fault
/// would read as two faults.
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
    // `ids=None` applies/rejects every pending promotion; the seam's argument
    // whitelist accepts the key with a null just as the Python model does.
    let args = json!({ "ids": model.get("ids").cloned().unwrap_or(Value::Null) });
    match mutate(&state, lang, op, args).await {
        Ok(value) => ok(&value),
        Err(refusal) => refusal,
    }
}
