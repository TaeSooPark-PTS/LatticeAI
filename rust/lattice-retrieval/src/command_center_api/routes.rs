//! The two `/api/command/*` handlers.

use axum::extract::{Request, State};
use axum::response::Response;
use axum::routing::get;
use axum::Router;

use crate::memory_api::shared::{ok_json, BrainState, Query};
use crate::memory_api::wsos;

use super::briefing;
use super::search::{self, SearchRequest};

/// The (method, path) table this family mounts, in axum spelling.
pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/api/command/briefing"),
    ("GET", "/api/command/search"),
];

/// The mountable router for `latticeai/api/command_center.py`.
pub fn router(state: BrainState) -> Router {
    Router::new()
        .route("/api/command/briefing", get(command_briefing))
        .route("/api/command/search", get(command_search))
        .with_state(state)
}

struct Caller {
    email: String,
    scope: Option<String>,
}

fn authenticate(
    state: &BrainState,
    headers: &axum::http::HeaderMap,
    query: Option<&str>,
) -> Result<Caller, Response> {
    let identity = state.require_user(headers)?;
    let scope = state.gate_read(headers, query, &identity.email)?;
    Ok(Caller {
        email: identity.email,
        scope,
    })
}

async fn command_briefing(State(state): State<BrainState>, request: Request) -> Response {
    let caller = match authenticate(&state, request.headers(), request.uri().query()) {
        Ok(caller) => caller,
        Err(refusal) => return refusal,
    };
    match briefing::briefing(&state, &caller.email, caller.scope.as_deref()).await {
        Ok(body) => ok_json(&body),
        Err(refusal) => refusal,
    }
}

async fn command_search(State(state): State<BrainState>, request: Request) -> Response {
    let params = Query::from_uri(request.uri());
    let q = match params.string("q", "", Some(300)) {
        Ok(q) => q,
        Err(refusal) => return refusal,
    };
    let limit = match params.int("limit", 8, Some(1), Some(20)) {
        Ok(limit) => limit,
        Err(refusal) => return refusal,
    };
    let caller = match authenticate(&state, request.headers(), request.uri().query()) {
        Ok(caller) => caller,
        Err(refusal) => return refusal,
    };
    let query = lattice_core::pytext::strip(&q);
    let now = state.now();
    if query.is_empty() {
        return ok_json(&search::empty_body(&now));
    }
    let email = caller.email.clone();
    let scope = caller.scope.clone();
    let doc = wsos::load(state.store(), state.data_dir());
    // `CommandCenterService(enable_graph=…)`: read before the closure takes over,
    // because the knowledge group is the one group that needs a graph.
    let enable_graph = state.graph_enabled();
    match state
        .read(move |conn| {
            let request = SearchRequest {
                query: &query,
                user_email: &email,
                workspace_id: scope.as_deref(),
                state: &doc,
                limit,
                enable_graph,
            };
            let (kept, total) = search::groups(conn, &request);
            Ok(search::body(&request, kept, total, &now))
        })
        .await
    {
        Ok(body) => ok_json(&body),
        Err(refusal) => refusal,
    }
}
