//! Internal helpers for audit, handlers, and reports.

use std::time::{SystemTime, UNIX_EPOCH};

use axum::http::StatusCode;
use axum::response::Response;
use lattice_auth::response::json_response;
use lattice_auth::OrderedMap;
use serde_json::{json, Map, Value};

pub(crate) fn filter_audit_log(
    events: &[Value],
    q: Option<&str>,
    actor: Option<&str>,
    action: Option<&str>,
    severity: Option<&str>,
    limit: usize,
) -> Vec<Value> {
    let needle = q.unwrap_or("").trim().to_lowercase();
    let actor_filter = actor.unwrap_or("").trim().to_lowercase();
    let action_filter = action.unwrap_or("").trim().to_lowercase();
    let severity_filter = severity.unwrap_or("").trim().to_lowercase();
    let matched: Vec<Value> = events
        .iter()
        .filter(|event| {
            if !needle.is_empty() && !event_public_text(event).contains(&needle) {
                return false;
            }
            if !actor_filter.is_empty() {
                let hay = format!(
                    "{} {}",
                    event
                        .get("user_email")
                        .and_then(Value::as_str)
                        .unwrap_or(""),
                    event.get("actor").and_then(Value::as_str).unwrap_or("")
                )
                .to_lowercase();
                if !hay.contains(&actor_filter) {
                    return false;
                }
            }
            if !action_filter.is_empty() {
                let ev = format!(
                    "{} {}",
                    event
                        .get("event_type")
                        .and_then(Value::as_str)
                        .unwrap_or(""),
                    event.get("action").and_then(Value::as_str).unwrap_or("")
                )
                .to_lowercase();
                if !ev.contains(&action_filter) {
                    return false;
                }
            }
            if !severity_filter.is_empty() {
                let sev = event
                    .get("severity")
                    .or_else(|| event.get("sev"))
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_lowercase();
                if sev != severity_filter {
                    return false;
                }
            }
            true
        })
        .cloned()
        .collect();
    let start = matched.len().saturating_sub(limit);
    matched[start..].to_vec()
}

fn event_public_text(event: &Value) -> String {
    [
        "event_type",
        "action",
        "user_email",
        "actor",
        "target",
        "target_email",
        "workspace_id",
        "severity",
        "sev",
    ]
    .iter()
    .filter_map(|k| event.get(*k))
    .filter(|v| !v.is_null())
    .map(value_as_string)
    .map(|s| s.to_lowercase())
    .collect::<Vec<_>>()
    .join(" ")
}

pub fn json_from_ordered(map: &OrderedMap) -> Value {
    serde_json::to_value(map).unwrap_or(json!({}))
}

