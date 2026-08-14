//! Security & audit command center — native (v11.6.0, WP-R2).
//!
//! Port of `latticeai/api/security_dashboard.py`. Spreadsheet export is the
//! one branch that must not grow a Rust xlsx crate: it delegates to the
//! worker's `POST /tools/create_xlsx` via [`WorkerSeamClient`]. The document
//! parser/generator matrix stays in Python.


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
    redact_secret_text,
    redact_structure, today_str, tz_name,
};

pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/admin/security/conversations/:conversation_id"),
    ("GET", "/admin/security/conversations/:conversation_id/raw"),
    ("GET", "/admin/security/events"),
    ("GET", "/admin/security/events/:event_id"),
    ("POST", "/admin/security/export"),
    ("GET", "/admin/security/files"),
    ("GET", "/admin/security/files/:file_id"),
    ("GET", "/admin/security/files/:file_id/content"),
    ("GET", "/admin/security/overview"),
    ("GET", "/admin/security/raw"),
    ("GET", "/admin/security/users"),
];

const CREATE_XLSX: &str = "/tools/create_xlsx";

#[derive(Clone)]
pub struct SecurityState {
    pub auth: Arc<AuthState>,
    pub data_dir: PathBuf,
    /// Optional worker used only for `format=excel|xlsx` export.
    pub worker: Option<WorkerSeamClient>,
    /// Override "today" (`YYYY-MM-DD`) so fixture replay can freeze the clock.
    pub today: Option<String>,
}

impl SecurityState {
    pub fn new(auth: Arc<AuthState>, data_dir: impl Into<PathBuf>) -> Self {
        Self {
            auth,
            data_dir: data_dir.into(),
            worker: None,
            today: None,
        }
    }

    fn today(&self) -> String {
        self.today.clone().unwrap_or_else(today_str)
    }
}

impl axum::extract::FromRef<SecurityState> for Arc<AuthState> {
    fn from_ref(s: &SecurityState) -> Self {
        Arc::clone(&s.auth)
    }
}

pub fn router(state: SecurityState) -> Router {
    Router::new()
        .route("/admin/security/overview", get(security_overview))
        .route("/admin/security/users", get(security_users))
        .route("/admin/security/events", get(security_events))
        .route(
            "/admin/security/events/:event_id",
            get(security_event_detail),
        )
        .route(
            "/admin/security/conversations/:conversation_id",
            get(security_conversation_summary),
        )
        .route(
            "/admin/security/conversations/:conversation_id/raw",
            get(security_conversation_raw),
        )
        .route("/admin/security/files", get(security_files))
        .route("/admin/security/files/:file_id", get(security_file_detail))
        .route(
            "/admin/security/files/:file_id/content",
            get(security_file_content),
        )
        .route("/admin/security/raw", get(security_raw))
        .route("/admin/security/export", post(security_export))
        .with_state(state)
}

fn require_admin(
    state: &SecurityState,
    headers: &HeaderMap,
) -> Result<(Identity, lattice_auth::Users), Response> {
    let identity = state.auth.require_admin(headers)?;
    Ok((identity, state.auth.users().load()))
}

fn events(state: &SecurityState) -> Vec<Value> {
    load_audit_log(&audit_log_path(&state.data_dir))
}

fn file_events(state: &SecurityState) -> Vec<Value> {
    events(state)
        .into_iter()
        .filter(|e| e.get("event_type").and_then(Value::as_str) == Some("document_upload"))
        .collect()
}

fn history(state: &SecurityState) -> Vec<Value> {
    load_chat_history(&state.data_dir)
}

fn log_view(
    state: &SecurityState,
    admin_email: &str,
    target_type: &str,
    target_id: &str,
    reason: &str,
) {
    let mut payload = Map::new();
    payload.insert("admin_email".into(), json!(admin_email));
    payload.insert("target_type".into(), json!(target_type));
    payload.insert("target_id".into(), json!(target_id));
    payload.insert("reason".into(), json!(reason));
    append_audit_event(
        &audit_log_path(&state.data_dir),
        "admin_view_sensitive_raw",
        payload,
    );
}

