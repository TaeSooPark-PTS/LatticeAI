use std::path::Path;

use axum::body::Body;
use axum::http::{header, HeaderValue, Response, StatusCode};
use serde_json::json;

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
