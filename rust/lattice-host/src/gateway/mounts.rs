//! What the gateway mounts besides its own routes and the reverse proxy.
//!
//! Four crates hand back router factories, and this module is the one place
//! that decides where each of them lands and what state it needs. Keeping that
//! decision here rather than in [`super::build_router`] means the mount map is
//! one screen of code that can be read against the plan:
//!
//! | Route family | Crate | Answered by |
//! |---|---|---|
//! | `/rust/search/{hybrid,keyword,vector}` | `lattice-host` (P1) | [`super::search`] |
//! | `/rust/search/service-hybrid`, `/rust/graph/*`, `/rust/history*`, `/rust/context/assemble` | `lattice-retrieval` | `lattice_retrieval::router` |
//! | `/rust/ingest/{plan,chunk}` | `lattice-ingest` | `lattice_ingest::router` |
//! | `/rust/agent/{preflight,exec,contract}` | `lattice-agent` | `lattice_agent::router` |
//! | `/rust/agent/{run,resume,approvals}` | `lattice-agent` | `lattice_agent::router` |
//! | `/host/jobs`, `/host/jobs/tick` | `lattice-jobs` | `lattice_jobs::router` |
//!
//! The three P1 lanes stay with `lattice-host`: `lattice-retrieval`'s router
//! does not claim them (its own search path is `service-hybrid`, the
//! three-channel service fusion, which is a different engine), and the P1
//! handlers are the ones the committed hybrid goldens are asserted through.
//! Moving them would mean re-proving parity to gain nothing.
//!
//! Everything here is additive: a mount that cannot be prepared (no agent
//! workspace, no scheduler wired) is simply absent, and the gateway's namespace
//! guard answers 404 for it rather than proxying a `/rust/*` path to the worker
//! under a name that promised a native answer.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use axum::Router;
use lattice_agent::proposals::ProposalStore;
use lattice_agent::sandbox::Workspace;
use lattice_agent::LoopConfig;
use lattice_ingest::IngestApiConfig;
use lattice_jobs::{parse_flag, Scheduler, SchedulerConfig};

/// Environment variable naming the agent workspace root — Python's
/// `LATTICEAI_AGENT_ROOT`, read by `latticeai.tools.AGENT_ROOT`.
pub const AGENT_ROOT_ENV: &str = "LATTICEAI_AGENT_ROOT";

/// Environment variable that switches the background jobs timer off.
pub const JOBS_ENV: &str = "LATTICEAI_JOBS";

/// Directory the supervised worker is given as its agent root, relative to
/// `~/.ltcai` (see `supervisor::worker_env`).
const DESKTOP_AGENT_ROOT: [&str; 2] = ["desktop-runtime", "agent_workspace"];

/// The agent workspace root, resolved from values the caller already read.
///
/// The order is the one the product enforces at runtime: an explicit
/// `LATTICEAI_AGENT_ROOT` wins (it is what the operator — or this very host —
/// hands the worker), then the desktop runtime root the supervisor pins, and
/// finally Python's own last resort, the relative `agent_workspace`.
pub fn resolve_agent_root(env_value: Option<&str>, home: Option<&Path>) -> PathBuf {
    let configured = env_value.map(str::trim).unwrap_or("");
    if !configured.is_empty() {
        return PathBuf::from(configured);
    }
    match home {
        Some(home) => home
            .join(lattice_core::paths::DEFAULT_DATA_DIR_NAME)
            .join(DESKTOP_AGENT_ROOT[0])
            .join(DESKTOP_AGENT_ROOT[1]),
        None => PathBuf::from(DESKTOP_AGENT_ROOT[1]),
    }
}

/// [`resolve_agent_root`] against this process's environment.
pub fn default_agent_root() -> PathBuf {
    let env_value = std::env::var(AGENT_ROOT_ENV).ok();
    let home = std::env::var_os("HOME")
        .map(PathBuf::from)
        .filter(|path| !path.as_os_str().is_empty());
    resolve_agent_root(env_value.as_deref(), home.as_deref())
}

