use std::path::{Path, PathBuf};
use std::sync::Arc;

use axum::Router;
use lattice_agent::kernel::proposals::ProposalStore;
use lattice_agent::surface::looproutes::LoopConfig;
use lattice_agent::tools::sandbox::Workspace;
use lattice_ingest::IngestApiConfig;
use lattice_jobs::{parse_flag, Scheduler, SchedulerConfig};

pub const AGENT_ROOT_ENV: &str = "LATTICEAI_AGENT_ROOT";

pub const JOBS_ENV: &str = "LATTICEAI_JOBS";

const DESKTOP_AGENT_ROOT: [&str; 2] = ["desktop-runtime", "agent_workspace"];

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

pub fn default_agent_root() -> PathBuf {
    let env_value = std::env::var(AGENT_ROOT_ENV).ok();
    let home = std::env::var_os("HOME")
        .map(PathBuf::from)
        .filter(|path| !path.as_os_str().is_empty());
    resolve_agent_root(env_value.as_deref(), home.as_deref())
}

pub fn jobs_enabled(raw: Option<&str>) -> bool {
    parse_flag(raw, true)
}

pub fn jobs_enabled_from_env() -> bool {
    jobs_enabled(std::env::var(JOBS_ENV).ok().as_deref())
}

pub fn scheduler(worker_origin: &str, client: reqwest::Client) -> Arc<Scheduler> {
    Arc::new(Scheduler::with_client(
        SchedulerConfig::from_env(worker_origin),
        client,
    ))
}

pub fn scheduler_with_graph(
    worker_origin: &str,
    client: reqwest::Client,
    graph: lattice_core::graph_write::GraphWriter,
) -> Arc<Scheduler> {
    Arc::new(
        Scheduler::with_client(SchedulerConfig::from_env(worker_origin), client).with_graph(graph),
    )
}

pub fn agent_loop_config(
    worker_origin: &str,
    client: reqwest::Client,
    proposals: Arc<dyn ProposalStore>,
) -> LoopConfig {
    LoopConfig {
        worker_origin: worker_origin.to_string(),
        runs_dir: lattice_agent::kernel::runs::default_runs_dir(),
        client: Some(client),
        proposals: None,
        hooks: None,
        external: None,
    }
    .with_proposals(proposals)
}

pub fn agent_router(root: &Path, config: LoopConfig) -> Option<Router> {
    agent_router_parts(root, config, true)
}

pub fn agent_router_parts(root: &Path, config: LoopConfig, include_loop: bool) -> Option<Router> {
    match Workspace::new(root) {
        Ok(workspace) => Some(if include_loop {
            lattice_agent::surface::router::router(workspace, config)
        } else {
            lattice_agent::surface::router::kernel_router(workspace)
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

pub fn native_router(
    db: PathBuf,
    agent_root: &Path,
    agent: LoopConfig,
    jobs: Option<Arc<Scheduler>>,
) -> Router {
    native_router_parts(db, agent_root, agent, jobs, true)
}

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

    fn test_loop_config(dir: &Path) -> LoopConfig {
        LoopConfig {
            runs_dir: dir.join("rust_agent_runs"),
            ..agent_loop_config(
                "http://127.0.0.1:1",
                crate::supervisor::http_client().expect("client"),
                Arc::new(lattice_agent::kernel::proposals::JsonProposalStore::new(
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
            Arc::new(lattice_agent::kernel::proposals::JsonProposalStore::new(
                dir.path(),
            )),
        );
        assert_eq!(config.worker_origin, "http://127.0.0.1:4899");
        assert!(config.client.is_some(), "the loopback pool is shared");
        assert!(
            config.runs_dir.ends_with("rust_agent_runs"),
            "the native store is not Python's: {:?}",
            config.runs_dir
        );
    }

    #[test]
    fn the_loop_stages_into_the_store_the_caller_named() {
        let dir = tempfile::tempdir().expect("tempdir");
        let store = Arc::new(lattice_agent::kernel::proposals::JsonProposalStore::new(
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
