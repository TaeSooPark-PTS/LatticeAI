//! Reverse proxy to the Python worker.
//!
//! Everything the host does not own itself is forwarded verbatim: method,
//! path, query, headers and body in, status/headers/body out. The response
//! body is *streamed* — SSE endpoints (`/api/chat/stream`, the agent step
//! feed) must deliver each event as it happens, so nothing here may buffer a
//! response.

use std::sync::Arc;

use axum::body::Body;
use axum::extract::{Request, State};
use axum::http::header::{CONTENT_LENGTH, TRANSFER_ENCODING};
use axum::http::{HeaderMap, HeaderName, HeaderValue, StatusCode, Uri};
use axum::response::{IntoResponse, Response};

use super::GatewayState;

/// Headers that describe a single hop and must never be forwarded.
pub const HOP_BY_HOP: [&str; 9] = [
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
];

/// Request bodies at or below this size are forwarded with their exact
/// `Content-Length`; anything larger (or of unknown length) is streamed.
pub const BUFFERED_REQUEST_LIMIT: usize = 1024 * 1024;

fn is_hop_by_hop(name: &HeaderName) -> bool {
    let lower = name.as_str().to_ascii_lowercase();
    HOP_BY_HOP.contains(&lower.as_str())
}

/// Header names listed inside a `Connection:` value — also single-hop.
fn connection_tokens(headers: &HeaderMap) -> Vec<String> {
    headers
        .get_all(axum::http::header::CONNECTION)
        .iter()
        .filter_map(|value| value.to_str().ok())
        .flat_map(|value| value.split(','))
        .map(|token| token.trim().to_ascii_lowercase())
        .filter(|token| !token.is_empty())
        .collect()
}

/// Copy every forwardable header from `src` into a fresh map.
pub fn forwardable_headers(src: &HeaderMap) -> HeaderMap {
    let tokens = connection_tokens(src);
    let mut out = HeaderMap::with_capacity(src.len());
    for (name, value) in src.iter() {
        if is_hop_by_hop(name) || tokens.contains(&name.as_str().to_ascii_lowercase()) {
            continue;
        }
        out.append(name.clone(), value.clone());
    }
    out
}

/// The upstream URL for an inbound request.
pub fn target_url(origin: &str, uri: &Uri) -> Result<reqwest::Url, String> {
    let path_and_query = uri
        .path_and_query()
        .map(|value| value.as_str())
        .unwrap_or("/");
    let raw = format!("{}{}", origin.trim_end_matches('/'), path_and_query);
    reqwest::Url::parse(&raw).map_err(|err| format!("invalid upstream url '{raw}': {err}"))
}

/// How the request body should be forwarded.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BodyPlan {
    /// No body at all (the request declared none).
    Empty,
    /// Buffer up to this many bytes and forward with an exact length.
    Buffer(usize),
    /// Forward as a chunked stream of unknown length.
    Stream,
}

/// Decide how to forward the body from the request's own framing headers.
pub fn body_plan(headers: &HeaderMap) -> BodyPlan {
    let declared = headers
        .get(CONTENT_LENGTH)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.trim().parse::<usize>().ok());
    match declared {
        Some(0) => BodyPlan::Empty,
        Some(len) if len <= BUFFERED_REQUEST_LIMIT => BodyPlan::Buffer(len),
        Some(_) => BodyPlan::Stream,
        None => {
            if headers.contains_key(TRANSFER_ENCODING) {
                BodyPlan::Stream
            } else {
                BodyPlan::Empty
            }
        }
    }
}

/// A 502 with an honest JSON body describing why the worker could not answer.
pub fn worker_unavailable(state: &GatewayState, detail: String) -> Response {
    let payload = serde_json::json!({
        "error": "worker_unavailable",
        "detail": detail,
        "worker": state.status(),
    });
    (StatusCode::BAD_GATEWAY, axum::Json(payload)).into_response()
}