/// Whether the background jobs timer should run, from a raw env value.
///
/// Parsed exactly as `latticeai/core/config.py` parses its own booleans (via
/// `lattice_jobs::parse_flag`), and **on** unless someone says otherwise: the
/// gap this closes is a backlog nobody drains.
pub fn jobs_enabled(raw: Option<&str>) -> bool {
    parse_flag(raw, true)
}

/// [`jobs_enabled`] against this process's environment.
pub fn jobs_enabled_from_env() -> bool {
    jobs_enabled(std::env::var(JOBS_ENV).ok().as_deref())
}

/// A scheduler for `worker_origin`, sharing the host's HTTP client so the
/// loopback connection pool is not duplicated.
///
/// Environment-configured (`LATTICEAI_JOBS_INTERVAL`,
/// `LATTICEAI_JOBS_AUTORESUME`). Building it does not start it: the timer only
/// runs once someone calls [`Scheduler::spawn`], and `/host/jobs` reports
/// `enabled: false` until then.
pub fn scheduler(worker_origin: &str, client: reqwest::Client) -> Arc<Scheduler> {
    Arc::new(Scheduler::with_client(
        SchedulerConfig::from_env(worker_origin),
        client,
    ))
}

/// The same scheduler, draining the embedding queue **natively**.
///
/// Without a writer the tick is `POST {worker}/api/index/drain`, which is what
/// it was until v11.6.0 §W3b moved the drain into `lattice_jobs::index_api`.
/// The worker no longer serves that path, so an unbound scheduler now spends
/// every minute failing a request and backing off — visible in the log,
/// invisible in the product, and the queue never drains. Handing it the writer
/// points `tick()` at the native drain instead.
pub fn scheduler_with_graph(
    worker_origin: &str,
    client: reqwest::Client,
    graph: lattice_core::graph_write::GraphWriter,
) -> Arc<Scheduler> {
    Arc::new(
        Scheduler::with_client(SchedulerConfig::from_env(worker_origin), client).with_graph(graph),
    )
}

/// The loop orchestrator's configuration: where its AI worker listens, where
/// paused runs are stored, and the client to reach the worker with.
///
/// The worker origin is the gateway's own (`StatusProvider::worker_origin`), so
/// the loop talks to the very worker this host supervises — the one that was
/// handed `LATTICEAI_AGENT_TOOL_SEAM=1` and is therefore the only worker whose
/// seam endpoints answer at all.
///
/// `proposals` is the store a paused-for-approval run stages into. The loop's
/// own default (`JsonProposalStore`) appends to `workspace_os.json` directly,
/// which is wrong whenever `lattice_platform`'s Review Center is mounted in
/// this process: the document has an owner there — `WorkspaceOsStore`, whose
/// `load_state` reads the `workspace_os_state` SQLite row *before* the file —
/// so a JSON-only append is invisible to it and is overwritten by its next
/// save. The caller therefore names the writer: the product hands the very
/// `GovernanceState` the Review Center routes were built from
/// ([`super::GatewayState::agent_loop_config`]), and a host with no product
/// mounted has no owner to disagree with and hands the JSON store.
///
/// v11.7.0 added a second, lower guard for the same hazard —
/// `OneDoorState::open` installs `lattice_agent::proposals::DocumentWriter`,
/// so a `JsonProposalStore` built *anywhere* in a process that has an owner
/// writes through it. Naming the store here is still right (it is what makes
/// the id the loop reports the Review Center's own); the install is what makes
/// forgetting to harmless.
pub fn agent_loop_config(
    worker_origin: &str,
    client: reqwest::Client,
    proposals: Arc<dyn ProposalStore>,
) -> LoopConfig {
    LoopConfig {
        worker_origin: worker_origin.to_string(),
        runs_dir: lattice_agent::runs::default_runs_dir(),
        client: Some(client),
        proposals: None,
        // The hooks registry is the product's, so `OneDoorState` binds it —
        // a host with no `/api/hooks` mounted has no registry to fire through.
        hooks: None,
    }
    .with_proposals(proposals)
}

