//! The marketplace / plugins / agents view of `workspace_os.json`.
//!
//! Until v11.6.0 this was a **third** writer of that document: its own
//! `read_to_string` → mutate → `atomic::write_text` with no lock and its own
//! `dump_indent2` formatting, so a plugin toggle could erase a review item
//! written a millisecond earlier (and rewrite the file in a different shape).
//! It is now a view over the one [`WorkspaceOsStore`]: reads are that store's
//! `load_state`, and every mutation runs inside its `mutate`, which holds the
//! write lock across the load and the save.

use std::collections::BTreeMap;
use std::path::Path;
use std::sync::Arc;

use serde_json::{json, Value};

use crate::workspaceos::workspace::store::{StoreError, WorkspaceOsStore};

use super::http::{json_hash, now_iso_seconds};

// ── workspace_os.json (shared by marketplace / plugins / agents) ───────────

#[derive(Clone)]
pub(crate) struct PlatformStore {
    os: Arc<WorkspaceOsStore>,
}

impl PlatformStore {
    pub(crate) fn new(data_dir: impl AsRef<Path>) -> Self {
        Self {
            os: WorkspaceOsStore::shared(data_dir.as_ref()),
        }
    }

    /// The document. Its defaults are the store's `default_state`, which is a
    /// superset of the nine keys this view reads — including the same five
    /// `agents`, so `list_agents` answers exactly what it used to.
    pub(crate) fn load(&self) -> Value {
        self.os.load_state()
    }

    /// Load, apply, save — under the store's lock, so a concurrent write to
    /// another branch of the same document cannot be lost.
    fn mutate<T>(&self, body: impl FnOnce(&mut Value) -> T) -> T {
        let mut produced: Option<T> = None;
        let _: Result<(), StoreError> = self.os.mutate(|state| {
            produced = Some(body(state));
            Ok(())
        });
        produced.expect("mutate always runs the body it was given")
    }

