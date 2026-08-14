//! Admin console family — native (v11.6.0, WP-R2).
//!
//! Port of `latticeai/api/admin.py` plus the audit-log surface that file
//! already owned (`latticeai/core/audit.py`). Other families (security
//! dashboard, feature toggles) call [`append_audit_event`] / [`load_audit_log`]
//! rather than growing a second writer.
//!
//! Storage is the same files Python uses, resolved through
//! `lattice_core::db::tables::state_files` and written atomically.


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
    clippy::useless_format,
    clippy::collapsible_str_replace,
    clippy::manual_repeat_n,
    clippy::module_inception
)]
use std::collections::BTreeMap;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use axum::extract::{ConnectInfo, Path as AxumPath, Query, State};
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::Response;
use axum::routing::{get, patch};
use axum::Router;
use fancy_regex::Regex;
use lattice_auth::policy::capabilities_for_role;
use lattice_auth::response::json_response;
use lattice_auth::{AuthState, Identity, OrderedMap};
use lattice_core::db::tables::state_files;
use lattice_core::messages::{self, LANGUAGE_HEADER};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

/// Mounted (method, path) pairs — axum 0.7 spelling. Greedy `{email:path}`
/// is `/*email`.
pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/admin/audit"),
    ("GET", "/admin/enterprise"),
    ("GET", "/admin/enterprise/siem-export"),
    ("GET", "/admin/health-summary"),
    ("GET", "/admin/invite-link"),
    ("GET", "/admin/log-retention"),
    ("GET", "/admin/policies"),
    ("GET", "/admin/product-hardening"),
    ("GET", "/admin/roles"),
    ("GET", "/admin/sso"),
    ("PATCH", "/admin/sso"),
    ("GET", "/admin/stats"),
    ("GET", "/admin/summary"),
    ("GET", "/admin/users"),
    ("DELETE", "/admin/users/*email"),
    ("PATCH", "/admin/users/*email"),
    ("PATCH", "/admin/vpc"),
    ("GET", "/admin/sensitivity"),
    ("GET", "/vpc/status"),
];

/// Community edition notice, verbatim from `enterprise_admin.COMMUNITY_NOTICE`.
pub const COMMUNITY_NOTICE: &str =
    "Community edition: this is an Enterprise extension point and is not \
enforced. Local-first behaviour is always available. See \
docs/ENTERPRISE.md and docs/EDITION_STRATEGY.md.";

const AUDIT_CAP: usize = 5000;
const DEFAULT_WORKSPACE_ID: &str = "personal";
const SSO_CALLBACK_PATH: &str = "/auth/sso/callback";

const AUDIT_PUBLIC_KEYS: &[&str] = &[
    "event_id",
    "contract",
    "event_type",
    "timestamp",
    "role",
    "user_email",
    "user_nickname",
    "source",
    "conversation_id",
    "workspace_id",
    "command",
    "scope",
    "target_email",
    "filename",
    "mime_type",
    "ext",
    "bytes",
    "extracted_chars",
    "graph_node",
    "keep_last",
    "removed",
    "kept",
    "started_at",
    "sensitivity",
    "sensitive_labels",
    "content_preview",
    "content_chars",
];

const AUDIT_DELETE_EVENTS: &[&str] = &["conversation_delete", "history_delete", "user_delete"];

// ── public audit API ─────────────────────────────────────────────────────────

/// Load `<data_dir>/audit_log.json`. Missing or corrupt → empty list.
pub fn load_audit_log(path: &Path) -> Vec<Value> {
    let Ok(text) = std::fs::read_to_string(path) else {
        return Vec::new();
    };
    match serde_json::from_str::<Value>(&text) {
        Ok(Value::Array(items)) => items,
        _ => Vec::new(),
    }
}

/// Append one event to `audit_log.json` (atomic replace, last 5000 kept).
///
/// `payload` is redacted first (`redact_secrets`). The event is:
/// `{event_id, event_type, timestamp, **payload, contract}`. Failures are
/// swallowed — an audit write must never take the product down.
pub fn append_audit_event(path: &Path, event_type: &str, payload: Map<String, Value>) {
    let safe = match redact_secrets(&Value::Object(payload)) {
        Value::Object(map) => map,
        other => {
            let mut map = Map::new();
            map.insert("payload".into(), other);
            map
        }
    };
    let timestamp = now_iso();
    let hash_src = json!([event_type, timestamp, Value::Object(safe.clone())]);
    let Ok(canonical) = serde_json::to_string(&sorted_json(&hash_src)) else {
        return;
    };
    let digest = Sha256::digest(canonical.as_bytes());
    let event_hash: String = digest.iter().take(12).map(|b| format!("{b:02x}")).collect();
    let event_id = format!("audit-{event_hash}");

    let mut event = Map::new();
    event.insert("event_id".into(), json!(event_id));
    event.insert("event_type".into(), json!(event_type));
    event.insert("timestamp".into(), json!(timestamp));
    for (key, value) in safe {
        event.entry(key).or_insert(value);
    }
    let contract = audit_event_contract(&event);
    event.insert("contract".into(), contract);

    let mut events = load_audit_log(path);
    events.push(Value::Object(event));
    if events.len() > AUDIT_CAP {
        events = events.split_off(events.len() - AUDIT_CAP);
    }
    let Ok(text) = lattice_auth::pyjson::dumps_indent2(&events) else {
        return;
    };
    lattice_auth::atomic::write_text(path, &text);
}

/// Convenience: resolve the audit file from a data dir via the I1 constant.
pub fn audit_log_path(data_dir: &Path) -> PathBuf {
    data_dir.join(state_files::AUDIT_LOG)
}

/// `classify_sensitive_message`.
pub fn classify_sensitive_message(item: &Value, index: usize) -> OrderedMap {
    let content = item.get("content").map(value_as_string).unwrap_or_default();
    let found = find_sensitive(&content);
    let severity = if found.is_empty() {
        "none".to_string()
    } else {
        found
            .iter()
            .max_by_key(|m| severity_score(m.get("severity").and_then(Value::as_str).unwrap_or("")))
            .and_then(|m| m.get("severity").and_then(Value::as_str))
            .unwrap_or("none")
            .to_string()
    };
    let preview_text: String = content.chars().take(240).collect();
    // Python `len(preview_text)` is characters, not UTF-8 bytes.
    let preview_end = preview_text.chars().count();
    let preview_matches: Vec<&Map<String, Value>> = found
        .iter()
        .filter(|m| m.get("start").and_then(Value::as_u64).unwrap_or(0) < preview_end as u64)
        .collect();
    let labels = {
        let mut set: Vec<String> = found
            .iter()
            .filter_map(|m| m.get("label").and_then(Value::as_str).map(str::to_string))
            .collect();
        set.sort();
        set.dedup();
        set
    };

    let mut out = OrderedMap::new();
    out.insert("index", json!(index));
    out.insert(
        "role",
        json!(item.get("role").and_then(Value::as_str).unwrap_or("")),
    );
    out.insert(
        "user_email",
        item.get("user_email").cloned().unwrap_or(Value::Null),
    );
    let nickname = item
        .get("user_nickname")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .or_else(|| item.get("user_email").and_then(Value::as_str))
        .unwrap_or("Unknown");
    out.insert("user_nickname", json!(nickname));
    out.insert(
        "timestamp",
        item.get("timestamp").cloned().unwrap_or(Value::Null),
    );
    out.insert("sensitivity", json!(severity));
    out.insert("labels", json!(labels));
    out.insert("risk_fields", json!(found));
    out.insert(
        "compliance_fields",
        if found.is_empty() {
            json!(["민감정보 미검출"])
        } else {
            json!([])
        },
    );
    out.insert(
        "preview",
        json!(mask_sensitive_text(&preview_text, &preview_matches)),
    );
    out
}

/// `build_sensitivity_report`.
pub fn build_sensitivity_report(history: &[Value]) -> OrderedMap {
    let items: Vec<OrderedMap> = history
        .iter()
        .enumerate()
        .map(|(i, item)| classify_sensitive_message(item, i))
        .collect();
    let risky: Vec<&OrderedMap> = items
        .iter()
        .filter(|item| {
            item.get("risk_fields")
                .and_then(Value::as_array)
                .map(|a| !a.is_empty())
                .unwrap_or(false)
        })
        .collect();
    let compliant: Vec<&OrderedMap> = items
        .iter()
        .filter(|item| {
            item.get("risk_fields")
                .and_then(Value::as_array)
                .map(|a| a.is_empty())
                .unwrap_or(true)
        })
        .collect();

    let mut field_counts = OrderedMap::new();
    let mut user_counts = OrderedMap::new();
    let mut severity_counts = OrderedMap::new();
    severity_counts.insert("high", json!(0));
    severity_counts.insert("medium", json!(0));
    severity_counts.insert("low", json!(0));
    severity_counts.insert("none", json!(compliant.len()));
    for item in &risky {
        let sev = item
            .get("sensitivity")
            .and_then(Value::as_str)
            .unwrap_or("none");
        let next = severity_counts
            .get(sev)
            .and_then(Value::as_u64)
            .unwrap_or(0)
            + 1;
        severity_counts.insert(sev, json!(next));
        let user_key = item
            .get("user_email")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .or_else(|| item.get("user_nickname").and_then(Value::as_str))
            .unwrap_or("Unknown");
        let next = user_counts
            .get(user_key)
            .and_then(Value::as_u64)
            .unwrap_or(0)
            + 1;
        user_counts.insert(user_key, json!(next));
        if let Some(fields) = item.get("risk_fields").and_then(Value::as_array) {
            for field in fields {
                if let Some(label) = field.get("label").and_then(Value::as_str) {
                    let next = field_counts.get(label).and_then(Value::as_u64).unwrap_or(0) + 1;
                    field_counts.insert(label, json!(next));
                }
            }
        }
    }

    let total = items.len();
    let risk_rate = if total == 0 {
        json!(0)
    } else {
        json!(((risky.len() as f64) / (total as f64) * 1000.0).round() / 10.0)
    };

    let mut summary = OrderedMap::new();
    summary.insert("total_messages", json!(total));
    summary.insert("risky_messages", json!(risky.len()));
    summary.insert("compliant_messages", json!(compliant.len()));
    summary.insert("risk_rate", risk_rate);
    summary.insert("severity_counts", json_from_ordered(&severity_counts));
    summary.insert("field_counts", json_from_ordered(&field_counts));
    summary.insert("user_counts", json_from_ordered(&user_counts));

    let risk_tail = tail_maps(&risky, 30);
    let compliant_tail = tail_maps(&compliant, 30);

    let mut report = OrderedMap::new();
    report.insert("summary", json_from_ordered(&summary));
    report.insert("risk_fields", json!(risk_tail));
    report.insert("compliance_fields", json!(compliant_tail));
    report
}

