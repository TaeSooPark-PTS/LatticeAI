//! Everything the product routes are built from, constructed exactly once.
//!
//! The One Door plan (docs/v11.6.0_ONE_DOOR_PLAN.md) turns this process into
//! the product server. Twelve crates hand back router factories and each one
//! wants some subset of the same seven things: the resolved runtime config, one
//! SQLite pool, one graph writer over that pool, one authentication state, one
//! loopback HTTP client pointed at the worker, the agent workspace, and the
//! workspace membership resolver.
//!
//! "Exactly once" is not tidiness, it is correctness in four places:
//!
//! * **`AuthState`** holds the session table, the rate-limit windows and the
//!   CSRF policy in memory. Two of them mean two rate-limit windows over one
//!   `users.json`, and a login that one half of the process cannot see (I2 §1).
//! * **`Store`** carries the single write connection. SQLite has one write lock
//!   per database, so a second pool only buys `SQLITE_BUSY` against ourselves
//!   (I1 §5.4).
//! * **`GraphWriter::open`** *is* the schema bootstrap, and it must run before
//!   any route serves — the position `KnowledgeGraphStore.__init__` holds in
//!   Python (W1 §6.1).
//! * **`WorkspaceResolver`** is what stops scoping from being a pass-through.
//!   Handing `None` is the documented standalone contract, i.e. every named
//!   workspace passes ungated (I2 §2, R1 §2).

use std::path::{Path, PathBuf};
use std::sync::Arc;

use lattice_agent::sandbox::Workspace;
use lattice_agent::LoopConfig;
use lattice_auth::{AuthConfig, AuthState};
use lattice_core::db::{RuntimeConfig, Store};
use lattice_core::graph_write::GraphWriter;
use lattice_core::worker::WorkerSeamClient;
use lattice_platform::hooks::{HooksStore, NativeHookSink};

use super::agent_catalog::{self, PlatformCatalog};
use lattice_platform::review_queue::GovernanceState;
use lattice_platform::workspace::{WorkspaceDeps, WorkspaceService, WorkspaceState};

use super::GatewayError;

/// Directory the graph writer stores blob payloads in, under the data dir.
pub const BLOB_DIR: &str = lattice_core::db::tables::state_files::KNOWLEDGE_GRAPH_BLOBS;

/// How deep to walk up from the executable when looking for a `static/` tree.
const STATIC_SEARCH_DEPTH: usize = 6;

/// The shared state every product family is built from.
pub struct OneDoorState {
    /// Data dir, static dir and worker origin, resolved from the environment.
    pub config: RuntimeConfig,
    /// The one connection pool over `knowledge_graph.sqlite`.
    pub store: Arc<Store>,
    /// The one writer. Cloning it shares the pool, so it shares the write lock.
    pub graph: GraphWriter,
    /// Sessions, roles, rate limits, CSRF. Process-wide.
    pub auth: Arc<AuthState>,
    /// The compute seam: `POST /worker/{embed,parse,render/*,asr,…}`.
    pub seam: WorkerSeamClient,
    /// The gateway's loopback pool, shared by every seam client built from it.
    pub client: reqwest::Client,
    /// `<data_dir>`, already resolved — the same value every family writes into.
    pub data_dir: PathBuf,
    /// The agent sandbox root, and the sandbox itself.
    pub agent_root: PathBuf,
    /// The sandbox the tool routes and the loop judge paths against.
    pub workspace: Workspace,
    /// `BRAIN_DIR` — the knowledge garden vault.
    pub brain_dir: PathBuf,
    /// The resolved `static/` root the SPA and assets are served from.
    pub static_dir: PathBuf,
    /// Workspace registry state; also the source of the membership resolver.
    pub workspace_state: WorkspaceState,
    /// Membership, for every family that scopes a read or a write.
    pub resolver: Arc<WorkspaceService>,
    /// The workspace-OS document: the Review Center, change proposals,
    /// automation and hooks all read and write through this one handle.
    ///
    /// Built here rather than inside the platform router because the agent loop
    /// stages proposals into it too. Since v11.7.0 `GovernanceState` is a
    /// *facade* over one `WorkspaceOsStore` handle — it holds no copy of the
    /// document, and every mutation is a load-apply-save under that store's
    /// write lock. What must not be duplicated is therefore the **handle**: a
    /// second `GovernanceState::open` over the same directory would take a
    /// second lock, so a review write and a workspace write could interleave
    /// and the loser's change would vanish. Cloning shares the `Arc`, so every
    /// clone writes under the one lock.
    pub governance: GovernanceState,
    /// The hooks registry: `hooks.json` plus the `hooks_runs.json` log.
    ///
    /// Opened here for the reason [`Self::governance`] is: it holds both
    /// documents in memory, and the agent loop is a *second producer of runs*
    /// now that user `pre_tool` / `post_tool` hooks fire for native tools
    /// (v11.7.0). A second `HooksStore::open` over the same directory would
    /// not see this one's records and would overwrite them on its next save,
    /// so `/api/hooks` and the loop share this handle.
    pub hooks: HooksStore,
    /// The loop orchestrator's configuration, already bound to
    /// [`Self::governance`] and [`Self::hooks`].
    pub loop_config: LoopConfig,
    /// One approval table for folder ingest / `/local/*`, redeemed by
    /// `/permissions/approve`. Two tables was the N7 404.
    pub local_approvals: Arc<lattice_ingest::local_files_api::LocalApprovals>,
}

