//! Review Center (`latticeai/api/review_queue.py`) — native.
//!
//! Persistence is the workspace-OS review-item store (`workspace_os.json` +
//! the `workspace_os_state` SQLite row). Status transitions are native.
//! Approving a `change_proposal` item applies the staged file natively, under
//! the agent sandbox ([`crate::change_proposals`] — the worker seam it used to
//! go through was retired in v11.6.0 §P1a); promoting an `agent_followup`
//! writes a workflow draft and delegates `ingest_event` to
//! `POST /worker/graph/mutate`.

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
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use axum::body::Body;
use axum::extract::{Path as AxumPath, Query, State};
use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_auth::response::json_response;
use lattice_auth::{AuthState, Identity, OrderedMap};
use lattice_core::db::tables::state_files;
use lattice_core::messages::{self, LANGUAGE_HEADER};
use lattice_core::worker::{WorkerSeamClient, WorkerSeamError};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::change_proposals::{self, ProposalConflict};

mod handlers;
mod http;
mod os;
mod store;
mod workflows;

use handlers::{
    approve_item, bulk_approve, bulk_dismiss, create_item, dismiss_item, get_item, list_items,
    review_counts, run_now_item, snooze_item, unsnooze_item,
};
use os::{load_workspace_os, save_workspace_os};

pub(crate) use handlers::approve_one;
pub(crate) use http::{
    gate_read, gate_write, http_detail, internal_server_error, into_value, json_hash, json_ok,
    json_status, language, localized, map_str, map_worker_error, not_found_localized, now_iso,
    parse_object, parse_object_optional, require_admin, require_field, require_user, sha256_text,
    string_field, string_field_or,
};
pub(crate) use store::{
    create_review_item, list_review_items, load_review_item, review_item_raw_view,
    review_item_view, ReviewError,
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
const WORKSPACE_OS_VERSION: &str = "11.5.2";
const BULK_ACTION_CAP: usize = 200;

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
    inner: Arc<Mutex<OsInner>>,
}

struct OsInner {
    state: Value,
}

impl GovernanceState {
    /// Open (or start) the on-disk workspace OS document.
    pub fn open(
        auth: Arc<AuthState>,
        data_dir: impl Into<PathBuf>,
        agent_root: impl Into<PathBuf>,
        worker: Option<WorkerSeamClient>,
    ) -> Self {
        let data_dir = data_dir.into();
        let agent_root = agent_root.into();
        let _ = std::fs::create_dir_all(&data_dir);
        let _ = std::fs::create_dir_all(&agent_root);
        let state = load_workspace_os(&data_dir);
        Self {
            auth,
            data_dir,
            agent_root,
            worker,
            inner: Arc::new(Mutex::new(OsInner { state })),
        }
    }

    /// The workspace-OS JSON document.
    pub fn state_path(&self) -> PathBuf {
        self.data_dir.join(state_files::WORKSPACE_OS)
    }

    pub(crate) fn with_state<T>(&self, f: impl FnOnce(&Value) -> T) -> T {
        let guard = self.inner.lock().expect("workspace os lock");
        f(&guard.state)
    }

    pub(crate) fn update_state<T>(&self, f: impl FnOnce(&mut Value) -> T) -> T {
        let mut guard = self.inner.lock().expect("workspace os lock");
        let out = f(&mut guard.state);
        save_workspace_os(&self.data_dir, &guard.state);
        out
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
