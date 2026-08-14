//! The one Server-Sent Events frame builder — a port of `latticeai/core/sse.py`.
//!
//! Five Python modules used to format their own frame and agreed by accident;
//! the port keeps the agreement structural. `event = None` produces the bare
//! `data:` shape, a named event produces the `event:`/`data:` shape, and both
//! end with the blank line that terminates a frame.
//!
//! Two rules ride here rather than in the callers, because the clients depend on
//! them and a caller that forgot one produces a stream that silently stops
//! parsing (scout_clients.md §1.4, §7.3):
//!
//! * the terminator is the literal sentinel `data: [DONE]`, matched by the SPA
//!   **before** its malformed-frame check;
//! * an error answer must never carry `text/event-stream`, because both SPA
//!   stream helpers check the content type and fall back to JSON parsing.
//!
//! Frames are rendered with `serde_json`'s compact separators, which is what
//! `json.dumps(..., ensure_ascii=False)` produces for the payloads chat builds
//! (no spaces after `:` or `,`). Non-ASCII is emitted verbatim, not `\uXXXX`.

use axum::body::Body;
use axum::http::{header, HeaderValue, StatusCode};
use axum::response::Response;
use serde_json::Value;

/// The literal every chat stream ends with.
pub const DONE: &str = "data: [DONE]\n\n";

/// The media type. Only ever on a 200 — see the module docs.
pub const EVENT_STREAM: &str = "text/event-stream";

/// The named event the live agent loop emits per step.
pub const AGENT_STEP: &str = "agent_step";

/// `core.sse.sse_frame(event, data)`.
pub fn frame(event: Option<&str>, data: &Value) -> String {
    let payload = serde_json::to_string(data).unwrap_or_else(|_| "null".into());
    match event {
        Some(name) if !name.is_empty() => format!("event: {name}\ndata: {payload}\n\n"),
        _ => format!("data: {payload}\n\n"),
    }
}

/// One anonymous `message` frame — the shape every chat client reads.
pub fn data_frame(data: &Value) -> String {
    frame(None, data)
}

/// `chat_helpers.single_text_stream` — one chunk, then the sentinel.
///
/// The model label defaults to `"system"` exactly as the Python default does;
/// the intent handlers pass `network_status`, `history` or `client_url`.
pub fn single_text_stream(text: &str, model: &str) -> String {
    let mut out = data_frame(&serde_json::json!({"chunk": text, "model": model}));
    out.push_str(DONE);
    out
}

/// `chat_stream.agent_payload_stream` — the answer, an empty trailer, sentinel.
///
/// Two frames carrying the same payload is not redundancy: clients that render
/// incrementally show the first and clients that wait for a terminal empty
/// chunk see the second, and the historical stream shape has both.
pub fn agent_payload_stream(answer: &str, payload: &Value, model: &str) -> String {
    let mut out = data_frame(&serde_json::json!({
        "chunk": answer, "model": model, "agent": payload,
    }));
    out.push_str(&data_frame(&serde_json::json!({
        "chunk": "", "model": model, "agent": payload,
    })));
    out.push_str(DONE);
    out
}

/// A `text/event-stream` 200 over an already-rendered body.
pub fn stream_response(body: impl Into<Body>, headers: &[(&str, &str)]) -> Response {
    let mut response = Response::new(body.into());
    *response.status_mut() = StatusCode::OK;
    response.headers_mut().insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("text/event-stream; charset=utf-8"),
    );
    // Same anti-buffering header the gateway proxy sets: without it an
    // intermediary can hold the first token until the stream closes.
    response
        .headers_mut()
        .insert("x-accel-buffering", HeaderValue::from_static("no"));
    for (name, value) in headers {
        if let (Ok(name), Ok(value)) = (
            axum::http::HeaderName::from_bytes(name.as_bytes()),
            HeaderValue::from_str(value),
        ) {
            response.headers_mut().insert(name, value);
        }
    }
    response
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn the_two_frame_shapes_are_the_python_ones() {
        assert_eq!(frame(None, &json!({"a": 1})), "data: {\"a\":1}\n\n");
        assert_eq!(
            frame(Some(AGENT_STEP), &json!({"a": 1})),
            "event: agent_step\ndata: {\"a\":1}\n\n"
        );
        // `event=""` is falsy in Python, so it takes the bare shape too.
        assert_eq!(frame(Some(""), &json!(1)), "data: 1\n\n");
    }

    #[test]
    fn korean_is_emitted_verbatim_like_ensure_ascii_false() {
        assert!(frame(None, &json!({"chunk": "안녕"})).contains("안녕"));
    }

    #[test]
    fn a_single_text_stream_is_one_chunk_and_the_sentinel() {
        assert_eq!(
            single_text_stream("hi", "system"),
            "data: {\"chunk\":\"hi\",\"model\":\"system\"}\n\ndata: [DONE]\n\n"
        );
    }

    #[test]
    fn an_agent_payload_stream_repeats_the_payload_with_an_empty_chunk() {
        let rendered = agent_payload_stream("done", &json!({"status": "ok"}), "m");
        let frames: Vec<&str> = rendered.split("\n\n").filter(|f| !f.is_empty()).collect();
        assert_eq!(frames.len(), 3);
        assert!(frames[0].contains("\"chunk\":\"done\""));
        assert!(frames[1].contains("\"chunk\":\"\""));
        assert_eq!(frames[2], "data: [DONE]");
    }

    #[test]
    fn a_stream_response_carries_the_event_stream_type_and_extra_headers() {
        let response = stream_response("data: x\n\n", &[("X-Model", "m")]);
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            response.headers()[header::CONTENT_TYPE],
            "text/event-stream; charset=utf-8"
        );
        assert_eq!(response.headers()["x-accel-buffering"], "no");
        assert_eq!(response.headers()["x-model"], "m");
        // An unusable header name is dropped rather than panicking mid-stream.
        let response = stream_response("", &[("bad header", "v"), ("X-Ok", "\u{7f}")]);
        assert!(response.headers().get("x-ok").is_none());
    }
}
