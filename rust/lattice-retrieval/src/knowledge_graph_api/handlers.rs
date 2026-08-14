//! Knowledge-graph read routes.

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

use super::read;
use super::{
    context_for_query, filter_scoped, graph_view, list_documents, neighbors, pending_promotions,
    pipeline_payload, provenance_coverage, scoped_documents, stats,
};
use crate::search_api::{
    engine_error, graph_disabled, http_error, language, ok, optional, Kind, Model, Query,
    RetrievalApiState,
};

// ── reads ───────────────────────────────────────────────────────────────────

pub(crate) async fn promotions(
    State(state): State<Arc<RetrievalApiState>>,
    headers: HeaderMap,
) -> Response {
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

pub(crate) async fn provenance_coverage_route(
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

pub(crate) async fn stats_route(
    State(state): State<Arc<RetrievalApiState>>,
    headers: HeaderMap,
) -> Response {
    let (_identity, scope) = match state.guard(&headers) {
        Ok(pair) => pair,
        Err(refusal) => return refusal,
    };
    match read(&state, move |conn| stats(conn, &scope)).await {
        Ok(value) => ok(&value),
        Err(refusal) => refusal,
    }
}

pub(crate) async fn schema_route(
    State(state): State<Arc<RetrievalApiState>>,
    headers: HeaderMap,
) -> Response {
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

pub(crate) async fn graph_route(
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

pub(crate) async fn documents_route(
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

pub(crate) async fn search_route(
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

pub(crate) async fn context_route(
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

pub(crate) async fn neighbors_route(
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

pub(crate) async fn pipeline_status(
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
