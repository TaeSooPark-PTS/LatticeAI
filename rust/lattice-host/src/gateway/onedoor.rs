use std::path::{Path, PathBuf};
use std::sync::Arc;

use lattice_agent::surface::looproutes::LoopConfig;
use lattice_agent::tools::sandbox::Workspace;
use lattice_auth::{AuthConfig, AuthState};
use lattice_core::db::{RuntimeConfig, Store};
use lattice_core::graph_write::{BrainIngestEvent, GraphWriter};
use lattice_core::worker::WorkerSeamClient;
use lattice_platform::governance::hooks::{HooksStore, NativeHookSink};
use lattice_platform::governance::workflow_designer::{
    BrainEventPayload, LocalGraphSink, WorkflowDesignerState,
};

use super::agent_catalog::{self, PlatformCatalog};
use lattice_platform::governance::review_queue::GovernanceState;
use lattice_platform::workspaceos::workspace::{WorkspaceDeps, WorkspaceService, WorkspaceState};

use super::GatewayError;

pub const BLOB_DIR: &str = lattice_core::db::tables::state_files::KNOWLEDGE_GRAPH_BLOBS;

const STATIC_SEARCH_DEPTH: usize = 6;

pub struct OneDoorState {
    pub config: RuntimeConfig,
    pub store: Arc<Store>,
    pub graph: GraphWriter,
    pub auth: Arc<AuthState>,
    pub seam: WorkerSeamClient,
    pub client: reqwest::Client,
    pub data_dir: PathBuf,
    pub agent_root: PathBuf,
    pub workspace: Workspace,
    pub brain_dir: PathBuf,
    pub static_dir: PathBuf,
    pub workspace_state: WorkspaceState,
    pub resolver: Arc<WorkspaceService>,
    pub governance: GovernanceState,
    pub workflow_designer: WorkflowDesignerState,
    pub hooks: HooksStore,
    pub loop_config: LoopConfig,
    pub local_approvals: Arc<lattice_ingest::local_files_api::LocalApprovals>,
}

impl OneDoorState {
    pub fn open(
        worker_origin: &str,
        client: reqwest::Client,
        agent_root: &Path,
        loop_config: LoopConfig,
    ) -> Result<Self, GatewayError> {
        Self::open_with_config(
            RuntimeConfig::from_env().with_worker_origin(worker_origin),
            worker_origin,
            client,
            agent_root,
            loop_config,
        )
    }

    pub fn open_with_config(
        config: RuntimeConfig,
        worker_origin: &str,
        client: reqwest::Client,
        agent_root: &Path,
        loop_config: LoopConfig,
    ) -> Result<Self, GatewayError> {
        Self::open_with_auth(
            config,
            worker_origin,
            client,
            agent_root,
            loop_config,
            AuthConfig::from_env().with_stored_sso(),
        )
    }