/// `build_admin_audit_report` (the router then tacks on `filters` and `graph`).
pub fn build_admin_audit_report(
    users: &lattice_auth::Users,
    auth: &AuthState,
    events: &[Value],
    graph_stats: Option<&Value>,
) -> OrderedMap {
    let mut per_user: BTreeMap<String, OrderedMap> = BTreeMap::new();

    let ensure = |per_user: &mut BTreeMap<String, OrderedMap>,
                  email: Option<&str>,
                  nickname: Option<&str>|
     -> String {
        let key = email
            .filter(|s| !s.is_empty())
            .or(nickname)
            .unwrap_or("Unknown")
            .to_string();
        if let Some(existing) = per_user.get_mut(&key) {
            let current = existing
                .get("nickname")
                .and_then(Value::as_str)
                .unwrap_or("");
            if let Some(nick) = nickname {
                if current == "Unknown" || current == email.unwrap_or("") || current.is_empty() {
                    existing.insert("nickname", json!(nick));
                }
            }
            return key;
        }
        let record = email.and_then(|e| users.get(e));
        let role = if let Some(e) = email {
            auth.get_user_role(e, users)
        } else {
            "unknown".into()
        };
        let nick = nickname
            .map(str::to_string)
            .or_else(|| {
                record.and_then(|r| {
                    r.get("nickname")
                        .and_then(Value::as_str)
                        .map(str::to_string)
                        .filter(|s| !s.is_empty())
                        .or_else(|| {
                            r.get("name")
                                .and_then(Value::as_str)
                                .map(str::to_string)
                                .filter(|s| !s.is_empty())
                        })
                })
            })
            .or_else(|| email.map(str::to_string))
            .unwrap_or_else(|| "Unknown".into());
        let mut bucket = OrderedMap::new();
        bucket.insert("email", json!(email.unwrap_or("Unknown")));
        bucket.insert("nickname", json!(nick));
        bucket.insert("role", json!(role));
        bucket.insert(
            "disabled",
            json!(record
                .and_then(|r| r.get("disabled"))
                .map(|v| v.as_bool().unwrap_or(!v.is_null()))
                .unwrap_or(false)),
        );
        bucket.insert("user_messages", json!(0));
        bucket.insert("assistant_messages", json!(0));
        bucket.insert("document_uploads", json!(0));
        bucket.insert("clear_events", json!(0));
        bucket.insert("delete_events", json!(0));
        bucket.insert("sensitive_events", json!(0));
        bucket.insert("high_sensitive_events", json!(0));
        bucket.insert("total_content_chars", json!(0));
        bucket.insert("last_activity_at", Value::Null);
        per_user.insert(key.clone(), bucket);
        key
    };

    for (email, user) in users.iter() {
        let nick = user
            .get("nickname")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .or_else(|| user.get("name").and_then(Value::as_str));
        ensure(&mut per_user, Some(email), nick);
    }

    let mut summary = OrderedMap::new();
    summary.insert("total_events", json!(events.len()));
    summary.insert("chat_events", json!(0u64));
    summary.insert("user_messages", json!(0u64));
    summary.insert("assistant_messages", json!(0u64));
    summary.insert("document_uploads", json!(0u64));
    summary.insert("clear_events", json!(0u64));
    summary.insert("delete_events", json!(0u64));
    summary.insert("sensitive_events", json!(0u64));
    summary.insert("high_sensitive_events", json!(0u64));

    let mut sensitive_events: Vec<Value> = Vec::new();
    let mut deletion_events: Vec<Value> = Vec::new();

    for event in events {
        let event_type = event
            .get("event_type")
            .and_then(Value::as_str)
            .unwrap_or("");
        let email = event.get("user_email").and_then(Value::as_str);
        let nick = event.get("user_nickname").and_then(Value::as_str);
        let key = ensure(&mut per_user, email, nick);
        let u = per_user.get_mut(&key).expect("just inserted");
        if let Some(ts) = event.get("timestamp").and_then(Value::as_str) {
            let last = u
                .get("last_activity_at")
                .and_then(Value::as_str)
                .unwrap_or("");
            if last.is_empty() || ts > last {
                u.insert("last_activity_at", json!(ts));
            }
        }
        let extra = event
            .get("content_chars")
            .and_then(Value::as_i64)
            .or_else(|| event.get("extracted_chars").and_then(Value::as_i64))
            .unwrap_or(0);
        let chars = u
            .get("total_content_chars")
            .and_then(Value::as_i64)
            .unwrap_or(0)
            + extra;
        u.insert("total_content_chars", json!(chars));

        let sensitivity = event
            .get("sensitivity")
            .and_then(Value::as_str)
            .unwrap_or("none");
        let labels_present = event
            .get("sensitive_labels")
            .and_then(Value::as_array)
            .map(|a| !a.is_empty())
            .unwrap_or(false);
        let is_sensitive = sensitivity != "none" || labels_present;

        match event_type {
            "chat_message" => {
                bump(&mut summary, "chat_events");
                match event.get("role").and_then(Value::as_str) {
                    Some("user") => {
                        bump(&mut summary, "user_messages");
                        bump(u, "user_messages");
                    }
                    Some("assistant") => {
                        bump(&mut summary, "assistant_messages");
                        bump(u, "assistant_messages");
                    }
                    _ => {}
                }
            }
            "document_upload" => {
                bump(&mut summary, "document_uploads");
                bump(u, "document_uploads");
            }
            "clear_command" => {
                bump(&mut summary, "clear_events");
                bump(u, "clear_events");
            }
            other if AUDIT_DELETE_EVENTS.contains(&other) => {
                bump(&mut summary, "delete_events");
                bump(u, "delete_events");
                deletion_events.push(public_audit_event(event));
            }
            _ => {}
        }
        if is_sensitive {
            bump(&mut summary, "sensitive_events");
            bump(u, "sensitive_events");
            if sensitivity == "high" {
                bump(&mut summary, "high_sensitive_events");
                bump(u, "high_sensitive_events");
            }
            sensitive_events.push(public_audit_event(event));
        }
    }

    let recent: Vec<Value> = events
        .iter()
        .rev()
        .take(50)
        .map(public_audit_event)
        .collect();

    if let Some(stats) = graph_stats {
        if !stats.is_null() && stats.as_object().map(|o| !o.is_empty()).unwrap_or(true) {
            summary.insert(
                "graph_nodes",
                json!(stats
                    .get("total_nodes")
                    .and_then(Value::as_u64)
                    .unwrap_or(0)),
            );
            summary.insert(
                "graph_edges",
                json!(stats
                    .get("total_edges")
                    .and_then(Value::as_u64)
                    .unwrap_or(0)),
            );
        }
    }

    let mut per_user_list: Vec<Value> = per_user
        .into_values()
        .map(|m| json_from_ordered(&m))
        .collect();
    per_user_list.sort_by(|a, b| {
        let ta = a
            .get("last_activity_at")
            .and_then(Value::as_str)
            .unwrap_or("");
        let tb = b
            .get("last_activity_at")
            .and_then(Value::as_str)
            .unwrap_or("");
        tb.cmp(ta)
    });

    let sens_tail = if sensitive_events.len() > 30 {
        sensitive_events.split_off(sensitive_events.len() - 30)
    } else {
        sensitive_events
    };
    let del_tail = if deletion_events.len() > 30 {
        deletion_events.split_off(deletion_events.len() - 30)
    } else {
        deletion_events
    };

    let mut result = OrderedMap::new();
    result.insert("summary", json_from_ordered(&summary));
    result.insert("per_user", json!(per_user_list));
    result.insert("recent_events", json!(recent));
    result.insert("sensitive_events", json!(sens_tail));
    result.insert("deletion_events", json!(del_tail));
    result
}

