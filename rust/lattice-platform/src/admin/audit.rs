//! Audit log load/append and sensitivity reports.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use fancy_regex::Regex;
use lattice_auth::{AuthState, OrderedMap};
use lattice_core::db::tables::state_files;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

use super::internal::{json_from_ordered, now_iso, value_as_string};
use super::redact::redact_secrets;

const AUDIT_CAP: usize = 5000;
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
    ranges.sort_by_key(|range| std::cmp::Reverse(range.0));
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
            out.extend(std::iter::repeat_n('*', mid));
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
