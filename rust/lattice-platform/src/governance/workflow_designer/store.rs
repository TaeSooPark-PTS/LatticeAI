//! The designer's view of `workspace_os.json`.
//!
//! Until v11.6.0 this was a **fourth** writer of that document: its own
//! default document, its own `"11.5.2"` version literal, its own `Mutex` and
//! its own `dumps_indent2` bytes. A workflow save therefore rewrote every
//! other branch of the file from a snapshot taken before the Review Center's
//! last write. It is now a view over the one [`WorkspaceOsStore`] — the
//! document, the lock, the version stamp and the bytes all belong to it, and
//! the `OrderedMap` shapes below are only how this module reads and builds its
//! own records.

use std::path::PathBuf;
use std::sync::Arc;

use lattice_auth::pyjson::OrderedMap;
use serde_json::{json, Value};

use crate::workspaceos::workspace::store::{StoreError, WorkspaceOsStore};

use super::contract::workflow_run_contract;
use super::time::{dumps_sorted, json_hash, now_iso};
use super::{DEFAULT_WORKSPACE_ID, TERMINAL_STATUSES};

// ── store ────────────────────────────────────────────────────────────────────

pub(crate) struct WorkflowStore {
    pub(crate) path: PathBuf,
    os: Arc<WorkspaceOsStore>,
}

impl WorkflowStore {
    /// `path` is `<data_dir>/workspace_os.json`; the store is keyed by the
    /// directory it sits in, so this joins whatever handle already exists.
    pub(crate) fn open(path: PathBuf) -> Self {
        let data_dir = path
            .parent()
            .map(std::path::Path::to_path_buf)
            .unwrap_or_else(|| PathBuf::from("."));
        Self {
            os: WorkspaceOsStore::shared(&data_dir),
            path,
        }
    }

    /// The document, in this module's `OrderedMap` shape.
    ///
    /// The store's `default_state` is a superset of the document this module
    /// used to default to, so every key read below is present either way.
    fn load(&self) -> OrderedMap {
        Self::as_ordered(&self.os.load_state())
    }

    fn as_ordered(state: &Value) -> OrderedMap {
        serde_json::from_value::<OrderedMap>(state.clone()).unwrap_or_default()
    }

    /// Load, apply, save — under the store's lock.
    ///
    /// The body sees the whole document (not just the two workflow keys), so a
    /// concurrent review or memory write is preserved rather than overwritten
    /// by a stale snapshot. `version` and `updated_at` are stamped by the
    /// store, which is why this module no longer carries a version literal.
    fn mutate<T>(&self, body: impl FnOnce(&mut OrderedMap) -> T) -> T {
        let mut produced: Option<T> = None;
        let _: Result<(), StoreError> = self.os.mutate(|state| {
            let mut doc = Self::as_ordered(state);
            produced = Some(body(&mut doc));
            *state = serde_json::to_value(&doc).unwrap_or_else(|_| state.clone());
            Ok(())
        });
        produced.expect("mutate always runs the body it was given")
    }

    fn listify(doc: &OrderedMap, key: &str) -> Vec<Value> {
        doc.get(key)
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
    }

    fn scoped(records: Vec<Value>, workspace_id: &str) -> Vec<Value> {
        records
            .into_iter()
            .filter(|record| {
                record
                    .get("workspace_id")
                    .and_then(Value::as_str)
                    .unwrap_or(DEFAULT_WORKSPACE_ID)
                    == workspace_id
            })
            .collect()
    }

    // Eight parameters because the Python row this writes has eight columns.
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn create_workflow(
        &self,
        name: &str,
        steps: Vec<Value>,
        nodes: Vec<Value>,
        metadata: Value,
        user_email: Option<&str>,
        workspace_id: &str,
        graph_node_id: Option<String>,
    ) -> OrderedMap {
        self.mutate(|doc| {
            let now = now_iso();
            let id = format!(
                "workflow-{}",
                &json_hash(&json!([name, steps, user_email, now]))[..16]
            );
            let mut workflow = OrderedMap::new();
            workflow.insert("id", json!(id));
            workflow.insert(
                "name",
                json!(if name.is_empty() {
                    "Untitled workflow"
                } else {
                    name
                }),
            );
            workflow.insert("steps", json!(steps));
            workflow.insert("user_email", json!(user_email));
            workflow.insert("workspace_id", json!(workspace_id));
            workflow.insert("metadata", metadata);
            workflow.insert("events", json!([{"type": "created", "timestamp": now}]));
            workflow.insert("created_at", json!(now));
            workflow.insert("updated_at", json!(now));
            workflow.insert("nodes", json!(nodes));
            if let Some(node_id) = graph_node_id {
                workflow.insert("graph_node_id", json!(node_id));
            }
            let mut workflows = Self::listify(doc, "workflows");
            workflows.push(serde_json::to_value(&workflow).unwrap_or(json!({})));
            doc.insert("workflows", json!(workflows));
            workflow
        })
    }

