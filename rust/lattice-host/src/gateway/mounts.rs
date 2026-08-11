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
use lattice_agent::sandbox::Workspace;
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

/// The agent kernel routes, or `None` when this machine has no workspace to
/// judge paths against.
///
/// `Workspace::new` creates and canonicalises the root; a failure means the
/// directory cannot exist (permissions, a file in the way), and mounting
/// routes that would answer every path question wrongly is worse than not
/// mounting them.
pub fn agent_router(root: &Path) -> Option<Router> {
    match Workspace::new(root) {
        Ok(workspace) => Some(lattice_agent::router(workspace)),
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
/// workspace the kernel judges paths against, and `jobs` the scheduler whose
/// status routes are exposed (absent ⇒ `/host/jobs` is not mounted at all).
pub fn native_router(db: PathBuf, agent_root: &Path, jobs: Option<Arc<Scheduler>>) -> Router {
    let mut router =
        lattice_retrieval::router(db).merge(lattice_ingest::router(IngestApiConfig::default()));
    if let Some(agent) = agent_router(agent_root) {
        router = router.merge(agent);
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

    #[test]
    fn an_unusable_agent_root_is_absent_rather_than_wrong() {
        let dir = tempfile::tempdir().expect("tempdir");
        let blocked = dir.path().join("a-file");
        std::fs::write(&blocked, b"not a directory").expect("write");
        assert!(agent_router(&blocked.join("root")).is_none());
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
            Some(jobs),
        );
        let _without_jobs = native_router(
            dir.path().join("knowledge_graph.sqlite"),
            &dir.path().join("agent_workspace"),
            None,
        );
    }
}
