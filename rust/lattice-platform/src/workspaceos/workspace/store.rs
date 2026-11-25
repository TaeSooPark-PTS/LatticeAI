use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, Weak};

use lattice_core::db::tables::{self, state_files};
use serde_json::{json, Map, Value};

use super::constants::{
    DEFAULT_WORKSPACE_ID, EXECUTION_EVENT_TYPES, WORKSPACE_AREAS, WORKSPACE_OS_VERSION,
};
use super::pyutil::{deep_merge, listify, now_iso};
use super::state::{default_state, migrate_workspaces};

pub const STATE_FILE_NAME: &str = state_files::WORKSPACE_OS;
pub const SNAPSHOTS_DIR_NAME: &str = state_files::WORKSPACE_SNAPSHOTS;
pub const EXPORTS_DIR_NAME: &str = state_files::WORKSPACE_EXPORTS;
pub const GRAPH_DB_FILE_NAME: &str = tables::GRAPH_DB;

const TIMELINE_CAP: usize = 10_000;
const TIMELINE_TRIM_TO: usize = 8_000;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StoreError {
    NotFound(String),
    Value(String),
    Permission(String),
}

impl std::fmt::Display for StoreError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::NotFound(detail) | Self::Value(detail) | Self::Permission(detail) => {
                formatter.write_str(detail)
            }
        }
    }
}

pub type EventSink = Arc<dyn Fn(&Value) + Send + Sync>;

pub struct WorkspaceOsStore {
    data_dir: PathBuf,
    state_path: PathBuf,
    sqlite_path: PathBuf,
    snapshots_dir: PathBuf,
    exports_dir: PathBuf,
    write_lock: Mutex<()>,
    event_sink: Option<EventSink>,
}

impl WorkspaceOsStore {
    pub fn open(data_dir: &Path) -> Self {
        let data_dir = data_dir.to_path_buf();
        let snapshots_dir = data_dir.join(SNAPSHOTS_DIR_NAME);
        let exports_dir = data_dir.join(EXPORTS_DIR_NAME);
        for directory in [&data_dir, &snapshots_dir, &exports_dir] {
            let _ = std::fs::create_dir_all(directory);
        }
        Self {
            state_path: data_dir.join(STATE_FILE_NAME),
            sqlite_path: data_dir.join(GRAPH_DB_FILE_NAME),
            snapshots_dir,
            exports_dir,
            data_dir,
            write_lock: Mutex::new(()),
            event_sink: None,
        }
    }

    pub fn shared(data_dir: &Path) -> Arc<Self> {
        static REGISTRY: Mutex<Option<HashMap<PathBuf, Weak<WorkspaceOsStore>>>> = Mutex::new(None);
        let _ = std::fs::create_dir_all(data_dir);
        let key = std::fs::canonicalize(data_dir).unwrap_or_else(|_| data_dir.to_path_buf());
        let mut guard = REGISTRY.lock().unwrap_or_else(|error| {
            REGISTRY.clear_poison();
            error.into_inner()
        });
        let registry = guard.get_or_insert_with(HashMap::new);
        if let Some(live) = registry.get(&key).and_then(Weak::upgrade) {
            return live;
        }
        let store = Arc::new(Self::open(data_dir));
        registry.insert(key, Arc::downgrade(&store));
        registry.retain(|_, weak| weak.strong_count() > 0);
        store
    }

    pub fn with_event_sink(mut self, sink: EventSink) -> Self {
        self.event_sink = Some(sink);
        self
    }

    pub fn data_dir(&self) -> &Path {
        &self.data_dir
    }
    pub fn state_path(&self) -> &Path {
        &self.state_path
    }
    pub fn snapshots_dir(&self) -> &Path {
        &self.snapshots_dir
    }
    pub fn exports_dir(&self) -> &Path {
        &self.exports_dir
    }

    fn connect(&self) -> Result<rusqlite::Connection, String> {
        let connection = lattice_core::db::open_read_write(&self.sqlite_path)
            .map_err(|error| format!("{error:?}"))?;
        connection
            .execute_batch(
                "CREATE TABLE IF NOT EXISTS workspace_os_state (\
                 id TEXT PRIMARY KEY, state_json TEXT NOT NULL, updated_at TEXT NOT NULL);\
                 CREATE TABLE IF NOT EXISTS workspace_os_meta (\
                 key TEXT PRIMARY KEY, value TEXT NOT NULL);",
            )
            .map_err(|error| error.to_string())?;
        Ok(connection)
    }

