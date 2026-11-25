use std::path::PathBuf;
use std::sync::Arc;

use axum::routing::{get, post};
use axum::Router;
use lattice_auth::AuthState;
use lattice_core::worker::WorkerSeamClient;
use serde_json::{json, Value};

use crate::workspaceos::workspace::store::{StoreError, WorkspaceOsStore};

mod handlers;
mod http;
mod os;
pub(crate) mod store;
mod workflows;

use handlers::{
    approve_item, bulk_approve, bulk_dismiss, create_item, dismiss_item, get_item, list_items,
    review_counts, run_now_item, snooze_item, unsnooze_item,
};

pub(crate) use http::{
    gate_read, gate_write, http_detail, into_value, json_ok, json_status, language, map_str,
    map_worker_error, now_iso, parse_object, parse_object_optional, require_admin, require_field,
    require_user, sha256_text, string_field, string_field_or,
};
pub(crate) use store::{
    create_review_item, list_review_items, load_review_item, review_item_raw_view,
    transition_dismiss, update_review_item, ReviewError,
};
pub(crate) use workflows::{
    create_workflow, daily_memory_digest_definition, get_workflow, list_agent_runs,
    list_workflow_runs, list_workflows, update_workflow_metadata,
};

pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/automation/reviews"),
    ("POST", "/automation/reviews"),
    ("GET", "/automation/reviews/counts"),
    ("POST", "/automation/reviews/bulk/approve"),
    ("POST", "/automation/reviews/bulk/dismiss"),
    ("GET", "/automation/reviews/:item_id"),
    ("POST", "/automation/reviews/:item_id/approve"),
    ("POST", "/automation/reviews/:item_id/dismiss"),
    ("POST", "/automation/reviews/:item_id/snooze"),
    ("POST", "/automation/reviews/:item_id/unsnooze"),
    ("POST", "/automation/reviews/:item_id/run_now"),
];

const DEFAULT_WORKSPACE_ID: &str = "personal";
const BULK_ACTION_CAP: usize = 200;

pub const REVIEW_TIMELINE_AREA: &str = "review";
pub const REVIEW_ITEM_CREATED_EVENT: &str = "review_item_created";
pub const REVIEW_ITEM_UPDATED_EVENT: &str = "review_item_updated";

const REVIEW_SOURCES: &[&str] = &[
    "agent_followup",
    "change_proposal",
    "chat_followup",
    "kg_change_digest",
    "trigger",
    "workflow_run",
];

const REVIEW_ITEM_KEYS: &[&str] = &[
    "id",
    "status",
    "effective_status",
    "title",
    "summary",
    "source",
    "kind",
    "payload",
    "provenance",
    "snoozed_until",
    "user_email",
    "workspace_id",
    "created_at",
    "updated_at",
];

#[derive(Clone)]
pub struct GovernanceState {
    pub auth: Arc<AuthState>,
    pub data_dir: PathBuf,
    pub agent_root: PathBuf,
    pub worker: Option<WorkerSeamClient>,
    os: Arc<WorkspaceOsStore>,
}

impl GovernanceState {
    pub fn open(
        auth: Arc<AuthState>,
        data_dir: impl Into<PathBuf>,
        agent_root: impl Into<PathBuf>,
        worker: Option<WorkerSeamClient>,
    ) -> Self {
        let data_dir = data_dir.into();
        Self::with_store(
            auth,
            WorkspaceOsStore::shared(&data_dir),
            agent_root,
            worker,
        )
    }

    pub fn with_store(
        auth: Arc<AuthState>,
        os: Arc<WorkspaceOsStore>,
        agent_root: impl Into<PathBuf>,
        worker: Option<WorkerSeamClient>,
    ) -> Self {
        let agent_root = agent_root.into();
        let _ = std::fs::create_dir_all(&agent_root);
        Self {
            auth,
            data_dir: os.data_dir().to_path_buf(),
            agent_root,
            worker,
            os,
        }
    }

    pub fn store(&self) -> &Arc<WorkspaceOsStore> {
        &self.os
    }

    pub fn state_path(&self) -> PathBuf {
        self.os.state_path().to_path_buf()
    }

    pub(crate) fn with_state<T>(&self, f: impl FnOnce(&Value) -> T) -> T {
        f(&self.os.load_state())
    }

    pub(crate) fn update_state<T>(&self, f: impl FnOnce(&mut Value) -> T) -> T {
        let mut produced: Option<T> = None;
        let _: Result<(), StoreError> = self.os.mutate(|doc| {
            produced = Some(f(doc));
            Ok(())
        });
        produced.expect("mutate always runs the body it was given")
    }

    fn build_review_event_payload(item: &Value, action: &str) -> (String, Value) {
        let workspace = item
            .get("workspace_id")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .unwrap_or(DEFAULT_WORKSPACE_ID)
            .to_string();
        let payload = json!({
            "item_id": item.get("id").cloned().unwrap_or(Value::Null),
            "action": action,
            "status": item.get("status").cloned().unwrap_or(Value::Null),
            "source": item.get("source").cloned().unwrap_or(Value::Null),
            "workspace_id": workspace,
        });
        (workspace, payload)
    }