    pub(crate) fn get_workflow(&self, workflow_id: &str, workspace_id: &str) -> Result<Value, ()> {
        let doc = self.load();
        Self::listify(&doc, "workflows")
            .into_iter()
            .find(|item| {
                item.get("id").and_then(Value::as_str) == Some(workflow_id)
                    && item
                        .get("workspace_id")
                        .and_then(Value::as_str)
                        .unwrap_or(DEFAULT_WORKSPACE_ID)
                        == workspace_id
            })
            .ok_or(())
    }

    pub(crate) fn update_workflow_definition(
        &self,
        workflow_id: &str,
        workspace_id: &str,
        name: Option<&str>,
        nodes: Option<Vec<Value>>,
        metadata: Option<Value>,
    ) -> Result<Value, ()> {
        self.mutate(|doc| {
            let mut workflows = Self::listify(doc, "workflows");
            let now = now_iso();
            let mut found = None;
            for item in &mut workflows {
                if item.get("id").and_then(Value::as_str) != Some(workflow_id) {
                    continue;
                }
                if item
                    .get("workspace_id")
                    .and_then(Value::as_str)
                    .unwrap_or(DEFAULT_WORKSPACE_ID)
                    != workspace_id
                {
                    continue;
                }
                let Some(object) = item.as_object_mut() else {
                    continue;
                };
                if let Some(name) = name.map(str::trim).filter(|value| !value.is_empty()) {
                    object.insert("name".into(), json!(name));
                }
                if let Some(nodes) = nodes.clone() {
                    object.insert("nodes".into(), json!(nodes));
                }
                if let Some(metadata) = metadata.clone() {
                    let mut merged = object
                        .get("metadata")
                        .and_then(Value::as_object)
                        .cloned()
                        .unwrap_or_default();
                    if let Some(patch) = metadata.as_object() {
                        for (key, value) in patch {
                            merged.insert(key.clone(), value.clone());
                        }
                    }
                    object.insert("metadata".into(), Value::Object(merged));
                }
                let mut events = object
                    .get("events")
                    .and_then(Value::as_array)
                    .cloned()
                    .unwrap_or_default();
                events.push(json!({"type": "edited", "timestamp": now}));
                object.insert("events".into(), json!(events));
                object.insert("updated_at".into(), json!(now));
                found = Some(item.clone());
                break;
            }
            let found = found.ok_or(())?;
            doc.insert("workflows", json!(workflows));
            Ok(found)
        })
    }

    pub(crate) fn list_workflows(&self, query: &str, workspace_id: &str) -> Vec<Value> {
        let doc = self.load();
        let mut workflows = Self::scoped(Self::listify(&doc, "workflows"), workspace_id);
        workflows.reverse();
        let q = query.trim().to_ascii_lowercase();
        if q.is_empty() {
            return workflows;
        }
        workflows
            .into_iter()
            .filter(|wf| {
                let name = wf
                    .get("name")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_ascii_lowercase();
                let steps =
                    dumps_sorted(wf.get("steps").unwrap_or(&json!([]))).to_ascii_lowercase();
                name.contains(&q) || steps.contains(&q)
            })
            .collect()
    }