pub(crate) fn value_as_string(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

pub(crate) fn parse_object_body(bytes: &[u8]) -> Result<Map<String, Value>, Response> {
    if bytes.is_empty() {
        return Ok(Map::new());
    }
    match serde_json::from_slice::<Value>(bytes) {
        Ok(Value::Object(map)) => Ok(map),
        Ok(other) => Err(pydantic_errors(&[problem_entry(
            "model_attributes_type",
            json!(["body"]),
            "Input should be a valid dictionary or object to extract fields from",
            other,
            None,
        )])),
        Err(error) => Err(pydantic_errors(&[problem_entry(
            "json_invalid",
            json!(["body", 0]),
            "JSON decode error",
            json!({}),
            Some(json!({ "error": error.to_string() })),
        )])),
    }
}

pub(crate) fn query_bound_error(kind: &str, ctx_key: &str, bound: i64, input: &str) -> Response {
    let msg = if kind == "less_than_equal" {
        format!("Input should be less than or equal to {bound}")
    } else {
        format!("Input should be greater than or equal to {bound}")
    };
    pydantic_errors(&[problem_entry(
        kind,
        json!(["query", "limit"]),
        &msg,
        json!(input),
        Some(json!({ ctx_key: bound })),
    )])
}

pub fn now_iso() -> String {
    format_unix(unix_now())
}

pub fn today_str() -> String {
    let (y, m, d, _, _, _) = civil_utc(unix_now());
    format!("{y:04}-{m:02}-{d:02}")
}

pub fn tz_name() -> String {
    for var in ["LATTICE_TZ", "LTCAI_TZ"] {
        if let Ok(name) = std::env::var(var) {
            let name = name.trim();
            if !name.is_empty() {
                return name.to_string();
            }
        }
    }
    "local".into()
}

pub(crate) fn unix_now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn format_unix(secs: u64) -> String {
    let (y, m, d, hh, mm, ss) = civil_utc(secs);
    format!("{y:04}-{m:02}-{d:02}T{hh:02}:{mm:02}:{ss:02}+00:00")
}

fn civil_utc(secs: u64) -> (i32, u32, u32, u32, u32, u32) {
    let z = secs;
    let ss = (z % 60) as u32;
    let mm = ((z / 60) % 60) as u32;
    let hh = ((z / 3600) % 24) as u32;
    let mut days = (z / 86400) as i64;
    let mut year = 1970i32;
    loop {
        let diy = if is_leap(year) { 366 } else { 365 };
        if days < diy {
            break;
        }
        days -= diy;
        year += 1;
    }
    let mdays = [
        31,
        if is_leap(year) { 29 } else { 28 },
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ];
    let mut month = 1u32;
    for dim in mdays {
        if days < dim {
            break;
        }
        days -= dim;
        month += 1;
    }
    (year, month, (days as u32) + 1, hh, mm, ss)
}

fn is_leap(year: i32) -> bool {
    (year % 4 == 0 && year % 100 != 0) || year % 400 == 0
}

pub(crate) fn parse_timestamp_unix(value: &str) -> Option<u64> {
    let cleaned = value.replace('Z', "+00:00");
    let date = cleaned.get(0..10)?;
    let time = cleaned.get(11..19).unwrap_or("00:00:00");
    let y = date.get(0..4)?.parse::<i32>().ok()?;
    let m = date.get(5..7)?.parse::<u32>().ok()?;
    let d = date.get(8..10)?.parse::<u32>().ok()?;
    let hh = time.get(0..2)?.parse::<u32>().ok()?;
    let mm = time.get(3..5)?.parse::<u32>().ok()?;
    let ss = time.get(6..8)?.parse::<u32>().ok()?;
    Some(ymd_hms_to_unix(y, m, d, hh, mm, ss))
}

fn ymd_hms_to_unix(y: i32, m: u32, d: u32, hh: u32, mm: u32, ss: u32) -> u64 {
    let mut days: i64 = 0;
    for year in 1970..y {
        days += if is_leap(year) { 366 } else { 365 };
    }
    let mdays = [
        31,
        if is_leap(y) { 29 } else { 28 },
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ];
    for month in 1..m {
        days += mdays[(month - 1) as usize] as i64;
    }
    days += (d as i64) - 1;
    (days as u64) * 86400 + (hh as u64) * 3600 + (mm as u64) * 60 + ss as u64
}

fn peer_is_loopback(peer: &str) -> bool {
    matches!(peer, "127.0.0.1" | "::1" | "localhost") || peer.starts_with("127.")
}

pub(crate) fn external_origin(
    host: &str,
    scheme: &str,
    forwarded_host: Option<&str>,
    forwarded_proto: Option<&str>,
    peer: Option<&str>,
) -> Option<String> {
    let trust = peer.map(peer_is_loopback).unwrap_or(false);
    let authority = if trust {
        forwarded_host
            .and_then(|h| h.split(',').next())
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .unwrap_or(host)
    } else {
        host
    };
    if authority.is_empty() {
        return None;
    }
    let mut resolved = if scheme.is_empty() {
        "http".to_string()
    } else {
        scheme.to_string()
    };
    if trust {
        if let Some(claimed) = forwarded_proto
            .and_then(|h| h.split(',').next())
            .map(|s| s.trim().to_ascii_lowercase())
        {
            if claimed == "http" || claimed == "https" {
                resolved = claimed;
            }
        }
    }
    Some(format!("{resolved}://{authority}"))
}

fn pydantic_errors(problems: &[OrderedMap]) -> Response {
    let rendered: Vec<String> = problems
        .iter()
        .filter_map(|entry| serde_json::to_string(entry).ok())
        .collect();
    json_response(
        StatusCode::UNPROCESSABLE_ENTITY,
        &format!("{{\"detail\":[{}]}}", rendered.join(",")),
        None,
    )
}

fn problem_entry(
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
