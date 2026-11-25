use std::net::SocketAddr;
use std::sync::Arc;

use axum::body::Body;
use axum::extract::{ConnectInfo, Request, State};
use axum::http::header::{CONTENT_LENGTH, CONTENT_TYPE, HOST, LOCATION, TRANSFER_ENCODING};
use axum::http::{HeaderMap, HeaderName, HeaderValue, StatusCode, Uri};
use axum::response::{IntoResponse, Response};

use super::GatewayState;

pub const ACCEL_BUFFERING: &str = "x-accel-buffering";

pub const EVENT_STREAM: &str = "text/event-stream";

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

pub const BUFFERED_REQUEST_LIMIT: usize = 1024 * 1024;

pub const FORWARDED_FOR: &str = "x-forwarded-for";
pub const FORWARDED_PROTO: &str = "x-forwarded-proto";
pub const FORWARDED_HOST: &str = "x-forwarded-host";

pub const FORWARDED_PROTO_VALUE: &str = "http";

fn is_hop_by_hop(name: &HeaderName) -> bool {
    let lower = name.as_str().to_ascii_lowercase();
    HOP_BY_HOP.contains(&lower.as_str())
}

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

pub fn apply_forwarded(
    headers: &mut HeaderMap,
    host: Option<&HeaderValue>,
    peer: Option<SocketAddr>,
) {
    for name in [FORWARDED_FOR, FORWARDED_PROTO, FORWARDED_HOST] {
        headers.remove(name);
    }
    if let Some(peer) = peer {
        if let Ok(value) = HeaderValue::from_str(&peer.ip().to_string()) {
            headers.insert(HeaderName::from_static(FORWARDED_FOR), value);
        }
    }
    headers.insert(
        HeaderName::from_static(FORWARDED_PROTO),
        HeaderValue::from_static(FORWARDED_PROTO_VALUE),
    );
    if let Some(host) = host {
        headers.insert(HeaderName::from_static(FORWARDED_HOST), host.clone());
    }
}

pub fn gateway_origin(host: Option<&HeaderValue>) -> Option<String> {
    let host = host?.to_str().ok()?.trim();
    (!host.is_empty()).then(|| format!("{FORWARDED_PROTO_VALUE}://{host}"))
}

pub fn rewrite_location(value: &str, worker_origin: &str, gateway: Option<&str>) -> Option<String> {
    let worker = worker_origin.trim_end_matches('/');
    let rest = value.strip_prefix(worker)?;
    if !(rest.is_empty() || rest.starts_with('/') || rest.starts_with('?') || rest.starts_with('#'))
    {
        return None;
    }
    let rest = if rest.is_empty() { "/" } else { rest };
    Some(match gateway {
        Some(origin) => format!("{}{rest}", origin.trim_end_matches('/')),
        None => rest.to_string(),
    })
}

