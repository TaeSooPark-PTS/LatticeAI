//! How a body becomes the exact HTTP response Starlette would have sent.
//!
//! Two shapes, and the difference between them is load-bearing:
//!
//! * `JSONResponse` (every route and every `HTTPException`) renders with
//!   `separators=(",", ":")` and declares `content-type: application/json` with
//!   **no** charset — Starlette only appends one for `text/*`;
//! * the CSRF middleware writes its own ASGI response and states
//!   `application/json; charset=utf-8`.
//!
//! Clients read `detail` off these bodies (`frontend/src/api/base.ts`), so the
//! shape is a contract, not a detail.

use axum::body::Body;
use axum::http::{header, HeaderValue, StatusCode};
use axum::response::Response;

/// Starlette's `JSONResponse` media type.
pub const JSON_MEDIA_TYPE: &str = "application/json";
/// The media type the CSRF guard writes by hand.
pub const JSON_MEDIA_TYPE_UTF8: &str = "application/json; charset=utf-8";

/// One JSON response, body verbatim, with an optional extra header.
///
/// `extra` carries the two headers this crate ever adds: `Set-Cookie` on the
/// login/logout pair and `Retry-After` on a rate-limit refusal.
pub fn json_response(
    status: StatusCode,
    body: &str,
    extra: Option<(header::HeaderName, HeaderValue)>,
) -> Response {
    build(status, body, JSON_MEDIA_TYPE, extra)
}

/// One JSON response that states `charset=utf-8`, as the CSRF guard does.
pub fn json_response_utf8(status: StatusCode, body: &str) -> Response {
    build(status, body, JSON_MEDIA_TYPE_UTF8, None)
}

fn build(
    status: StatusCode,
    body: &str,
    media_type: &str,
    extra: Option<(header::HeaderName, HeaderValue)>,
) -> Response {
    let mut builder = Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, media_type);
    if let Some((name, value)) = extra {
        builder = builder.header(name, value);
    }
    builder
        .body(Body::from(body.to_owned()))
        // The only way this fails is an invalid header name/value, and every
        // one of them is a constant in this crate.
        .unwrap_or_else(|_| Response::new(Body::from(body.to_owned())))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn json_response_states_no_charset() {
        let response = json_response(StatusCode::OK, "{}", None);
        assert_eq!(
            response.headers().get(header::CONTENT_TYPE).unwrap(),
            "application/json"
        );
        assert_eq!(response.status(), StatusCode::OK);
    }

    #[test]
    fn csrf_response_states_charset() {
        let response = json_response_utf8(StatusCode::FORBIDDEN, "{}");
        assert_eq!(
            response.headers().get(header::CONTENT_TYPE).unwrap(),
            "application/json; charset=utf-8"
        );
    }

    #[test]
    fn extra_header_rides_along() {
        let response = json_response(
            StatusCode::TOO_MANY_REQUESTS,
            "{}",
            Some((header::RETRY_AFTER, HeaderValue::from_static("3"))),
        );
        assert_eq!(response.headers().get(header::RETRY_AFTER).unwrap(), "3");
    }
}