impl OneDoorState {
    /// Build everything, in dependency order, or say why it could not be built.
    ///
    /// `worker_origin` is the origin this host supervises — the worker that was
    /// handed `LATTICEAI_AGENT_TOOL_SEAM=1` and is therefore the only one whose
    /// seams answer at all. `client` is the gateway's pool, shared so the
    /// loopback connections are not duplicated per family.
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

    /// [`OneDoorState::open`] over a configuration the caller resolved.
    ///
    /// The product reads the environment; a test must not, because
    /// `RuntimeConfig::from_env` would point the schema bootstrap at
    /// `~/.ltcai/knowledge_graph.sqlite` — the real Brain of whoever is running
    /// the suite.
    pub fn open_with_config(
        config: RuntimeConfig,
        worker_origin: &str,
        client: reqwest::Client,
        agent_root: &Path,
        loop_config: LoopConfig,
    ) -> Result<Self, GatewayError> {
        let data_dir = config.data_dir().to_path_buf();
        let store = Arc::new(
            config
                .open_store()
                .map_err(|err| GatewayError::State(format!("cannot open the brain: {err}")))?,
        );
        // The bootstrap. Before this returns, the schema is the one a Python
        // build would have made; after it, nothing else may create tables.
        let graph = GraphWriter::open(Arc::clone(&store), data_dir.join(BLOB_DIR))
            .map_err(|err| GatewayError::State(format!("cannot open the graph writer: {err}")))?;

        let auth = AuthState::new(AuthConfig::from_env().with_stored_sso());
        let seam = WorkerSeamClient::with_client(client.clone(), worker_origin);

        let workspace = Workspace::new(agent_root).map_err(|err| {
            GatewayError::State(format!(
                "cannot use {} as the agent workspace: {err}",
                agent_root.display()
            ))
        })?;

        let workspace_state = WorkspaceState::new(Arc::clone(&auth), &data_dir)
            .with_deps(WorkspaceDeps {
                seam: lattice_platform::workspace::GraphSeam::Native(graph.clone()),
                ..WorkspaceDeps::default()
            })
            .with_worker(seam.clone());
        let resolver = Arc::new(workspace_state.resolver());

        // One store, two families. `GovernanceState::open` would build a second
        // `WorkspaceOsStore` over the same `workspace_os.json` — a second lock,
        // so a review write and a workspace write could interleave and the
        // loser's change would vanish. Sharing the `Arc` is what makes the
        // single-writer guarantee hold across the whole process (§F-A).
        let governance = GovernanceState::with_store(
            Arc::clone(&auth),
            Arc::clone(&workspace_state.store),
            agent_root,
            Some(seam.clone()),
        );
        // §P1c's integrator item: staging is in-process now, so the loop writes
        // through the Review Center's own handle rather than a JSON store the
        // Review Center would neither see nor preserve.
        let loop_config = loop_config.with_proposals(Arc::new(governance.clone()));
        // …and the same for the memory tiers and brain synthesis, which write
        // review items and memories into that document from `lattice-retrieval`.
        // That crate cannot name the platform's store (the dependency runs the
        // other way), so it declares the port and this is the one place that
        // owns both types (§F-A).
        let shared_writer = Arc::new(SharedStateWriter(Arc::clone(&workspace_state.store)));
        lattice_retrieval::memory_api::wsos::install_state_writer(shared_writer.clone());
        // …and once more for `lattice-agent`'s `JsonProposalStore` (§F-G). The
        // loop is handed `governance` two lines up, so the *default* store is
        // never the one that stages in this process — but "no future caller
        // forgets" is a rule nobody can enforce by reading. With the owner
        // installed, a `JsonProposalStore` built anywhere in this process
        // writes through the same lock, the same SQLite row and the same
        // mirror file instead of appending to a document the Review Center
        // would overwrite.
        lattice_agent::proposals::install_document_writer(shared_writer);
        // v11.7.0 F-BC: the same argument, one document over. A user `pre_tool`
        // hook can block a native write and every fire is logged, so the loop
        // and `/api/hooks` must be looking at one registry.
        let hooks = HooksStore::open(&data_dir);
        let loop_config = loop_config.with_hooks(Arc::new(NativeHookSink::new(
            hooks.clone(),
            // `dispatch_tool(source="agent")` — what the loop called itself.
            "agent",
        )));
        // v12.0.0: one catalog. Until now a run was *told* about installed
        // skills (three lines in the executor prompt) and could not invoke one,
        // and the MCP surface was something only the user's editor could reach.
        // Both are menu rows now, resolved from the very directory `POST /mcp`
        // scans so a skill means the same thing on both surfaces — see
        // `super::agent_catalog` for what each kind does and how it is governed.
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
            hooks,
            loop_config,
            local_approvals: lattice_ingest::local_files_api::LocalApprovals::new(),
        })
    }

    /// A read-only `{total_nodes, total_edges}` probe for the admin summary.
    ///
    /// Bound as a callback rather than reached for directly because
    /// `lattice-platform` must not learn the graph's SQL: the admin report only
    /// wants two numbers, and a store that has no `nodes` table yet answers
    /// zeroes rather than failing the whole page.
    pub fn graph_stats(&self) -> Arc<dyn Fn() -> Result<serde_json::Value, String> + Send + Sync> {
        let store = Arc::clone(&self.store);
        Arc::new(move || {
            store
                .with_read_conn(|conn| {
                    let count = |table: &str| -> i64 {
                        conn.query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |row| {
                            row.get(0)
                        })
                        .unwrap_or(0)
                    };
                    Ok(serde_json::json!({
                        "total_nodes": count("nodes"),
                        "total_edges": count("edges"),
                    }))
                })
                .map_err(|err| err.to_string())
        })
    }
}