fn user_label(users: &lattice_auth::Users, email: Option<&str>) -> String {
    let Some(email) = email.filter(|s| !s.is_empty()) else {
        return "Unknown".into();
    };
    if let Some(u) = users.get(email) {
        if let Some(n) = u
            .get("nickname")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
        {
            return n.to_string();
        }
        if let Some(n) = u
            .get("name")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
        {
            return n.to_string();
        }
    }
    email.to_string()
}

fn summarize_user_risk(history: &[Value], files: &[Value]) -> Vec<Map<String, Value>> {
    let mut buckets: std::collections::BTreeMap<String, Map<String, Value>> =
        std::collections::BTreeMap::new();
    for (idx, item) in history.iter().enumerate() {
        if item.get("role").and_then(Value::as_str) != Some("user") {
            continue;
        }
        let email = item
            .get("user_email")
            .and_then(Value::as_str)
            .or_else(|| item.get("user_nickname").and_then(Value::as_str))
            .unwrap_or("Unknown")
            .to_string();
        let nickname = item
            .get("user_nickname")
            .and_then(Value::as_str)
            .unwrap_or(email.as_str())
            .to_string();
        let bucket = buckets.entry(email).or_insert_with(|| empty_bucket());
        bucket.insert("user".into(), json!(nickname));
        bump(bucket, "total_chats");
        let cls = classify_sensitive_message(item, idx);
        let sensitivity = cls
            .get("sensitivity")
            .and_then(Value::as_str)
            .unwrap_or("none");
        if sensitivity != "none" {
            bump(bucket, "risky_chats");
            if sensitivity == "high" {
                bump(bucket, "high_risk_events");
            }
        } else {
            bump(bucket, "compliant_chats");
        }
        if let Some(ts) = item.get("timestamp").and_then(Value::as_str) {
            let last = bucket
                .get("last_activity_at")
                .and_then(Value::as_str)
                .unwrap_or("");
            if last.is_empty() || ts > last {
                bucket.insert("last_activity_at".into(), json!(ts));
            }
        }
    }
    for fe in files {
        let email = fe
            .get("user_email")
            .and_then(Value::as_str)
            .unwrap_or("Unknown")
            .to_string();
        let bucket = buckets.entry(email).or_insert_with(empty_bucket);
        bump(bucket, "uploaded_files");
        let sensitivity = fe
            .get("sensitivity")
            .and_then(Value::as_str)
            .unwrap_or("none");
        let labels = fe
            .get("sensitive_labels")
            .and_then(Value::as_array)
            .map(|a| !a.is_empty())
            .unwrap_or(false);
        if sensitivity != "none" || labels {
            bump(bucket, "risky_files");
            if sensitivity == "high" {
                bump(bucket, "high_risk_events");
            }
        } else {
            bump(bucket, "compliant_files");
        }
    }
    let mut out: Vec<Map<String, Value>> = buckets
        .into_iter()
        .map(|(email, mut b)| {
            let total = as_u64(&b, "total_chats") + as_u64(&b, "uploaded_files");
            let risk = as_u64(&b, "risky_chats") + as_u64(&b, "risky_files");
            b.insert("email".into(), json!(email));
            let rate = if total == 0 {
                0.0
            } else {
                ((risk as f64) / (total as f64) * 1000.0).round() / 10.0
            };
            b.insert("risk_rate".into(), json!(rate));
            b
        })
        .collect();
    out.sort_by(|a, b| {
        let ah = as_u64(a, "high_risk_events");
        let bh = as_u64(b, "high_risk_events");
        let ar = as_u64(a, "risky_chats") + as_u64(a, "risky_files");
        let br = as_u64(b, "risky_chats") + as_u64(b, "risky_files");
        bh.cmp(&ah).then(br.cmp(&ar))
    });
    out
}

