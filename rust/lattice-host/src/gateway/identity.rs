//! Two things the front door has to say about *who is calling*.
//!
//! `lattice-auth` answers the question; these are the two places the gateway
//! has to apply the answer on behalf of a crate that cannot.
//!
//! 1. [`require_user_layer`] — the page redirects and `/local/sysinfo` are
//!    `require_user` in Python and have no auth of their own in Rust (WP-I4 §4.4
//!    is explicit: "mounted bare they publish CPU/RAM to anything that can reach
//!    the port"). The gate is a layer here rather than a line in each handler
//!    because those handlers are pure redirects and a second copy of the check
//!    is a second thing to get wrong.
//!
//! 2. [`inject_user_role`] — `RunBody.user_role` decides whether the agent
//!    loop's native tools may run `run_command`, `build_project`,
//!    `deploy_project` or anything `computer_*`. W4 §4 ported
//!    `ToolDispatchService.check_role`, which Python fed from the *session*;
//!    absent, the field defaults to `user` and every one of those tools refuses
//!    for everybody. So the gateway resolves it and **overwrites** whatever
//!    arrived: a client that could name its own role could grant itself shell
//!    access by typing `"owner"`, which is the whole point of the check.

use std::sync::Arc;

use axum::body::Body;
use axum::extract::{Request, State};
use axum::http::{Method, StatusCode};
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use lattice_auth::AuthState;
use serde_json::Value;

/// The field the loop reads the caller's role from.
pub const ROLE_FIELD: &str = "user_role";

/// The routes whose body carries a [`ROLE_FIELD`] the server owns.
///
/// Named rather than pattern-matched: the layer sits over the whole native
/// router, and a rule like "any POST with a JSON body" would buffer every
/// request in the process to edit two of them.
pub const ROLE_INJECTED_PATHS: [&str; 1] = ["/rust/agent/run"];

/// Bodies larger than this are passed through untouched.
///
/// A run request is a message plus a policy table; a megabyte is already far
/// past generous, and a layer that buffers without a ceiling is a memory bug
/// waiting for a client that streams.
pub const MAX_REWRITTEN_BODY: usize = 1024 * 1024;

/// Refuse anything the session does not authorise, then run the route.
pub async fn require_user_layer(
    State(auth): State<Arc<AuthState>>,
    request: Request,
    next: Next,
) -> Response {
    match auth.require_user(request.headers()) {
        Ok(_) => next.run(request).await,
        Err(refusal) => refusal,
    }
}

/// Stamp the authenticated caller's role onto the agent-run body.
///
/// Silent on every path but [`ROLE_INJECTED_PATHS`]. On those, an unreadable or
/// non-object body is forwarded unchanged so the route answers its own 400
/// rather than this layer inventing one, and an unauthenticated caller is
/// refused here — the loop routes carry no auth of their own.
pub async fn inject_user_role(
    State(auth): State<Arc<AuthState>>,
    request: Request,
    next: Next,
) -> Response {
    let path = request.uri().path().to_string();
    if request.method() != Method::POST || !ROLE_INJECTED_PATHS.contains(&path.as_str()) {
        return next.run(request).await;
    }
    let role = match auth.require_user(request.headers()) {
        Ok(identity) => identity.role,
        Err(refusal) => return refusal,
    };
    let (mut parts, body) = request.into_parts();
    let bytes = match axum::body::to_bytes(body, MAX_REWRITTEN_BODY).await {
        Ok(bytes) => bytes,
        Err(err) => {
            return (
                StatusCode::BAD_REQUEST,
                axum::Json(serde_json::json!({
                    "error": "invalid_request",
                    "detail": format!("could not read the agent run request: {err}"),
                })),
            )
                .into_response()
        }
    };
    let rewritten = with_role(&bytes, &role).unwrap_or_else(|| bytes.to_vec());
    // The length changed; a stale `Content-Length` would be a framing lie.
    parts.headers.remove(axum::http::header::CONTENT_LENGTH);
    parts.headers.insert(
        axum::http::header::CONTENT_LENGTH,
        axum::http::HeaderValue::from(rewritten.len()),
    );
    next.run(Request::from_parts(parts, Body::from(rewritten)))
        .await
}

/// `body` with `user_role` set to `role`, or `None` when it is not an object.
pub fn with_role(body: &[u8], role: &str) -> Option<Vec<u8>> {
    let mut parsed: Value = serde_json::from_slice(body).ok()?;
    let object = parsed.as_object_mut()?;
    object.insert(ROLE_FIELD.to_string(), Value::String(role.to_string()));
    serde_json::to_vec(&parsed).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn role_of(bytes: &[u8]) -> Option<String> {
        let value: Value = serde_json::from_slice(bytes).ok()?;
        value
            .get(ROLE_FIELD)?
            .as_str()
            .map(std::string::ToString::to_string)
    }

    #[test]
    fn the_role_is_written_onto_a_body_that_has_none() {
        let rewritten = with_role(br#"{"message":"hi"}"#, "owner").expect("object");
        assert_eq!(role_of(&rewritten).as_deref(), Some("owner"));
        // …and the rest of the body survives.
        let value: Value = serde_json::from_slice(&rewritten).expect("json");
        assert_eq!(value["message"], "hi");
    }

    #[test]
    fn a_claimed_role_is_overwritten_rather_than_honoured() {
        let rewritten =
            with_role(br#"{"message":"hi","user_role":"owner"}"#, "user").expect("object");
        assert_eq!(
            role_of(&rewritten).as_deref(),
            Some("user"),
            "a client that can name its own role can grant itself shell access"
        );
    }

    #[test]
    fn a_body_that_is_not_an_object_is_left_alone() {
        assert!(with_role(b"[1,2,3]", "owner").is_none());
        assert!(with_role(b"not json", "owner").is_none());
        assert!(with_role(b"", "owner").is_none());
    }

    #[test]
    fn only_the_named_paths_are_rewritten() {
        assert_eq!(ROLE_INJECTED_PATHS, ["/rust/agent/run"]);
        assert!(!ROLE_INJECTED_PATHS.contains(&"/rust/agent/resume"));
        // Resume reconstructs its `RunBody` from the persisted record
        // (`looproutes::resume` reads `record["req"]`), so the role stamped on
        // the original run is the role the resumed run executes under. A second
        // injection point would be a second authority on the same fact.
    }
}
