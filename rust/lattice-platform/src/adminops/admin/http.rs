//! Shared HTTP helpers for the R2 families.

use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use lattice_auth::response::json_response;
use lattice_auth::OrderedMap;
use lattice_core::messages::{self, LANGUAGE_HEADER};
use serde_json::{json, Value};

pub fn language_from(headers: &HeaderMap) -> &'static str {
    messages::resolve_language(
        headers.get(LANGUAGE_HEADER).and_then(|v| v.to_str().ok()),
        headers
            .get(axum::http::header::ACCEPT_LANGUAGE)
            .and_then(|v| v.to_str().ok()),
    )
}

pub fn json_ok(body: &OrderedMap) -> Response {
    json_response(
        StatusCode::OK,
        &serde_json::to_string(body).unwrap_or_else(|_| "{}".into()),
        None,
    )
}

pub fn json_ok_value(body: &Value) -> Response {
    json_response(
        StatusCode::OK,
        &serde_json::to_string(body).unwrap_or_else(|_| "null".into()),
        None,
    )
}

pub fn json_status(status: StatusCode, body: &OrderedMap) -> Response {
    json_response(
        status,
        &serde_json::to_string(body).unwrap_or_else(|_| "{}".into()),
        None,
    )
}

pub fn message_error(status: u16, id: &str, lang: &str, args: &[(&str, &str)]) -> Response {
    let err = messages::http_error(status, id, lang, args);
    let body = serde_json::to_string(&err.body).unwrap_or_else(|_| "{\"detail\":\"\"}".into());
    json_response(
        StatusCode::from_u16(err.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
        &body,
        None,
    )
}

pub fn detail_status(status: StatusCode, detail: &str) -> Response {
    let mut body = OrderedMap::new();
    body.insert("detail", json!(detail));
    json_status(status, &body)
}

pub fn workspace_from_headers(headers: &HeaderMap) -> Option<String> {
    lattice_auth::workspace_scope_from_request(headers, None)
}
