//! Router factory, shared state, and the mounted-route table.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use axum::extract::FromRef;
use axum::routing::{delete, get, patch, post};
use axum::Router;
use lattice_auth::AuthState;
use lattice_core::worker::WorkerSeamClient;

use super::computer::VsCodePresence;
use super::deps::WorkspaceDeps;
use super::handlers_core;
use super::handlers_more;
use super::service::{IdentityFn, WorkspaceService};
use super::store::WorkspaceOsStore;

/// Mounted (method, path) pairs — axum 0.7 spelling. Greedy `{node_id:path}`
/// is `/*node_id`. Page shells (`GET /workspace`, `GET /onboarding`) live
/// with [`crate::ui_redirects`].
pub const MOUNTED: &[(&str, &str)] = &[
    ("POST", "/workspace/activate"),
    ("GET", "/workspace/agents"),
    ("POST", "/workspace/agents/runs"),
    ("GET", "/workspace/audit-timeline"),
    ("GET", "/workspace/computer-memory"),
    ("POST", "/workspace/computer-memory"),
    ("POST", "/workspace/computer-memory/activity"),
    ("GET", "/workspace/editions"),
    ("GET", "/workspace/indexing"),
    ("POST", "/workspace/indexing/:source_id/pause"),
    ("POST", "/workspace/indexing/:source_id/remove"),
    ("POST", "/workspace/indexing/:source_id/resume"),
    ("GET", "/workspace/memories"),
    ("POST", "/workspace/memories"),
    ("GET", "/workspace/memories/search"),
    ("DELETE", "/workspace/memories/:memory_id"),
    ("POST", "/workspace/onboarding/complete"),
    ("GET", "/workspace/onboarding/hardware"),
    ("GET", "/workspace/onboarding/model-recommendations"),
    ("GET", "/workspace/onboarding/status"),
    ("POST", "/workspace/onboarding/step"),
    ("POST", "/workspace/orgs"),
    ("GET", "/workspace/orgs/:workspace_id"),
    ("PATCH", "/workspace/orgs/:workspace_id"),
    ("POST", "/workspace/orgs/:workspace_id/archive"),
    ("POST", "/workspace/orgs/:workspace_id/members"),
    ("DELETE", "/workspace/orgs/:workspace_id/members/:user_id"),
    ("PATCH", "/workspace/orgs/:workspace_id/members/:user_id"),
    ("GET", "/workspace/orgs/:workspace_id/summary"),
    ("GET", "/workspace/os"),
    ("GET", "/workspace/registry"),
    ("GET", "/workspace/relationships/*node_id"),
    ("GET", "/workspace/skills"),
    ("POST", "/workspace/skills/disable"),
    ("POST", "/workspace/skills/enable"),
    ("POST", "/workspace/skills/install"),
    ("POST", "/workspace/skills/uninstall"),
    ("POST", "/workspace/skills/update"),
    ("GET", "/workspace/snapshots"),
    ("POST", "/workspace/snapshots"),
    ("POST", "/workspace/snapshots/compare"),
    ("GET", "/workspace/snapshots/:snapshot_id"),
    ("POST", "/workspace/snapshots/:snapshot_id/export"),
    ("POST", "/workspace/snapshots/:snapshot_id/restore"),
    ("GET", "/workspace/snapshots/:snapshot_id/:area"),
    ("GET", "/workspace/time-machine"),
    ("GET", "/workspace/time-machine/:snapshot_id/:area"),
    ("GET", "/workspace/traces"),
    ("POST", "/workspace/vscode/send"),
    ("GET", "/workspace/vscode/status"),
    ("POST", "/workspace/vscode/status"),
    ("GET", "/workspace/workflows"),
    ("POST", "/workspace/workflows"),
    ("POST", "/workspace/workflows/:workflow_id/events"),
];

/// Everything a workspace handler needs.
#[derive(Clone)]
pub struct WorkspaceState {
    /// Process-wide auth.
    pub auth: Arc<AuthState>,
    /// Native Workspace OS store.
    pub store: Arc<WorkspaceOsStore>,
    /// Graph seam + providers.
    pub deps: WorkspaceDeps,
    /// In-process VS Code presence.
    pub vscode: VsCodePresence,
    /// Data directory (audit log, chat history).
    pub data_dir: PathBuf,
    /// Worker seam — used by onboarding to fill the same probe + catalog
    /// `/models/recommendations` and `/setup/scan` already serve.
    pub worker: Option<WorkerSeamClient>,
}

impl WorkspaceState {
    /// Open the store under `data_dir` and attach default (graph-absent) deps.
    ///
    /// `shared`, not `open`: every family that writes this document must hold
    /// the same handle, and the Review Center / marketplace / designer reach
    /// the store through the same registry (v11.7.0 §F-A).
    pub fn new(auth: Arc<AuthState>, data_dir: impl AsRef<Path>) -> Self {
        let data_dir = data_dir.as_ref().to_path_buf();
        Self {
            auth,
            store: WorkspaceOsStore::shared(&data_dir),
            deps: WorkspaceDeps::default(),
            vscode: VsCodePresence::new(),
            data_dir,
            worker: None,
        }
    }

    /// Replace the default providers / seam.
    pub fn with_deps(mut self, deps: WorkspaceDeps) -> Self {
        self.deps = deps;
        self
    }

    /// Point onboarding at the worker the host already supervises.
    pub fn with_worker(mut self, worker: WorkerSeamClient) -> Self {
        self.worker = Some(worker);
        self
    }

