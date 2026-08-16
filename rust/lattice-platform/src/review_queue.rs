//! Review Center (`latticeai/api/review_queue.py`) — native.
//!
//! Persistence is the workspace-OS review-item store (`workspace_os.json` +
//! the `workspace_os_state` SQLite row). Status transitions are native.
//! Approving a `change_proposal` item applies the staged file natively, under
//! the agent sandbox ([`crate::change_proposals`] — the worker seam it used to
//! go through was retired in v11.6.0 §P1a); promoting an `agent_followup`
//! writes a workflow draft and calls `ingest_event` on the native write
//! engine.
//!
//! ## One writer for `workspace_os.json` (v11.7.0)
//!
//! Until v11.6.0 this family kept its **own** copy of the document — an
//! `Arc<Mutex<Value>>` loaded once at `open` and written back on every
//! mutation — while [`crate::workspace::WorkspaceOsStore`] wrote the same file
//! and the same SQLite row with a reload-per-mutation discipline. Two stores
//! over one document is last-writer-wins: a workspace-side write (a timeline
//! event, a memory, a snapshot) landing between this family's load and its save
//! was silently erased, and this family's cache never saw it at all.
//!
//! [`GovernanceState`] is now a thin facade over one [`WorkspaceOsStore`]
//! handle, so there is exactly one lock, one merge policy and one version
//! stamp. The host clones the *same* `Arc` into both
//! ([`GovernanceState::with_store`]), which is what makes the guarantee hold
//! process-wide rather than per-family.

use std::path::PathBuf;
use std::sync::Arc;

use axum::routing::{get, post};
use axum::Router;
use lattice_auth::AuthState;
use lattice_core::worker::WorkerSeamClient;
use serde_json::{json, Value};

use crate::workspace::store::{StoreError, WorkspaceOsStore};

mod handlers;
mod http;
mod os;
mod store;
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

/// Mounted (method, axum-path) pairs. Greedy converters are `*name`.
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

/// Timeline `area` every review-item event is filed under.
pub const REVIEW_TIMELINE_AREA: &str = "review";
/// Timeline `event_type` for a review item that has just been created.
pub const REVIEW_ITEM_CREATED_EVENT: &str = "review_item_created";
/// Timeline `event_type` for a review item whose status has just changed.
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

/// Shared platform state for the R7 families.
#[derive(Clone)]
pub struct GovernanceState {
    /// Process-wide identity.
    pub auth: Arc<AuthState>,
    /// `LATTICEAI_DATA_DIR`.
    pub data_dir: PathBuf,
    /// Agent workspace root (`LATTICEAI_AGENT_ROOT`).
    pub agent_root: PathBuf,
    /// Worker seam (apply + hook run + graph mutate).
    pub worker: Option<WorkerSeamClient>,
    /// The one workspace-OS store. Cloning a `GovernanceState` shares it.
    os: Arc<WorkspaceOsStore>,
}

impl GovernanceState {
    /// Open (or join) the workspace OS document under `data_dir`.
    ///
    /// `WorkspaceOsStore::shared` is what makes this safe to call from
    /// anywhere: naming the same directory twice yields the same handle, so
    /// this cannot become the second writer it used to be.
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

    /// The Review Center over a workspace-OS store somebody else opened.
    ///
    /// This is the product wiring: the host hands the very `Arc` the Workspace
    /// OS routes were built from, so a review write and a workspace write take
    /// the same lock and neither can erase the other.
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

    /// The store this state reads and writes through.
    pub fn store(&self) -> &Arc<WorkspaceOsStore> {
        &self.os
    }

    /// The workspace-OS JSON document.
    pub fn state_path(&self) -> PathBuf {
        self.os.state_path().to_path_buf()
    }

    pub(crate) fn with_state<T>(&self, f: impl FnOnce(&Value) -> T) -> T {
        f(&self.os.load_state())
    }

    /// Load, mutate, save — under the store's lock, as one step.
    ///
    /// The body cannot refuse; [`Self::try_update_state`] is the one that can.
    pub(crate) fn update_state<T>(&self, f: impl FnOnce(&mut Value) -> T) -> T {
        let mut produced: Option<T> = None;
        let _: Result<(), StoreError> = self.os.mutate(|doc| {
            produced = Some(f(doc));
            Ok(())
        });
        produced.expect("mutate always runs the body it was given")
    }

    /// [`Self::update_state`] for a body that can refuse: on `Err` the
    /// document is left exactly as it was, rather than re-saved unchanged.
    pub(crate) fn try_update_state<T, E>(
        &self,
        f: impl FnOnce(&mut Value) -> Result<T, E>,
    ) -> Result<T, E> {
        let mut refusal: Option<E> = None;
        let outcome = self.os.mutate(|doc| match f(doc) {
            Ok(value) => Ok(value),
            Err(error) => {
                refusal = Some(error);
                // The payload is never read: `mutate` only distinguishes
                // "saved" from "left alone", and the caller's own error is
                // what comes back out.
                Err(StoreError::Value(String::new()))
            }
        });
        match outcome {
            Ok(value) => Ok(value),
            Err(_) => Err(refusal.expect("a refusal is the only way mutate fails here")),
        }
    }

    /// Record one review-item timeline event — the single payload shape every
    /// review-mutating route ends up emitting.
    ///
    /// Called **after** the mutation has been saved, never from inside a
    /// `mutate` body: `record_timeline_event` takes the same lock.
    pub(crate) fn record_review_event(&self, event_type: &str, item: &Value, action: &str) {
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
        self.os.record_timeline_event(
            REVIEW_TIMELINE_AREA,
            event_type,
            payload,
            Some(workspace.as_str()),
        );
    }

    /// Seed a Daily Memory Digest recipe workflow (test / fixture setup).
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

    /// Seed a non-automation workflow so run-now can 404 it as "not an automation".
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

/// The Review Center router.
pub fn router(state: GovernanceState) -> Router {
    Router::new()
        .route("/automation/reviews", get(list_items).post(create_item))
        .route("/automation/reviews/counts", get(review_counts))
        .route("/automation/reviews/bulk/approve", post(bulk_approve))
        .route("/automation/reviews/bulk/dismiss", post(bulk_dismiss))
        .route("/automation/reviews/:item_id", get(get_item))
        .route("/automation/reviews/:item_id/approve", post(approve_item))
        .route("/automation/reviews/:item_id/dismiss", post(dismiss_item))
        .route("/automation/reviews/:item_id/snooze", post(snooze_item))
        .route("/automation/reviews/:item_id/unsnooze", post(unsnooze_item))
        .route("/automation/reviews/:item_id/run_now", post(run_now_item))
        .with_state(state)
}