/// The agent kernel **and loop** routes, or `None` when this machine has no
/// workspace to judge paths against.
///
/// `Workspace::new` creates and canonicalises the root; a failure means the
/// directory cannot exist (permissions, a file in the way), and mounting
/// routes that would answer every path question wrongly is worse than not
/// mounting them.
pub fn agent_router(root: &Path, config: LoopConfig) -> Option<Router> {
    agent_router_parts(root, config, true)
}

/// [`agent_router`], optionally omitting the loop half.
///
/// The product's `agents::router` already merges `loop_router` at
/// `/rust/agent/{run,resume,approvals}`. Mounting it again here panics.
pub fn agent_router_parts(root: &Path, config: LoopConfig, include_loop: bool) -> Option<Router> {
    match Workspace::new(root) {
        Ok(workspace) => Some(if include_loop {
            lattice_agent::router(workspace, config)
        } else {
            lattice_agent::router::kernel_router(workspace)
        }),
        Err(err) => {
            eprintln!(
                "lattice-host: /rust/agent is unavailable — cannot use {} as the agent workspace: {err}",
                root.display()
            );
            None
        }
    }
}

/// Every mounted native router, merged into one.
///
/// `db` is the knowledge graph the retrieval routes read, `agent_root` the
/// workspace the kernel judges paths against, `agent` the loop orchestrator's
/// configuration, and `jobs` the scheduler whose status routes are exposed
/// (absent ⇒ `/host/jobs` is not mounted at all).
pub fn native_router(
    db: PathBuf,
    agent_root: &Path,
    agent: LoopConfig,
    jobs: Option<Arc<Scheduler>>,
) -> Router {
    native_router_parts(db, agent_root, agent, jobs, true)
}

