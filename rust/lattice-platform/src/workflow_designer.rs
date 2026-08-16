//! Workflow designer — port of `latticeai/api/workflow_designer.py`.
//!
//! Definitions and runs live in `workspace_os.json` (the same file
//! `WorkspaceOSStore` writes). This module owns only the `workflows` /
//! `workflow_runs` arrays; every other key is left untouched so R1's workspace
//! document stays the document of record.
//!
//! `GET /workflows` is a STATIC page shell (WP-I4) and is not mounted here.

use std::path::Path;
use std::sync::Arc;

use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_auth::pyjson::OrderedMap;
use lattice_auth::response::json_response;
use lattice_auth::{AuthState, Identity};
use lattice_core::db::tables::state_files;
use serde_json::json;

mod contract;
mod definition;
mod executor;
mod handlers;
mod recipes;
mod store;
mod time;

pub(crate) use executor::{
    execute_workflow_now, is_awaiting_approval, resume_paused, start_run, store_data_dir,
    ToolContext,
};
use handlers::{
    automation_recipes, create_definition, export_definition, get_definition, import_definition,
    install_recipe, list_all_runs, list_definition_runs, list_definitions, resume_run,
    run_definition, run_replay, stop_run, trigger_status, update_definition, validate_workflow,
};
use store::WorkflowStore;
use time::json_hash;

/// Routes this family mounts. `GET /workflows` is I4's.
pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/workflows/api/automation/recipes"),
    ("POST", "/workflows/api/automation/recipes/:recipe_id"),
    ("GET", "/workflows/api/definitions"),
    ("POST", "/workflows/api/definitions"),
    ("GET", "/workflows/api/definitions/:workflow_id"),
    ("PATCH", "/workflows/api/definitions/:workflow_id"),
    ("POST", "/workflows/api/definitions/:workflow_id/run"),
    ("GET", "/workflows/api/definitions/:workflow_id/runs"),
    ("GET", "/workflows/api/export/:workflow_id"),
    ("POST", "/workflows/api/import"),
    ("GET", "/workflows/api/runs"),
    ("GET", "/workflows/api/runs/:run_id/replay"),
    ("POST", "/workflows/api/runs/:run_id/resume"),
    ("POST", "/workflows/api/runs/:run_id/stop"),
    ("GET", "/workflows/api/triggers"),
    ("POST", "/workflows/api/validate"),
];

const NODE_TYPES: &[&str] = &[
    "trigger",
    "tool",
    "skill",
    "plugin",
    "agent",
    "condition",
    "output",
];
const WORKFLOW_ENGINE_VERSION: &str = "2.2.0";
const DEFAULT_WORKSPACE_ID: &str = "personal";
const ACTIVE_STATUSES: &[&str] = &["queued", "running", "cancelling", "awaiting_approval"];
const TERMINAL_STATUSES: &[&str] = &[
    "ok",
    "failed",
    "cancelled",
    "interrupted",
    "partial",
    "rejected",
];

/// Optional graph ingest. Production wires the worker seam; tests stub an id.
pub trait GraphSink: Send + Sync {
    fn ingest_workflow(&self, name: &str, workflow_id: &str) -> Option<String>;
}

/// What the family needs to serve the designer.
#[derive(Clone)]
pub struct WorkflowDesignerState {
    auth: Arc<AuthState>,
    pub(crate) store: Arc<WorkflowStore>,
    pub(crate) graph: Option<Arc<dyn GraphSink>>,
    pub(crate) trigger_tz: String,
    pub(crate) trigger_tick_seconds: f64,
}

impl WorkflowDesignerState {
    /// Point the designer at `data_dir/workspace_os.json`.
    pub fn new(auth: Arc<AuthState>, data_dir: impl AsRef<Path>) -> Self {
        let path = data_dir.as_ref().join(state_files::WORKSPACE_OS);
        Self {
            auth,
            store: Arc::new(WorkflowStore::open(path)),
            graph: None,
            trigger_tz: std::env::var("LATTICE_TZ").unwrap_or_else(|_| "UTC".into()),
            trigger_tick_seconds: 5.0,
        }
    }

    /// Attach a graph sink so creates can stamp `graph_node_id`.
    pub fn with_graph(mut self, graph: Arc<dyn GraphSink>) -> Self {
        self.graph = Some(graph);
        self
    }

    /// On-disk path this instance reads and writes.
    pub fn path(&self) -> &Path {
        &self.store.path
    }
}

/// A graph sink that mints a stable local id without touching the KG.
pub struct LocalGraphSink;

impl GraphSink for LocalGraphSink {
    fn ingest_workflow(&self, name: &str, workflow_id: &str) -> Option<String> {
        Some(format!(
            "node-{}",
            &json_hash(&json!([name, workflow_id]))[..16]
        ))
    }
}

/// Router factory.
pub fn router(state: WorkflowDesignerState) -> Router {
    Router::new()
        .route(
            "/workflows/api/definitions",
            get(list_definitions).post(create_definition),
        )
        .route(
            "/workflows/api/definitions/:workflow_id",
            get(get_definition).patch(update_definition),
        )
        .route(
            "/workflows/api/definitions/:workflow_id/run",
            post(run_definition),
        )
        .route(
            "/workflows/api/definitions/:workflow_id/runs",
            get(list_definition_runs),
        )
        .route("/workflows/api/validate", post(validate_workflow))
        .route("/workflows/api/runs", get(list_all_runs))
        .route("/workflows/api/runs/:run_id/stop", post(stop_run))
        .route("/workflows/api/runs/:run_id/resume", post(resume_run))
        .route("/workflows/api/runs/:run_id/replay", get(run_replay))
        .route("/workflows/api/triggers", get(trigger_status))
        .route("/workflows/api/automation/recipes", get(automation_recipes))
        .route(
            "/workflows/api/automation/recipes/:recipe_id",
            post(install_recipe),
        )
        .route("/workflows/api/export/:workflow_id", get(export_definition))
        .route("/workflows/api/import", post(import_definition))
        .with_state(state)
}

fn ok(body: &OrderedMap) -> Response {
    let rendered = serde_json::to_string(body).unwrap_or_else(|_| "{}".into());
    json_response(StatusCode::OK, &rendered, None)
}

fn require_user(state: &WorkflowDesignerState, headers: &HeaderMap) -> Result<Identity, Response> {
    state.auth.require_user(headers)
}

fn scope_from_request(headers: &HeaderMap, query_ws: Option<&str>) -> String {
    query_ws
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .or_else(|| {
            headers
                .get("x-workspace-id")
                .and_then(|value| value.to_str().ok())
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .map(str::to_string)
        })
        .unwrap_or_else(|| DEFAULT_WORKSPACE_ID.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use definition::validate_definition;

    #[test]
    fn validate_rejects_unknown_types() {
        let errors = validate_definition(&json!({
            "name": "bad",
            "nodes": [{"id": "x", "type": "not-a-type"}]
        }));
        assert!(errors.iter().any(|e| e.contains("trigger")));
        assert!(errors.iter().any(|e| e.contains("unknown type")));
    }

    #[test]
    fn validate_accepts_a_manual_trigger() {
        let errors = validate_definition(&json!({
            "name": "ok",
            "nodes": [{"id": "trigger", "type": "trigger", "name": "Manual", "config": {"trigger": "manual"}, "next": null}]
        }));
        assert!(errors.is_empty());
    }
}