    fn load_sqlite_state(&self) -> Option<Value> {
        let connection = self.connect().ok()?;
        let stored: Option<String> = connection
            .query_row(
                "SELECT state_json FROM workspace_os_state WHERE id='current'",
                [],
                |row| row.get(0),
            )
            .ok();
        let parsed: Value = serde_json::from_str(&stored?).ok()?;
        parsed.is_object().then_some(parsed)
    }

    fn save_sqlite_state(&self, state: &Value) {
        let Ok(connection) = self.connect() else {
            return;
        };
        let payload = serde_json::to_string(state).unwrap_or_else(|_| "{}".into());
        let updated_at = state
            .get("updated_at")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .map(str::to_string)
            .unwrap_or_else(now_iso);
        let _ = connection.execute(
            "INSERT OR REPLACE INTO workspace_os_state(id, state_json, updated_at) \
             VALUES('current', ?1, ?2)",
            rusqlite::params![payload, updated_at],
        );
    }

    fn import_json_state_once(&self, default: &Value) -> Value {
        let Ok(text) = std::fs::read_to_string(&self.state_path) else {
            return default.clone();
        };
        let Ok(loaded) = serde_json::from_str::<Value>(&text) else {
            return default.clone();
        };
        if !loaded.is_object() {
            return default.clone();
        }
        self.back_up_pre_sqlite_state();
        deep_merge(default, Some(&loaded))
    }

    fn back_up_pre_sqlite_state(&self) {
        let Some(parent) = self.state_path.parent() else {
            return;
        };
        let prefix = format!("{STATE_FILE_NAME}.pre-sqlite.");
        if let Ok(entries) = std::fs::read_dir(parent) {
            for entry in entries.flatten() {
                let name = entry.file_name();
                let name = name.to_string_lossy();
                if name.starts_with(&prefix) && name.ends_with(".json") {
                    return;
                }
            }
        }
        let stamp = now_iso().replace(':', "-");
        let _ = std::fs::copy(
            &self.state_path,
            parent.join(format!("{prefix}{stamp}.json")),
        );
    }

    pub fn load_state(&self) -> Value {
        let default = default_state();
        let loaded = self.load_sqlite_state();
        let imported = loaded.is_none();
        let loaded = loaded.unwrap_or_else(|| self.import_json_state_once(&default));
        let mut state = deep_merge(&default, Some(&loaded));
        state["version"] = json!(WORKSPACE_OS_VERSION);
        migrate_workspaces(&mut state);
        if imported {
            self.persist(&mut state);
        }
        state
    }

    pub fn save_state(&self, state: &mut Value) {
        self.persist(state);
    }

    fn persist(&self, state: &mut Value) {
        state["version"] = json!(WORKSPACE_OS_VERSION);
        state["updated_at"] = json!(now_iso());
        self.save_sqlite_state(state);
        let text = serde_json::to_string_pretty(state).unwrap_or_else(|_| "{}".into());
        lattice_auth::atomic::write_text(&self.state_path, &text);
    }

    pub fn mutate<T>(
        &self,
        body: impl FnOnce(&mut Value) -> Result<T, StoreError>,
    ) -> Result<T, StoreError> {
        let guard = self.write_lock.lock().unwrap_or_else(|error| {
            self.write_lock.clear_poison();
            error.into_inner()
        });
        let mut state = self.load_state();
        let outcome = body(&mut state)?;
        self.persist(&mut state);
        drop(guard);
        Ok(outcome)
    }

    pub fn append_timeline_to_state(
        state: &mut Value,
        area: &str,
        event_type: &str,
        payload: Value,
        workspace_id: Option<&str>,
    ) -> Value {
        let mut entry = json!({
            "area": area,
            "event_type": event_type,
            "timestamp": now_iso(),
            "payload": if payload.is_object() { payload } else { json!({}) },
        });
        if let Some(workspace) = workspace_id.filter(|value| !value.is_empty()) {
            entry["workspace_id"] = json!(workspace);
        }
        let mut events = listify(state.get("timeline"));
        events.push(entry.clone());
        if events.len() > TIMELINE_CAP {
            events = events.split_off(events.len() - TIMELINE_TRIM_TO);
        }
        state["timeline"] = Value::Array(events);
        entry
    }

    pub fn notify_timeline_entry(&self, entry: &Value) {
        if let Some(sink) = &self.event_sink {
            let mut echoed = entry.clone();
            echoed["type"] = json!("timeline");
            sink(&echoed);
        }
    }

