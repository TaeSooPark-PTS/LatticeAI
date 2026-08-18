use std::sync::Arc;

use axum::body::Body;
use axum::extract::{RawQuery, State};
use axum::http::{header, HeaderMap, HeaderValue, Response, StatusCode};

use crate::shell::ui_redirects::app_redirect;

use super::http::{file_response, invite_denied};
use super::invite::{constant_time_eq, issue_invite_cookie};
use super::*;

pub(crate) async fn root(
    State(state): State<Arc<StaticUiState>>,
    headers: HeaderMap,
    RawQuery(query): RawQuery,
) -> Response<Body> {
    let config = &state.config;
    if !config.invite_gate_enabled {
        return app_redirect("account", query.as_deref());
    }
    // A valid claim is enough; note that Python drops the query on this branch
    // (it calls the helper without the request), and so does this.
    if invite_authorized(config, &headers) {
        return app_redirect("account", None);
    }
    let supplied = query
        .as_deref()
        .and_then(|raw| first_query_value(raw, "code"));
    if !config.invite_code.is_empty() {
        if let Some(code) = supplied {
            if constant_time_eq(code.as_bytes(), config.invite_code.as_bytes()) {
                // Redirect rather than render, so the code leaves the URL bar
                // and the browser history with it.
                let mut response = app_redirect("account", None);
                if let Some(cookie) = issue_invite_cookie(config) {
                    response.headers_mut().append(header::SET_COOKIE, cookie);
                }
                return response;
            }
        }
    }
    invite_denied()
}

/// `GET /account` — where logout and manual navigation land.
pub(crate) async fn account(
    State(state): State<Arc<StaticUiState>>,
    headers: HeaderMap,
) -> Response<Body> {
    if !invite_authorized(&state.config, &headers) {
        return invite_denied();
    }
    app_redirect("account", None)
}

/// `GET /app` — the React shell.
pub(crate) async fn app_shell(
    State(state): State<Arc<StaticUiState>>,
    headers: HeaderMap,
) -> Response<Body> {
    if !invite_authorized(&state.config, &headers) {
        return invite_denied();
    }
    let page = state
        .config
        .static_dir
        .join(SHELL_RELATIVE[0])
        .join(SHELL_RELATIVE[1]);
    match tokio::fs::read(&page).await {
        Ok(bytes) => {
            let mut response = file_response(bytes, "text/html; charset=utf-8");
            let headers = response.headers_mut();
            headers.insert(
                header::CACHE_CONTROL,
                HeaderValue::from_static("no-cache, no-store, must-revalidate"),
            );
            headers.insert(header::PRAGMA, HeaderValue::from_static("no-cache"));
            headers.insert(header::EXPIRES, HeaderValue::from_static("0"));
            headers.insert(
                header::CONTENT_SECURITY_POLICY,
                HeaderValue::from_static(PRODUCTION_CSP),
            );
            response
        }
        Err(_) => json_detail(StatusCode::NOT_FOUND, "React shell not found."),
    }
}

/// `GET /manifest.json` — the PWA manifest, with the media type browsers want.
pub(crate) async fn manifest(State(state): State<Arc<StaticUiState>>) -> Response<Body> {
    match tokio::fs::read(state.config.static_dir.join("manifest.json")).await {
        Ok(bytes) => file_response(bytes, "application/manifest+json"),
        Err(_) => json_detail(StatusCode::NOT_FOUND, "Not Found"),
    }
}

/// `GET|HEAD /favicon.ico` — the `.ico`, else the 32px png, else nothing.
pub(crate) async fn favicon(State(state): State<Arc<StaticUiState>>) -> Response<Body> {
    let root = &state.config.static_dir;
    if let Ok(bytes) = tokio::fs::read(root.join("favicon.ico")).await {
        return file_response(bytes, "image/x-icon");
    }
    if let Ok(bytes) = tokio::fs::read(root.join("icons").join("favicon-32.png")).await {
        return file_response(bytes, "image/png");
    }
    json_detail(StatusCode::NOT_FOUND, "Not Found")
}

/// `GET /sw.js` — the service worker, allowed to claim the whole origin.
pub(crate) async fn service_worker(State(state): State<Arc<StaticUiState>>) -> Response<Body> {
    match tokio::fs::read(state.config.static_dir.join("sw.js")).await {
        Ok(bytes) => {
            let mut response = file_response(bytes, "application/javascript");
            response.headers_mut().insert(
                header::HeaderName::from_static("service-worker-allowed"),
                HeaderValue::from_static("/"),
            );
            response
        }
        Err(_) => json_detail(StatusCode::NOT_FOUND, "Not Found"),
    }
}
