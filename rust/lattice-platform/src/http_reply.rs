use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use lattice_auth::response::json_response;
use lattice_auth::OrderedMap;
use lattice_core::messages;
use serde_json::{json, Value};

pub(crate) fn now_iso_utc() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let days = (secs / 86_400) as i64;
    let rem = secs % 86_400;
    let (year, month, day) = civil_from_days(days);
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}Z",
        rem / 3600,
        (rem % 3600) / 60,
        rem % 60
    )
}

fn civil_from_days(days: i64) -> (i32, u32, u32) {
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = if m <= 2 { y + 1 } else { y };
    (year as i32, m as u32, d as u32)
}

pub(crate) fn json_ok(body: impl serde::Serialize) -> Response {
    let text = serde_json::to_string(&body).unwrap_or_else(|_| "{}".into());
    json_response(StatusCode::OK, &text, None)
}

pub(crate) fn json_status(status: StatusCode, body: &Value) -> Response {
    let text = serde_json::to_string(body).unwrap_or_else(|_| "{}".into());
    json_response(status, &text, None)
}

pub(crate) fn detail(status: StatusCode, message: &str) -> Response {
    let mut map = OrderedMap::new();
    map.insert("detail", json!(message));
    let text = serde_json::to_string(&map).unwrap_or_else(|_| "{}".into());
    json_response(status, &text, None)
}

pub(crate) fn problem(
    kind: &str,
    loc: Value,
    msg: &str,
    input: Value,
    ctx: Option<Value>,
) -> OrderedMap {
    let mut entry = OrderedMap::new();
    entry.insert("type", json!(kind));
    entry.insert("loc", loc);
    entry.insert("msg", json!(msg));
    entry.insert("input", input);
    if let Some(ctx) = ctx {
        entry.insert("ctx", ctx);
    }
    entry
}

pub(crate) fn fastapi_errors(problems: &[OrderedMap]) -> Response {
    let mut envelope = OrderedMap::new();
    let list: Vec<Value> = problems
        .iter()
        .filter_map(|entry| serde_json::to_value(entry).ok())
        .collect();
    envelope.insert("detail", Value::Array(list));
    let body = serde_json::to_string(&envelope).unwrap_or_else(|_| "{}".into());
    json_response(StatusCode::UNPROCESSABLE_ENTITY, &body, None)
}

pub(crate) fn missing_body() -> Response {
    fastapi_errors(&[problem(
        "missing",
        json!(["body"]),
        "Field required",
        Value::Null,
        None,
    )])
}

pub(crate) fn missing_fields(input: &serde_json::Map<String, Value>, names: &[&str]) -> Response {
    let problems: Vec<OrderedMap> = names
        .iter()
        .map(|name| {
            problem(
                "missing",
                json!(["body", name]),
                "Field required",
                Value::Object(input.clone()),
                None,
            )
        })
        .collect();
    fastapi_errors(&problems)
}

pub(crate) fn parse_json_object(bytes: &[u8]) -> Result<serde_json::Map<String, Value>, Response> {
    if bytes.is_empty() {
        return Err(missing_body());
    }
    let parsed: Value = serde_json::from_slice(bytes).map_err(|error| {
        fastapi_errors(&[problem(
            "json_invalid",
            json!(["body", 0]),
            "JSON decode error",
            json!({}),
            Some(json!({ "error": error.to_string() })),
        )])
    })?;
    parsed.as_object().cloned().ok_or_else(|| {
        fastapi_errors(&[problem(
            "model_attributes_type",
            json!(["body"]),
            "Input should be a valid dictionary or object to extract fields from",
            parsed,
            None,
        )])
    })
}

pub(crate) fn language_of(headers: &HeaderMap) -> &'static str {
    messages::resolve_language_from_headers(
        headers
            .iter()
            .filter_map(|(k, v)| v.to_str().ok().map(|v| (k.as_str(), v))),
    )
}

pub(crate) fn message_detail(status: u16, id: &str, headers: &HeaderMap) -> Response {
    let err = messages::http_error(status, id, language_of(headers), &[]);
    let (code, body) = err.into_response_parts();
    json_status(
        StatusCode::from_u16(code).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
        &body,
    )
}

pub(crate) fn message_error(status: u16, id: &str, lang: &str, args: &[(&str, &str)]) -> Response {
    let err = messages::http_error(status, id, lang, args);
    let body = serde_json::to_string(&err.body).unwrap_or_else(|_| "{\"detail\":\"\"}".into());
    json_response(
        StatusCode::from_u16(err.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
        &body,
        None,
    )
}

pub(crate) fn json_from_ordered(map: &OrderedMap) -> Value {
    serde_json::to_value(map).unwrap_or(json!({}))
}

pub(crate) fn now_iso_offset() -> String {
    let utc = now_iso_utc();
    match utc.strip_suffix('Z') {
        Some(prefix) => format!("{prefix}+00:00"),
        None => utc,
    }
}

pub(crate) fn workspace_from_headers(headers: &HeaderMap) -> Option<String> {
    lattice_auth::workspace_scope_from_request(headers, None)
}
