use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Map, Value};

#[cfg(test)]
use super::constants::COMPUTER_MEMORY_NOTICE;
use super::constants::DEFAULT_COMPUTER_MEMORY_SCOPES;
use super::pyutil::{json_hash_prefix, now_iso};
use super::store::{StoreError, WorkspaceOsStore};

pub fn config(store: &WorkspaceOsStore) -> Value {
    store
        .load_state()
        .get("computer_memory")
        .cloned()
        .unwrap_or(Value::Null)
}

pub fn configure(
    store: &WorkspaceOsStore,
    enabled: bool,
    approved_by: Option<&str>,
    consent: &Value,
    scopes: &[Value],
) -> Result<Value, StoreError> {
    let consent = if consent.is_object() {
        consent.clone()
    } else {
        json!({})
    };
    let approved = consent
        .get("approved")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    if enabled && !approved {
        return Err(StoreError::Permission(
            "Local Computer Memory requires explicit approval.".into(),
        ));
    }
    let stored = store.mutate(|state| {
        let object = state.as_object_mut().expect("state document is an object");
        let block = object.entry("computer_memory").or_insert_with(|| json!({}));
        if !block.is_object() {
            *block = json!({});
        }
        let previous_at = block.get("approved_at").cloned().unwrap_or(Value::Null);
        let previous_by = block.get("approved_by").cloned().unwrap_or(Value::Null);
        let previous_scopes = block.get("scopes").cloned();
        block["enabled"] = json!(enabled);
        block["approved"] = json!(enabled);
        block["approved_at"] = if enabled {
            json!(now_iso())
        } else {
            previous_at
        };
        block["approved_by"] = if enabled {
            approved_by.map_or(Value::Null, |email| json!(email))
        } else {
            previous_by
        };
        block["scopes"] = match (scopes.is_empty(), previous_scopes) {
            (false, _) => Value::Array(scopes.to_vec()),
            (true, Some(Value::Array(kept))) if !kept.is_empty() => Value::Array(kept),
            _ => json!(DEFAULT_COMPUTER_MEMORY_SCOPES),
        };
        block["consent"] = consent.clone();
        let stored = block.clone();
        let flags = object.entry("feature_flags").or_insert_with(|| json!({}));
        if !flags.is_object() {
            *flags = json!({});
        }
        flags["local_computer_memory"] = json!(enabled);
        Ok(stored)
    })?;
    store.record_timeline_event(
        "memory",
        "computer_memory_configured",
        json!({"enabled": enabled, "approved_by": approved_by}),
        None,
    );
    Ok(stored)
}

pub fn plan_activity(activity: &Value) -> Value {
    let now = now_iso();
    let mut record = Map::new();
    record.insert(
        "id".into(),
        json!(format!(
            "activity-{}",
            json_hash_prefix(&json!([activity, now]), 16)
        )),
    );
    record.insert("timestamp".into(), json!(now));
    if let Value::Object(map) = activity {
        for (key, value) in map {
            record.insert(key.clone(), value.clone());
        }
    }
    Value::Object(record)
}

pub fn activity_title(activity: &Value) -> String {
    let text = activity
        .get("summary")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .or_else(|| {
            activity
                .get("path")
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty())
        })
        .unwrap_or("Computer activity");
    text.chars().take(120).collect()
}

pub fn record_activity(
    store: &WorkspaceOsStore,
    mut record: Value,
    graph: Option<Result<Value, String>>,
) -> Result<Value, StoreError> {
    if !store
        .load_state()
        .get("computer_memory")
        .and_then(|block| block.get("enabled"))
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        return Ok(json!({"status": "ignored",
                         "reason": "local computer memory is disabled"}));
    }
    if let Some(Err(error)) = graph {
        record["graph_error"] = json!(error);
    }
    let activity_id = record
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let stored = record.clone();
    store.mutate(|state| {
        let block = state
            .as_object_mut()
            .expect("state document is an object")
            .entry("computer_memory")
            .or_insert_with(|| json!({}));
        let activities = block
            .as_object_mut()
            .expect("computer_memory is an object")
            .entry("activities")
            .or_insert_with(|| json!([]));
        if let Some(list) = activities.as_array_mut() {
            list.push(stored);
        }
        Ok(())
    })?;
    store.record_timeline_event(
        "memory",
        "computer_activity",
        json!({"activity_id": activity_id}),
        None,
    );
    Ok(json!({"status": "ok", "activity": record}))
}

pub const PRESENCE_WINDOW_MS: i64 = 60_000;

#[derive(Debug, Clone)]
pub struct VsCodePresence {
    record: Arc<Mutex<Map<String, Value>>>,
}

