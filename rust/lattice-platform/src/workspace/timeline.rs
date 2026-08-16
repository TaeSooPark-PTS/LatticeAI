//! The Time Machine feed and the admin audit timeline.
//!
//! Port of `core/workspace_timeline.py`. Two readers over the same idea: one
//! merges every workspace-scoped record into a single reverse-chronological
//! feed, the other filters the *audit* log and labels each event with a
//! category.
//!
//! Both sort descending by an ISO timestamp **string**, and Python's sort is
//! stable, so records sharing a second keep their insertion order. Rust's
//! `sort_by` is stable too, which is why the comparison is written as
//! `right.cmp(left)` rather than sorting ascending and reversing — reversing
//! would also reverse the ties, and the fixtures were captured from a run where
//! several events share a second.

use serde_json::{json, Map, Value};

use super::pyutil::listify;
use super::store::WorkspaceOsStore;

/// `_audit_category` — the label the admin timeline groups by.
///
/// First match wins, in the order Python tests them; `admin_view_sensitive_raw`
/// contains both `admin` and `sensitive`, and lands in `sensitive_data` because
/// that test comes first.
pub fn audit_category(event: &Value) -> &'static str {
    let raw = event
        .get("event_type")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_lowercase();
    let has = |needle: &str| raw.contains(needle);
    if has("model") || has("chat") {
        "model_usage"
    } else if has("file") || has("document") || has("local") {
        "file_access"
    } else if has("folder") || has("permission") {
        "folder_approval"
    } else if has("sensitive") || has("secret") {
        "sensitive_data"
    } else if has("admin") || has("user") || has("sso") {
        "admin_action"
    } else if has("security") || has("auth") || has("login") {
        "security_event"
    } else {
        "workspace_event"
    }
}

/// The filters `GET /workspace/audit-timeline` accepts.
#[derive(Debug, Default, Clone)]
pub struct AuditFilter {
    /// Substring of `user_email` (or `user`).
    pub user: Option<String>,
    /// Substring of `event_type`.
    pub event_type: Option<String>,
    /// Substring of the whole event, as Python's `str(event)` renders it.
    pub model: Option<String>,
    /// Lower bound on `timestamp`.
    pub since: Option<String>,
    /// Upper bound on `timestamp`.
    pub until: Option<String>,
    /// How many events to answer with; clamped to `1..=1000`.
    pub limit: i64,
}

/// `filter_audit_timeline`.
pub fn filter_audit_timeline(audit_events: &[Value], filter: &AuditFilter) -> Value {
    let since = lattice_core::parse_iso(filter.since.as_deref());
    let until = lattice_core::parse_iso(filter.until.as_deref());
    let lower = |value: &Option<String>| {
        value
            .as_deref()
            .filter(|text| !text.is_empty())
            .map(str::to_lowercase)
    };
    let user = lower(&filter.user);
    let event_type = lower(&filter.event_type);
    let model = lower(&filter.model);

    let mut filtered: Vec<Value> = Vec::new();
    for event in audit_events {
        let stamp = lattice_core::parse_iso(event.get("timestamp").and_then(Value::as_str));
        if let Some(needle) = &user {
            let who = event
                .get("user_email")
                .and_then(Value::as_str)
                .or_else(|| event.get("user").and_then(Value::as_str))
                .unwrap_or_default()
                .to_lowercase();
            if !who.contains(needle) {
                continue;
            }
        }
        if let Some(needle) = &event_type {
            let kind = event
                .get("event_type")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_lowercase();
            if !kind.contains(needle) {
                continue;
            }
        }
        if let Some(needle) = &model {
            if !py_repr(event).to_lowercase().contains(needle) {
                continue;
            }
        }
        if let (Some(since), Some(stamp)) = (since, stamp) {
            if stamp < since {
                continue;
            }
        }
        if let (Some(until), Some(stamp)) = (until, stamp) {
            if stamp > until {
                continue;
            }
        }
        let mut labelled = match event {
            Value::Object(map) => map.clone(),
            _ => Map::new(),
        };
        labelled.insert("category".into(), json!(audit_category(event)));
        filtered.push(Value::Object(labelled));
    }
    sort_newest_first(&mut filtered);
    let total = filtered.len();
    filtered.truncate(filter.limit.clamp(1, 1_000) as usize);
    json!({"events": filtered, "total": total})
}

