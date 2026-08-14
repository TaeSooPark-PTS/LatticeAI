#![allow(
    dead_code,
    unused_imports,
    unused_variables,
    unused_assignments,
    unused_mut,
    private_interfaces,
    clippy::result_large_err,
    clippy::needless_lifetimes,
    clippy::too_many_arguments,
    clippy::type_complexity,
    clippy::collapsible_if,
    clippy::needless_as_bytes,
    clippy::redundant_closure,
    clippy::needless_return,
    clippy::manual_clamp,
    clippy::ptr_arg,
    clippy::unnecessary_sort_by,
    clippy::result_unit_err,
    clippy::useless_vec,
    clippy::uninlined_format_args,
    clippy::manual_contains,
    clippy::needless_borrows_for_generic_args,
    clippy::implicit_clone,
    clippy::unnecessary_map_or,
    clippy::match_like_matches_macro,
    clippy::manual_range_contains,
    clippy::derivable_impls,
    clippy::needless_pass_by_ref_mut,
    clippy::redundant_guards,
    clippy::map_identity,
    clippy::iter_overeager_cloned,
    clippy::explicit_auto_deref,
    clippy::bool_comparison,
    clippy::nonminimal_bool,
    clippy::if_same_then_else,
    clippy::question_mark,
    clippy::single_char_pattern,
    clippy::manual_pattern_char_comparison,
    clippy::manual_is_ascii_check,
    clippy::repeat_once,
    clippy::unused_self,
    clippy::module_inception
)]

use std::path::PathBuf;
use std::sync::Arc;

use axum::extract::{Path, Query, State};
use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_auth::{AuthState, Identity, OrderedMap};
use lattice_core::worker::WorkerSeamClient;
use serde_json::{json, Map, Value};

use crate::admin::{
    append_audit_event, audit_log_path, build_sensitivity_report, classify_sensitive_message,
    detail_status, json_ok, json_status, load_audit_log, load_chat_history, now_iso,
    redact_secret_text, redact_structure, today_str, tz_name,
};

use super::export::utf8_json;
use super::*;

pub(crate) async fn security_overview(
    State(state): State<SecurityState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    require_admin(&state, &headers)?;
    let history = history(&state);
    let evs = events(&state);
    let report = build_sensitivity_report(&history);
    let summary = report.get("summary").cloned().unwrap_or(json!({}));
    let sev = summary.get("severity_counts").cloned().unwrap_or(json!({}));
    let today = state.today();
    let today_events = evs
        .iter()
        .filter(|e| {
            e.get("timestamp")
                .map(|v| {
                    v.as_str()
                        .unwrap_or("")
                        .chars()
                        .take(10)
                        .collect::<String>()
                })
                .as_deref()
                == Some(today.as_str())
        })
        .count();
    let files = file_events(&state);
    let mut cards = OrderedMap::new();
    cards.insert("events_today", json!(today_events));
    cards.insert(
        "high_risk_events",
        json!(sev.get("high").and_then(Value::as_u64).unwrap_or(0)),
    );
    cards.insert(
        "risky_chats",
        json!(summary
            .get("risky_messages")
            .and_then(Value::as_u64)
            .unwrap_or(0)),
    );
    cards.insert(
        "risky_files",
        json!(files.iter().filter(|f| is_risky_file(f)).count()),
    );
    cards.insert(
        "secret_blocks",
        json!(evs
            .iter()
            .filter(|e| {
                let t = e.get("event_type").and_then(Value::as_str).unwrap_or("");
                t == "secret_block"
                    || t == "external_send_block"
                    || e.get("sensitive_labels")
                        .and_then(Value::as_array)
                        .map(|a| a.iter().any(|x| x.as_str() == Some("secret")))
                        .unwrap_or(false)
            })
            .count()),
    );
    cards.insert(
        "external_blocks",
        json!(evs
            .iter()
            .filter(|e| e.get("event_type").and_then(Value::as_str) == Some("external_send_block"))
            .count()),
    );
    cards.insert(
        "admin_raw_views",
        json!(evs
            .iter()
            .filter(
                |e| e.get("event_type").and_then(Value::as_str) == Some("admin_view_sensitive_raw")
            )
            .count()),
    );
    cards.insert(
        "review_required",
        json!(
            sev.get("high").and_then(Value::as_u64).unwrap_or(0)
                + sev.get("medium").and_then(Value::as_u64).unwrap_or(0)
        ),
    );
    let mut out = OrderedMap::new();
    out.insert("generated_at", json!(now_iso()));
    out.insert("timezone", json!(tz_name()));
    out.insert("cards", crate::admin::json_from_ordered(&cards));
    out.insert(
        "field_counts",
        summary.get("field_counts").cloned().unwrap_or(json!({})),
    );
    out.insert("severity_counts", sev);
    out.insert(
        "risk_rate",
        summary.get("risk_rate").cloned().unwrap_or(json!(0)),
    );
    Ok(json_ok(&out))
}

