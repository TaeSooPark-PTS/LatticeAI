//! Shared HTTP glue for the workspace handlers.

use axum::body::Body;
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use lattice_auth::messages::detail_error;
use lattice_auth::response::json_response;
use lattice_auth::{workspace_scope_from_request, AuthState, Identity};
use serde_json::Value;

use super::service::WorkspaceService;
use super::store::StoreError;

/// Compact JSON, the way FastAPI's `JSONResponse` writes it.
pub fn ok(value: &Value) -> Response {
    json_response(
        StatusCode::OK,
        &serde_json::to_string(value).unwrap_or_else(|_| "{}".into()),
        None,
    )
}

/// FastAPI `{"detail": …}` envelope.
pub fn detail(status: StatusCode, message: &str) -> Response {
    detail_error(status, message)
}

/// Starlette's uncaught-exception page: 500 `text/plain; charset=utf-8`.
pub fn internal_error() -> Response {
    Response::builder()
        .status(StatusCode::INTERNAL_SERVER_ERROR)
        .header("content-type", "text/plain; charset=utf-8")
        .body(Body::from("Internal Server Error"))
        .unwrap_or_else(|_| Response::new(Body::from("Internal Server Error")))
}

/// The Korean 404 `_require_graph` writes when the graph is off.
pub const GRAPH_DISABLED: &str =
    "지식 그래프가 비활성화되어 있습니다. LATTICEAI_ENABLE_GRAPH=true 설정 후 다시 시도해 주세요.";

/// Map a store error the way most mutating org/memory routes do.
pub fn map_store(error: StoreError, not_found_prefix: &str) -> Response {
    match error {
        StoreError::NotFound(id) => {
            detail(StatusCode::NOT_FOUND, &format!("{not_found_prefix}: {id}"))
        }
        StoreError::Permission(message) => detail(StatusCode::FORBIDDEN, &message),
        StoreError::Value(message) => detail(StatusCode::BAD_REQUEST, &message),
    }
}

/// A store error that must stay an uncaught 500 (onboarding, indexing, skills).
pub fn uncaught(error: StoreError) -> Response {
    let _ = error;
    internal_error()
}

/// `require_user`, already rendered on refusal.
pub fn user(auth: &AuthState, headers: &HeaderMap) -> Result<Identity, Response> {
    auth.require_user(headers)
}

/// `require_admin`.
pub fn admin(auth: &AuthState, headers: &HeaderMap) -> Result<Identity, Response> {
    auth.require_admin(headers)
}

/// Email as Python's `require_user` returns it — empty for the local owner.
pub fn email_of(identity: &Identity) -> Option<&str> {
    if identity.email.is_empty() {
        None
    } else {
        Some(identity.email.as_str())
    }
}

/// The named workspace (header, then query).
pub fn named_scope(headers: &HeaderMap, query: Option<&str>) -> Option<String> {
    workspace_scope_from_request(headers, query)
}

/// `_gate_read`.
pub fn gate_read(
    service: &WorkspaceService,
    headers: &HeaderMap,
    query: Option<&str>,
    identity: &Identity,
) -> Result<String, Response> {
    service
        .resolve_read(named_scope(headers, query).as_deref(), email_of(identity))
        .map_err(|error| detail(StatusCode::FORBIDDEN, &error.to_string()))
}

/// `_gate_write`.
pub fn gate_write(
    service: &WorkspaceService,
    headers: &HeaderMap,
    query: Option<&str>,
    identity: &Identity,
) -> Result<String, Response> {
    service
        .resolve_write(named_scope(headers, query).as_deref(), email_of(identity))
        .map_err(|error| detail(StatusCode::FORBIDDEN, &error.to_string()))
}

/// Parse a query integer, falling back to `default`.
pub fn query_i64(query: &[(String, String)], name: &str, default: i64) -> i64 {
    query
        .iter()
        .find(|(key, _)| key == name)
        .and_then(|(_, value)| value.parse().ok())
        .unwrap_or(default)
}

/// Parse an optional query string.
pub fn query_str<'a>(query: &'a [(String, String)], name: &str) -> Option<&'a str> {
    query
        .iter()
        .find(|(key, _)| key == name)
        .map(|(_, value)| value.as_str())
        .filter(|value| !value.is_empty())
}