/// `timeline()` — every workspace-scoped record as one feed.
pub fn timeline(
    store: &WorkspaceOsStore,
    audit_events: &[Value],
    limit: i64,
    workspace_id: Option<&str>,
) -> Value {
    let state = store.load_state();
    let scoped = |key: &str| WorkspaceOsStore::scoped(listify(state.get(key)), workspace_id);
    let mut events: Vec<Value> = scoped("timeline");

    let wrap = |area: &str, event_type: &str, stamp_key: &str, record: &Value| {
        json!({
            "area": area,
            "event_type": event_type,
            "timestamp": record.get(stamp_key).cloned().unwrap_or(Value::Null),
            "workspace_id": WorkspaceOsStore::record_workspace(record),
            "payload": record,
        })
    };
    for snapshot in scoped("snapshots") {
        events.push(wrap("snapshot", "snapshot", "created_at", &snapshot));
    }
    for trace in scoped("traces") {
        events.push(wrap("graph", "answer_trace", "created_at", &trace));
    }
    for run in scoped("agent_runs") {
        events.push(wrap("agent", "agent_run", "created_at", &run));
    }
    for workflow in scoped("workflows") {
        events.push(wrap("workflow", "workflow", "created_at", &workflow));
    }
    for audit in audit_events {
        events.push(json!({
            "area": "audit",
            "event_type": audit
                .get("event_type")
                .filter(|value| !value.is_null())
                .cloned()
                .unwrap_or_else(|| json!("audit")),
            "timestamp": audit.get("timestamp").cloned().unwrap_or(Value::Null),
            "payload": audit,
        }));
    }
    sort_newest_first(&mut events);
    events.truncate(limit.clamp(1, 500) as usize);
    json!({"events": events})
}

/// `list_traces` — newest first, optionally filtered by conversation.
pub fn list_traces(
    store: &WorkspaceOsStore,
    conversation_id: Option<&str>,
    limit: i64,
    workspace_id: Option<&str>,
) -> Value {
    let state = store.load_state();
    let mut traces = WorkspaceOsStore::scoped(listify(state.get("traces")), workspace_id);
    if let Some(conversation) = conversation_id.filter(|value| !value.is_empty()) {
        traces.retain(|trace| {
            trace.get("conversation_id").and_then(Value::as_str) == Some(conversation)
        });
    }
    let cap = limit.clamp(1, 200) as usize;
    if traces.len() > cap {
        traces = traces.split_off(traces.len() - cap);
    }
    traces.reverse();
    json!({"traces": traces})
}

/// `sort(key=lambda item: item.get("timestamp") or "", reverse=True)`.
fn sort_newest_first(events: &mut [Value]) {
    events.sort_by(|left, right| {
        let key = |item: &Value| {
            item.get("timestamp")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string()
        };
        key(right).cmp(&key(left))
    });
}