fn empty_bucket() -> Map<String, Value> {
    let mut b = Map::new();
    b.insert("user".into(), json!("Unknown"));
    b.insert("total_chats".into(), json!(0));
    b.insert("compliant_chats".into(), json!(0));
    b.insert("risky_chats".into(), json!(0));
    b.insert("uploaded_files".into(), json!(0));
    b.insert("compliant_files".into(), json!(0));
    b.insert("risky_files".into(), json!(0));
    b.insert("high_risk_events".into(), json!(0));
    b.insert("last_activity_at".into(), Value::Null);
    b
}

fn bump(map: &mut Map<String, Value>, key: &str) {
    let next = as_u64(map, key) + 1;
    map.insert(key.into(), json!(next));
}

fn as_u64(map: &Map<String, Value>, key: &str) -> u64 {
    map.get(key).and_then(Value::as_u64).unwrap_or(0)
}

fn is_risky_file(fe: &Value) -> bool {
    let sensitivity = fe
        .get("sensitivity")
        .and_then(Value::as_str)
        .unwrap_or("none");
    let labels = fe
        .get("sensitive_labels")
        .and_then(Value::as_array)
        .map(|a| !a.is_empty())
        .unwrap_or(false);
    sensitivity != "none" || labels
}

async fn security_overview(
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

async fn security_users(
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

async fn security_events(
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

async fn security_event_detail(
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

async fn security_conversation_summary(
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

async fn security_conversation_raw(
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

async fn security_files(
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

async fn security_file_detail(
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

async fn security_file_content(
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

async fn security_raw(
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

async fn security_export(
    State(state): State<SecurityState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Response, Response> {
    let (admin, _) = require_admin(&state, &headers)?;
    let parsed = if body.is_empty() {
        Map::new()
    } else {
        match serde_json::from_slice::<Value>(&body) {
            Ok(Value::Object(m)) => m,
            _ => Map::new(),
        }
    };
    let scope = parsed
        .get("scope")
        .and_then(Value::as_str)
        .unwrap_or("events")
        .to_ascii_lowercase();
    let fmt = parsed
        .get("format")
        .and_then(Value::as_str)
        .unwrap_or("json")
        .to_ascii_lowercase();
    let rows = match scope.as_str() {
        "events" => events(&state),
        "users" => summarize_user_risk(&history(&state), &file_events(&state))
            .into_iter()
            .map(Value::Object)
            .collect(),
        "files" => file_events(&state),
        "overview" => {
            let report = build_sensitivity_report(&history(&state));
            vec![report.get("summary").cloned().unwrap_or(json!({}))]
        }
        _ => {
            return Err(detail_status(
                StatusCode::BAD_REQUEST,
                "지원하지 않는 scope입니다.",
            ))
        }
    };
    log_view(
        &state,
        &admin.email,
        "export",
        &format!("{scope}:{fmt}"),
        "export",
    );

    match fmt.as_str() {
        "json" => {
            let body = serde_json::to_string_pretty(&redact_structure(&json!(rows)))
                .unwrap_or_else(|_| "[]".into());
            Ok(attachment(
                StatusCode::OK,
                "application/json; charset=utf-8",
                &format!("security_{scope}.json"),
                body.into_bytes(),
            ))
        }
        "csv" => {
            let bytes = csv_dump(&rows);
            Ok(attachment(
                StatusCode::OK,
                "text/csv; charset=utf-8",
                &format!("security_{scope}.csv"),
                bytes,
            ))
        }
        "excel" | "xlsx" => {
            if rows_have_non_scalar(&rows) {
                // Python's openpyxl raises on list-valued cells → Starlette 500.
                return Ok(plain_500());
            }
            let bytes = excel_via_worker(&state, &rows).await?;
            Ok(attachment(
                StatusCode::OK,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                &format!("security_{scope}.xlsx"),
                bytes,
            ))
        }
        "pdf" => {
            let bytes = minimal_pdf("Lattice AI Security Report", &rows);
            Ok(attachment(
                StatusCode::OK,
                "application/pdf",
                &format!("security_{scope}.pdf"),
                bytes,
            ))
        }
        "txt" => {
            let lines: Vec<String> = rows
                .iter()
                .map(|r| {
                    serde_json::to_string(&redact_structure(r)).unwrap_or_else(|_| "{}".into())
                })
                .collect();
            Ok(attachment(
                StatusCode::OK,
                "text/plain; charset=utf-8",
                &format!("security_{scope}.txt"),
                lines.join("\n").into_bytes(),
            ))
        }
        _ => Err(detail_status(
            StatusCode::BAD_REQUEST,
            "지원하지 않는 포맷입니다.",
        )),
    }
}

fn rows_have_non_scalar(rows: &[Value]) -> bool {
    rows.iter().any(|r| {
        r.as_object()
            .map(|o| {
                o.values()
                    .any(|v| matches!(v, Value::Array(_) | Value::Object(_)))
            })
            .unwrap_or(false)
    })
}

async fn excel_via_worker(state: &SecurityState, rows: &[Value]) -> Result<Vec<u8>, Response> {
    let Some(worker) = state.worker.as_ref() else {
        return Err(detail_status(
            StatusCode::BAD_GATEWAY,
            "xlsx export requires the document worker (POST /tools/create_xlsx).",
        ));
    };
    let headers = sheet_headers(rows);
    let mut table: Vec<Vec<Value>> = Vec::new();
    table.push(headers.iter().map(|h| json!(h)).collect());
    for r in rows {
        let obj = r.as_object();
        table.push(
            headers
                .iter()
                .map(|h| {
                    let v = obj.and_then(|o| o.get(h)).cloned().unwrap_or(Value::Null);
                    match v {
                        Value::String(s) => json!(redact_secret_text(&s)),
                        other => other,
                    }
                })
                .collect(),
        );
    }
    let mut body = OrderedMap::new();
    body.insert("rows", json!(table));
    body.insert("filename", json!("security_export.xlsx"));
    body.insert("sheet_name", json!("security_export"));
    let payload = serde_json::to_value(&body).unwrap_or(json!({}));
    let reply = worker.post_json(CREATE_XLSX, &payload).await.map_err(|e| {
        detail_status(
            StatusCode::from_u16(e.status().unwrap_or(502)).unwrap_or(StatusCode::BAD_GATEWAY),
            &e.to_string(),
        )
    })?;
    if let Some(path) = reply.get("path").and_then(Value::as_str) {
        if let Ok(bytes) = std::fs::read(path) {
            return Ok(bytes);
        }
        // Relative path — try as-is from cwd, then from data_dir.
        let via_data = state.data_dir.join(path);
        if let Ok(bytes) = std::fs::read(&via_data) {
            return Ok(bytes);
        }
    }
    if let Some(b64) = reply.get("bytes_b64").and_then(Value::as_str) {
        if let Ok(bytes) = base64::Engine::decode(&base64::engine::general_purpose::STANDARD, b64) {
            return Ok(bytes);
        }
    }
    Err(detail_status(
        StatusCode::BAD_GATEWAY,
        "worker create_xlsx did not return a readable spreadsheet",
    ))
}

fn sheet_headers(rows: &[Value]) -> Vec<String> {
    let mut headers = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for r in rows {
        if let Some(obj) = r.as_object() {
            for k in obj.keys() {
                if seen.insert(k.clone()) {
                    headers.push(k.clone());
                }
            }
        }
    }
    headers
}

fn csv_dump(rows: &[Value]) -> Vec<u8> {
    if rows.is_empty() {
        return Vec::new();
    }
    let headers = sheet_headers(rows);
    let mut out = String::new();
    out.push_str(&headers.join(","));
    out.push_str("\r\n");
    for r in rows {
        let obj = r.as_object();
        let cells: Vec<String> = headers
            .iter()
            .map(|h| {
                let v = obj.and_then(|o| o.get(h)).cloned().unwrap_or(Value::Null);
                let sanitized = redact_structure(&v);
                csv_escape(&value_cell(&sanitized))
            })
            .collect();
        out.push_str(&cells.join(","));
        out.push_str("\r\n");
    }
    out.into_bytes()
}

fn value_cell(v: &Value) -> String {
    match v {
        Value::Null => String::new(),
        Value::String(s) => s.clone(),
        Value::Bool(b) => {
            if *b {
                "True".into()
            } else {
                "False".into()
            }
        }
        Value::Number(n) => n.to_string(),
        // csv.DictWriter uses Python `str()`, not JSON (`['secret']` not `["secret"]`).
        Value::Array(items) => format!(
            "[{}]",
            items
                .iter()
                .map(|item| match item {
                    Value::String(s) => format!("'{s}'"),
                    other => value_cell(other),
                })
                .collect::<Vec<_>>()
                .join(", ")
        ),
        Value::Object(map) => format!(
            "{{{}}}",
            map.iter()
                .map(|(k, val)| format!("'{k}': {}", value_cell(val)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
    }
}

fn csv_escape(s: &str) -> String {
    if s.contains(',') || s.contains('"') || s.contains('\n') || s.contains('\r') {
        format!("\"{}\"", s.replace('"', "\"\""))
    } else {
        s.to_string()
    }
}

fn minimal_pdf(title: &str, rows: &[Value]) -> Vec<u8> {
    let generated = now_iso();
    let mut text = format!("{title}\nGenerated: {generated}\n\n");
    for row in rows.iter().take(30) {
        text.push_str(&serde_json::to_string(&redact_structure(row)).unwrap_or_default());
        text.push('\n');
    }
    // Minimal valid PDF so the fixture's `%PDF` magic matches.
    let stream = format!("BT /F1 12 Tf 72 720 Td ({title}) Tj ET\n");
    let escaped = stream.replace('(', "\\(").replace(')', "\\)");
    format!(
        "%PDF-1.1\n\
         1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n\
         2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n\
         3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R>>endobj\n\
         4 0 obj<</Length {}>>stream\n{escaped}\nendstream endobj\n\
         xref\n0 5\n0000000000 65535 f \ntrailer<</Size 5/Root 1 0 R>>\nstartxref\n0\n%%EOF\n",
        escaped.len()
    )
    .into_bytes()
}

fn utf8_json(status: StatusCode, body: String) -> Response {
    json_response_typed(
        status,
        "application/json; charset=utf-8",
        body.into_bytes(),
        None,
    )
}

fn attachment(status: StatusCode, content_type: &str, filename: &str, body: Vec<u8>) -> Response {
    json_response_typed(
        status,
        content_type,
        body,
        Some((
            header::CONTENT_DISPOSITION,
            HeaderValue::from_str(&format!("attachment; filename={filename}"))
                .unwrap_or_else(|_| HeaderValue::from_static("attachment")),
        )),
    )
}

fn plain_500() -> Response {
    json_response_typed(
        StatusCode::INTERNAL_SERVER_ERROR,
        "text/plain; charset=utf-8",
        b"Internal Server Error".to_vec(),
        None,
    )
}

fn json_response_typed(
    status: StatusCode,
    content_type: &str,
    body: Vec<u8>,
    extra: Option<(header::HeaderName, HeaderValue)>,
) -> Response {
    let mut builder = Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, content_type);
    if let Some((name, value)) = extra {
        builder = builder.header(name, value);
    }
    builder
        .body(axum::body::Body::from(body))
        .unwrap_or_else(|_| Response::new(axum::body::Body::empty()))
}