/// [`native_router`], with the loop omitted when the product already mounts it.
pub fn native_router_parts(
    db: PathBuf,
    agent_root: &Path,
    agent: LoopConfig,
    jobs: Option<Arc<Scheduler>>,
    include_loop: bool,
) -> Router {
    let mut router =
        lattice_retrieval::router(db).merge(lattice_ingest::router(IngestApiConfig::default()));
    if let Some(mounted) = agent_router_parts(agent_root, agent, include_loop) {
        router = router.merge(mounted);
    }
    if let Some(scheduler) = jobs {
        router = router.merge(lattice_jobs::router(scheduler));
    }
    router
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_explicit_agent_root_wins() {
        assert_eq!(
            resolve_agent_root(Some(" /srv/work "), Some(Path::new("/home/u"))),
            PathBuf::from("/srv/work")
        );
    }

    #[test]
    fn the_default_is_the_root_the_supervisor_hands_the_worker() {
        for blank in [None, Some(""), Some("   ")] {
            assert_eq!(
                resolve_agent_root(blank, Some(Path::new("/home/u"))),
                PathBuf::from("/home/u/.ltcai/desktop-runtime/agent_workspace"),
                "the host must judge paths against the same root the worker uses"
            );
        }
    }

    #[test]
    fn without_a_home_the_relative_name_is_the_last_resort() {
        assert_eq!(
            resolve_agent_root(None, None),
            PathBuf::from("agent_workspace"),
            "Python's own fallback, not an invented one"
        );
    }

    #[test]
    fn the_process_default_resolves_without_touching_the_disk() {
        let root = default_agent_root();
        assert!(root.ends_with("agent_workspace"));
    }

    #[test]
    fn jobs_are_on_unless_switched_off() {
        assert!(jobs_enabled(None));
        assert!(jobs_enabled(Some("")));
        assert!(jobs_enabled(Some("1")));
        assert!(jobs_enabled(Some("yes")));
        assert!(!jobs_enabled(Some("0")));
        assert!(!jobs_enabled(Some("false")));
        assert!(!jobs_enabled(Some("off")));
        // Unparseable falls back to the default, as `_bool` does in Python.
        assert!(jobs_enabled(Some("perhaps")));
        let _ = jobs_enabled_from_env();
    }

    #[test]
    fn a_scheduler_is_built_stopped() {
        let scheduler = scheduler(
            "http://127.0.0.1:4825/",
            crate::supervisor::http_client().expect("client"),
        );
        assert!(!scheduler.is_running(), "mounting is not starting");
        assert_eq!(scheduler.config().worker_origin, "http://127.0.0.1:4825");
    }

    /// A loop config whose run store *and* proposal store are throwaway
    /// directories, so a test can never read or write the real ones — §P1c §5
    /// found a suite staging into the developer's own Review Center.
    fn test_loop_config(dir: &Path) -> LoopConfig {
        LoopConfig {
            runs_dir: dir.join("rust_agent_runs"),
            ..agent_loop_config(
                "http://127.0.0.1:1",
                crate::supervisor::http_client().expect("client"),
                Arc::new(lattice_agent::proposals::JsonProposalStore::new(
                    dir.join("proposals"),
                )),
            )
        }
    }

    #[test]
    fn an_unusable_agent_root_is_absent_rather_than_wrong() {
        let dir = tempfile::tempdir().expect("tempdir");
        let blocked = dir.path().join("a-file");
        std::fs::write(&blocked, b"not a directory").expect("write");
        assert!(agent_router(&blocked.join("root"), test_loop_config(dir.path())).is_none());
    }

    #[test]
    fn the_loop_config_points_at_the_supervised_worker() {
        let dir = tempfile::tempdir().expect("tempdir");
        let config = agent_loop_config(
            "http://127.0.0.1:4899",
            crate::supervisor::http_client().expect("client"),
            Arc::new(lattice_agent::proposals::JsonProposalStore::new(dir.path())),
        );
        assert_eq!(config.worker_origin, "http://127.0.0.1:4899");
        assert!(config.client.is_some(), "the loopback pool is shared");
        assert!(
            config.runs_dir.ends_with("rust_agent_runs"),
            "the native store is not Python's: {:?}",
            config.runs_dir
        );
    }

    /// The integrator item from §P1c: a config built here must carry the store
    /// the caller named, never fall back to the loop's `JsonProposalStore`
    /// default. `proposals: None` is what selects that default, so `is_some()`
    /// is the whole assertion — and the store it carries is the caller's, which
    /// is what makes the product's Review Center see a staged proposal.
    #[test]
    fn the_loop_stages_into_the_store_the_caller_named() {
        let dir = tempfile::tempdir().expect("tempdir");
        let store = Arc::new(lattice_agent::proposals::JsonProposalStore::new(
            dir.path().join("named"),
        ));
        let config = agent_loop_config(
            "http://127.0.0.1:4899",
            crate::supervisor::http_client().expect("client"),
            store.clone(),
        );
        let bound = config
            .proposals
            .as_ref()
            .expect("the caller's proposal store must be bound, not the default");
        assert!(
            std::sync::Arc::ptr_eq(&(store as Arc<dyn ProposalStore>), bound),
            "the very store the caller handed over, not a second one over the same directory"
        );
    }

    #[test]
    fn the_native_router_merges_without_route_conflicts() {
        let dir = tempfile::tempdir().expect("tempdir");
        let jobs = scheduler(
            "http://127.0.0.1:1",
            crate::supervisor::http_client().expect("client"),
        );
        let _router = native_router(
            dir.path().join("knowledge_graph.sqlite"),
            &dir.path().join("agent_workspace"),
            test_loop_config(dir.path()),
            Some(jobs),
        );
        let _without_jobs = native_router(
            dir.path().join("knowledge_graph.sqlite"),
            &dir.path().join("agent_workspace"),
            test_loop_config(dir.path()),
            None,
        );
    }
}