/// `str(dict)` — CPython's `repr`, which the `model` filter searches.
///
/// Only the `model` filter uses it, and only as a haystack for a lowercase
/// substring test, but the haystack is what decides whether an event matches:
/// `{'model': 'local:test'}` contains `local:test` with single quotes around
/// it, and a JSON rendering would not put the quotes in the same places.
fn py_repr(value: &Value) -> String {
    match value {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Number(number) => number.to_string(),
        Value::String(text) => py_repr_str(text),
        Value::Array(items) => format!(
            "[{}]",
            items.iter().map(py_repr).collect::<Vec<_>>().join(", ")
        ),
        Value::Object(map) => format!(
            "{{{}}}",
            map.iter()
                .map(|(key, item)| format!("{}: {}", py_repr_str(key), py_repr(item)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
    }
}

fn py_repr_str(text: &str) -> String {
    if text.contains('\'') && !text.contains('"') {
        format!("\"{text}\"")
    } else {
        format!("'{}'", text.replace('\\', "\\\\").replace('\'', "\\'"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn store() -> (tempfile::TempDir, WorkspaceOsStore) {
        let dir = tempfile::tempdir().expect("tempdir");
        let store = WorkspaceOsStore::open(dir.path());
        (dir, store)
    }

    #[test]
    fn every_category_branch_is_reachable_and_ordered() {
        let of = |event_type: &str| audit_category(&json!({"event_type": event_type}));
        assert_eq!(of("model_switch"), "model_usage");
        assert_eq!(of("chat_turn"), "model_usage");
        assert_eq!(of("document_upload"), "file_access");
        assert_eq!(of("local_read"), "file_access");
        assert_eq!(of("folder_approved"), "folder_approval");
        assert_eq!(of("permission_granted"), "folder_approval");
        assert_eq!(of("secret_seen"), "sensitive_data");
        // `admin_view_sensitive_raw` matches both `sensitive` and `admin`;
        // `sensitive` is tested first, so it wins.
        assert_eq!(of("admin_view_sensitive_raw"), "sensitive_data");
        assert_eq!(of("user_created"), "admin_action");
        assert_eq!(of("sso_configured"), "admin_action");
        assert_eq!(of("login_failed"), "security_event");
        assert_eq!(of("workspace_created"), "workspace_event");
        assert_eq!(audit_category(&json!({})), "workspace_event");
    }

    #[test]
    fn the_audit_filter_matches_substrings_and_reports_the_untruncated_total() {
        let events = vec![
            json!({"event_type": "workspace_created", "user_email": "owner@lattice.test",
                   "timestamp": "2026-08-01T10:00:00"}),
            json!({"event_type": "workspace_created", "user_email": "other@lattice.test",
                   "timestamp": "2026-08-02T10:00:00"}),
            json!({"event_type": "login", "user": "owner@lattice.test",
                   "timestamp": "2026-08-03T10:00:00"}),
        ];
        let all = filter_audit_timeline(
            &events,
            &AuditFilter {
                limit: 100,
                ..Default::default()
            },
        );
        assert_eq!(all["total"], json!(3));
        // Newest first.
        assert_eq!(all["events"][0]["event_type"], json!("login"));
        assert_eq!(all["events"][0]["category"], json!("security_event"));

        let by_user = filter_audit_timeline(
            &events,
            &AuditFilter {
                user: Some("OWNER@lattice.test".into()),
                limit: 100,
                ..Default::default()
            },
        );
        assert_eq!(by_user["total"], json!(2));

        let by_type = filter_audit_timeline(
            &events,
            &AuditFilter {
                event_type: Some("workspace_created".into()),
                limit: 10,
                ..Default::default()
            },
        );
        assert_eq!(by_type["total"], json!(2));
        assert_eq!(by_type["events"][0]["workspace_id"], Value::Null);
    }

    #[test]
    fn the_since_until_window_bounds_the_feed_and_the_limit_clamps() {
        let events: Vec<Value> = (1..=5)
            .map(|day| json!({"event_type": "x", "timestamp": format!("2026-08-0{day}T00:00:00")}))
            .collect();
        let windowed = filter_audit_timeline(
            &events,
            &AuditFilter {
                since: Some("2026-08-02T00:00:00".into()),
                until: Some("2026-08-04T00:00:00".into()),
                limit: 100,
                ..Default::default()
            },
        );
        assert_eq!(windowed["total"], json!(3));
        let clamped = filter_audit_timeline(
            &events,
            &AuditFilter {
                limit: 0,
                ..Default::default()
            },
        );
        assert_eq!(clamped["events"].as_array().unwrap().len(), 1);
        assert_eq!(clamped["total"], json!(5));
        let huge = filter_audit_timeline(
            &events,
            &AuditFilter {
                limit: 99_999,
                ..Default::default()
            },
        );
        assert_eq!(huge["events"].as_array().unwrap().len(), 5);
    }

    #[test]
    fn the_model_filter_searches_the_python_repr_of_the_event() {
        let events = vec![
            json!({"event_type": "chat", "model": "local:test", "timestamp": "2026-08-01T00:00:00"}),
            json!({"event_type": "chat", "model": "openai:gpt", "timestamp": "2026-08-02T00:00:00"}),
        ];
        let matched = filter_audit_timeline(
            &events,
            &AuditFilter {
                model: Some("local:test".into()),
                limit: 10,
                ..Default::default()
            },
        );
        assert_eq!(matched["total"], json!(1));
        assert_eq!(
            py_repr(&json!({"a": 1, "b": [true, null, "x"]})),
            "{'a': 1, 'b': [True, None, 'x']}"
        );
        assert_eq!(py_repr(&json!("it's")), "\"it's\"");
        assert_eq!(py_repr(&json!("say \"hi\" it's")), "'say \"hi\" it\\'s'");
    }

    #[test]
    fn the_feed_merges_every_branch_and_scopes_them() {
        let (_dir, store) = store();
        store
            .mutate(|state| {
                state["timeline"] = json!([
                    {"area": "workspace", "event_type": "seed", "timestamp": "2026-08-01T00:00:01",
                     "payload": {}, "workspace_id": "personal"}
                ]);
                state["snapshots"] = json!([{"id": "s1", "created_at": "2026-08-01T00:00:02",
                                             "workspace_id": "personal"}]);
                state["traces"] = json!([{"id": "t1", "created_at": "2026-08-01T00:00:03"}]);
                state["agent_runs"] = json!([{"id": "r1", "created_at": "2026-08-01T00:00:04",
                                              "workspace_id": "org-x"}]);
                state["workflows"] = json!([{"id": "w1", "created_at": "2026-08-01T00:00:05",
                                             "workspace_id": "personal"}]);
                Ok(())
            })
            .unwrap();
        let audit = vec![json!({"event_type": "login", "timestamp": "2026-08-01T00:00:06"})];

        let feed = timeline(&store, &audit, 100, None);
        let events = feed["events"].as_array().unwrap();
        assert_eq!(events.len(), 6);
        assert_eq!(events[0]["area"], json!("audit"));
        assert_eq!(events[1]["area"], json!("workflow"));
        assert_eq!(events[2]["area"], json!("agent"));
        assert_eq!(events[2]["workspace_id"], json!("org-x"));
        assert_eq!(events[3]["area"], json!("graph"));
        assert_eq!(events[4]["area"], json!("snapshot"));
        assert_eq!(events[4]["payload"]["id"], json!("s1"));

        // Scoped: the org run drops out, the audit events never do.
        let personal = timeline(&store, &audit, 100, Some("personal"));
        assert_eq!(personal["events"].as_array().unwrap().len(), 5);
        // An event with no `event_type` falls back to "audit".
        let untyped = timeline(
            &store,
            &[json!({"timestamp": "2026-09-01T00:00:00"})],
            1,
            None,
        );
        assert_eq!(untyped["events"][0]["event_type"], json!("audit"));
    }

    #[test]
    fn the_feed_limit_clamps_the_same_way() {
        let (_dir, store) = store();
        let audit: Vec<Value> = (0..600)
            .map(|index| json!({"event_type": "x", "timestamp": format!("2026-08-01T00:00:{index:02}")}))
            .collect();
        assert_eq!(
            timeline(&store, &audit, 9_999, None)["events"]
                .as_array()
                .unwrap()
                .len(),
            500
        );
        assert_eq!(
            timeline(&store, &audit, -3, None)["events"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
    }
}
