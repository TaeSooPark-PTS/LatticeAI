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

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::pin::Pin;
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use axum::body::Body;
use axum::extract::{RawQuery, State};
use axum::http::{header, HeaderMap, HeaderValue, Response, StatusCode};
use axum::middleware::{from_fn, Next};
use axum::routing::{get, MethodRouter};
use axum::Router;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use tower_http::services::ServeDir;

use lattice_core::worker::{WorkerSeamClient, WorkerSeamError};

use crate::ui_redirects::app_redirect;

use super::*;

pub fn json_detail(status: StatusCode, detail: &str) -> Response<Body> {
    let body = serde_json::to_vec(&json!({ "detail": detail }))
        .unwrap_or_else(|_| b"{\"detail\":\"Internal Server Error\"}".to_vec());
    let mut response = Response::new(Body::from(body));
    *response.status_mut() = status;
    response.headers_mut().insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("application/json"),
    );
    response
}

/// FastAPI's 405, with the `Allow` header the route declares.
///
/// The body is written even when the refused method is `HEAD`: hyper drops it on
/// the wire and keeps the `Content-Length` that describes it, which is exactly
/// what Starlette does (a `HEAD /manifest.json` is `Content-Length: 31` with no
/// bytes). Emptying it here instead would report a length of zero and quietly
/// disagree with the recording.
pub fn method_not_allowed(allow: &'static str) -> Response<Body> {
    let mut response = json_detail(StatusCode::METHOD_NOT_ALLOWED, "Method Not Allowed");
    response
        .headers_mut()
        .insert(header::ALLOW, HeaderValue::from_static(allow));
    response
}

/// A 200 carrying file bytes and an explicit content type.
pub(crate) fn file_response(bytes: Vec<u8>, content_type: &'static str) -> Response<Body> {
    let mut response = Response::new(Body::from(bytes));
    response
        .headers_mut()
        .insert(header::CONTENT_TYPE, HeaderValue::from_static(content_type));
    response
}

/// The invitation wall: 403, HTML, no cache directives (Python sets none).
pub(crate) fn invite_denied() -> Response<Body> {
    let mut response = Response::new(Body::from(INVITE_DENIED_HTML));
    *response.status_mut() = StatusCode::FORBIDDEN;
    response.headers_mut().insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("text/html; charset=utf-8"),
    );
    response
}

/// Starlette's `mimetypes` answer for a served file, charset rule included.
///
/// Two entries are **machine-dependent** in Python and pinned here to the
/// answer a developer box and the release builder give (CPython also reads
/// `/etc/apache2/mime.types`): `.ico` is `image/x-icon` — matching the explicit
/// media type the `/favicon.ico` route hard-codes, so the same bytes do not
/// change type depending on which door they came through — and `.xml` is
/// `application/xml`. The fixture records both answers.
pub fn asset_content_type(path: &str) -> &'static str {
    let extension = Path::new(path)
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase();
    match extension.as_str() {
        "html" | "htm" => "text/html; charset=utf-8",
        "js" | "mjs" => "text/javascript; charset=utf-8",
        "css" => "text/css; charset=utf-8",
        "txt" => "text/plain; charset=utf-8",
        "md" => "text/markdown; charset=utf-8",
        "json" => "application/json",
        "xml" => "application/xml",
        "wasm" => "application/wasm",
        "pdf" => "application/pdf",
        "svg" => "image/svg+xml",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "webp" => "image/webp",
        "ico" => "image/x-icon",
        "woff" => "font/woff",
        "woff2" => "font/woff2",
        "ttf" => "font/ttf",
        "otf" => "font/otf",
        "mp4" => "video/mp4",
        "webm" => "video/webm",
        // Starlette's fallback when `mimetypes` has no answer — `.map` files
        // (vite source maps) take this branch.
        _ => "application/octet-stream",
    }
}