    pub fn record_timeline_event(
        &self,
        area: &str,
        event_type: &str,
        payload: Value,
        workspace_id: Option<&str>,
    ) -> Value {
        let mut produced: Option<Value> = None;
        let _ = self.mutate(|state| {
            produced = Some(Self::append_timeline_to_state(
                state,
                area,
                event_type,
                payload,
                workspace_id,
            ));
            Ok(())
        });
        let entry = produced.unwrap_or_else(|| json!({}));
        self.notify_timeline_entry(&entry);
        entry
    }

    pub fn emit_execution_event(
        &self,
        area: &str,
        event_type: &str,
        payload: Value,
        workspace_id: Option<&str>,
    ) {
        if !EXECUTION_EVENT_TYPES.contains(&event_type) {
            return;
        }
        self.record_timeline_event(area, event_type, payload, workspace_id);
    }

    pub fn emit_replayable_timeline_events(
        &self,
        area: &str,
        run_id: &str,
        timeline: &[Value],
        workspace_id: Option<&str>,
    ) {
        for (index, item) in timeline.iter().enumerate() {
            let event_type = item
                .get("event")
                .and_then(Value::as_str)
                .or_else(|| item.get("event_type").and_then(Value::as_str));
            let Some(event_type) = event_type.filter(|name| EXECUTION_EVENT_TYPES.contains(name))
            else {
                continue;
            };
            let mut payload = match item {
                Value::Object(map) => map.clone(),
                _ => Map::new(),
            };
            payload.remove("context_packet");
            payload.insert("run_id".into(), json!(run_id));
            payload.insert("timeline_index".into(), json!(index));
            self.emit_execution_event(area, event_type, Value::Object(payload), workspace_id);
        }
    }

    pub fn active_workspace_id(state: &Value) -> String {
        let active = state
            .get("active_workspace")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .unwrap_or(DEFAULT_WORKSPACE_ID);
        let known = state
            .get("workspaces")
            .and_then(Value::as_object)
            .is_some_and(|map| map.contains_key(active));
        if known {
            active.to_string()
        } else {
            DEFAULT_WORKSPACE_ID.to_string()
        }
    }

    pub fn resolve_scope(workspace_id: Option<&str>, state: &Value) -> String {
        match workspace_id.filter(|value| !value.is_empty()) {
            Some(named) => named.to_string(),
            None => Self::active_workspace_id(state),
        }
    }

    pub fn record_workspace(record: &Value) -> String {
        record
            .get("workspace_id")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .unwrap_or(DEFAULT_WORKSPACE_ID)
            .to_string()
    }

    pub fn scoped(records: Vec<Value>, workspace_id: Option<&str>) -> Vec<Value> {
        let Some(target) = workspace_id.filter(|value| !value.is_empty()) else {
            return records;
        };
        records
            .into_iter()
            .filter(|item| Self::record_workspace(item) == target)
            .collect()
    }