fn public_audit_event(event: &Value) -> Value {
    let Some(obj) = event.as_object() else {
        return json!({});
    };
    let mut out = Map::new();
    for key in AUDIT_PUBLIC_KEYS {
        if let Some(value) = obj.get(*key) {
            out.insert((*key).to_string(), value.clone());
        }
    }
    Value::Object(out)
}

// ── redaction ────────────────────────────────────────────────────────────────

/// `core.security.redact_secret_text`.
pub fn redact_secret_text(text: &str) -> String {
    if text.is_empty() {
        return String::new();
    }
    let mut redacted = text.to_string();
    if let Ok(re) = Regex::new(r"\bbot(\d{5,20}):[A-Za-z0-9_-]{8,}\b") {
        redacted = re.replace_all(&redacted, "bot${1}:REDACTED").into_owned();
    }
    if let Ok(re) = Regex::new(r"(?<![A-Za-z0-9_:-])(\d{5,20}):[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])")
    {
        redacted = re.replace_all(&redacted, "bot${1}:REDACTED").into_owned();
    }
    let patterns = [
        r#"(?i)\b(api[_ -]?key|secret|token|password|passwd|authorization|bearer|client[_ -]?secret|webhook|dsn)\s*[:=]\s*['\"]?([^\s'\",;]{8,})['\"]?"#,
        r"\b(sk-[A-Za-z0-9_\-]{16,})\b",
        r"\b(xai-[A-Za-z0-9_\-]{16,})\b",
        r"\b(gsk_[A-Za-z0-9_\-]{16,})\b",
        r"\b(ghp_[A-Za-z0-9_]{30,})\b",
        r"\b(xox[baprs]-[A-Za-z0-9-]{10,})\b",
        r"\b(AKIA[0-9A-Z]{16})\b",
        r"(?i)\b(postgres(?:ql)?://[^@\s]+:[^@\s]+@[^\s]+)",
        r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----",
    ];
    for pat in patterns {
        let Ok(re) = Regex::new(pat) else {
            continue;
        };
        redacted = re
            .replace_all(&redacted, |caps: &fancy_regex::Captures| {
                if caps.get(2).is_some() {
                    format!(
                        "{}=[REDACTED_SECRET]",
                        caps.get(1).map(|m| m.as_str()).unwrap_or("")
                    )
                } else {
                    "[REDACTED_SECRET]".into()
                }
            })
            .into_owned();
    }
    redacted
}

/// Recursively redact string leaves. Keys named like secrets become
/// `[REDACTED_SECRET]` (Python `redact_secrets`).
pub fn redact_secrets(value: &Value) -> Value {
    match value {
        Value::String(text) => json!(redact_secret_text(text)),
        Value::Object(map) => {
            let mut out = Map::new();
            for (key, item) in map {
                if is_secret_key(key) {
                    out.insert(key.clone(), json!("[REDACTED_SECRET]"));
                } else {
                    out.insert(key.clone(), redact_secrets(item));
                }
            }
            Value::Object(out)
        }
        Value::Array(items) => Value::Array(items.iter().map(redact_secrets).collect()),
        other => other.clone(),
    }
}

/// Walk values only — keys stay. Used by the security dashboard (not
/// `redact_secrets`, which blanks secret-*named* fields).
pub fn redact_structure(value: &Value) -> Value {
    match value {
        Value::String(text) => json!(redact_secret_text(text)),
        Value::Object(map) => {
            let mut out = Map::new();
            for (key, item) in map {
                out.insert(key.clone(), redact_structure(item));
            }
            Value::Object(out)
        }
        Value::Array(items) => Value::Array(items.iter().map(redact_structure).collect()),
        other => other.clone(),
    }
}

fn is_secret_key(key: &str) -> bool {
    let lowered = key.to_lowercase().replace('-', "_");
    [
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "bearer",
        "private_key",
        "client_secret",
        "webhook",
        "dsn",
        "credential",
    ]
    .iter()
    .any(|hint| lowered.contains(hint))
}

// ── history + vpc + sso helpers ──────────────────────────────────────────────

/// Read `chat_history.json` (array, or `{messages|items|history: [...]}`).
pub fn load_chat_history(data_dir: &Path) -> Vec<Value> {
    let path = data_dir.join(state_files::CHAT_HISTORY);
    let Ok(text) = std::fs::read_to_string(&path) else {
        return Vec::new();
    };
    match serde_json::from_str::<Value>(&text) {
        Ok(Value::Array(items)) => items,
        Ok(Value::Object(map)) => ["messages", "items", "history"]
            .iter()
            .find_map(|k| map.get(*k).and_then(Value::as_array).cloned())
            .unwrap_or_default(),
        _ => Vec::new(),
    }
}

pub fn matches_workspace_scope(item: &Value, workspace_id: Option<&str>) -> bool {
    let Some(scope) = workspace_id.filter(|s| !s.is_empty()) else {
        return true;
    };
    let item_scope = item
        .get("workspace_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    if item_scope.is_empty() && scope == DEFAULT_WORKSPACE_ID {
        return true;
    }
    item_scope == scope
}

pub fn default_vpc_config() -> OrderedMap {
    let mut cfg = OrderedMap::new();
    cfg.insert("provider", json!("AWS"));
    cfg.insert("region", json!("ap-northeast-2"));
    cfg.insert("cidr_block", json!("10.42.0.0/16"));
    cfg.insert("private_subnets", json!(["10.42.10.0/24", "10.42.20.0/24"]));
    cfg.insert("endpoint", json!("ltcai-private.local"));
    cfg.insert("vpn_status", json!("standby"));
    cfg.insert("peering_status", json!("not_configured"));
    cfg.insert(
        "notes",
        json!("로컬 MLX 브릿지를 프라이빗 서브넷 또는 VPN 뒤에서 운영할 때 쓰는 네트워크 프로필입니다."),
    );
    cfg.insert("updated_at", Value::Null);
    cfg
}

pub fn load_vpc_config(data_dir: &Path) -> OrderedMap {
    let path = data_dir.join(state_files::VPC_CONFIG);
    let mut cfg = default_vpc_config();
    let Ok(text) = std::fs::read_to_string(&path) else {
        return cfg;
    };
    let Ok(stored) = serde_json::from_str::<Map<String, Value>>(&text) else {
        return cfg;
    };
    for (key, value) in stored {
        cfg.insert(key, value);
    }
    cfg
}

pub fn save_vpc_config(data_dir: &Path, mut cfg: OrderedMap) {
    cfg.insert("updated_at", json!(now_iso()));
    let path = data_dir.join(state_files::VPC_CONFIG);
    if let Ok(text) = lattice_auth::pyjson::dumps_indent2(&cfg) {
        lattice_auth::atomic::write_text(&path, &text);
    }
}

pub fn default_sso_redirect(port: u16) -> String {
    format!("http://localhost:{port}{SSO_CALLBACK_PATH}")
}

pub fn load_sso_config(data_dir: &Path, port: u16) -> OrderedMap {
    let mut cfg = OrderedMap::new();
    cfg.insert("enabled", json!(false));
    cfg.insert("provider_name", json!("SSO"));
    cfg.insert("discovery_url", json!(""));
    cfg.insert("client_id", json!(""));
    cfg.insert("client_secret", json!(""));
    cfg.insert("redirect_uri", json!(default_sso_redirect(port)));
    cfg.insert("scopes", json!("openid email profile"));

    let path = data_dir.join(state_files::SSO_CONFIG);
    if let Ok(text) = std::fs::read_to_string(&path) {
        if let Ok(stored) = serde_json::from_str::<Map<String, Value>>(&text) {
            for (key, value) in stored {
                if !value.is_null() {
                    cfg.insert(key, value);
                }
            }
        }
    }
    let provider = cfg
        .get("provider_name")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .unwrap_or("SSO")
        .to_string();
    cfg.insert("provider_name", json!(provider));
    for key in ["discovery_url", "client_id", "client_secret"] {
        let text = cfg
            .get(key)
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        cfg.insert(key, json!(text));
    }
    let redirect = cfg
        .get("redirect_uri")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| default_sso_redirect(port));
    cfg.insert("redirect_uri", json!(redirect));
    let scopes = cfg
        .get("scopes")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .unwrap_or("openid email profile")
        .to_string();
    cfg.insert("scopes", json!(scopes));
    let enabled = cfg.get("enabled").and_then(Value::as_bool).unwrap_or(false)
        && !cfg
            .get("discovery_url")
            .and_then(Value::as_str)
            .unwrap_or("")
            .is_empty()
        && !cfg
            .get("client_id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .is_empty()
        && !cfg
            .get("client_secret")
            .and_then(Value::as_str)
            .unwrap_or("")
            .is_empty();
    cfg.insert("enabled", json!(enabled));
    cfg
}

pub fn public_sso_config(cfg: &OrderedMap, port: u16) -> OrderedMap {
    let mut out = OrderedMap::new();
    out.insert(
        "enabled",
        json!(cfg.get("enabled").and_then(Value::as_bool).unwrap_or(false)),
    );
    out.insert(
        "provider_name",
        json!(cfg
            .get("provider_name")
            .and_then(Value::as_str)
            .unwrap_or("")),
    );
    out.insert(
        "discovery_url",
        json!(cfg
            .get("discovery_url")
            .and_then(Value::as_str)
            .unwrap_or("")),
    );
    out.insert(
        "client_id",
        json!(cfg.get("client_id").and_then(Value::as_str).unwrap_or("")),
    );
    out.insert(
        "redirect_uri",
        json!(cfg
            .get("redirect_uri")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .map(str::to_string)
            .unwrap_or_else(|| default_sso_redirect(port))),
    );
    out.insert(
        "scopes",
        json!(cfg
            .get("scopes")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .unwrap_or("openid email profile")),
    );
    out.insert(
        "secret_configured",
        json!(!cfg
            .get("client_secret")
            .and_then(Value::as_str)
            .unwrap_or("")
            .is_empty()),
    );
    out
}