/// Forward one request to the worker and stream the answer back.
pub async fn proxy_handler(State(state): State<Arc<GatewayState>>, request: Request) -> Response {
    let origin = state.worker_origin();
    let (parts, body) = request.into_parts();
    let url = match target_url(&origin, &parts.uri) {
        Ok(url) => url,
        Err(detail) => return worker_unavailable(&state, detail),
    };

    let mut headers = forwardable_headers(&parts.headers);
    let plan = body_plan(&parts.headers);
    let upstream_body = match plan {
        BodyPlan::Empty => {
            headers.remove(CONTENT_LENGTH);
            None
        }
        BodyPlan::Buffer(limit) => match axum::body::to_bytes(body, limit.max(1)).await {
            Ok(bytes) => Some(reqwest::Body::from(bytes)),
            Err(err) => {
                return (
                    StatusCode::BAD_REQUEST,
                    axum::Json(serde_json::json!({
                        "error": "invalid_request_body",
                        "detail": err.to_string(),
                    })),
                )
                    .into_response()
            }
        },
        BodyPlan::Stream => {
            // Unknown length upstream: chunked, so the length header must go.
            headers.remove(CONTENT_LENGTH);
            Some(reqwest::Body::wrap_stream(body.into_data_stream()))
        }
    };

    let mut request = state.client().request(parts.method, url).headers(headers);
    if let Some(body) = upstream_body {
        request = request.body(body);
    }

    let response = match request.send().await {
        Ok(response) => response,
        Err(err) => {
            return worker_unavailable(
                &state,
                format!("upstream request to {origin} failed: {err}"),
            )
        }
    };

    let status = StatusCode::from_u16(response.status().as_u16())
        .unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
    let mut builder = Response::builder().status(status);
    for (name, value) in response.headers().iter() {
        if is_hop_by_hop(name) {
            continue;
        }
        builder = builder.header(name.clone(), value.clone());
    }
    match builder.body(Body::from_stream(response.bytes_stream())) {
        Ok(response) => response,
        Err(err) => worker_unavailable(&state, format!("malformed upstream response: {err}")),
    }
}

/// Convenience for tests and callers building header maps by hand.
pub fn header_value(value: &str) -> HeaderValue {
    HeaderValue::from_str(value).unwrap_or_else(|_| HeaderValue::from_static(""))
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::header::{CONNECTION, HOST};

    fn headers(pairs: &[(&str, &str)]) -> HeaderMap {
        let mut map = HeaderMap::new();
        for (name, value) in pairs {
            map.append(
                HeaderName::from_bytes(name.as_bytes()).expect("header name"),
                header_value(value),
            );
        }
        map
    }

    #[test]
    fn hop_by_hop_headers_are_dropped() {
        let src = headers(&[
            ("host", "gateway.local"),
            ("connection", "keep-alive"),
            ("transfer-encoding", "chunked"),
            ("upgrade", "websocket"),
            ("authorization", "Bearer t"),
            ("x-custom", "kept"),
        ]);
        let out = forwardable_headers(&src);
        assert!(!out.contains_key(HOST));
        assert!(!out.contains_key(CONNECTION));
        assert!(!out.contains_key(TRANSFER_ENCODING));
        assert_eq!(
            out.get("authorization").and_then(|v| v.to_str().ok()),
            Some("Bearer t")
        );
        assert_eq!(
            out.get("x-custom").and_then(|v| v.to_str().ok()),
            Some("kept")
        );
    }

    #[test]
    fn headers_named_by_connection_are_dropped_too() {
        let src = headers(&[
            ("connection", "x-hop, keep-alive"),
            ("x-hop", "single hop only"),
            ("x-keep", "kept"),
        ]);
        let out = forwardable_headers(&src);
        assert!(!out.contains_key("x-hop"));
        assert!(out.contains_key("x-keep"));
    }

    #[test]
    fn repeated_headers_survive_as_repeats() {
        let src = headers(&[("set-cookie", "a=1"), ("set-cookie", "b=2")]);
        let out = forwardable_headers(&src);
        assert_eq!(out.get_all("set-cookie").iter().count(), 2);
    }

    #[test]
    fn target_url_keeps_path_and_query() {
        let uri: Uri = "/api/search?q=hello%20world&k=5".parse().expect("uri");
        let url = target_url("http://127.0.0.1:4825/", &uri).expect("url");
        assert_eq!(
            url.as_str(),
            "http://127.0.0.1:4825/api/search?q=hello%20world&k=5"
        );
    }

    #[test]
    fn target_url_defaults_to_root() {
        let uri: Uri = "/".parse().expect("uri");
        let url = target_url("http://127.0.0.1:4825", &uri).expect("url");
        assert_eq!(url.as_str(), "http://127.0.0.1:4825/");
    }

    #[test]
    fn target_url_rejects_a_broken_origin() {
        let uri: Uri = "/health".parse().expect("uri");
        assert!(target_url("not-a-url", &uri).is_err());
    }

    #[test]
    fn body_plan_reads_the_framing_headers() {
        assert_eq!(body_plan(&HeaderMap::new()), BodyPlan::Empty);
        assert_eq!(
            body_plan(&headers(&[("content-length", "0")])),
            BodyPlan::Empty
        );
        assert_eq!(
            body_plan(&headers(&[("content-length", "12")])),
            BodyPlan::Buffer(12)
        );
        assert_eq!(
            body_plan(&headers(&[("transfer-encoding", "chunked")])),
            BodyPlan::Stream
        );
        let huge = (BUFFERED_REQUEST_LIMIT + 1).to_string();
        assert_eq!(
            body_plan(&headers(&[("content-length", &huge)])),
            BodyPlan::Stream
        );
    }
}