pub(crate) async fn security_users(
    State(state): State<SecurityState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    let (_, users) = require_admin(&state, &headers)?;
    let mut per_user = summarize_user_risk(&history(&state), &file_events(&state));
    for row in &mut per_user {
        let email = row
            .get("email")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let meta = users.get(&email);
        row.insert(
            "role".into(),
            json!(meta
                .and_then(|m| m.get("role"))
                .and_then(Value::as_str)
                .unwrap_or("user")),
        );
        row.insert(
            "disabled".into(),
            json!(meta
                .and_then(|m| m.get("disabled"))
                .map(|v| v.as_bool().unwrap_or(!v.is_null()))
                .unwrap_or(false)),
        );
        row.insert("user".into(), json!(user_label(&users, Some(&email))));
    }
    let mut out = OrderedMap::new();
    out.insert("users", json!(per_user));
    out.insert("total", json!(per_user.len()));
    Ok(json_ok(&out))
}

pub(crate) async fn security_events(
    State(state): State<SecurityState>,
    headers: HeaderMap,
    Query(query): Query<std::collections::HashMap<String, String>>,
) -> Result<Response, Response> {
    require_admin(&state, &headers)?;
    let user = query.get("user").cloned();
    let typ = query.get("type").cloned();
    let severity = query.get("severity").cloned();
    let date_from = query.get("from").cloned();
    let date_to = query.get("to").cloned();
    let limit = match query.get("limit") {
        None => 200usize,
        Some(raw) => match raw.parse::<usize>() {
            Ok(n) if (1..=2000).contains(&n) => n,
            _ => {
                let mut body = OrderedMap::new();
                body.insert(
                    "detail",
                    json!([{
                        "type": "less_than_equal",
                        "loc": ["query", "limit"],
                        "msg": "Input should be less than or equal to 2000",
                        "input": raw,
                        "ctx": {"le": 2000}
                    }]),
                );
                return Err(json_status(StatusCode::UNPROCESSABLE_ENTITY, &body));
            }
        },
    };
    let evs = events(&state);
    let mut out: Vec<Value> = Vec::new();
    for (idx, e) in evs.iter().enumerate() {
        let ts = e.get("timestamp").and_then(Value::as_str).unwrap_or("");
        if let Some(ref u) = user {
            if e.get("user_email").and_then(Value::as_str) != Some(u.as_str())
                && e.get("user_nickname").and_then(Value::as_str) != Some(u.as_str())
            {
                continue;
            }
        }
        if let Some(ref t) = typ {
            if e.get("event_type").and_then(Value::as_str) != Some(t.as_str()) {
                continue;
            }
        }
        if let Some(ref s) = severity {
            if e.get("sensitivity")
                .and_then(Value::as_str)
                .unwrap_or("none")
                != s
            {
                continue;
            }
        }
        if let Some(ref f) = date_from {
            if ts < f.as_str() {
                continue;
            }
        }
        if let Some(ref t) = date_to {
            if ts > t.as_str() {
                continue;
            }
        }
        let mut mc = e.as_object().cloned().unwrap_or_default();
        mc.insert(
            "event_id".into(),
            json!(e
                .get("event_id")
                .and_then(Value::as_str)
                .map(str::to_string)
                .unwrap_or_else(|| idx.to_string())),
        );
        if mc.contains_key("content_preview") {
            mc.insert(
                "content_preview".into(),
                json!(redact_secret_text(
                    &mc.get("content_preview")
                        .map(|v| v.as_str().unwrap_or(&v.to_string()).to_string())
                        .unwrap_or_default()
                )),
            );
        }
        out.push(Value::Object(mc));
    }
    out.sort_by(|a, b| {
        let ta = a.get("timestamp").and_then(Value::as_str).unwrap_or("");
        let tb = b.get("timestamp").and_then(Value::as_str).unwrap_or("");
        tb.cmp(ta)
    });
    let total = out.len();
    out.truncate(limit);
    let mut body = OrderedMap::new();
    body.insert("events", json!(out));
    body.insert("total", json!(total));
    Ok(json_ok(&body))
}