    pub fn open_with_auth(
        config: RuntimeConfig,
        worker_origin: &str,
        client: reqwest::Client,
        agent_root: &Path,
        loop_config: LoopConfig,
        auth_config: AuthConfig,
    ) -> Result<Self, GatewayError> {
        let data_dir = config.data_dir().to_path_buf();
        let store = Arc::new(
            config
                .open_store()
                .map_err(|err| GatewayError::State(format!("cannot open the brain: {err}")))?,
        );
        let auth = AuthState::new(auth_config);
        auth.set_audit_sink(Arc::new(super::sinks::PlatformAuthAudit::new(&data_dir)));
        let seam = WorkerSeamClient::with_client(client.clone(), worker_origin);

        let workspace = Workspace::new(agent_root).map_err(|err| {
            GatewayError::State(format!(
                "cannot use {} as the agent workspace: {err}",
                agent_root.display()
            ))
        })?;

        let workspace_state = WorkspaceState::new(Arc::clone(&auth), &data_dir);
        let governance = GovernanceState::with_store(
            Arc::clone(&auth),
            Arc::clone(&workspace_state.store),
            agent_root,
            Some(seam.clone()),
        );
        let workflow_designer = WorkflowDesignerState::new(Arc::clone(&auth), &data_dir)
            .with_graph(Arc::new(LocalGraphSink))
            .with_governance(governance.clone());
        let triggers = workflow_designer.triggers();
        let graph = GraphWriter::open(Arc::clone(&store), data_dir.join(BLOB_DIR))
            .map_err(|err| GatewayError::State(format!("cannot open the graph writer: {err}")))?
            .with_ingest_listener(Arc::new(move |event: &BrainIngestEvent| {
                if event.duplicate {
                    return;
                }
                let payload = BrainEventPayload {
                    node_id: event.node_id.clone(),
                    title: event.title.clone(),
                    source_type: event.source_type.clone(),
                    workspace_id: event.workspace_id.clone(),
                    owner: event.owner.clone(),
                };
                let now = std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_secs_f64();
                let engine = Arc::clone(&triggers);
                if let Ok(handle) = tokio::runtime::Handle::try_current() {
                    handle.spawn_blocking(move || {
                        let _ = engine.dispatch_brain_event(&payload, now);
                    });
                } else {
                    let _ = engine.dispatch_brain_event(&payload, now);
                }
            }));

        let workspace_state = workspace_state
            .with_deps(WorkspaceDeps {
                seam: lattice_platform::workspaceos::workspace::GraphSeam::Native(graph.clone()),
                ..WorkspaceDeps::default()
            })
            .with_worker(seam.clone());
        let resolver = Arc::new(workspace_state.resolver());
        let loop_config = loop_config.with_proposals(Arc::new(governance.clone()));
        let shared_writer = Arc::new(SharedStateWriter(Arc::clone(&workspace_state.store)));
        lattice_retrieval::memory_api::wsos::install_state_writer(shared_writer.clone());
        lattice_agent::kernel::proposals::install_document_writer(shared_writer);
        let hooks = HooksStore::open(&data_dir);
        let loop_config =
            loop_config.with_hooks(Arc::new(NativeHookSink::new(hooks.clone(), "agent")));
        let loop_config = loop_config.with_catalog(Arc::new(PlatformCatalog::new(
            agent_catalog::skills_dir_for(Arc::clone(&auth), &data_dir),
        )));

        Ok(Self {
            static_dir: resolve_static_root(config.static_dir()),
            brain_dir: lattice_agent::tools::vault::default_brain_dir(),
            agent_root: agent_root.to_path_buf(),
            config,
            store,
            graph,
            auth,
            seam,
            client,
            data_dir,
            workspace,
            workspace_state,
            resolver,
            governance,
            workflow_designer,
            hooks,
            loop_config,
            local_approvals: lattice_ingest::local_files_api::LocalApprovals::new(),
        })
    }

    pub fn graph_stats(&self) -> Arc<dyn Fn() -> Result<serde_json::Value, String> + Send + Sync> {
        let store = Arc::clone(&self.store);
        Arc::new(move || probe_store_graph_stats(&store))
    }
}

pub fn probe_store_graph_stats(store: &Store) -> Result<serde_json::Value, String> {
    store
        .with_read_conn(probe_conn_graph_stats)
        .map_err(|err| format!("unavailable: {err}"))
}

fn probe_conn_graph_stats(
    conn: &rusqlite::Connection,
) -> Result<serde_json::Value, lattice_core::db::CoreError> {
    let table_or_view_exists = |name: &str| -> Result<bool, rusqlite::Error> {
        conn.query_row(
            "SELECT EXISTS(
                SELECT 1 FROM sqlite_master
                WHERE type IN ('table', 'view') AND name = ?1
            )",
            [name],
            |row| row.get(0),
        )
    };
    let count_existing = |table: &str| -> Result<i64, rusqlite::Error> {
        match table {
            "nodes" => conn.query_row("SELECT COUNT(*) FROM nodes", [], |row| row.get(0)),
            "edges" => conn.query_row("SELECT COUNT(*) FROM edges", [], |row| row.get(0)),
            _ => unreachable!("unsupported graph table probe"),
        }
    };

    let has_nodes = table_or_view_exists("nodes")?;
    let has_edges = table_or_view_exists("edges")?;
    let total_nodes = if has_nodes {
        count_existing("nodes")?
    } else {
        0
    };
    let total_edges = if has_edges {
        count_existing("edges")?
    } else {
        0
    };
    let schema_initialized = has_nodes && has_edges;
    Ok(serde_json::json!({
        "status": if schema_initialized { "ok" } else { "empty" },
        "schema_initialized": schema_initialized,
        "total_nodes": total_nodes,
        "total_edges": total_edges,
    }))
}

struct SharedStateWriter(Arc<lattice_platform::workspaceos::workspace::WorkspaceOsStore>);

impl SharedStateWriter {
    fn apply(&self, body: &mut dyn FnMut(&mut serde_json::Value)) -> Result<(), String> {
        self.0
            .mutate(|state| {
                body(state);
                Ok(())
            })
            .map_err(|error| error.to_string())
    }
}