    /// The resolver other families (and this one) hand to I2.
    pub fn resolver(&self) -> WorkspaceService {
        let auth = Arc::clone(&self.auth);
        let resolve: IdentityFn =
            Arc::new(move |email| auth.users().load().user_id_for_email(email));
        WorkspaceService::new(Arc::clone(&self.store), resolve)
    }
}

impl FromRef<WorkspaceState> for Arc<AuthState> {
    fn from_ref(state: &WorkspaceState) -> Self {
        Arc::clone(&state.auth)
    }
}

/// Router factory. The integrator merges this onto the gateway.
pub fn router(state: WorkspaceState) -> Router {
    Router::new()
        .route("/workspace/os", get(handlers_core::os_summary))
        .route(
            "/workspace/onboarding/status",
            get(handlers_core::onboarding_status),
        )
        .route(
            "/workspace/onboarding/step",
            post(handlers_core::onboarding_step),
        )
        .route(
            "/workspace/onboarding/complete",
            post(handlers_core::onboarding_complete),
        )
        .route(
            "/workspace/onboarding/hardware",
            get(handlers_core::onboarding_hardware),
        )
        .route(
            "/workspace/onboarding/model-recommendations",
            get(handlers_core::onboarding_models),
        )
        .route("/workspace/traces", get(handlers_core::traces))
        .route("/workspace/indexing", get(handlers_core::indexing))
        .route(
            "/workspace/indexing/:source_id/pause",
            post(handlers_core::indexing_pause),
        )
        .route(
            "/workspace/indexing/:source_id/resume",
            post(handlers_core::indexing_resume),
        )
        .route(
            "/workspace/indexing/:source_id/remove",
            post(handlers_core::indexing_remove),
        )
        .route(
            "/workspace/snapshots",
            get(handlers_core::snapshots_list).post(handlers_core::snapshots_create),
        )
        .route(
            "/workspace/snapshots/compare",
            post(handlers_core::snapshots_compare),
        )
        .route(
            "/workspace/snapshots/:snapshot_id",
            get(handlers_core::snapshots_get),
        )
        .route(
            "/workspace/snapshots/:snapshot_id/:area",
            get(handlers_core::snapshots_area),
        )
        .route(
            "/workspace/snapshots/:snapshot_id/export",
            post(handlers_core::snapshots_export),
        )
        .route(
            "/workspace/snapshots/:snapshot_id/restore",
            post(handlers_core::snapshots_restore),
        )
        .route("/workspace/time-machine", get(handlers_core::time_machine))
        .route(
            "/workspace/time-machine/:snapshot_id/:area",
            get(handlers_core::time_machine_view),
        )
        .route(
            "/workspace/memories",
            get(handlers_core::memories_list).post(handlers_core::memories_upsert),
        )
        .route(
            "/workspace/memories/search",
            get(handlers_core::memories_search),
        )
        .route(
            "/workspace/memories/:memory_id",
            delete(handlers_core::memories_delete),
        )
        .route("/workspace/agents", get(handlers_more::agents_list))
        .route("/workspace/agents/runs", post(handlers_more::agents_run))
        .route(
            "/workspace/relationships/*node_id",
            get(handlers_more::relationships),
        )
        .route(
            "/workspace/computer-memory",
            get(handlers_more::computer_get).post(handlers_more::computer_config),
        )
        .route(
            "/workspace/computer-memory/activity",
            post(handlers_more::computer_activity),
        )
        .route(
            "/workspace/workflows",
            get(handlers_more::workflows_list).post(handlers_more::workflows_create),
        )
        .route(
            "/workspace/workflows/:workflow_id/events",
            post(handlers_more::workflows_event),
        )
        .route("/workspace/skills", get(handlers_more::skills_list))
        .route(
            "/workspace/skills/install",
            post(handlers_more::skills_install),
        )
        .route(
            "/workspace/skills/uninstall",
            post(handlers_more::skills_uninstall),
        )
        .route(
            "/workspace/skills/enable",
            post(handlers_more::skills_enable),
        )
        .route(
            "/workspace/skills/disable",
            post(handlers_more::skills_disable),
        )
        .route(
            "/workspace/skills/update",
            post(handlers_more::skills_update),
        )
        .route(
            "/workspace/audit-timeline",
            get(handlers_more::audit_timeline),
        )
        .route(
            "/workspace/vscode/status",
            get(handlers_more::vscode_status).post(handlers_more::vscode_status_update),
        )
        .route("/workspace/vscode/send", post(handlers_more::vscode_send))
        .route("/workspace/registry", get(handlers_more::registry))
        .route("/workspace/editions", get(handlers_more::editions))
        .route("/workspace/activate", post(handlers_more::activate))
        .route("/workspace/orgs", post(handlers_more::org_create))
        .route(
            "/workspace/orgs/:workspace_id",
            get(handlers_more::org_get).patch(handlers_more::org_update),
        )
        .route(
            "/workspace/orgs/:workspace_id/summary",
            get(handlers_more::org_summary),
        )
        .route(
            "/workspace/orgs/:workspace_id/archive",
            post(handlers_more::org_archive),
        )
        .route(
            "/workspace/orgs/:workspace_id/members",
            post(handlers_more::org_add_member),
        )
        .route(
            "/workspace/orgs/:workspace_id/members/:user_id",
            patch(handlers_more::org_update_member).delete(handlers_more::org_remove_member),
        )
        .with_state(state)
}