    pub fn summary(&self) -> Value {
        let state = self.load_state();
        let count = |key: &str| listify(state.get(key)).len();
        let dict_count = |key: &str| {
            state
                .get(key)
                .and_then(Value::as_object)
                .map_or(0, serde_json::Map::len)
        };
        json!({
            "version": WORKSPACE_OS_VERSION,
            "identity": state.get("identity").cloned().unwrap_or(Value::Null),
            "active_workspace": state.get("active_workspace").cloned().unwrap_or(Value::Null),
            "workspace_count": dict_count("workspaces"),
            "navigation": WORKSPACE_AREAS,
            "feature_flags": state.get("feature_flags").cloned().unwrap_or(Value::Null),
            "updated_at": state.get("updated_at").cloned().unwrap_or(Value::Null),
            "counts": {
                "snapshots": count("snapshots"),
                "traces": count("traces"),
                "memories": count("memories"),
                "memory_snapshots": count("memory_snapshots"),
                "agent_runs": count("agent_runs"),
                "handoffs": count("handoffs"),
                "workflows": count("workflows"),
                "workflow_runs": count("workflow_runs"),
                "skills": dict_count("skill_registry"),
                "plugins": dict_count("plugin_registry"),
                "templates": dict_count("template_registry"),
                "timeline": count("timeline"),
            },
            "onboarding": state.get("onboarding").cloned().unwrap_or(Value::Null),
            "storage": {
                "state_path": self.state_path.to_string_lossy(),
                "snapshots_dir": self.snapshots_dir.to_string_lossy(),
                "exports_dir": self.exports_dir.to_string_lossy(),
            },
        })
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
    fn a_fresh_store_creates_its_three_directories() {
        let (dir, store) = store();
        assert!(dir.path().join(SNAPSHOTS_DIR_NAME).is_dir());
        assert!(dir.path().join(EXPORTS_DIR_NAME).is_dir());
        assert_eq!(store.state_path(), dir.path().join(STATE_FILE_NAME));
    }

    #[test]
    fn the_registry_hands_the_same_store_to_every_caller_of_a_directory() {
        let dir = tempfile::tempdir().expect("tempdir");
        let first = WorkspaceOsStore::shared(dir.path());
        let second = WorkspaceOsStore::shared(dir.path());
        assert!(Arc::ptr_eq(&first, &second), "one directory, one store");
        let other = tempfile::tempdir().expect("tempdir");
        assert!(!Arc::ptr_eq(
            &first,
            &WorkspaceOsStore::shared(other.path())
        ));
        first
            .mutate(|state| {
                state["identity"] = json!("shared");
                Ok(())
            })
            .expect("mutate");
        assert_eq!(second.load_state()["identity"], json!("shared"));

        let path = dir.path().to_path_buf();
        drop(first);
        drop(second);
        let rebuilt = WorkspaceOsStore::shared(&path);
        assert_eq!(rebuilt.data_dir(), path);
    }

    #[test]
    fn the_first_load_writes_both_stores() {
        let (dir, store) = store();
        let state = store.load_state();
        assert_eq!(state["version"], json!(WORKSPACE_OS_VERSION));
        assert!(dir.path().join(STATE_FILE_NAME).is_file());
        assert!(dir.path().join(GRAPH_DB_FILE_NAME).is_file());
        assert_eq!(store.load_state()["created_at"], state["created_at"]);
    }

    #[test]
    fn a_pre_sqlite_json_document_is_adopted_and_backed_up_once() {
        let (dir, store) = store();
        std::fs::write(
            dir.path().join(STATE_FILE_NAME),
            serde_json::to_string(&json!({"identity": "Legacy", "memories": [{"id": "m1"}]}))
                .unwrap(),
        )
        .unwrap();
        let state = store.load_state();
        assert_eq!(state["identity"], json!("Legacy"));
        assert_eq!(state["memories"][0]["id"], json!("m1"));
        let backups = || {
            std::fs::read_dir(dir.path())
                .unwrap()
                .flatten()
                .filter(|entry| entry.file_name().to_string_lossy().contains(".pre-sqlite."))
                .count()
        };
        assert_eq!(backups(), 1);
        store.load_state();
        assert_eq!(backups(), 1);
    }

    #[test]
    fn unreadable_json_falls_back_to_the_default_document() {
        let (dir, store) = store();
        std::fs::write(dir.path().join(STATE_FILE_NAME), b"{not json").unwrap();
        assert_eq!(store.load_state()["identity"], json!("AI Workspace OS"));
        std::fs::remove_file(dir.path().join(GRAPH_DB_FILE_NAME)).unwrap();
        std::fs::write(dir.path().join(STATE_FILE_NAME), b"[1, 2]").unwrap();
        assert_eq!(store.load_state()["identity"], json!("AI Workspace OS"));
    }

    #[test]
    fn a_mutation_round_trips_through_both_stores() {
        let (dir, store) = store();
        store
            .mutate(|state| {
                state["active_workspace"] = json!("personal");
                state["memories"] = json!([{"id": "m1", "workspace_id": "personal"}]);
                Ok(())
            })
            .expect("mutate");
        assert_eq!(store.load_state()["memories"][0]["id"], json!("m1"));
        let on_disk: Value = serde_json::from_str(
            &std::fs::read_to_string(dir.path().join(STATE_FILE_NAME)).unwrap(),
        )
        .unwrap();
        assert_eq!(on_disk["memories"][0]["id"], json!("m1"));
    }

    #[test]
    fn a_refused_mutation_writes_nothing() {
        let (_dir, store) = store();
        let before = store.load_state()["updated_at"].clone();
        let outcome: Result<(), StoreError> = store.mutate(|state| {
            state["memories"] = json!([{"id": "nope"}]);
            Err(StoreError::Value("no".into()))
        });
        assert_eq!(outcome.unwrap_err(), StoreError::Value("no".into()));
        assert!(store.load_state()["memories"]
            .as_array()
            .unwrap()
            .is_empty());
        assert_eq!(store.load_state()["updated_at"], before);
    }

    #[test]
    fn timeline_events_append_carry_the_workspace_and_reach_the_sink() {
        let dir = tempfile::tempdir().unwrap();
        let seen = Arc::new(Mutex::new(Vec::<Value>::new()));
        let recorder = Arc::clone(&seen);
        let store = WorkspaceOsStore::open(dir.path()).with_event_sink(Arc::new(move |event| {
            recorder.lock().unwrap().push(event.clone());
        }));
        let entry =
            store.record_timeline_event("workspace", "test", json!({"a": 1}), Some("org-x"));
        assert_eq!(entry["area"], json!("workspace"));
        assert_eq!(entry["workspace_id"], json!("org-x"));
        assert_eq!(seen.lock().unwrap()[0]["type"], json!("timeline"));
        let unscoped = store.record_timeline_event("memory", "test2", json!(null), None);
        assert!(unscoped.get("workspace_id").is_none());
        assert_eq!(unscoped["payload"], json!({}));
        assert_eq!(store.load_state()["timeline"].as_array().unwrap().len(), 2);
    }

    #[test]
    fn only_execution_event_types_are_emitted() {
        let (_dir, store) = store();
        store.emit_execution_event("agent", "not_an_execution_event", json!({}), None);
        assert!(store.load_state()["timeline"]
            .as_array()
            .unwrap()
            .is_empty());
        store.emit_execution_event("agent", "agent_started", json!({}), None);
        assert_eq!(store.load_state()["timeline"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn replayable_events_drop_the_context_packet_and_number_themselves() {
        let (_dir, store) = store();
        store.emit_replayable_timeline_events(
            "agent",
            "run-1",
            &[
                json!({"event": "agent_started", "context_packet": {"big": true}}),
                json!({"step": "ignored"}),
                json!({"event_type": "workflow_completed"}),
            ],
            Some("personal"),
        );
        let timeline = store.load_state();
        let events = timeline["timeline"].as_array().unwrap();
        assert_eq!(events.len(), 2);
        assert_eq!(events[0]["payload"]["run_id"], json!("run-1"));
        assert_eq!(events[0]["payload"]["timeline_index"], json!(0));
        assert!(events[0]["payload"].get("context_packet").is_none());
        assert_eq!(events[1]["event_type"], json!("workflow_completed"));
        assert_eq!(events[1]["payload"]["timeline_index"], json!(2));
    }

    #[test]
    fn the_timeline_is_trimmed_rather_than_grown_without_bound() {
        let (_dir, store) = store();
        store
            .mutate(|state| {
                state["timeline"] = Value::Array(vec![json!({"i": 0}); TIMELINE_CAP]);
                Ok(())
            })
            .unwrap();
        store.record_timeline_event("workspace", "overflow", json!({}), None);
        assert_eq!(
            store.load_state()["timeline"].as_array().unwrap().len(),
            TIMELINE_TRIM_TO
        );
    }

    #[test]
    fn scope_helpers_answer_the_way_the_python_ones_do() {
        let state = json!({"active_workspace": "org-a", "workspaces": {"org-a": {}}});
        assert_eq!(WorkspaceOsStore::active_workspace_id(&state), "org-a");
        let stale = json!({"active_workspace": "org-gone", "workspaces": {"personal": {}}});
        assert_eq!(WorkspaceOsStore::active_workspace_id(&stale), "personal");
        assert_eq!(
            WorkspaceOsStore::resolve_scope(Some("org-b"), &state),
            "org-b"
        );
        assert_eq!(WorkspaceOsStore::resolve_scope(None, &state), "org-a");
        assert_eq!(WorkspaceOsStore::resolve_scope(Some(""), &state), "org-a");
        assert_eq!(
            WorkspaceOsStore::record_workspace(&json!({"workspace_id": "org-c"})),
            "org-c"
        );
        assert_eq!(WorkspaceOsStore::record_workspace(&json!({})), "personal");
        let records = vec![
            json!({"workspace_id": "org-a"}),
            json!({}),
            json!({"workspace_id": "personal"}),
        ];
        assert_eq!(
            WorkspaceOsStore::scoped(records.clone(), Some("personal")).len(),
            2
        );
        assert_eq!(WorkspaceOsStore::scoped(records, None).len(), 3);
    }

    #[test]
    fn the_summary_counts_every_branch_of_the_document() {
        let (dir, store) = store();
        store
            .mutate(|state| {
                state["memories"] = json!([{"id": "m"}]);
                state["skill_registry"] = json!({"a": {}, "b": {}});
                Ok(())
            })
            .unwrap();
        let summary = store.summary();
        assert_eq!(summary["counts"]["memories"], json!(1));
        assert_eq!(summary["counts"]["skills"], json!(2));
        assert_eq!(summary["counts"]["traces"], json!(0));
        assert_eq!(summary["workspace_count"], json!(1));
        assert_eq!(summary["navigation"], json!(WORKSPACE_AREAS));
        assert_eq!(
            summary["storage"]["exports_dir"],
            json!(dir.path().join(EXPORTS_DIR_NAME).to_string_lossy())
        );
    }
}
