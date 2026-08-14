//! `POST /api/evidence/actions`.

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
