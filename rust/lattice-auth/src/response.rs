use axum::body::Body;
use axum::http::{header, HeaderValue, StatusCode};
use axum::response::Response;

pub const JSON_MEDIA_TYPE: &str = "application/json";
pub const JSON_MEDIA_TYPE_UTF8: &str = "application/json; charset=utf-8";

pub fn json_response(
    status: StatusCode,
    body: &str,
    extra: Option<(header::HeaderName, HeaderValue)>,
) -> Response {
    build(status, body, JSON_MEDIA_TYPE, extra)
}

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
