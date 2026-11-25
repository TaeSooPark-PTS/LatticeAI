use std::sync::Arc;

use axum::body::Body;
use axum::extract::{Request, State};
use axum::http::{Method, StatusCode};
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use lattice_auth::AuthState;
use serde_json::Value;

pub const ROLE_FIELD: &str = "user_role";

pub const ROLE_INJECTED_PATHS: [&str; 1] = ["/rust/agent/run"];

pub const MAX_REWRITTEN_BODY: usize = 1024 * 1024;

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
    parts.headers.remove(axum::http::header::CONTENT_LENGTH);
    parts.headers.insert(
        axum::http::header::CONTENT_LENGTH,
        axum::http::HeaderValue::from(rewritten.len()),
    );
    next.run(Request::from_parts(parts, Body::from(rewritten)))
        .await
}

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
    }
}