impl lattice_retrieval::memory_api::wsos::StateWriter for SharedStateWriter {
    fn mutate(&self, body: &mut dyn FnMut(&mut serde_json::Value)) -> Result<(), String> {
        self.apply(body)
    }

    fn record_event(
        &self,
        area: &str,
        event_type: &str,
        payload: serde_json::Value,
        workspace_id: Option<&str>,
    ) -> Result<(), String> {
        self.0
            .record_timeline_event(area, event_type, payload, workspace_id);
        Ok(())
    }
}

impl lattice_agent::kernel::proposals::DocumentWriter for SharedStateWriter {
    fn mutate(&self, body: &mut dyn FnMut(&mut serde_json::Value)) -> Result<(), String> {
        self.apply(body)
    }
}

impl std::fmt::Debug for OneDoorState {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("OneDoorState")
            .field("data_dir", &self.data_dir)
            .field("static_dir", &self.static_dir)
            .field("agent_root", &self.agent_root)
            .field("worker_origin", &self.seam.origin())
            .finish()
    }
}

pub fn resolve_static_root(explicit: Option<&Path>) -> PathBuf {
    let base = base_dir_candidates()
        .into_iter()
        .find(|candidate| candidate.join("static").is_dir())
        .unwrap_or_else(|| std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")));
    lattice_platform::shell::static_ui::resolve_static_dir(explicit, &base, None)
}

fn base_dir_candidates() -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    if let Ok(cwd) = std::env::current_dir() {
        candidates.push(cwd);
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(macos) = exe.parent() {
            candidates.push(macos.join("../Resources"));
        }
        for ancestor in exe.ancestors().skip(1).take(STATIC_SEARCH_DEPTH) {
            candidates.push(ancestor.to_path_buf());
        }
    }
    candidates
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_explicit_static_dir_replaces_the_whole_chain() {
        let dir = tempfile::tempdir().expect("tempdir");
        let explicit = dir.path().join("assets");
        std::fs::create_dir_all(&explicit).expect("mkdir");
        assert_eq!(resolve_static_root(Some(&explicit)), explicit);
    }

    #[test]
    fn without_an_override_a_path_is_still_named() {
        let resolved = resolve_static_root(None);
        assert!(
            resolved.ends_with("static"),
            "the fallback must still name static/: {resolved:?}"
        );
    }

    #[test]
    fn the_candidates_start_at_the_working_directory() {
        let candidates = base_dir_candidates();
        assert!(!candidates.is_empty());
        if let Ok(cwd) = std::env::current_dir() {
            assert_eq!(candidates[0], cwd);
        }
    }

    #[test]
    fn the_blob_directory_is_the_one_the_ownership_map_names() {
        assert_eq!(BLOB_DIR, "knowledge_graph_blobs");
    }

    #[test]
    fn graph_stats_distinguishes_empty_store_from_sql_failure() {
        let dir = tempfile::tempdir().expect("tempdir");
        let db_path = dir.path().join("knowledge_graph.sqlite");
        let store = Arc::new(Store::open(&db_path).expect("open store"));

        let uninit = probe_store_graph_stats(&store).expect("uninitialized probe");
        assert_eq!(uninit["status"], "empty");
        assert_eq!(uninit["schema_initialized"], false);
        assert_eq!(uninit["total_nodes"], 0);
        assert_eq!(uninit["total_edges"], 0);

        let writer =
            GraphWriter::open(Arc::clone(&store), dir.path().join(BLOB_DIR)).expect("bootstrap");
        let empty = probe_store_graph_stats(&store).expect("empty probe");
        assert_eq!(empty["status"], "ok");
        assert_eq!(empty["schema_initialized"], true);
        assert_eq!(empty["total_nodes"], 0);
        assert_eq!(empty["total_edges"], 0);
        drop(writer);

        let broken_path = dir.path().join("broken_graph.sqlite");
        let broken_store = Store::open(&broken_path).expect("open broken store");
        broken_store
            .with_write_txn(|txn| {
                txn.execute_batch(
                    "CREATE VIEW nodes AS SELECT missing_column FROM nonexistent_backing_table;
                     CREATE TABLE edges (id TEXT PRIMARY KEY);",
                )?;
                Ok(())
            })
            .expect("create broken view");
        let err = probe_store_graph_stats(&broken_store).expect_err("broken SQL must not return 0");
        assert!(
            err.starts_with("unavailable:"),
            "expected unavailable prefix, got: {err}"
        );
    }
}
