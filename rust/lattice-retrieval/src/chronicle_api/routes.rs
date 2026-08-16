//! The three `/api/chronicle/*` handlers.

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
    let Some(day) = pytime::parse_day(&date) else {
        return message_response(422, "chronicle.bad_date", caller.lang, &[]);
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
    let Some(stamp) = pytime::parse_timestamp(&ts) else {
        return message_response(422, "chronicle.bad_timestamp", caller.lang, &[]);
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