fn find_event<'a>(events: &'a [Value], event_id: &str) -> Option<&'a Value> {
    if let Ok(idx) = event_id.parse::<usize>() {
        if idx < events.len() {
            return Some(&events[idx]);
        }
    }
    events
        .iter()
        .find(|e| e.get("event_id").and_then(Value::as_str) == Some(event_id))
}

pub(crate) async fn security_event_detail(
    State(state): State<SecurityState>,
    Path(event_id): Path<String>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    let (admin, _) = require_admin(&state, &headers)?;
    let evs = events(&state);
    let Some(target) = find_event(&evs, &event_id) else {
        return Err(detail_status(
            StatusCode::NOT_FOUND,
            "이벤트를 찾을 수 없습니다.",
        ));
    };
    log_view(&state, &admin.email, "event", &event_id, "security_review");
    let mut masked = target.as_object().cloned().unwrap_or_default();
    if masked.contains_key("content_preview") {
        masked.insert(
            "content_preview".into(),
            json!(redact_secret_text(
                &masked
                    .get("content_preview")
                    .map(|v| v.as_str().unwrap_or(&v.to_string()).to_string())
                    .unwrap_or_default()
            )),
        );
    }
    let mut out = OrderedMap::new();
    out.insert("event", Value::Object(masked));
    out.insert("raw_available", json!(true));
    Ok(json_ok(&out))
}

pub(crate) async fn security_conversation_summary(
    State(state): State<SecurityState>,
    Path(conversation_id): Path<String>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    require_admin(&state, &headers)?;
    let items: Vec<Value> = history(&state)
        .iter()
        .filter(|h| {
            h.get("conversation_id").and_then(Value::as_str) == Some(conversation_id.as_str())
        })
        .enumerate()
        .map(|(i, h)| crate::admin::json_from_ordered(&classify_sensitive_message(h, i)))
        .collect();
    let risky = items
        .iter()
        .filter(|it| {
            it.get("sensitivity")
                .and_then(Value::as_str)
                .unwrap_or("none")
                != "none"
        })
        .count();
    let mut out = OrderedMap::new();
    out.insert("conversation_id", json!(conversation_id));
    out.insert("messages_total", json!(items.len()));
    out.insert("risky_messages", json!(risky));
    out.insert("items", json!(items));
    Ok(json_ok(&out))
}

pub(crate) async fn security_conversation_raw(
    State(state): State<SecurityState>,
    Path(conversation_id): Path<String>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    let (admin, _) = require_admin(&state, &headers)?;
    let hist: Vec<Value> = history(&state)
        .into_iter()
        .filter(|h| {
            h.get("conversation_id").and_then(Value::as_str) == Some(conversation_id.as_str())
        })
        .collect();
    if hist.is_empty() {
        return Err(detail_status(
            StatusCode::NOT_FOUND,
            "대화를 찾을 수 없습니다.",
        ));
    }
    log_view(
        &state,
        &admin.email,
        "conversation",
        &conversation_id,
        "security_review",
    );
    let masked: Vec<Value> = hist
        .into_iter()
        .map(|h| {
            let mut cleaned = h.as_object().cloned().unwrap_or_default();
            if cleaned.contains_key("content") {
                cleaned.insert(
                    "content".into(),
                    json!(redact_secret_text(
                        &cleaned
                            .get("content")
                            .map(|v| v.as_str().unwrap_or(&v.to_string()).to_string())
                            .unwrap_or_default()
                    )),
                );
            }
            Value::Object(cleaned)
        })
        .collect();
    let mut out = OrderedMap::new();
    out.insert("conversation_id", json!(conversation_id));
    out.insert("messages", json!(masked));
    Ok(json_ok(&out))
}