pub fn target_url(origin: &str, uri: &Uri) -> Result<reqwest::Url, String> {
    let path_and_query = uri
        .path_and_query()
        .map(|value| value.as_str())
        .unwrap_or("/");
    let raw = format!("{}{}", origin.trim_end_matches('/'), path_and_query);
    reqwest::Url::parse(&raw).map_err(|err| format!("invalid upstream url '{raw}': {err}"))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BodyPlan {
    Empty,
    Buffer(usize),
    Stream,
}

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

pub fn needs_buffering_hint(headers: &HeaderMap) -> bool {
    if headers.contains_key(ACCEL_BUFFERING) {
        return false;
    }
    headers
        .get(CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .map(|value| {
            value
                .split(';')
                .next()
                .unwrap_or_default()
                .trim()
                .eq_ignore_ascii_case(EVENT_STREAM)
        })
        .unwrap_or(false)
}

pub fn worker_unavailable(state: &GatewayState, detail: String) -> Response {
    let payload = serde_json::json!({
        "error": "worker_unavailable",
        "detail": detail,
        "worker": state.status(),
    });
    (StatusCode::BAD_GATEWAY, axum::Json(payload)).into_response()
}

pub async fn proxy_handler(State(state): State<Arc<GatewayState>>, request: Request) -> Response {
    let origin = state.worker_origin();
    let (parts, body) = request.into_parts();
    let url = match target_url(&origin, &parts.uri) {
        Ok(url) => url,
        Err(detail) => return worker_unavailable(&state, detail),
    };

    let inbound_host = parts.headers.get(HOST).cloned();
    let peer = parts
        .extensions
        .get::<ConnectInfo<SocketAddr>>()
        .map(|ConnectInfo(addr)| *addr);
    let mut headers = forwardable_headers(&parts.headers);
    apply_forwarded(&mut headers, inbound_host.as_ref(), peer);
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
            headers.remove(CONTENT_LENGTH);
            Some(reqwest::Body::wrap_stream(body.into_data_stream()))
        }
    };

    let mut request = state
        .proxy_client()
        .request(parts.method, url)
        .headers(headers);
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
    let gateway = gateway_origin(inbound_host.as_ref());
    let mut builder = Response::builder().status(status);
    for (name, value) in response.headers().iter() {
        if is_hop_by_hop(name) {
            continue;
        }
        if name == LOCATION {
            if let Some(rewritten) = value
                .to_str()
                .ok()
                .and_then(|raw| rewrite_location(raw, &origin, gateway.as_deref()))
                .and_then(|rewritten| HeaderValue::from_str(&rewritten).ok())
            {
                builder = builder.header(LOCATION, rewritten);
                continue;
            }
        }
        builder = builder.header(name.clone(), value.clone());
    }
    if needs_buffering_hint(response.headers()) {
        builder = builder.header(ACCEL_BUFFERING, HeaderValue::from_static("no"));
    }
    match builder.body(Body::from_stream(response.bytes_stream())) {
        Ok(response) => response,
        Err(err) => worker_unavailable(&state, format!("malformed upstream response: {err}")),
    }
}

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
    fn only_unmarked_event_streams_get_the_no_buffering_hint() {
        assert!(needs_buffering_hint(&headers(&[(
            "content-type",
            "text/event-stream"
        )])));
        assert!(needs_buffering_hint(&headers(&[(
            "content-type",
            "text/event-stream; charset=utf-8"
        )])));
        assert!(needs_buffering_hint(&headers(&[(
            "content-type",
            "TEXT/Event-Stream"
        )])));
        assert!(!needs_buffering_hint(&headers(&[
            ("content-type", "text/event-stream"),
            ("x-accel-buffering", "yes"),
        ])));
        assert!(!needs_buffering_hint(&headers(&[(
            "content-type",
            "application/json"
        )])));
        assert!(!needs_buffering_hint(&HeaderMap::new()));
    }

    #[test]
    fn the_forwarded_facts_replace_whatever_the_caller_claimed() {
        let mut out = headers(&[
            ("x-forwarded-for", "203.0.113.9"),
            ("x-forwarded-host", "evil.example"),
            ("x-forwarded-proto", "https"),
        ]);
        apply_forwarded(
            &mut out,
            Some(&header_value("127.0.0.1:4825")),
            Some("127.0.0.1:51000".parse().expect("addr")),
        );
        assert_eq!(
            out.get_all(FORWARDED_FOR).iter().count(),
            1,
            "the claimed chain is replaced, not appended to"
        );
        assert_eq!(
            out.get(FORWARDED_FOR).and_then(|v| v.to_str().ok()),
            Some("127.0.0.1")
        );
        assert_eq!(
            out.get(FORWARDED_HOST).and_then(|v| v.to_str().ok()),
            Some("127.0.0.1:4825")
        );
        assert_eq!(
            out.get(FORWARDED_PROTO).and_then(|v| v.to_str().ok()),
            Some("http")
        );
    }

    #[test]
    fn an_unknowable_fact_is_left_unstated_rather_than_invented() {
        let mut out = HeaderMap::new();
        apply_forwarded(&mut out, None, None);
        assert!(!out.contains_key(FORWARDED_FOR), "no peer, no claim");
        assert!(!out.contains_key(FORWARDED_HOST), "no Host, no claim");
        assert_eq!(
            out.get(FORWARDED_PROTO).and_then(|v| v.to_str().ok()),
            Some("http"),
            "the gateway does know its own scheme"
        );
        assert_eq!(gateway_origin(None), None);
        assert_eq!(gateway_origin(Some(&header_value("  "))), None);
        assert_eq!(
            gateway_origin(Some(&header_value("localhost:4825"))),
            Some("http://localhost:4825".to_string())
        );
    }

    #[test]
    fn only_a_location_naming_the_worker_is_rewritten() {
        let worker = "http://127.0.0.1:4899";
        let gateway = Some("http://localhost:4825");
        assert_eq!(
            rewrite_location("http://127.0.0.1:4899/app/", worker, gateway).as_deref(),
            Some("http://localhost:4825/app/")
        );
        assert_eq!(
            rewrite_location("http://127.0.0.1:4899", worker, gateway).as_deref(),
            Some("http://localhost:4825/"),
            "a bare origin still names a path"
        );
        assert_eq!(
            rewrite_location("http://127.0.0.1:4899/a?b=1", worker, gateway).as_deref(),
            Some("http://localhost:4825/a?b=1")
        );
        assert_eq!(
            rewrite_location(
                "http://127.0.0.1:4899/app/",
                "http://127.0.0.1:4899/",
                gateway
            )
            .as_deref(),
            Some("http://localhost:4825/app/")
        );
        assert_eq!(rewrite_location("/app#/chat", worker, gateway), None);
        assert_eq!(
            rewrite_location("https://idp.example/authorize", worker, gateway),
            None
        );
        assert_eq!(
            rewrite_location("http://127.0.0.1:48990/app", worker, gateway),
            None
        );
        assert_eq!(
            rewrite_location("http://127.0.0.1:4899/app/", worker, None).as_deref(),
            Some("/app/")
        );
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