    // Nine parameters because the run row this writes has nine columns.
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn record_workflow_run(
        &self,
        workflow_id: &str,
        name: &str,
        status: &str,
        timeline: Vec<Value>,
        outputs: Value,
        user_email: Option<&str>,
        workspace_id: &str,
        pause: Option<Value>,
    ) -> OrderedMap {
        self.mutate(|doc| {
            let now = now_iso();
            let id = format!(
                "workflow-run-{}",
                &json_hash(&json!([workflow_id, name, status, now]))[..16]
            );
            let mut run = OrderedMap::new();
            run.insert("id", json!(id));
            run.insert("record_schema_version", json!(2));
            run.insert("workflow_id", json!(workflow_id));
            run.insert(
                "name",
                json!(if name.is_empty() { "workflow" } else { name }),
            );
            run.insert("mode", json!("live"));
            run.insert("status", json!(status));
            run.insert("timeline", json!(timeline));
            run.insert("outputs", outputs);
            run.insert("user_email", json!(user_email));
            run.insert("workspace_id", json!(workspace_id));
            run.insert("created_at", json!(now));
            if let Some(pause) = pause {
                run.insert("pause", pause);
            }
            let contract = workflow_run_contract(&run);
            run.insert(
                "contract",
                serde_json::to_value(&contract).unwrap_or(json!({})),
            );
            let mut runs = Self::listify(doc, "workflow_runs");
            runs.push(serde_json::to_value(&run).unwrap_or(json!({})));
            doc.insert("workflow_runs", json!(runs));
            let mut workflows = Self::listify(doc, "workflows");
            for wf in &mut workflows {
                if wf.get("id").and_then(Value::as_str) == Some(workflow_id) {
                    if let Some(object) = wf.as_object_mut() {
                        let mut events = object
                            .get("events")
                            .and_then(Value::as_array)
                            .cloned()
                            .unwrap_or_default();
                        events.push(json!({
                            "type": "run",
                            "timestamp": now,
                            "payload": {"run_id": id, "status": status},
                        }));
                        object.insert("events".into(), json!(events));
                        object.insert("updated_at".into(), json!(now));
                    }
                    break;
                }
            }
            doc.insert("workflows", json!(workflows));
            run
        })
    }

    pub(crate) fn update_workflow_run(
        &self,
        run_id: &str,
        workspace_id: &str,
        patch: &[(&str, Value)],
    ) -> Result<OrderedMap, ()> {
        self.mutate(|doc| {
            let mut runs = Self::listify(doc, "workflow_runs");
            let now = now_iso();
            let mut found_idx = None;
            for (index, item) in runs.iter().enumerate() {
                if item.get("id").and_then(Value::as_str) == Some(run_id)
                    && item
                        .get("workspace_id")
                        .and_then(Value::as_str)
                        .unwrap_or(DEFAULT_WORKSPACE_ID)
                        == workspace_id
                {
                    found_idx = Some(index);
                    break;
                }
            }
            let index = found_idx.ok_or(())?;
            let Some(object) = runs[index].as_object_mut() else {
                return Err(());
            };
            for (key, value) in patch {
                if *key == "pause" && value.is_null() {
                    object.remove(*key);
                } else {
                    object.insert((*key).into(), value.clone());
                }
            }
            object.insert("updated_at".into(), json!(now));
            let status = object
                .get("status")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            if TERMINAL_STATUSES.contains(&status.as_str()) {
                object
                    .entry("completed_at".to_string())
                    .or_insert_with(|| json!(now));
            }
            let mut ordered = OrderedMap::new();
            for (key, value) in object.iter() {
                ordered.insert(key.clone(), value.clone());
            }
            let contract = workflow_run_contract(&ordered);
            object.insert(
                "contract".into(),
                serde_json::to_value(&contract).unwrap_or(json!({})),
            );
            let snapshot = runs[index].clone();
            doc.insert("workflow_runs", json!(runs));
            let mut out = OrderedMap::new();
            if let Some(map) = snapshot.as_object() {
                for (key, value) in map {
                    out.insert(key.clone(), value.clone());
                }
            }
            Ok(out)
        })
    }

    pub(crate) fn get_workflow_run(&self, run_id: &str, workspace_id: &str) -> Result<Value, ()> {
        let doc = self.load();
        Self::listify(&doc, "workflow_runs")
            .into_iter()
            .find(|item| {
                item.get("id").and_then(Value::as_str) == Some(run_id)
                    && item
                        .get("workspace_id")
                        .and_then(Value::as_str)
                        .unwrap_or(DEFAULT_WORKSPACE_ID)
                        == workspace_id
            })
            .ok_or(())
    }

    /// A notification step lands on the workspace timeline.
    pub(crate) fn record_notification(&self, workspace_id: &str, payload: Value) -> Value {
        self.os
            .record_timeline_event("workflow", "notification", payload, Some(workspace_id))
    }

    pub(crate) fn list_workflow_runs(
        &self,
        workflow_id: Option<&str>,
        limit: usize,
        workspace_id: &str,
    ) -> Vec<Value> {
        let doc = self.load();
        let mut runs = Self::scoped(Self::listify(&doc, "workflow_runs"), workspace_id);
        if let Some(workflow_id) = workflow_id {
            runs.retain(|run| run.get("workflow_id").and_then(Value::as_str) == Some(workflow_id));
        }
        let cap = limit.clamp(1, 300);
        let start = runs.len().saturating_sub(cap);
        let mut slice = runs[start..].to_vec();
        slice.reverse();
        slice
    }
}