impl Default for VsCodePresence {
    fn default() -> Self {
        let mut record = Map::new();
        record.insert("connected".into(), json!(false));
        record.insert("status".into(), json!("offline"));
        record.insert("index_status".into(), json!("unknown"));
        record.insert("last_seen_ms".into(), json!(0));
        Self {
            record: Arc::new(Mutex::new(record)),
        }
    }
}

impl VsCodePresence {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn status(&self, now_ms: i64) -> Value {
        let record = self.record.lock().unwrap_or_else(|error| {
            self.record.clear_poison();
            error.into_inner()
        });
        let last_seen = record
            .get("last_seen_ms")
            .and_then(Value::as_i64)
            .unwrap_or(0);
        let connected = last_seen > 0 && (now_ms - last_seen) < PRESENCE_WINDOW_MS;
        let mut payload = record.clone();
        payload.insert("connected".into(), json!(connected));
        let status = if connected {
            record.get("status").cloned().unwrap_or(Value::Null)
        } else {
            json!("offline")
        };
        payload.insert("status".into(), status);
        Value::Object(payload)
    }

    #[allow(clippy::too_many_arguments)]
    pub fn report(
        &self,
        now_ms: i64,
        status: &str,
        index_status: &str,
        workspace_folder: &str,
        extension_version: &str,
        active_file: &str,
        detail: &str,
        user_email: &str,
    ) -> Value {
        self.update(|record| {
            record.insert("connected".into(), json!(true));
            record.insert(
                "status".into(),
                json!(if status.is_empty() {
                    "connected"
                } else {
                    status
                }),
            );
            record.insert(
                "index_status".into(),
                json!(if index_status.is_empty() {
                    "unknown"
                } else {
                    index_status
                }),
            );
            record.insert("workspace_folder".into(), json!(workspace_folder));
            record.insert("extension_version".into(), json!(extension_version));
            record.insert("active_file".into(), json!(active_file));
            record.insert("detail".into(), json!(detail));
            record.insert("last_seen_ms".into(), json!(now_ms));
            record.insert("user_email".into(), json!(user_email));
        })
    }

    pub fn synced(
        &self,
        now_ms: i64,
        workspace_folder: &str,
        extension_version: &str,
        active_file: &str,
        user_email: &str,
    ) -> Value {
        self.update(|record| {
            record.insert("connected".into(), json!(true));
            record.insert("status".into(), json!("synced"));
            record.insert("index_status".into(), json!("synced"));
            record.insert("workspace_folder".into(), json!(workspace_folder));
            record.insert("extension_version".into(), json!(extension_version));
            record.insert("active_file".into(), json!(active_file));
            record.insert("last_seen_ms".into(), json!(now_ms));
            record.insert("user_email".into(), json!(user_email));
        })
    }

    fn update(&self, body: impl FnOnce(&mut Map<String, Value>)) -> Value {
        let mut record = self.record.lock().unwrap_or_else(|error| {
            self.record.clear_poison();
            error.into_inner()
        });
        body(&mut record);
        Value::Object(record.clone())
    }
}