pub fn save_sso_config(data_dir: &Path, port: u16, update: Map<String, Value>) -> OrderedMap {
    let mut current = load_sso_config(data_dir, port);
    let mut update = update;
    if update.get("client_secret").and_then(Value::as_str) == Some("") {
        update.remove("client_secret");
    }
    for (key, value) in update {
        if !value.is_null() {
            current.insert(key, value);
        }
    }
    let enabled = current
        .get("enabled")
        .and_then(Value::as_bool)
        .unwrap_or(false)
        && !current
            .get("discovery_url")
            .and_then(Value::as_str)
            .unwrap_or("")
            .is_empty()
        && !current
            .get("client_id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .is_empty()
        && !current
            .get("client_secret")
            .and_then(Value::as_str)
            .unwrap_or("")
            .is_empty();
    current.insert("enabled", json!(enabled));
    let path = data_dir.join(state_files::SSO_CONFIG);
    if let Ok(text) = lattice_auth::pyjson::dumps_indent2(&current) {
        lattice_auth::atomic::write_text(&path, &text);
    }
    current
}

// ── enterprise + hardening ───────────────────────────────────────────────────

pub fn poc_overview() -> OrderedMap {
    let mut edition = OrderedMap::new();
    edition.insert("edition", json!("community"));
    edition.insert("is_enterprise", json!(false));
    let mut capabilities = OrderedMap::new();
    for cap in [
        "sso_advanced",
        "idp_provisioning",
        "scim",
        "rbac_abac_advanced",
        "tenant_isolation",
        "compliance_retention",
        "siem_export",
        "private_vpc",
        "air_gapped_deployment",
        "dlp_policy",
        "ediscovery",
        "admin_policy_packs",
        "graph_promotion_review",
    ] {
        capabilities.insert(cap, json!(false));
    }
    edition.insert("capabilities", json_from_ordered(&capabilities));
    edition.insert(
        "community_notice",
        json!("All listed capabilities are Enterprise-only extension points. The open-source Community edition ships none of them enabled; see docs/ENTERPRISE.md and docs/EDITION_STRATEGY.md."),
    );

    let mut admin_policies = OrderedMap::new();
    admin_policies.insert("capability", json!("admin_policy_packs"));
    admin_policies.insert("enabled", json!(false));
    admin_policies.insert("enforced", json!(false));
    let mut effective = OrderedMap::new();
    effective.insert("base_roles", json!(["owner", "admin", "member", "viewer"]));
    effective.insert(
        "local_file_access",
        json!("approval-token gated (per path/user/action)"),
    );
    effective.insert("package_install", json!("admin-only with audit trail"));
    effective.insert("network_binding", json!("127.0.0.1 by default"));
    effective.insert("managed_policy_packs", json!([]));
    admin_policies.insert("effective_policy", json_from_ordered(&effective));
    admin_policies.insert("note", json!(COMMUNITY_NOTICE));

    let mut local_export = OrderedMap::new();
    local_export.insert("available", json!(true));
    local_export.insert("endpoint", json!("/admin/security/export"));
    local_export.insert("formats", json!(["json", "csv", "xlsx", "txt", "pdf"]));
    local_export.insert(
        "note",
        json!("Community local audit export is always available to admins."),
    );
    let mut siem_streaming = OrderedMap::new();
    siem_streaming.insert("capability", json!("siem_export"));
    siem_streaming.insert("enabled", json!(false));
    siem_streaming.insert("note", json!(COMMUNITY_NOTICE));
    let mut retention = OrderedMap::new();
    retention.insert("capability", json!("compliance_retention"));
    retention.insert("enabled", json!(false));
    retention.insert("note", json!(COMMUNITY_NOTICE));
    let mut audit_export = OrderedMap::new();
    audit_export.insert("local_export", json_from_ordered(&local_export));
    audit_export.insert("siem_streaming", json_from_ordered(&siem_streaming));
    audit_export.insert("compliance_retention", json_from_ordered(&retention));

    let mut org = OrderedMap::new();
    let mut baseline = OrderedMap::new();
    baseline.insert("workspaces", json!(["personal", "organization"]));
    baseline.insert("roles", json!(["owner", "admin", "member", "viewer"]));
    baseline.insert(
        "data_isolation",
        json!("single-tenant local storage (~/.ltcai)"),
    );
    org.insert("community_baseline", json_from_ordered(&baseline));
    let mut gov = OrderedMap::new();
    for cap in [
        "tenant_isolation",
        "rbac_abac_advanced",
        "scim",
        "idp_provisioning",
        "sso_advanced",
        "dlp_policy",
        "ediscovery",
        "private_vpc",
        "air_gapped_deployment",
    ] {
        gov.insert(cap, json!(false));
    }
    org.insert("governance_capabilities", json_from_ordered(&gov));
    org.insert("note", json!(COMMUNITY_NOTICE));

    let mut out = OrderedMap::new();
    out.insert("edition", json_from_ordered(&edition));
    out.insert("admin_policies", json_from_ordered(&admin_policies));
    out.insert("audit_export", json_from_ordered(&audit_export));
    out.insert("siem_export", json_from_ordered(&siem_export_stub()));
    out.insert("organization_settings", json_from_ordered(&org));
    out
}

pub fn siem_export_stub() -> OrderedMap {
    let mut record = OrderedMap::new();
    record.insert("ts", json!("1970-01-01T00:00:00Z"));
    record.insert("actor", json!("admin@example.com"));
    record.insert("act", json!("model_load"));
    record.insert("sev", json!("informational"));
    record.insert("kind", json!("audit_event"));
    record.insert("id", json!("evt_sample"));
    let mut envelope = OrderedMap::new();
    envelope.insert("format", json!("ltcai.siem.v1"));
    envelope.insert("encoding", json!("ndjson"));
    envelope.insert("vendor", json!("LatticeAI"));
    envelope.insert("product", json!("Workspace OS"));
    envelope.insert("records", json!([json_from_ordered(&record)]));
    let mut out = OrderedMap::new();
    out.insert("capability", json!("siem_export"));
    out.insert("enabled", json!(false));
    out.insert("streamed", json!(false));
    out.insert("destination", Value::Null);
    out.insert("preview_envelope", json_from_ordered(&envelope));
    out.insert("note", json!(COMMUNITY_NOTICE));
    out
}

pub fn default_product_hardening(
    data_dir: &Path,
    host: &str,
    port: u16,
    auth_required: bool,
) -> OrderedMap {
    let env_flag = |key: &str| -> bool {
        std::env::var(key)
            .ok()
            .map(|v| {
                matches!(
                    v.trim().to_ascii_lowercase().as_str(),
                    "1" | "true" | "yes" | "on"
                )
            })
            .unwrap_or(false)
    };
    let present = |keys: &[&str]| {
        keys.iter().any(|k| {
            std::env::var(k)
                .map(|v| !v.trim().is_empty())
                .unwrap_or(false)
        })
    };

    let mut startup = OrderedMap::new();
    startup.insert("local_only_default", json!(true));
    startup.insert("host", json!(host));
    startup.insert("port", json!(port));
    startup.insert("network_exposed", json!(false));
    startup.insert("auth_required", json!(auth_required));
    startup.insert(
        "cors_network_allowed",
        json!(env_flag("LATTICEAI_CORS_ALLOW_NETWORK")),
    );

    let mut updater = OrderedMap::new();
    updater.insert("enabled", json!(env_flag("LATTICEAI_ENABLE_UPDATES")));
    updater.insert(
        "limitation",
        json!("No external update checks run unless explicitly enabled by policy."),
    );
    let mut desktop = OrderedMap::new();
    desktop.insert("sidecar_lifecycle", json!("managed"));
    desktop.insert("restart_supported", json!(true));
    desktop.insert("shutdown_supported", json!(true));
    desktop.insert("updater", json_from_ordered(&updater));

    let mut first_run = OrderedMap::new();
    first_run.insert("data_dir", json!(data_dir.display().to_string()));
    first_run.insert("data_dir_exists", json!(data_dir.exists()));
    first_run.insert(
        "python_available",
        json!(which("python3") || which("python")),
    );
    first_run.insert("docker_available", json!(which("docker")));
    first_run.insert("docker_required", json!(false));
    first_run.insert("postgres_required", json!(false));

    let integration = |enabled: bool, cred: bool, detail: &str| {
        let mut m = OrderedMap::new();
        m.insert("enabled", json!(enabled));
        m.insert("credential_present", json!(cred));
        m.insert("opt_in_required", json!(true));
        m.insert("automatic_egress", json!(enabled));
        m.insert("detail", json!(detail));
        json_from_ordered(&m)
    };
    let mut integrations = OrderedMap::new();
    integrations.insert(
        "telegram",
        integration(
            env_flag("LATTICEAI_ENABLE_TELEGRAM"),
            present(&["LATTICEAI_TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN"]),
            if env_flag("LATTICEAI_ENABLE_TELEGRAM") {
                "enabled by LATTICEAI_ENABLE_TELEGRAM"
            } else {
                "disabled; token presence alone does not start Telegram"
            },
        ),
    );
    integrations.insert(
        "brain_network",
        integration(
            env_flag("LATTICEAI_BRAIN_NETWORK_AUTO_PUSH"),
            false,
            "peer pushes are user/admin initiated; no automatic peer sync by default",
        ),
    );
    integrations.insert(
        "updates",
        integration(
            env_flag("LATTICEAI_ENABLE_UPDATES"),
            false,
            "desktop updater checks are disabled unless LATTICEAI_ENABLE_UPDATES is true",
        ),
    );
    integrations.insert(
        "model_downloads",
        integration(
            env_flag("LATTICEAI_ALLOW_MODEL_DOWNLOADS"),
            present(&["HF_TOKEN", "HUGGINGFACEHUB_API_TOKEN"]),
            "model downloads require an explicit load/autoload setting",
        ),
    );
    integrations.insert(
        "docker",
        integration(
            env_flag("LATTICEAI_DOCKER_AUTO_START"),
            false,
            "Docker setup requires explicit runtime consent; auto-start is disabled by default",
        ),
    );
    integrations.insert(
        "postgres",
        integration(
            false,
            false,
            "Postgres scale mode is used only when storage engine and DSN are explicitly configured",
        ),
    );
    integrations.insert(
        "external_connectors",
        integration(
            env_flag("LATTICEAI_ENABLE_EXTERNAL_CONNECTORS"),
            present(&[
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "GITHUB_TOKEN",
                "SLACK_BOT_TOKEN",
                "DISCORD_BOT_TOKEN",
            ]),
            "connector credentials are inert until the connector is explicitly enabled and invoked",
        ),
    );
    let mut privacy = OrderedMap::new();
    privacy.insert("local_only_default", json!(true));
    privacy.insert("integrations", json_from_ordered(&integrations));

    let mut permissions = OrderedMap::new();
    permissions.insert("export_requires_admin", json!(true));
    permissions.insert("import_requires_admin", json!(true));
    permissions.insert("restore_requires_admin", json!(true));
    permissions.insert("destructive_restore_requires_confirmation", json!(true));
    permissions.insert("workspace_isolation_enforced", json!(true));
    permissions.insert("audit_log_visible_to_admin", json!(true));

    let mut failure = OrderedMap::new();
    failure.insert("archive_corruption", json!("fail_closed"));
    failure.insert("partial_archive", json!("fail_closed"));
    failure.insert("signature_mismatch", json!("fail_closed"));
    failure.insert("unsupported_version", json!("fail_closed"));
    failure.insert("missing_docker", json!("honest_unavailable"));
    failure.insert("missing_postgres", json!("honest_unavailable"));
    failure.insert("permission_denied", json!("honest_error"));

    let mut out = OrderedMap::new();
    out.insert("version", json!(env!("CARGO_PKG_VERSION")));
    out.insert("startup", json_from_ordered(&startup));
    out.insert("desktop", json_from_ordered(&desktop));
    out.insert("first_run", json_from_ordered(&first_run));
    out.insert("privacy", json_from_ordered(&privacy));
    let exports = data_dir.join(lattice_core::db::tables::state_files::WORKSPACE_EXPORTS);
    let backup = crate::portability::backup_health_payload(&exports);
    let mut storage = OrderedMap::new();
    storage.insert("available", json!(true));
    storage.insert(
        "active",
        json!(crate::portability::sqlite_capabilities(
            &data_dir.join("knowledge_graph.sqlite")
        )),
    );
    storage.insert(
        "postgres",
        json!(crate::portability::postgres_capabilities()),
    );
    storage.insert("backup_health", json!(backup.clone()));
    out.insert("storage", json_from_ordered(&storage));
    out.insert("backup", json!(backup));
    let identity = crate::network::DeviceIdentity::load_or_create(
        &data_dir.join(lattice_core::db::tables::state_files::DEVICE_IDENTITY),
    );
    out.insert("device_identity", json!(identity.describe()));
    out.insert("permissions", json_from_ordered(&permissions));
    out.insert("failure_policy", json_from_ordered(&failure));
    out
}

// ── HTTP helpers (shared with the other R2 families) ─────────────────────────

pub fn language_from(headers: &HeaderMap) -> &'static str {
    messages::resolve_language(
        headers.get(LANGUAGE_HEADER).and_then(|v| v.to_str().ok()),
        headers
            .get(axum::http::header::ACCEPT_LANGUAGE)
            .and_then(|v| v.to_str().ok()),
    )
}

