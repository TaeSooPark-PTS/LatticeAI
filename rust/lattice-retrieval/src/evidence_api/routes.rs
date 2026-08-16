//! `POST /api/evidence/actions`.

use axum::extract::{Request, State};
use axum::response::Response;
use axum::routing::post;
use axum::Router;

use crate::memory_api::shared::{body_str, body_str_list, json_body, ok_json, BrainState};

use super::service;

/// The (method, path) table this family mounts, in axum spelling.
pub const MOUNTED: &[(&str, &str)] = &[("POST", "/api/evidence/actions")];

/// The mountable router for `latticeai/api/evidence_actions.py`.
pub fn router(state: BrainState) -> Router {
    Router::new()
        .route("/api/evidence/actions", post(actions))
        .with_state(state)
}

async fn actions(State(state): State<BrainState>, request: Request) -> Response {
    let (parts, body) = request.into_parts();
    let payload = match json_body(body).await {
        Ok(payload) => payload,
        Err(refusal) => return refusal,
    };
    let request = Request::from_parts(parts, axum::body::Body::empty());
    let identity = match state.require_user(request.headers()) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let question = body_str(&payload, "question", "");
    let source_ids = body_str_list(&payload, "source_ids");
    let language = body_str(&payload, "language", "ko");
    let allowed = state.allowed_workspaces(&identity.email);
    match state
        .read(move |conn| {
            let ids = service::candidate_ids(&source_ids);
            let resolved = service::resolve(Some(conn), &ids, allowed.as_ref());
            Ok(service::actions_for(&question, &language, &resolved))
        })
        .await
    {
        Ok(body) => ok_json(&body),
        Err(refusal) => refusal,
    }
}