pub fn now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|elapsed| elapsed.as_millis() as i64)
        .unwrap_or(0)
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
    fn a_fresh_install_has_computer_memory_off_with_the_notice() {
        let (_dir, store) = store();
        let block = config(&store);
        assert_eq!(block["enabled"], json!(false));
        assert_eq!(block["approved"], json!(false));
        assert_eq!(block["scopes"], json!(DEFAULT_COMPUTER_MEMORY_SCOPES));
        assert_eq!(block["notice"], json!(COMPUTER_MEMORY_NOTICE));
        assert_eq!(block["activities"], json!([]));
    }

    #[test]
    fn enabling_without_consent_is_refused_and_writes_nothing() {
        let (_dir, store) = store();
        assert_eq!(
            configure(
                &store,
                true,
                Some("a@b.test"),
                &json!({}),
                &[json!("window_titles")]
            )
            .unwrap_err(),
            StoreError::Permission("Local Computer Memory requires explicit approval.".into())
        );
        assert_eq!(config(&store)["enabled"], json!(false));
        assert_eq!(
            store.load_state()["feature_flags"]["local_computer_memory"],
            json!(false)
        );
    }

    #[test]
    fn enabling_with_consent_records_who_when_and_what() {
        let (_dir, store) = store();
        let block = configure(
            &store,
            true,
            Some("owner@lattice.test"),
            &json!({"approved": true, "by": "owner"}),
            &[json!("Documents")],
        )
        .unwrap();
        assert_eq!(block["enabled"], json!(true));
        assert_eq!(block["approved"], json!(true));
        assert_eq!(block["approved_by"], json!("owner@lattice.test"));
        assert!(block["approved_at"].is_string());
        assert_eq!(block["scopes"], json!(["Documents"]));
        assert_eq!(block["consent"], json!({"approved": true, "by": "owner"}));
        assert_eq!(
            store.load_state()["feature_flags"]["local_computer_memory"],
            json!(true)
        );
    }

    #[test]
    fn disabling_keeps_the_previous_approval_trail_and_scopes() {
        let (_dir, store) = store();
        configure(
            &store,
            true,
            Some("owner@lattice.test"),
            &json!({"approved": true}),
            &[json!("Documents")],
        )
        .unwrap();
        let block = configure(&store, false, None, &json!({}), &[]).unwrap();
        assert_eq!(block["enabled"], json!(false));
        assert_eq!(block["approved"], json!(false));
        assert_eq!(block["approved_by"], json!("owner@lattice.test"));
        assert_eq!(block["scopes"], json!(["Documents"]));
        assert_eq!(
            store.load_state()["feature_flags"]["local_computer_memory"],
            json!(false)
        );
    }

    #[test]
    fn activity_is_ignored_while_the_feature_is_off() {
        let (_dir, store) = store();
        let answer =
            record_activity(&store, plan_activity(&json!({"kind": "window"})), None).unwrap();
        assert_eq!(answer["status"], json!("ignored"));
        assert_eq!(answer["reason"], json!("local computer memory is disabled"));
        assert!(config(&store)["activities"].as_array().unwrap().is_empty());
    }

    #[test]
    fn activity_is_stored_once_enabled_and_carries_a_graph_error() {
        let (_dir, store) = store();
        configure(&store, true, None, &json!({"approved": true}), &[]).unwrap();
        let planned = plan_activity(&json!({"kind": "window", "title": "Terminal"}));
        assert!(planned["id"].as_str().unwrap().starts_with("activity-"));
        assert_eq!(planned["title"], json!("Terminal"));
        let answer = record_activity(&store, planned, Some(Err("seam down".into()))).unwrap();
        assert_eq!(answer["status"], json!("ok"));
        assert_eq!(answer["activity"]["graph_error"], json!("seam down"));
        assert_eq!(config(&store)["activities"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn the_activity_title_prefers_summary_then_path_then_a_default() {
        assert_eq!(activity_title(&json!({"summary": "s", "path": "p"})), "s");
        assert_eq!(activity_title(&json!({"path": "p"})), "p");
        assert_eq!(activity_title(&json!({})), "Computer activity");
        assert_eq!(
            activity_title(&json!({"summary": "가".repeat(200)}))
                .chars()
                .count(),
            120
        );
    }

    #[test]
    fn presence_starts_offline_and_reports_connected_within_the_window() {
        let presence = VsCodePresence::new();
        let idle = presence.status(1_000_000);
        assert_eq!(idle["connected"], json!(false));
        assert_eq!(idle["status"], json!("offline"));
        assert_eq!(idle["index_status"], json!("unknown"));

        let reported = presence.report(
            1_000_000,
            "connected",
            "ok",
            "/workspace/fixture",
            "11.5.2",
            "src/main.rs",
            "",
            "owner@lattice.test",
        );
        assert_eq!(reported["connected"], json!(true));
        assert_eq!(reported["status"], json!("connected"));
        assert_eq!(reported["last_seen_ms"], json!(1_000_000));
        assert_eq!(reported["user_email"], json!("owner@lattice.test"));

        assert_eq!(presence.status(1_030_000)["connected"], json!(true));
        assert_eq!(presence.status(1_030_000)["status"], json!("connected"));
        let stale = presence.status(1_070_000);
        assert_eq!(stale["connected"], json!(false));
        assert_eq!(stale["status"], json!("offline"));
        assert_eq!(stale["active_file"], json!("src/main.rs"));
    }

    #[test]
    fn empty_status_fields_fall_back_the_way_python_does() {
        let presence = VsCodePresence::new();
        let reported = presence.report(1, "", "", "", "", "", "", "");
        assert_eq!(reported["status"], json!("connected"));
        assert_eq!(reported["index_status"], json!("unknown"));
    }

    #[test]
    fn sending_from_the_editor_marks_the_presence_synced() {
        let presence = VsCodePresence::new();
        let synced = presence.synced(5_000, "/w", "11.5.2", "src/lib.rs", "owner@lattice.test");
        assert_eq!(synced["status"], json!("synced"));
        assert_eq!(synced["index_status"], json!("synced"));
        assert_eq!(synced["active_file"], json!("src/lib.rs"));
        assert!(now_ms() > 1_700_000_000_000);
    }
}