/// The `workspace_os.json` port both leaf crates declare, answered by the
/// platform store that owns the document.
///
/// Three crates want the same thing — load, apply, save, under one lock — and
/// the only reason this adapter exists is that `lattice-retrieval` and
/// `lattice-agent` cannot name `lattice-platform` (the dependency runs the
/// other way, and inverting it for one file write would be an architecture
/// change rather than a fix). This host is the one place that can see all
/// three types, so it is where they are joined.
struct SharedStateWriter(Arc<lattice_platform::workspace::WorkspaceOsStore>);

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
        // `record_timeline_event` is the owner's: it caps the timeline at
        // 10,000 entries and echoes to the realtime sink. A review item this
        // process wrote from `lattice-retrieval` therefore reaches the same
        // readers as one the Review Center wrote itself.
        self.0
            .record_timeline_event(area, event_type, payload, workspace_id);
        Ok(())
    }
}

impl lattice_agent::proposals::DocumentWriter for SharedStateWriter {
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

/// The `static/` root, resolved the way `latticeai/core/config.py:225` does.
///
/// Python computes `BASE_DIR` from `__file__`, which a Rust binary has no
/// equivalent of, so the base is found rather than derived: the working
/// directory first (a dev run from the checkout), then the ancestors of this
/// executable (a `rust/target/release/lattice-host` run, and the bundled
/// `Resources/` tree beside a packaged binary). The first candidate that
/// actually holds a `static/` directory wins; if none does, the working
/// directory is used so the resulting 404s name a path a person recognises,
/// which is what I4 §2 means by "an honest 404, never cwd-guessing".
///
/// `LATTICEAI_STATIC_DIR` *replaces* the whole chain, as it does in Python.
pub fn resolve_static_root(explicit: Option<&Path>) -> PathBuf {
    let base = base_dir_candidates()
        .into_iter()
        .find(|candidate| candidate.join("static").is_dir())
        .unwrap_or_else(|| std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")));
    lattice_platform::static_ui::resolve_static_dir(explicit, &base, None)
}

/// Where a `static/` tree could plausibly be, nearest first.
fn base_dir_candidates() -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    if let Ok(cwd) = std::env::current_dir() {
        candidates.push(cwd);
    }
    if let Ok(exe) = std::env::current_exe() {
        // `…/Contents/MacOS/lattice-host` → `…/Contents/Resources`.
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
        // Whatever this machine resolves to, the answer must be a `static`
        // directory name rather than an empty path — a 404 has to say where it
        // looked.
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
}