fn file_id_of(f: &Value) -> String {
    f.get("file_id")
        .and_then(Value::as_str)
        .or_else(|| f.get("filename").and_then(Value::as_str))
        .unwrap_or("")
        .to_string()
}

pub(crate) async fn security_files(
    State(state): State<SecurityState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    require_admin(&state, &headers)?;
    let files: Vec<Value> = file_events(&state)
        .iter()
        .map(|file| {
            let mut value = redact_structure(file);
            if let Some(object) = value.as_object_mut() {
                if !object.contains_key("uploaded_at") {
                    if let Some(stamp) = object.get("timestamp").cloned() {
                        object.insert("uploaded_at".into(), stamp);
                    }
                }
            }
            value
        })
        .collect();
    let mut out = OrderedMap::new();
    out.insert("files", json!(files));
    out.insert("total", json!(files.len()));
    Ok(json_ok(&out))
}

pub(crate) async fn security_file_detail(
    State(state): State<SecurityState>,
    Path(file_id): Path<String>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    let (admin, _) = require_admin(&state, &headers)?;
    let files = file_events(&state);
    let Some(target) = files.iter().find(|f| file_id_of(f) == file_id) else {
        return Err(detail_status(
            StatusCode::NOT_FOUND,
            "파일을 찾을 수 없습니다.",
        ));
    };
    log_view(&state, &admin.email, "file", &file_id, "security_review");
    let mut cleaned = match redact_structure(target) {
        Value::Object(m) => m,
        other => {
            let mut m = Map::new();
            m.insert("file".into(), other);
            m
        }
    };
    if !cleaned.contains_key("uploaded_at") {
        if let Some(stamp) = cleaned.get("timestamp").cloned() {
            cleaned.insert("uploaded_at".into(), stamp);
        }
    }
    if cleaned.contains_key("content_preview") {
        cleaned.insert(
            "content_preview".into(),
            json!(redact_secret_text(
                &target
                    .get("content_preview")
                    .map(|v| v.as_str().unwrap_or(&v.to_string()).to_string())
                    .unwrap_or_default()
            )),
        );
    }
    let mut out = OrderedMap::new();
    out.insert("file", Value::Object(cleaned));
    Ok(json_ok(&out))
}

pub(crate) async fn security_file_content(
    State(state): State<SecurityState>,
    Path(file_id): Path<String>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    let (admin, _) = require_admin(&state, &headers)?;
    let files = file_events(&state);
    let Some(target) = files.iter().find(|f| file_id_of(f) == file_id) else {
        return Err(detail_status(
            StatusCode::NOT_FOUND,
            "파일을 찾을 수 없습니다.",
        ));
    };
    log_view(
        &state,
        &admin.email,
        "file_content",
        &file_id,
        "raw_content_review",
    );
    let text = target
        .get("extracted_text")
        .or_else(|| target.get("content_preview"))
        .map(|v| v.as_str().unwrap_or(&v.to_string()).to_string())
        .unwrap_or_default();
    let mut out = OrderedMap::new();
    out.insert("file_id", json!(file_id));
    out.insert("text", json!(redact_secret_text(&text)));
    Ok(json_ok(&out))
}

pub(crate) async fn security_raw(
    State(state): State<SecurityState>,
    headers: HeaderMap,
    Query(query): Query<std::collections::HashMap<String, String>>,
) -> Result<Response, Response> {
    let (admin, _) = require_admin(&state, &headers)?;
    let scope = query
        .get("scope")
        .cloned()
        .unwrap_or_else(|| "audit".into());
    log_view(&state, &admin.email, "raw", &scope, "raw_explorer");
    let payload = match scope.as_str() {
        "audit" => Value::Array(events(&state)),
        "history" => Value::Array(history(&state)),
        "files" => Value::Array(file_events(&state)),
        _ => {
            return Err(detail_status(
                StatusCode::BAD_REQUEST,
                "지원하지 않는 scope입니다.",
            ))
        }
    };
    let text = serde_json::to_string(&redact_structure(&payload)).unwrap_or_else(|_| "[]".into());
    Ok(utf8_json(StatusCode::OK, text))
}
