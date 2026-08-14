//! On-disk workflow / run store over `workspace_os.json`.

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
use std::sync::Mutex;

use lattice_auth::atomic;
use lattice_auth::pyjson::{dumps_indent2, OrderedMap};
use serde_json::{json, Value};

use super::contract::workflow_run_contract;
use super::time::{dumps_sorted, json_hash, now_iso};
use super::{DEFAULT_WORKSPACE_ID, TERMINAL_STATUSES, WORKSPACE_OS_VERSION};

// ── store ────────────────────────────────────────────────────────────────────

pub(crate) struct WorkflowStore {
    pub(crate) path: PathBuf,
    lock: Mutex<()>,
}

impl WorkflowStore {
    pub(crate) fn open(path: PathBuf) -> Self {
        Self {
            path,
            lock: Mutex::new(()),
        }
    }

    fn default_document() -> OrderedMap {
        let now = now_iso();
        let mut personal = OrderedMap::new();
        personal.insert("workspace_id", json!(DEFAULT_WORKSPACE_ID));
        personal.insert("id", json!(DEFAULT_WORKSPACE_ID));
        personal.insert("name", json!("Personal Workspace"));
        personal.insert("type", json!("personal"));
        personal.insert("owner_user_id", Value::Null);
        personal.insert("members", json!([]));
        personal.insert("status", json!("active"));
        personal.insert("created_at", json!(now));
        personal.insert("updated_at", json!(now));
        let mut workspaces = OrderedMap::new();
        workspaces.insert(
            DEFAULT_WORKSPACE_ID,
            serde_json::to_value(&personal).unwrap_or(json!({})),
        );
        let mut doc = OrderedMap::new();
        doc.insert("version", json!(WORKSPACE_OS_VERSION));
        doc.insert("identity", json!("AI Workspace OS"));
        doc.insert("created_at", json!(now));
        doc.insert("updated_at", json!(now));
        doc.insert("active_workspace", json!(DEFAULT_WORKSPACE_ID));
        doc.insert(
            "workspaces",
            serde_json::to_value(&workspaces).unwrap_or(json!({})),
        );
        doc.insert("workflows", json!([]));
        doc.insert("workflow_runs", json!([]));
        doc
    }

    fn load(&self) -> OrderedMap {
        match std::fs::read_to_string(&self.path) {
            Ok(text) => serde_json::from_str::<OrderedMap>(&text)
                .unwrap_or_else(|_| Self::default_document()),
            Err(_) => Self::default_document(),
        }
    }

    fn save(&self, mut doc: OrderedMap) {
        doc.insert("version", json!(WORKSPACE_OS_VERSION));
        doc.insert("updated_at", json!(now_iso()));
        if let Ok(text) = dumps_indent2(&doc) {
            if let Some(parent) = self.path.parent() {
                let _ = std::fs::create_dir_all(parent);
            }
            atomic::write_text(&self.path, &text);
        }
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
        let _guard = self.lock.lock().expect("workflow lock");
        let mut doc = self.load();
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
        let mut workflows = Self::listify(&doc, "workflows");
        workflows.push(serde_json::to_value(&workflow).unwrap_or(json!({})));
        doc.insert("workflows", json!(workflows));
        self.save(doc);
        workflow
    }

    pub(crate) fn get_workflow(&self, workflow_id: &str, workspace_id: &str) -> Result<Value, ()> {
        let _guard = self.lock.lock().expect("workflow lock");
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
        let _guard = self.lock.lock().expect("workflow lock");
        let mut doc = self.load();
        let mut workflows = Self::listify(&doc, "workflows");
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
        self.save(doc);
        Ok(found)
    }

    pub(crate) fn list_workflows(&self, query: &str, workspace_id: &str) -> Vec<Value> {
        let _guard = self.lock.lock().expect("workflow lock");
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
        let _guard = self.lock.lock().expect("workflow lock");
        let mut doc = self.load();
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
        let mut runs = Self::listify(&doc, "workflow_runs");
        runs.push(serde_json::to_value(&run).unwrap_or(json!({})));
        doc.insert("workflow_runs", json!(runs));
        let mut workflows = Self::listify(&doc, "workflows");
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
        self.save(doc);
        run
    }

    pub(crate) fn update_workflow_run(
        &self,
        run_id: &str,
        workspace_id: &str,
        patch: &[(&str, Value)],
    ) -> Result<OrderedMap, ()> {
        let _guard = self.lock.lock().expect("workflow lock");
        let mut doc = self.load();
        let mut runs = Self::listify(&doc, "workflow_runs");
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
        self.save(doc);
        let mut out = OrderedMap::new();
        if let Some(map) = snapshot.as_object() {
            for (key, value) in map {
                out.insert(key.clone(), value.clone());
            }
        }
        Ok(out)
    }

    pub(crate) fn get_workflow_run(&self, run_id: &str, workspace_id: &str) -> Result<Value, ()> {
        let _guard = self.lock.lock().expect("workflow lock");
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

    pub(crate) fn list_workflow_runs(
        &self,
        workflow_id: Option<&str>,
        limit: usize,
        workspace_id: &str,
    ) -> Vec<Value> {
        let _guard = self.lock.lock().expect("workflow lock");
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