    pub(crate) fn update_state_with_review_event(
        &self,
        event_type: &str,
        action: &str,
        f: impl FnOnce(&mut Value) -> Value,
    ) -> Value {
        let mut produced: Option<(Value, Value)> = None;
        let _: Result<(), StoreError> = self.os.mutate(|doc| {
            let item = f(doc);
            let (workspace, payload) = Self::build_review_event_payload(&item, action);
            let entry = WorkspaceOsStore::append_timeline_to_state(
                doc,
                REVIEW_TIMELINE_AREA,
                event_type,
                payload,
                Some(workspace.as_str()),
            );
            produced = Some((item, entry));
            Ok(())
        });
        let (item, entry) = produced.expect("mutate always runs the body it was given");
        self.os.notify_timeline_entry(&entry);
        item
    }

    pub(crate) fn try_update_state_with_review_event<E>(
        &self,
        event_type: &str,
        action: &str,
        f: impl FnOnce(&mut Value) -> Result<Value, E>,
    ) -> Result<Value, E> {
        let mut refusal: Option<E> = None;
        let outcome = self.os.mutate(|doc| match f(doc) {
            Ok(item) => {
                let (workspace, payload) = Self::build_review_event_payload(&item, action);
                let entry = WorkspaceOsStore::append_timeline_to_state(
                    doc,
                    REVIEW_TIMELINE_AREA,
                    event_type,
                    payload,
                    Some(workspace.as_str()),
                );
                Ok((item, entry))
            }
            Err(error) => {
                refusal = Some(error);
                Err(StoreError::Value(String::new()))
            }
        });
        match outcome {
            Ok((item, entry)) => {
                self.os.notify_timeline_entry(&entry);
                Ok(item)
            }
            Err(_) => Err(refusal.expect("a refusal is the only way mutate fails here")),
        }
    }

    pub fn seed_recipe_workflow(&self, workflow_id: &str) -> Value {
        let now = now_iso();
        let definition = daily_memory_digest_definition(false);
        let mut workflow = json!({
            "id": workflow_id,
            "name": definition["name"],
            "steps": [{"action": "agent", "goal": definition["nodes"][1]["config"]["goal"]}],
            "user_email": "owner@lattice.test",
            "workspace_id": DEFAULT_WORKSPACE_ID,
            "metadata": definition["metadata"],
            "nodes": definition["nodes"],
            "events": [{"type": "created", "timestamp": now}],
            "created_at": now,
            "updated_at": now,
        });
        self.update_state(|state| {
            let workflows = state
                .as_object_mut()
                .expect("state object")
                .entry("workflows")
                .or_insert_with(|| json!([]));
            if let Some(list) = workflows.as_array_mut() {
                list.retain(|row| row.get("id").and_then(Value::as_str) != Some(workflow_id));
                list.push(workflow.clone());
            }
        });
        if let Some(meta) = workflow.get_mut("metadata").and_then(Value::as_object_mut) {
            meta.entry("suggestion_id").or_insert(Value::Null);
        }
        workflow
    }

    pub fn seed_plain_workflow(&self, workflow_id: &str) -> Value {
        let now = now_iso();
        let workflow = json!({
            "id": workflow_id,
            "name": "Fixture workflow",
            "steps": [],
            "user_email": "owner@lattice.test",
            "workspace_id": DEFAULT_WORKSPACE_ID,
            "metadata": {"origin": "fixture"},
            "nodes": [
                {"id": "trigger", "type": "trigger", "name": "Manual start", "config": {"trigger": "manual"}, "next": "output"},
                {"id": "output", "type": "output", "name": "Output", "config": {}, "next": null}
            ],
            "events": [{"type": "created", "timestamp": now}],
            "created_at": now,
            "updated_at": now,
        });
        self.update_state(|state| {
            let workflows = state
                .as_object_mut()
                .expect("state object")
                .entry("workflows")
                .or_insert_with(|| json!([]));
            if let Some(list) = workflows.as_array_mut() {
                list.retain(|row| row.get("id").and_then(Value::as_str) != Some(workflow_id));
                list.push(workflow.clone());
            }
        });
        workflow
    }
}

impl axum::extract::FromRef<GovernanceState> for Arc<AuthState> {
    fn from_ref(state: &GovernanceState) -> Self {
        Arc::clone(&state.auth)
    }
}

pub fn router(state: GovernanceState) -> Router {
    Router::new()
        .route("/automation/reviews", get(list_items).post(create_item))
        .route("/automation/reviews/counts", get(review_counts))
        .route("/automation/reviews/bulk/approve", post(bulk_approve))
        .route("/automation/reviews/bulk/dismiss", post(bulk_dismiss))
        .route("/automation/reviews/{item_id}", get(get_item))
        .route("/automation/reviews/{item_id}/approve", post(approve_item))
        .route("/automation/reviews/{item_id}/dismiss", post(dismiss_item))
        .route("/automation/reviews/{item_id}/snooze", post(snooze_item))
        .route(
            "/automation/reviews/{item_id}/unsnooze",
            post(unsnooze_item),
        )
        .route("/automation/reviews/{item_id}/run_now", post(run_now_item))
        .with_state(state)
}
