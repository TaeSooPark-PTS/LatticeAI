//! Security & audit command center — native (v11.6.0, WP-R2).
//!
//! Port of `latticeai/api/security_dashboard.py`. Spreadsheet export is the
//! one branch that must not grow a Rust xlsx crate: it asks the compute seam
//! (`POST /worker/render/xlsx`) for the workbook's bytes via
//! [`WorkerSeamClient`] and writes the response itself. The document
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
    redact_secret_text, redact_structure, today_str, tz_name,
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

mod export;
mod handlers;

use export::security_export;
use handlers::{
    security_conversation_raw, security_conversation_summary, security_event_detail,
    security_events, security_file_content, security_file_detail, security_files,
    security_overview, security_raw, security_users,
};

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

pub(crate) fn require_admin(
    state: &SecurityState,
    headers: &HeaderMap,
) -> Result<(Identity, lattice_auth::Users), Response> {
    let identity = state.auth.require_admin(headers)?;
    Ok((identity, state.auth.users().load()))
}

pub(crate) fn events(state: &SecurityState) -> Vec<Value> {
    load_audit_log(&audit_log_path(&state.data_dir))
}

pub(crate) fn file_events(state: &SecurityState) -> Vec<Value> {
    events(state)
        .into_iter()
        .filter(|e| e.get("event_type").and_then(Value::as_str) == Some("document_upload"))
        .collect()
}

pub(crate) fn history(state: &SecurityState) -> Vec<Value> {
    load_chat_history(&state.data_dir)
}

pub(crate) fn log_view(
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

pub(crate) fn user_label(users: &lattice_auth::Users, email: Option<&str>) -> String {
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

pub(crate) fn summarize_user_risk(history: &[Value], files: &[Value]) -> Vec<Map<String, Value>> {
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

pub(crate) fn is_risky_file(fe: &Value) -> bool {
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