pub fn json_ok(body: &OrderedMap) -> Response {
    json_response(
        StatusCode::OK,
        &serde_json::to_string(body).unwrap_or_else(|_| "{}".into()),
        None,
    )
}

pub fn json_ok_value(body: &Value) -> Response {
    json_response(
        StatusCode::OK,
        &serde_json::to_string(body).unwrap_or_else(|_| "null".into()),
        None,
    )
}

pub fn json_status(status: StatusCode, body: &OrderedMap) -> Response {
    json_response(
        status,
        &serde_json::to_string(body).unwrap_or_else(|_| "{}".into()),
        None,
    )
}

pub fn message_error(status: u16, id: &str, lang: &str, args: &[(&str, &str)]) -> Response {
    let err = messages::http_error(status, id, lang, args);
    let body = serde_json::to_string(&err.body).unwrap_or_else(|_| "{\"detail\":\"\"}".into());
    json_response(
        StatusCode::from_u16(err.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
        &body,
        None,
    )
}

pub fn detail_status(status: StatusCode, detail: &str) -> Response {
    let mut body = OrderedMap::new();
    body.insert("detail", json!(detail));
    json_status(status, &body)
}

pub fn workspace_from_headers(headers: &HeaderMap) -> Option<String> {
    lattice_auth::workspace_scope_from_request(headers, None)
}

// ── router state ─────────────────────────────────────────────────────────────

/// What the admin family needs from the host.
#[derive(Clone)]
pub struct AdminState {
    pub auth: Arc<AuthState>,
    pub data_dir: PathBuf,
    pub invite_code: String,
    pub invite_gate_enabled: bool,
    pub default_port: u16,
    pub enable_graph: bool,
    pub graph_stats: Arc<dyn Fn() -> Result<Value, String> + Send + Sync>,
    pub hardening: Option<Arc<dyn Fn() -> OrderedMap + Send + Sync>>,
}

impl AdminState {
    pub fn new(auth: Arc<AuthState>, data_dir: impl Into<PathBuf>) -> Self {
        let data_dir = data_dir.into();
        let default_port = auth.config().port;
        let invite_gate_enabled = auth.config().invite_gate_enabled;
        let invite_code = std::env::var("LATTICEAI_INVITE_CODE").unwrap_or_default();
        let hardening_dir = data_dir.clone();
        let host = auth.config().host.clone();
        let require_auth = auth.config().require_auth;
        Self {
            auth,
            data_dir,
            invite_code,
            invite_gate_enabled,
            default_port,
            enable_graph: true,
            graph_stats: Arc::new(|| Ok(json!({"total_nodes": 0, "total_edges": 0}))),
            hardening: Some(Arc::new(move || {
                default_product_hardening(&hardening_dir, &host, default_port, require_auth)
            })),
        }
    }
}

impl axum::extract::FromRef<AdminState> for Arc<AuthState> {
    fn from_ref(s: &AdminState) -> Self {
        Arc::clone(&s.auth)
    }
}

pub fn router(state: AdminState) -> Router {
    Router::new()
        .route("/admin/summary", get(admin_summary))
        .route("/admin/health-summary", get(admin_health_summary))
        .route("/admin/stats", get(admin_stats))
        .route("/admin/users", get(admin_users))
        .route("/admin/sensitivity", get(admin_sensitivity))
        .route("/admin/audit", get(admin_audit))
        .route("/admin/roles", get(admin_roles))
        .route("/admin/policies", get(admin_policies))
        .route("/admin/log-retention", get(admin_log_retention))
        .route("/admin/product-hardening", get(admin_product_hardening))
        .route("/vpc/status", get(vpc_status))
        .route("/admin/vpc", patch(admin_update_vpc))
        .route(
            "/admin/users/*email",
            patch(admin_update_user).delete(admin_delete_user),
        )
        .route("/admin/invite-link", get(admin_invite_link))
        .route("/admin/sso", get(admin_sso).patch(admin_update_sso))
        .route("/admin/enterprise", get(admin_enterprise))
        .route("/admin/enterprise/siem-export", get(admin_enterprise_siem))
        .with_state(state)
}

/// Union of every R2 family's mounted routes (OpenAPI contract test).
pub fn family_mounted() -> Vec<(&'static str, &'static str)> {
    let mut out = MOUNTED.to_vec();
    out.extend_from_slice(crate::security_dashboard::MOUNTED);
    out.extend_from_slice(crate::features::MOUNTED);
    out.extend_from_slice(crate::funnel_metrics::MOUNTED);
    out.extend_from_slice(crate::setup::MOUNTED);
    out
}

// ── handlers ─────────────────────────────────────────────────────────────────

fn require_admin(
    state: &AdminState,
    headers: &HeaderMap,
) -> Result<(Identity, lattice_auth::Users), Response> {
    let identity = state.auth.require_admin(headers)?;
    let users = state.auth.users().load();
    Ok((identity, users))
}

fn require_user(state: &AdminState, headers: &HeaderMap) -> Result<Identity, Response> {
    state.auth.require_user(headers)
}

fn scoped_history(state: &AdminState, headers: &HeaderMap) -> Vec<Value> {
    let scope = workspace_from_headers(headers);
    load_chat_history(&state.data_dir)
        .into_iter()
        .filter(|item| matches_workspace_scope(item, scope.as_deref()))
        .collect()
}

fn scoped_audit(state: &AdminState, headers: &HeaderMap) -> Vec<Value> {
    let scope = workspace_from_headers(headers);
    load_audit_log(&audit_log_path(&state.data_dir))
        .into_iter()
        .filter(|item| matches_workspace_scope(item, scope.as_deref()))
        .collect()
}

async fn admin_summary(
    State(state): State<AdminState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    let (_, users) = require_admin(&state, &headers)?;
    let history = scoped_history(&state, &headers);
    let user_msgs = history
        .iter()
        .filter(|i| i.get("role").and_then(Value::as_str) == Some("user"))
        .count();
    let asst_msgs = history
        .iter()
        .filter(|i| i.get("role").and_then(Value::as_str) == Some("assistant"))
        .count();
    let admin_users = users
        .iter()
        .filter(|(email, _)| state.auth.get_user_role(email, &users) == "admin")
        .count();
    let active = users
        .iter()
        .filter(|(_, u)| {
            !u.get("disabled")
                .map(|v| v.as_bool().unwrap_or(!v.is_null()))
                .unwrap_or(false)
        })
        .count();
    let last = history
        .last()
        .and_then(|i| i.get("timestamp"))
        .cloned()
        .unwrap_or(Value::Null);
    let mut out = OrderedMap::new();
    out.insert("total_users", json!(users.len()));
    out.insert("active_users", json!(active));
    out.insert("admin_users", json!(admin_users));
    out.insert("total_messages", json!(history.len()));
    out.insert("user_messages", json!(user_msgs));
    out.insert("assistant_messages", json!(asst_msgs));
    out.insert("last_message_at", last);
    Ok(json_ok(&out))
}

async fn admin_health_summary(
    State(state): State<AdminState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    let (_, users) = require_admin(&state, &headers)?;
    let mut issues: Vec<Value> = Vec::new();
    let disabled = users
        .iter()
        .filter(|(_, u)| {
            u.get("disabled")
                .map(|v| v.as_bool().unwrap_or(!v.is_null()))
                .unwrap_or(false)
        })
        .count();
    if disabled > 0 {
        let mut issue = OrderedMap::new();
        issue.insert("area", json!("users"));
        issue.insert("severity", json!("warning"));
        issue.insert("message", json!(format!("{disabled} disabled user(s)")));
        issues.push(json_from_ordered(&issue));
    }
    let report = build_sensitivity_report(&scoped_history(&state, &headers));
    if let Some(high) = report
        .get("summary")
        .and_then(|s| s.get("severity_counts"))
        .and_then(|c| c.get("high"))
        .and_then(Value::as_u64)
    {
        if high > 0 {
            let mut issue = OrderedMap::new();
            issue.insert("area", json!("security"));
            issue.insert("severity", json!("high"));
            issue.insert("message", json!(format!("{high} high-risk event(s)")));
            issues.push(json_from_ordered(&issue));
        }
    }
    if state.enable_graph {
        match (state.graph_stats)() {
            Ok(stats) if stats.get("error").is_some() => {
                let mut issue = OrderedMap::new();
                issue.insert("area", json!("brain_ops"));
                issue.insert("severity", json!("warning"));
                issue.insert("message", json!("Knowledge graph unavailable"));
                issues.push(json_from_ordered(&issue));
            }
            Err(err) => {
                let mut issue = OrderedMap::new();
                issue.insert("area", json!("brain_ops"));
                issue.insert("severity", json!("warning"));
                let clipped: String = err.chars().take(160).collect();
                issue.insert("message", json!(clipped));
                issues.push(json_from_ordered(&issue));
            }
            _ => {}
        }
    }
    if let Some(hardening) = &state.hardening {
        let report = hardening();
        if report
            .get("startup")
            .and_then(|s| s.get("network_exposed"))
            .and_then(Value::as_bool)
            .unwrap_or(false)
        {
            let mut issue = OrderedMap::new();
            issue.insert("area", json!("runtime_trust"));
            issue.insert("severity", json!("warning"));
            issue.insert("message", json!("Server is network-exposed"));
            issues.push(json_from_ordered(&issue));
        }
    }
    let issue_count = issues.len();
    let mut out = OrderedMap::new();
    out.insert(
        "status",
        json!(if issue_count > 0 { "attention" } else { "ok" }),
    );
    out.insert("issue_count", json!(issue_count));
    out.insert("issues", json!(issues));
    Ok(json_ok(&out))
}

async fn admin_stats(
    State(state): State<AdminState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    require_admin(&state, &headers)?;
    let history = scoped_history(&state, &headers);
    let mut daily: BTreeMap<String, (u64, u64)> = BTreeMap::new();
    for item in &history {
        let ts = item.get("timestamp").and_then(Value::as_str).unwrap_or("");
        let day = if ts.is_empty() {
            "unknown".to_string()
        } else {
            ts.chars().take(10).collect()
        };
        match item.get("role").and_then(Value::as_str) {
            Some("user") => daily.entry(day).or_default().0 += 1,
            Some("assistant") => daily.entry(day).or_default().1 += 1,
            _ => {}
        }
    }
    let mut days: Vec<String> = daily.keys().cloned().collect();
    days.sort();
    let tail = if days.len() > 14 {
        days.split_off(days.len() - 14)
    } else {
        days
    };
    let rows: Vec<Value> = tail
        .into_iter()
        .map(|d| {
            let (u, a) = daily.get(&d).copied().unwrap_or((0, 0));
            let mut row = OrderedMap::new();
            row.insert("date", json!(d));
            row.insert("user", json!(u));
            row.insert("assistant", json!(a));
            json_from_ordered(&row)
        })
        .collect();
    let mut out = OrderedMap::new();
    out.insert("daily", json!(rows));
    Ok(json_ok(&out))
}

async fn admin_users(
    State(state): State<AdminState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    let (_, users) = require_admin(&state, &headers)?;
    let list: Vec<Value> = users
        .iter()
        .map(|(email, record)| json_from_ordered(&state.auth.public_user(email, record, &users)))
        .collect();
    Ok(json_ok_value(&Value::Array(list)))
}

async fn admin_sensitivity(
    State(state): State<AdminState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    require_admin(&state, &headers)?;
    Ok(json_ok(&build_sensitivity_report(&scoped_history(
        &state, &headers,
    ))))
}

async fn admin_audit(
    State(state): State<AdminState>,
    headers: HeaderMap,
    Query(query): Query<std::collections::HashMap<String, String>>,
) -> Result<Response, Response> {
    let (_, users) = require_admin(&state, &headers)?;
    if let Some(raw) = query.get("limit") {
        if let Ok(n) = raw.parse::<i64>() {
            if n < 1 {
                return Err(query_bound_error("greater_than_equal", "ge", 1, raw));
            }
            if n > 250 {
                return Err(query_bound_error("less_than_equal", "le", 250, raw));
            }
        }
    }
    let limit = query
        .get("limit")
        .and_then(|s| s.parse::<i64>().ok())
        .unwrap_or(50)
        .clamp(1, 250) as usize;
    let scoped = scoped_audit(&state, &headers);
    let filtered = filter_audit_log(
        &scoped,
        query.get("q").map(String::as_str),
        query.get("actor").map(String::as_str),
        query.get("action").map(String::as_str),
        query.get("severity").map(String::as_str),
        limit,
    );
    let graph = if state.enable_graph {
        match (state.graph_stats)() {
            Ok(v) => v,
            Err(e) => json!({"error": e}),
        }
    } else {
        json!({"disabled": true})
    };
    let mut report = build_admin_audit_report(&users, &state.auth, &filtered, Some(&graph));
    let mut filters = OrderedMap::new();
    filters.insert("q", json!(query.get("q").cloned().unwrap_or_default()));
    filters.insert(
        "actor",
        json!(query.get("actor").cloned().unwrap_or_default()),
    );
    filters.insert(
        "action",
        json!(query.get("action").cloned().unwrap_or_default()),
    );
    filters.insert(
        "severity",
        json!(query.get("severity").cloned().unwrap_or_default()),
    );
    filters.insert(
        "limit",
        json!(query
            .get("limit")
            .and_then(|s| s.parse::<i64>().ok())
            .unwrap_or(50)),
    );
    filters.insert("matched_events", json!(filtered.len()));
    filters.insert("scoped_events", json!(scoped.len()));
    report.insert("filters", json_from_ordered(&filters));
    report.insert("graph", graph);
    Ok(json_ok(&report))
}

async fn admin_roles(
    State(state): State<AdminState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    let (_, users) = require_admin(&state, &headers)?;
    let mut counts: BTreeMap<String, u64> = BTreeMap::new();
    for (email, _) in users.iter() {
        let role = state.auth.get_user_role(email, &users);
        *counts.entry(role).or_default() += 1;
    }
    for role in ["owner", "admin", "member", "user", "viewer"] {
        counts.entry(role.into()).or_insert(0);
    }
    let order = |r: &str| match r {
        "owner" => 0,
        "admin" => 1,
        "member" => 2,
        "user" => 3,
        "viewer" => 4,
        _ => 99,
    };
    let mut roles_list: Vec<String> = counts.keys().cloned().collect();
    roles_list.sort_by(|a, b| order(a).cmp(&order(b)).then(a.cmp(b)));
    let rows: Vec<Value> = roles_list
        .into_iter()
        .map(|role| {
            let mut row = OrderedMap::new();
            row.insert("role", json!(role.clone()));
            row.insert("members", json!(counts.get(&role).copied().unwrap_or(0)));
            row.insert("caps", json!(capabilities_for_role(&role)));
            json_from_ordered(&row)
        })
        .collect();
    let mut out = OrderedMap::new();
    out.insert("roles", json!(rows));
    Ok(json_ok(&out))
}

async fn admin_policies(
    State(state): State<AdminState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    require_admin(&state, &headers)?;
    let gate = if state.invite_gate_enabled {
        "Signed access gate"
    } else {
        "Disabled"
    };
    let policies = [
        (
            "local_file_access",
            "Local file access",
            "Approval-token gated (per path/user/action)",
            true,
        ),
        (
            "package_install",
            "Package install",
            "Admin-only with audit trail",
            true,
        ),
        (
            "data_residency",
            "Data residency",
            "Single-tenant local storage (~/.ltcai)",
            true,
        ),
        (
            "model_egress",
            "Model egress",
            "Local-only by default (no external inference in local mode)",
            true,
        ),
        (
            "invite_gate",
            "Invite gate",
            gate,
            state.invite_gate_enabled,
        ),
        (
            "log_retention",
            "Log retention",
            "90 day local audit window with manual export before pruning",
            true,
        ),
    ];
    let rows: Vec<Value> = policies
        .into_iter()
        .map(|(id, label, value, enforced)| {
            let mut row = OrderedMap::new();
            row.insert("id", json!(id));
            row.insert("label", json!(label));
            row.insert("value", json!(value));
            row.insert("enforced", json!(enforced));
            json_from_ordered(&row)
        })
        .collect();
    let mut out = OrderedMap::new();
    out.insert("policies", json!(rows));
    Ok(json_ok(&out))
}

async fn admin_log_retention(
    State(state): State<AdminState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    require_admin(&state, &headers)?;
    let events = scoped_audit(&state, &headers);
    let cutoff = unix_now().saturating_sub(90 * 24 * 3600);
    let mut retained = 0u64;
    let mut prune = 0u64;
    for event in &events {
        let ts = event
            .get("timestamp")
            .or_else(|| event.get("ts"))
            .and_then(Value::as_str);
        if let Some(parsed) = ts.and_then(parse_timestamp_unix) {
            if parsed < cutoff {
                prune += 1;
            } else {
                retained += 1;
            }
        } else {
            retained += 1;
        }
    }
    let mut out = OrderedMap::new();
    out.insert("mode", json!("local-first"));
    out.insert("retention_days", json!(90));
    out.insert("total_events", json!(events.len()));
    out.insert("retained_events", json!(retained));
    out.insert("prune_candidates", json!(prune));
    out.insert("export_before_prune", json!(true));
    out.insert("editable", json!(false));
    out.insert(
        "reason",
        json!("Retention is reported in Community mode; destructive pruning requires an explicit export workflow."),
    );
    Ok(json_ok(&out))
}

async fn admin_product_hardening(
    State(state): State<AdminState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    require_admin(&state, &headers)?;
    match &state.hardening {
        Some(h) => Ok(json_ok(&h())),
        None => {
            let mut out = OrderedMap::new();
            out.insert("available", json!(false));
            out.insert(
                "reason",
                json!("Product hardening status provider is not configured."),
            );
            Ok(json_ok(&out))
        }
    }
}

async fn vpc_status(
    State(state): State<AdminState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    Ok(json_ok(&load_vpc_config(&state.data_dir)))
}

async fn admin_update_vpc(
    State(state): State<AdminState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Response, Response> {
    require_admin(&state, &headers)?;
    let parsed = parse_object_body(&body)?;
    let mut cfg = load_vpc_config(&state.data_dir);
    for (key, value) in parsed {
        if key == "private_subnets" {
            if let Some(list) = value.as_array() {
                let cleaned: Vec<Value> = list
                    .iter()
                    .filter_map(|s| s.as_str())
                    .map(str::trim)
                    .filter(|s| !s.is_empty())
                    .map(|s| json!(s))
                    .collect();
                cfg.insert(key, json!(cleaned));
                continue;
            }
        }
        cfg.insert(key, value);
    }
    save_vpc_config(&state.data_dir, cfg.clone());
    Ok(json_ok(&load_vpc_config(&state.data_dir)))
}

async fn admin_update_user(
    State(state): State<AdminState>,
    AxumPath(email): AxumPath<String>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Response, Response> {
    let lang = language_from(&headers);
    let (admin, users) = require_admin(&state, &headers)?;
    let email = email.trim_start_matches('/').to_string();
    let parsed = parse_object_body(&body)?;
    if users.get(&email).is_none() {
        return Err(message_error(404, "auth.user_not_found", lang, &[]));
    }
    let before = state
        .auth
        .public_user(&email, users.get(&email).unwrap(), &users);
    let mut record = users.get(&email).unwrap().clone();
    if let Some(role) = parsed.get("role") {
        if !role.is_null() {
            let role = role.as_str().unwrap_or("");
            if role != "admin" && role != "user" {
                return Err(message_error(400, "admin.invalid_role", lang, &[]));
            }
            record.insert("role".into(), json!(role));
        }
    }
    if let Some(disabled) = parsed.get("disabled") {
        if !disabled.is_null() {
            let flag = disabled.as_bool().unwrap_or(false);
            if email == admin.email && flag {
                return Err(message_error(400, "admin.cannot_disable_self", lang, &[]));
            }
            record.insert("disabled".into(), json!(flag));
        }
    }
    let mut next = users;
    next.insert(email.clone(), record);
    state.auth.users().save(&next);
    let after = state
        .auth
        .public_user(&email, next.get(&email).unwrap(), &next);
    let mut payload = Map::new();
    payload.insert("user_email".into(), json!(admin.email));
    payload.insert("target_email".into(), json!(email));
    payload.insert("before".into(), json_from_ordered(&before));
    payload.insert("after".into(), json_from_ordered(&after));
    append_audit_event(&audit_log_path(&state.data_dir), "user_update", payload);
    Ok(json_ok(&after))
}

async fn admin_delete_user(
    State(state): State<AdminState>,
    AxumPath(email): AxumPath<String>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    let lang = language_from(&headers);
    let (admin, users) = require_admin(&state, &headers)?;
    let email = email.trim_start_matches('/').to_string();
    if email == admin.email {
        return Err(message_error(400, "admin.cannot_delete_self", lang, &[]));
    }
    let Some(record) = users.get(&email).cloned() else {
        return Err(message_error(404, "auth.user_not_found", lang, &[]));
    };
    let deleted = state.auth.public_user(&email, &record, &users);
    let mut payload = Map::new();
    payload.insert("user_email".into(), json!(admin.email));
    payload.insert("target_email".into(), json!(email));
    payload.insert("deleted_user".into(), json_from_ordered(&deleted));
    append_audit_event(&audit_log_path(&state.data_dir), "user_delete", payload);
    let mut next = lattice_auth::Users::new();
    for (e, rec) in users.iter() {
        if e != email {
            next.insert(e.to_string(), rec.clone());
        }
    }
    state.auth.users().save(&next);
    let mut out = OrderedMap::new();
    out.insert("status", json!("ok"));
    out.insert("deleted", json_from_ordered(&deleted));
    Ok(json_ok(&out))
}

async fn admin_invite_link(
    State(state): State<AdminState>,
    headers: HeaderMap,
    request: axum::extract::Request,
) -> Result<Response, Response> {
    require_admin(&state, &headers)?;
    let peer = request
        .extensions()
        .get::<ConnectInfo<SocketAddr>>()
        .map(|c| c.0.ip().to_string());
    let host = headers
        .get(header::HOST)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    let forwarded_host = headers
        .get("x-forwarded-host")
        .and_then(|v| v.to_str().ok());
    let forwarded_proto = headers
        .get("x-forwarded-proto")
        .and_then(|v| v.to_str().ok());
    let origin = external_origin(
        host,
        "http",
        forwarded_host,
        forwarded_proto,
        peer.as_deref(),
    )
    .unwrap_or_else(|| format!("http://localhost:{}", state.default_port));
    let url = if state.invite_gate_enabled {
        format!("{origin}/?code={}", state.invite_code)
    } else {
        format!("{origin}/")
    };
    let mut out = OrderedMap::new();
    out.insert("invite_url", json!(url));
    out.insert("invite_code", json!(state.invite_code));
    out.insert("gate_enabled", json!(state.invite_gate_enabled));
    Ok(json_ok(&out))
}

async fn admin_sso(
    State(state): State<AdminState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    require_admin(&state, &headers)?;
    let cfg = load_sso_config(&state.data_dir, state.default_port);
    Ok(json_ok(&public_sso_config(&cfg, state.default_port)))
}

async fn admin_update_sso(
    State(state): State<AdminState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Response, Response> {
    let (admin, _) = require_admin(&state, &headers)?;
    let update = parse_object_body(&body)?;
    let saved = save_sso_config(&state.data_dir, state.default_port, update);
    let mut payload = Map::new();
    payload.insert("user_email".into(), json!(admin.email));
    payload.insert(
        "provider_name".into(),
        saved.get("provider_name").cloned().unwrap_or(Value::Null),
    );
    payload.insert(
        "discovery_url".into(),
        saved.get("discovery_url").cloned().unwrap_or(Value::Null),
    );
    payload.insert(
        "enabled".into(),
        json!(saved
            .get("enabled")
            .and_then(Value::as_bool)
            .unwrap_or(false)),
    );
    append_audit_event(
        &audit_log_path(&state.data_dir),
        "sso_config_update",
        payload,
    );
    Ok(json_ok(&public_sso_config(&saved, state.default_port)))
}

async fn admin_enterprise(
    State(state): State<AdminState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    require_admin(&state, &headers)?;
    Ok(json_ok(&poc_overview()))
}

async fn admin_enterprise_siem(
    State(state): State<AdminState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    require_admin(&state, &headers)?;
    Ok(json_ok(&siem_export_stub()))
}

// ── internals ────────────────────────────────────────────────────────────────

fn filter_audit_log(
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

fn find_sensitive(content: &str) -> Vec<Map<String, Value>> {
    let rules: [(&str, &str, &str, &str); 8] = [
        ("rrn", "주민등록번호", "high", r"\b\d{6}[- ]?[1-4]\d{6}\b"),
        ("card", "카드번호", "high", r"\b(?:\d[ -]?){13,19}\b"),
        (
            "account",
            "계좌번호",
            "medium",
            r"(?:계좌|account|bank).{0,12}\d[\d -]{8,24}",
        ),
        (
            "password",
            "비밀번호/인증정보",
            "high",
            r"(?i)(?:password|passwd|비밀번호|암호|token|api[_ -]?key|secret)\s*[:=]\s*[^\s,;]{4,}",
        ),
        (
            "email",
            "이메일",
            "low",
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        ),
        (
            "phone",
            "전화번호",
            "medium",
            r"\b(?:01[016789]|02|0[3-6][1-5])[- ]?\d{3,4}[- ]?\d{4}\b",
        ),
        (
            "address",
            "주소",
            "medium",
            r"(?:[가-힣]+(?:시|도)\s*)?[가-힣]+(?:시|군|구)\s+[가-힣0-9\s-]+(?:로|길)\s*\d*",
        ),
        (
            "health",
            "건강/의료정보",
            "medium",
            r"(?i)(?:진단|병명|처방|복용|수술|장애|임신|혈액형|알레르기|medical|diagnosis)",
        ),
    ];
    let mut found = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for (key, label, severity, pat) in rules {
        let Ok(re) = Regex::new(pat) else {
            continue;
        };
        let mut start = 0;
        while start <= content.len() {
            let Ok(Some(m)) = re.find_from_pos(content, start) else {
                break;
            };
            let sig = (key, m.start(), m.end());
            if seen.insert(sig) {
                let mut row = Map::new();
                row.insert("type".into(), json!(key));
                row.insert("label".into(), json!(label));
                row.insert("severity".into(), json!(severity));
                // Python `re.Match.start/end` are character offsets.
                row.insert("start".into(), json!(char_offset(content, m.start())));
                row.insert("end".into(), json!(char_offset(content, m.end())));
                found.push(row);
            }
            if m.end() == start {
                start += 1;
            } else {
                start = m.end();
            }
        }
    }
    found
}

fn char_offset(text: &str, byte: usize) -> usize {
    text.get(..byte.min(text.len()))
        .map(|slice| slice.chars().count())
        .unwrap_or_else(|| text.chars().count())
}

fn mask_sensitive_text(text: &str, matches: &[&Map<String, Value>]) -> String {
    let mut ranges: Vec<(usize, usize)> = matches
        .iter()
        .filter_map(|m| {
            Some((
                m.get("start")?.as_u64()? as usize,
                m.get("end")?.as_u64()? as usize,
            ))
        })
        .collect();
    ranges.sort_by(|a, b| b.0.cmp(&a.0));
    let mut chars: Vec<char> = text.chars().collect();
    for (start, end) in ranges {
        if start >= chars.len() || end > chars.len() || start >= end {
            continue;
        }
        let value: String = chars[start..end].iter().collect();
        let replacement: Vec<char> = if value.chars().count() <= 4 {
            vec!['*'; value.chars().count()]
        } else {
            let value_chars: Vec<char> = value.chars().collect();
            let mid = (value_chars.len() - 4).min(12);
            let mut out = Vec::with_capacity(2 + mid + 2);
            out.extend_from_slice(&value_chars[..2]);
            out.extend(std::iter::repeat('*').take(mid));
            out.extend_from_slice(&value_chars[value_chars.len() - 2..]);
            out
        };
        chars.splice(start..end, replacement);
    }
    chars.into_iter().collect()
}

fn severity_score(s: &str) -> i32 {
    match s {
        "low" => 1,
        "medium" => 2,
        "high" => 3,
        _ => 0,
    }
}

fn bump(map: &mut OrderedMap, key: &str) {
    let next = map.get(key).and_then(Value::as_u64).unwrap_or(0) + 1;
    map.insert(key, json!(next));
}

fn tail_maps(items: &[&OrderedMap], n: usize) -> Vec<Value> {
    let start = items.len().saturating_sub(n);
    items[start..]
        .iter()
        .map(|m| json_from_ordered(m))
        .collect()
}

pub fn json_from_ordered(map: &OrderedMap) -> Value {
    serde_json::to_value(map).unwrap_or(json!({}))
}

fn value_as_string(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

fn which(name: &str) -> bool {
    std::env::var_os("PATH")
        .map(|paths| {
            std::env::split_paths(&paths).any(|dir| {
                let p = dir.join(name);
                p.is_file()
            })
        })
        .unwrap_or(false)
}

fn parse_object_body(bytes: &[u8]) -> Result<Map<String, Value>, Response> {
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

fn query_bound_error(kind: &str, ctx_key: &str, bound: i64, input: &str) -> Response {
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

fn sorted_json(value: &Value) -> Value {
    match value {
        Value::Object(map) => {
            let mut keys: Vec<_> = map.keys().cloned().collect();
            keys.sort();
            let mut out = Map::new();
            for k in keys {
                if let Some(v) = map.get(&k) {
                    out.insert(k, sorted_json(v));
                }
            }
            Value::Object(out)
        }
        Value::Array(items) => Value::Array(items.iter().map(sorted_json).collect()),
        other => other.clone(),
    }
}

fn audit_event_contract(event: &Map<String, Value>) -> Value {
    let event_type = event
        .get("event_type")
        .and_then(Value::as_str)
        .unwrap_or("event");
    let ts = event.get("timestamp").cloned().unwrap_or(Value::Null);
    let identity = event.get("event_id").cloned().unwrap_or_else(|| {
        if let Some(t) = ts.as_str() {
            json!(format!("{event_type}@{t}"))
        } else {
            json!(event_type)
        }
    });
    let mut timeline = OrderedMap::new();
    timeline.insert("event", json!(event_type));
    timeline.insert("timestamp", ts.clone());
    timeline.insert(
        "status",
        event
            .get("status")
            .cloned()
            .unwrap_or_else(|| json!(event_type)),
    );
    let mut artifact = OrderedMap::new();
    artifact.insert("type", json!("audit_payload"));
    artifact.insert("payload", Value::Object(event.clone()));
    let mut body = OrderedMap::new();
    body.insert(
        "run_id",
        event.get("run_id").cloned().unwrap_or(Value::Null),
    );
    body.insert(
        "agent_id",
        json!(event
            .get("agent_id")
            .and_then(Value::as_str)
            .or_else(|| event.get("workflow_id").and_then(Value::as_str))
            .map(str::to_string)
            .unwrap_or_else(|| format!("audit:{event_type}"))),
    );
    body.insert("runtime", json!("audit"));
    body.insert("mode", json!("event"));
    body.insert("goal", json!(event_type));
    body.insert("roles", json!([]));
    body.insert("current_role", Value::Null);
    body.insert(
        "retries",
        json!(event.get("retries").and_then(Value::as_i64).unwrap_or(0)),
    );
    body.insert("timeline", json!([json_from_ordered(&timeline)]));
    body.insert("artifacts", json!([json_from_ordered(&artifact)]));
    body.insert("blocking_reasons", json!([]));
    body.insert("is_terminal", json!(true));
    body.insert("family", json!("agent-run-contract/v1"));
    body.insert("schema_version", json!("audit-event-contract/v1"));
    body.insert("kind", json!("audit_event"));
    body.insert("id", identity);
    body.insert("status", json!(event_type));
    body.insert("timestamp", ts);
    json_from_ordered(&body)
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

fn unix_now() -> u64 {
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

fn parse_timestamp_unix(value: &str) -> Option<u64> {
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

fn external_origin(
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
