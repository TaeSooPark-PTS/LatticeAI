//! The thirteen `/api/brain/*` handlers, in FastAPI's order of operations.

use axum::extract::{Request, State};
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use serde_json::Value;

use crate::memory_api::shared::{
    body_opt_bool, body_required_str, body_str, detail_response, json_body, ok_json, BrainState,
    Query,
};

use super::{consistency, digest, health, proposals};

/// The (method, path) table this family mounts, in axum spelling.
pub const MOUNTED: &[(&str, &str)] = &[
    ("POST", "/api/brain/consolidate"),
    ("GET", "/api/brain/contradictions"),
    ("POST", "/api/brain/contradictions/propose"),
    ("POST", "/api/brain/contradictions/resolve"),
    ("GET", "/api/brain/duplicates"),
    ("GET", "/api/brain/garden"),
    ("GET", "/api/brain/health"),
    ("GET", "/api/brain/importance"),
    ("GET", "/api/brain/insights"),
    ("GET", "/api/brain/proactive-brief"),
    ("GET", "/api/brain/quality-report"),
    ("POST", "/api/brain/synthesize"),
    ("GET", "/api/brain/vector-freshness"),
];

/// The mountable router for `latticeai/api/brain_intelligence.py`.
pub fn router(state: BrainState) -> Router {
    Router::new()
        .route("/api/brain/health", get(brain_health))
        .route("/api/brain/insights", get(brain_insights))
        .route("/api/brain/contradictions", get(brain_contradictions))
        .route("/api/brain/garden", get(brain_garden))
        .route("/api/brain/vector-freshness", get(brain_vector_freshness))
        .route("/api/brain/duplicates", get(brain_duplicates))
        .route("/api/brain/quality-report", get(brain_quality_report))
        .route("/api/brain/proactive-brief", get(brain_proactive_brief))
        .route("/api/brain/importance", get(brain_importance))
        .route("/api/brain/synthesize", post(brain_synthesize))
        .route(
            "/api/brain/contradictions/propose",
            post(brain_propose_contradictions),
        )
        .route(
            "/api/brain/contradictions/resolve",
            post(brain_resolve_contradiction),
        )
        .route("/api/brain/consolidate", post(brain_consolidate))
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
    write: bool,
) -> Result<Caller, Response> {
    let identity = state.require_user(headers)?;
    let scope = if write {
        state.gate_write(headers, query, &identity.email)?
    } else {
        state.gate_read(headers, query, &identity.email)?
    };
    Ok(Caller {
        email: identity.email,
        scope,
    })
}

async fn brain_health(State(state): State<BrainState>, request: Request) -> Response {
    let caller = match authenticate(&state, request.headers(), request.uri().query(), false) {
        Ok(caller) => caller,
        Err(refusal) => return refusal,
    };
    ok_json(&health::health_report(&state, caller.scope.as_deref()).await)
}

async fn brain_insights(State(state): State<BrainState>, request: Request) -> Response {
    let caller = match authenticate(&state, request.headers(), request.uri().query(), false) {
        Ok(caller) => caller,
        Err(refusal) => return refusal,
    };
    let _ = caller.email;
    ok_json(&digest::insights(&state, caller.scope.as_deref()).await)
}

async fn brain_contradictions(State(state): State<BrainState>, request: Request) -> Response {
    let caller = match authenticate(&state, request.headers(), request.uri().query(), false) {
        Ok(caller) => caller,
        Err(refusal) => return refusal,
    };
    ok_json(&consistency::contradictions(&state, &caller.email, caller.scope.as_deref()).await)
}

async fn brain_garden(State(state): State<BrainState>, request: Request) -> Response {
    let params = Query::from_uri(request.uri());
    let limit = match params.int("limit", 8, None, None) {
        Ok(limit) => limit,
        Err(refusal) => return refusal,
    };
    let caller = match authenticate(&state, request.headers(), request.uri().query(), false) {
        Ok(caller) => caller,
        Err(refusal) => return refusal,
    };
    ok_json(&digest::garden_overview(&state, &caller.email, caller.scope.as_deref(), limit).await)
}

