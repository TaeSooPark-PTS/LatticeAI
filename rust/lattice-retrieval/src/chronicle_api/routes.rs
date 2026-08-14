//! The three `/api/chronicle/*` handlers.

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
use axum::extract::{Path, Request, State};
use axum::response::Response;
use axum::routing::get;
use axum::Router;

use crate::memory_api::shared::{message_response, missing_query, ok_json, BrainState, Query};

use super::pytime;
use super::service;

/// The (method, path) table this family mounts, in axum spelling.
pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/api/chronicle/as-of"),
    ("GET", "/api/chronicle/day/:date"),
    ("GET", "/api/chronicle/overview"),
];

/// The mountable router for `latticeai/api/chronicle.py`.
pub fn router(state: BrainState) -> Router {
    Router::new()
        .route("/api/chronicle/overview", get(overview))
        .route("/api/chronicle/day/:date", get(day))
        .route("/api/chronicle/as-of", get(as_of))
        .with_state(state)
}

struct Caller {
    email: String,
    scope: Option<String>,
    lang: &'static str,
}

fn authenticate(
    state: &BrainState,
    headers: &axum::http::HeaderMap,
    query: Option<&str>,
) -> Result<Caller, Response> {
    let lang = crate::memory_api::shared::lang_of(headers);
    let identity = state.require_user(headers)?;
    let scope = state.gate_read(headers, query, &identity.email)?;
    Ok(Caller {
        email: identity.email,
        scope,
        lang,
    })
}

async fn overview(State(state): State<BrainState>, request: Request) -> Response {
    let caller = match authenticate(&state, request.headers(), request.uri().query()) {
        Ok(caller) => caller,
        Err(refusal) => return refusal,
    };
    let graph = state.graph_enabled();
    let email = caller.email.clone();
    let scope = caller.scope.clone();
    match state
        .read(move |conn| Ok(service::overview(conn, graph, &email, scope.as_deref())))
        .await
    {
        Ok(body) => ok_json(&body),
        Err(refusal) => refusal,
    }
}

async fn day(
    State(state): State<BrainState>,
    Path(date): Path<String>,
    request: Request,
) -> Response {
    let caller = match authenticate(&state, request.headers(), request.uri().query()) {
        Ok(caller) => caller,
        Err(refusal) => return refusal,
    };
    let day = match pytime::parse_day(&date) {
        Ok(day) => day,
        Err(()) => {
            return message_response(422, "chronicle.bad_date", caller.lang, &[]);
        }
    };
    let graph = state.graph_enabled();
    let email = caller.email.clone();
    let scope = caller.scope.clone();
    match state
        .read(move |conn| Ok(service::day(conn, graph, &day, &email, scope.as_deref())))
        .await
    {
        Ok(body) => ok_json(&body),
        Err(refusal) => refusal,
    }
}

async fn as_of(State(state): State<BrainState>, request: Request) -> Response {
    let params = Query::from_uri(request.uri());
    let ts = match params.required_string("ts", Some(64)) {
        Ok(ts) => ts,
        Err(refusal) => {
            // FastAPI's `Query(...)` with no default is `missing`, not our
            // required_string helper's same shape — it already is.
            let _ = missing_query("ts");
            return refusal;
        }
    };
    let caller = match authenticate(&state, request.headers(), request.uri().query()) {
        Ok(caller) => caller,
        Err(refusal) => return refusal,
    };
    let stamp = match pytime::parse_timestamp(&ts) {
        Ok(stamp) => stamp,
        Err(()) => {
            return message_response(422, "chronicle.bad_timestamp", caller.lang, &[]);
        }
    };
    let graph = state.graph_enabled();
    let scope = caller.scope.clone();
    match state
        .read(move |conn| service::as_of(conn, graph, &stamp, scope.as_deref()))
        .await
    {
        Ok(body) => ok_json(&body),
        Err(refusal) => refusal,
    }
}
