//! Shared HTTP / time helpers used by sibling R8 modules.

use std::time::{SystemTime, UNIX_EPOCH};

use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use lattice_auth::{AuthState, Identity, OrderedMap};
use lattice_core::messages;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

// ── shared HTTP / time / store (used by sibling R8 modules) ────────────────

pub(crate) fn now_iso_seconds() -> String {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let dt = chrono_naive(secs);
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}",
        dt.0, dt.1, dt.2, dt.3, dt.4, dt.5
    )
}

pub(crate) fn now_iso_full() -> String {
    let dur = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let dt = chrono_naive(dur.as_secs());
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}.{:06}",
        dt.0,
        dt.1,
        dt.2,
        dt.3,
        dt.4,
        dt.5,
        dur.subsec_micros()
    )
}

/// Civil time from a unix timestamp, local-offset-free (UTC numbers).
fn chrono_naive(secs: u64) -> (i32, u32, u32, u32, u32, u32) {
    let z = secs as i64;
    let days = z.div_euclid(86_400);
    let rem = z.rem_euclid(86_400) as u32;
    let hour = rem / 3600;
    let min = (rem % 3600) / 60;
    let sec = rem % 60;
    let (y, m, d) = civil_from_days(days);
    (y, m, d, hour, min, sec)
}

fn civil_from_days(z: i64) -> (i32, u32, u32) {
    // Howard Hinnant civil_from_days
    let z = z + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    (y as i32, m as u32, d as u32)
}

pub(crate) fn lang(headers: &HeaderMap) -> &'static str {
    messages::resolve_language(
        headers
            .get(messages::LANGUAGE_HEADER)
            .and_then(|v| v.to_str().ok()),
        headers
            .get(axum::http::header::ACCEPT_LANGUAGE)
            .and_then(|v| v.to_str().ok()),
    )
}

pub(crate) fn json_text(status: StatusCode, text: &str) -> Response {
    lattice_auth::response::json_response(status, text, None)
}

pub(crate) fn json_status(status: StatusCode, body: &OrderedMap) -> Response {
    let text = serde_json::to_string(body).unwrap_or_else(|_| "{}".into());
    json_text(status, &text)
}

pub(crate) fn json_value(status: StatusCode, body: &Value) -> Response {
    let text = serde_json::to_string(body).unwrap_or_else(|_| "{}".into());
    json_text(status, &text)
}

pub(crate) fn detail(status: StatusCode, message: &str) -> Response {
    let mut body = OrderedMap::new();
    body.insert("detail", json!(message));
    json_status(status, &body)
}

pub(crate) fn localized(status: u16, id: &str, headers: &HeaderMap) -> Response {
    let err = messages::http_error(status, id, lang(headers), &[]);
    let (code, body) = err.into_response_parts();
    json_value(
        StatusCode::from_u16(code).unwrap_or(StatusCode::BAD_REQUEST),
        &body,
    )
}

pub(crate) fn missing_fields(input: &Value, fields: &[&str]) -> Response {
    let mut details = Vec::new();
    for field in fields {
        let mut entry = OrderedMap::new();
        entry.insert("type", json!("missing"));
        entry.insert("loc", json!(["body", field]));
        entry.insert("msg", json!("Field required"));
        entry.insert("input", input.clone());
        details.push(Value::Object(
            entry
                .iter()
                .map(|(k, v)| (k.to_string(), v.clone()))
                .collect(),
        ));
    }
    // Preserve insertion order of each entry: serialize via OrderedMap list.
    let rendered: Vec<String> = fields
        .iter()
        .map(|field| {
            let mut entry = OrderedMap::new();
            entry.insert("type", json!("missing"));
            entry.insert("loc", json!(["body", field]));
            entry.insert("msg", json!("Field required"));
            entry.insert("input", input.clone());
            serde_json::to_string(&entry).unwrap_or_else(|_| "{}".into())
        })
        .collect();
    let body = format!("{{\"detail\":[{}]}}", rendered.join(","));
    let _ = details;
    json_text(StatusCode::UNPROCESSABLE_ENTITY, &body)
}

pub(crate) fn parse_json_object(bytes: &[u8]) -> Result<Value, Response> {
    match serde_json::from_slice::<Value>(bytes) {
        Ok(Value::Object(map)) => Ok(Value::Object(map)),
        Ok(other) => {
            let mut entry = OrderedMap::new();
            entry.insert("type", json!("model_attributes_type"));
            entry.insert("loc", json!(["body"]));
            entry.insert(
                "msg",
                json!("Input should be a valid dictionary or object to extract fields from"),
            );
            entry.insert("input", other);
            let body = format!(
                "{{\"detail\":[{}]}}",
                serde_json::to_string(&entry).unwrap_or_else(|_| "{}".into())
            );
            Err(json_text(StatusCode::UNPROCESSABLE_ENTITY, &body))
        }
        Err(error) => {
            let mut ctx = OrderedMap::new();
            ctx.insert("error", json!(error.to_string()));
            let mut entry = OrderedMap::new();
            entry.insert("type", json!("json_invalid"));
            entry.insert("loc", json!(["body", 0]));
            entry.insert("msg", json!("JSON decode error"));
            entry.insert("input", json!({}));
            entry.insert("ctx", serde_json::to_value(&ctx).unwrap_or(json!({})));
            let body = format!(
                "{{\"detail\":[{}]}}",
                serde_json::to_string(&entry).unwrap_or_else(|_| "{}".into())
            );
            Err(json_text(StatusCode::UNPROCESSABLE_ENTITY, &body))
        }
    }
}

pub(crate) fn require_user(auth: &AuthState, headers: &HeaderMap) -> Result<Identity, Response> {
    auth.require_user(headers)
}

pub(crate) fn require_admin(auth: &AuthState, headers: &HeaderMap) -> Result<Identity, Response> {
    auth.require_admin(headers)
}

pub(crate) fn requested_scope(headers: &HeaderMap, query: Option<&str>) -> String {
    lattice_auth::workspace_scope_from_request(headers, query)
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "personal".into())
}

pub(crate) fn value_to_ordered(value: &Value) -> OrderedMap {
    let mut out = OrderedMap::new();
    if let Some(obj) = value.as_object() {
        for (k, v) in obj {
            out.insert(k.clone(), v.clone());
        }
    }
    out
}

pub(crate) fn dump_indent2(value: &Value) -> String {
    serde_json::to_string_pretty(value).unwrap_or_else(|_| "[]".into())
}

pub(crate) fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

pub(crate) fn json_hash(value: &Value) -> String {
    let payload = serde_json::to_string(value).unwrap_or_default();
    sha256_hex(payload.as_bytes())
}