async fn brain_vector_freshness(State(state): State<BrainState>, request: Request) -> Response {
    if let Err(refusal) = authenticate(&state, request.headers(), request.uri().query(), false) {
        return refusal;
    }
    ok_json(&health::vector_freshness(&state).await)
}

async fn brain_duplicates(State(state): State<BrainState>, request: Request) -> Response {
    let caller = match authenticate(&state, request.headers(), request.uri().query(), false) {
        Ok(caller) => caller,
        Err(refusal) => return refusal,
    };
    ok_json(&digest::graph_duplicates(&state, caller.scope.as_deref()).await)
}

async fn brain_quality_report(State(state): State<BrainState>, request: Request) -> Response {
    let caller = match authenticate(&state, request.headers(), request.uri().query(), false) {
        Ok(caller) => caller,
        Err(refusal) => return refusal,
    };
    ok_json(&digest::quality_report(&state, caller.scope.as_deref()).await)
}

async fn brain_proactive_brief(State(state): State<BrainState>, request: Request) -> Response {
    let caller = match authenticate(&state, request.headers(), request.uri().query(), false) {
        Ok(caller) => caller,
        Err(refusal) => return refusal,
    };
    ok_json(&proposals::proactive_brief(&state, &caller.email, caller.scope.as_deref()).await)
}

async fn brain_importance(State(state): State<BrainState>, request: Request) -> Response {
    let caller = match authenticate(&state, request.headers(), request.uri().query(), false) {
        Ok(caller) => caller,
        Err(refusal) => return refusal,
    };
    ok_json(&digest::importance_report(&state, caller.scope.as_deref()).await)
}

async fn brain_synthesize(State(state): State<BrainState>, request: Request) -> Response {
    let caller = match authenticate(&state, request.headers(), request.uri().query(), true) {
        Ok(caller) => caller,
        Err(refusal) => return refusal,
    };
    ok_json(&proposals::synthesize(&state, &caller.email, caller.scope.as_deref()).await)
}

async fn brain_propose_contradictions(
    State(state): State<BrainState>,
    request: Request,
) -> Response {
    let caller = match authenticate(&state, request.headers(), request.uri().query(), true) {
        Ok(caller) => caller,
        Err(refusal) => return refusal,
    };
    ok_json(
        &proposals::propose_contradictions(&state, &caller.email, caller.scope.as_deref()).await,
    )
}

async fn brain_resolve_contradiction(
    State(state): State<BrainState>,
    request: Request,
) -> Response {
    let (parts, body) = request.into_parts();
    let payload = match json_body(body).await {
        Ok(payload) => payload,
        Err(refusal) => return refusal,
    };
    let item_id = match body_required_str(&payload, "item_id") {
        Ok(item_id) => item_id,
        Err(refusal) => return refusal,
    };
    let resolution = body_str(&payload, "resolution", "keep_both_temporal");
    let request = Request::from_parts(parts, axum::body::Body::empty());
    let caller = match authenticate(&state, request.headers(), request.uri().query(), true) {
        Ok(caller) => caller,
        Err(refusal) => return refusal,
    };
    match proposals::resolve_contradiction(&state, &item_id, &resolution, caller.scope.as_deref())
        .await
    {
        Ok(body) => ok_json(&body),
        Err((status, detail)) => detail_response(status, &detail),
    }
}

async fn brain_consolidate(State(state): State<BrainState>, request: Request) -> Response {
    let (parts, body) = request.into_parts();
    let payload = match json_body(body).await {
        Ok(payload) => payload,
        Err(refusal) => return refusal,
    };
    // `dry_run` takes precedence when provided; omitted keeps the apply flag.
    let apply = match body_opt_bool(&payload, "dry_run") {
        Some(dry_run) => !dry_run,
        None => match payload.get("apply") {
            Some(Value::Bool(flag)) => *flag,
            _ => false,
        },
    };
    let request = Request::from_parts(parts, axum::body::Body::empty());
    let caller = match authenticate(&state, request.headers(), request.uri().query(), apply) {
        Ok(caller) => caller,
        Err(refusal) => return refusal,
    };
    ok_json(&consistency::consolidate(&state, apply, &caller.email, caller.scope.as_deref()).await)
}
