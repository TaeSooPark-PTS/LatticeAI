use axum::body::Body;
use axum::http::{header, HeaderValue, StatusCode};
use axum::response::Response;
use serde_json::Value;

pub const DONE: &str = "data: [DONE]\n\n";

pub const EVENT_STREAM: &str = "text/event-stream";

pub const AGENT_STEP: &str = "agent_step";

pub fn frame(event: Option<&str>, data: &Value) -> String {
    let payload = serde_json::to_string(data).unwrap_or_else(|_| "null".into());
    match event {
        Some(name) if !name.is_empty() => format!("event: {name}\ndata: {payload}\n\n"),
        _ => format!("data: {payload}\n\n"),
    }
}

pub fn data_frame(data: &Value) -> String {
    frame(None, data)
}

pub fn single_text_stream(text: &str, model: &str) -> String {
    let mut out = data_frame(&serde_json::json!({"chunk": text, "model": model}));
    out.push_str(DONE);
    out
}

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

pub fn stream_response(body: impl Into<Body>, headers: &[(&str, &str)]) -> Response {
    let mut response = Response::new(body.into());
    *response.status_mut() = StatusCode::OK;
    response.headers_mut().insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("text/event-stream; charset=utf-8"),
    );
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
        let response = stream_response("", &[("bad header", "v"), ("X-Ok", "\u{7f}")]);
        assert!(response.headers().get("x-ok").is_none());
    }
}