    fn scope<'a>(&self, requested: Option<&'a str>) -> &'a str {
        match requested {
            Some(s) if !s.is_empty() => s,
            _ => "personal",
        }
    }

    pub(crate) fn list_agents(&self) -> Value {
        let state = self.load();
        let agents = state
            .get("agents")
            .cloned()
            .unwrap_or_else(|| json!(default_agents()));
        let runs = state
            .get("agent_runs")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        json!({ "agents": agents, "runs": runs })
    }

    pub(crate) fn get_agent_run(&self, run_id: &str) -> Option<Value> {
        let state = self.load();
        state
            .get("agent_runs")
            .and_then(Value::as_array)
            .and_then(|runs| {
                runs.iter()
                    .find(|r| r.get("id").and_then(Value::as_str) == Some(run_id))
            })
            .cloned()
    }

    pub(crate) fn list_handoffs(&self, run_id: Option<&str>) -> Value {
        let state = self.load();
        let mut handoffs: Vec<Value> = state
            .get("handoffs")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        if let Some(run_id) = run_id {
            handoffs.retain(|h| h.get("run_id").and_then(Value::as_str) == Some(run_id));
        }
        handoffs.reverse();
        json!({ "handoffs": handoffs })
    }

    pub(crate) fn list_memory_snapshots(&self, limit: usize) -> Value {
        let state = self.load();
        let snaps = state
            .get("memory_snapshots")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let cap = limit.clamp(1, 200);
        let start = snaps.len().saturating_sub(cap);
        let mut out = snaps[start..].to_vec();
        out.reverse();
        json!({ "snapshots": out })
    }

    pub(crate) fn create_memory_snapshot(
        &self,
        label: &str,
        reason: &str,
        user_email: Option<&str>,
        workspace_id: Option<&str>,
        memory_ids: Option<&[String]>,
    ) -> Value {
        self.mutate(|state| {
            let scope = self.scope(workspace_id).to_string();
            let memories: Vec<Value> = state
                .get("memories")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default()
                .into_iter()
                .filter(|m| {
                    if let Some(email) = user_email {
                        match m.get("user_email") {
                            None | Some(Value::Null) => true,
                            Some(Value::String(s)) => s == email,
                            _ => false,
                        }
                    } else {
                        true
                    }
                })
                .filter(|m| {
                    if let Some(ids) = memory_ids {
                        m.get("id")
                            .and_then(Value::as_str)
                            .map(|id| ids.iter().any(|x| x == id))
                            .unwrap_or(false)
                    } else {
                        true
                    }
                })
                .collect();
            let created = now_iso_seconds();
            let hash_src = json!([label, scope, memories, created]);
            let id = format!("memory-snapshot-{}", &json_hash(&hash_src)[..16]);
            let snapshot = json!({
                "id": id,
                "label": label,
                "reason": reason,
                "workspace_id": scope,
                "user_email": user_email,
                "memory_count": memories.len(),
                "memories": memories,
                "created_at": created,
            });
            if let Some(obj) = state.as_object_mut() {
                let list = obj.entry("memory_snapshots").or_insert_with(|| json!([]));
                if let Some(arr) = list.as_array_mut() {
                    arr.push(snapshot.clone());
                }
            }
            snapshot
        })
    }

    pub(crate) fn create_workflow(
        &self,
        name: &str,
        steps: Vec<Value>,
        nodes: Vec<Value>,
        metadata: Value,
        user_email: Option<&str>,
        workspace_id: Option<&str>,
    ) -> Value {
        self.mutate(|state| {
            let scope = self.scope(workspace_id).to_string();
            let created = now_iso_seconds();
            let hash_src = json!([name, steps, user_email, created]);
            let id = format!("workflow-{}", &json_hash(&hash_src)[..16]);
            let mut workflow = json!({
                "id": id,
                "name": if name.is_empty() { "Untitled workflow" } else { name },
                "steps": steps,
                "user_email": user_email,
                "workspace_id": scope,
                "metadata": metadata,
                "events": [{"type": "created", "timestamp": created}],
                "created_at": created,
                "updated_at": created,
            });
            if !nodes.is_empty() {
                workflow["nodes"] = json!(nodes);
            }
            if let Some(obj) = state.as_object_mut() {
                let list = obj.entry("workflows").or_insert_with(|| json!([]));
                if let Some(arr) = list.as_array_mut() {
                    arr.push(workflow.clone());
                }
            }
            workflow
        })
    }

    pub(crate) fn list_plugin_registry(&self) -> BTreeMap<String, Value> {
        let state = self.load();
        state
            .get("plugin_registry")
            .and_then(Value::as_object)
            .map(|m| m.iter().map(|(k, v)| (k.clone(), v.clone())).collect())
            .unwrap_or_default()
    }

    pub(crate) fn set_plugin_enabled(&self, plugin_id: &str, enabled: bool) -> Value {
        self.mutate(|state| {
            let updated = now_iso_seconds();
            let entry = {
                let obj = state.as_object_mut().expect("state object");
                let registry = obj.entry("plugin_registry").or_insert_with(|| json!({}));
                let map = registry.as_object_mut().expect("registry object");
                let slot = map
                    .entry(plugin_id.to_string())
                    .or_insert_with(|| json!({"id": plugin_id}));
                slot["enabled"] = json!(enabled);
                slot["updated_at"] = json!(updated);
                slot.clone()
            };
            entry
        })
    }

    pub(crate) fn mark_plugin_uninstalled(&self, plugin_id: &str) -> Value {
        self.mutate(|state| {
            let updated = now_iso_seconds();
            let registry_entry = {
                let obj = state.as_object_mut().expect("state object");
                let registry = obj.entry("plugin_registry").or_insert_with(|| json!({}));
                let map = registry.as_object_mut().expect("registry object");
                let slot = map
                    .entry(plugin_id.to_string())
                    .or_insert_with(|| json!({"id": plugin_id}));
                slot["installed"] = json!(false);
                slot["enabled"] = json!(false);
                slot["updated_at"] = json!(updated);
                slot.clone()
            };
            json!({
                "status": "ok",
                "plugin_id": plugin_id,
                "registry": registry_entry,
            })
        })
    }

    pub(crate) fn list_template_registry(&self, workspace_id: Option<&str>) -> Value {
        let state = self.load();
        let registry = state
            .get("template_registry")
            .cloned()
            .unwrap_or_else(|| json!({}));
        if workspace_id.is_none() {
            return registry;
        }
        let scope = self.scope(workspace_id);
        let mut filtered = serde_json::Map::new();
        if let Some(obj) = registry.as_object() {
            for (k, v) in obj {
                let ws = v
                    .get("workspace_id")
                    .and_then(Value::as_str)
                    .unwrap_or("personal");
                if ws == scope {
                    filtered.insert(k.clone(), v.clone());
                }
            }
        }
        Value::Object(filtered)
    }

    pub(crate) fn mark_template_installed(
        &self,
        kind: &str,
        template_id: &str,
        version: &str,
        metadata: Value,
        workspace_id: Option<&str>,
    ) -> Value {
        self.mutate(|state| {
            let scope = self.scope(workspace_id).to_string();
            let key = if scope == "personal" {
                format!("{kind}:{template_id}")
            } else {
                format!("{scope}:{kind}:{template_id}")
            };
            let updated = now_iso_seconds();
            let entry = json!({
                "id": template_id,
                "kind": kind,
                "version": version,
                "installed": true,
                "workspace_id": scope,
                "metadata": metadata,
                "updated_at": updated,
            });
            if let Some(obj) = state.as_object_mut() {
                let registry = obj.entry("template_registry").or_insert_with(|| json!({}));
                if let Some(map) = registry.as_object_mut() {
                    map.insert(key, entry.clone());
                }
            }
            entry
        })
    }
}

fn default_agents() -> Vec<Value> {
    vec![
        json!({"id":"agent:planner","name":"Planner","role":"Breaks workspace goals into executable plans.","status":"available","relationships":["agent:executor","agent:reviewer"]}),
        json!({"id":"agent:executor","name":"Executor","role":"Runs approved tool and code workflows.","status":"available","relationships":["agent:planner","agent:reviewer"]}),
        json!({"id":"agent:reviewer","name":"Reviewer","role":"Checks outputs, tests, and regressions.","status":"available","relationships":["agent:executor","agent:release"]}),
        json!({"id":"agent:researcher","name":"Researcher","role":"Finds and curates relevant workspace knowledge.","status":"available","relationships":["agent:planner"]}),
        json!({"id":"agent:release","name":"Release Agent","role":"Coordinates versioning, packaging, and release checks.","status":"available","relationships":["agent:reviewer"]}),
    ]
}
