//! The loopback-only IPC/API gateway.
//!
//! Binding anywhere other than 127.0.0.1 is refused outright: this front door
//! exposes the worker's whole API surface, and the product's promise is that
//! the brain never leaves the machine.

pub mod clock;
pub mod mounts;
pub mod params;
pub mod proxy;
pub mod routes;
pub mod search;

use std::fmt;
use std::future::Future;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use axum::routing::get;
use axum::Router;
use lattice_jobs::Scheduler;
use tokio::net::TcpListener;

use crate::supervisor::{check_health, http_client, Supervisor, WorkerStatus};

/// What the gateway needs to know about the worker it fronts.
///
/// Deliberately sync and object safe: the gateway holds `Arc<dyn
/// StatusProvider>` so tests can front a fake worker without a supervisor.
pub trait StatusProvider: Send + Sync + 'static {
    /// Current worker snapshot.
    fn status(&self) -> WorkerStatus;
    /// Where to proxy to, e.g. `http://127.0.0.1:4825`.
    fn worker_origin(&self) -> String;
}

impl StatusProvider for Supervisor {
    fn status(&self) -> WorkerStatus {
        Supervisor::status(self)
    }

    fn worker_origin(&self) -> String {
        Supervisor::worker_origin(self)
    }
}

/// Shared state for every gateway handler.
pub struct GatewayState {
    provider: Arc<dyn StatusProvider>,
    client: reqwest::Client,
    db_path: PathBuf,
    agent_root: PathBuf,
    agent_runs_dir: Option<PathBuf>,
    jobs: Option<Arc<Scheduler>>,
}

impl GatewayState {
    /// Build state with a fresh HTTP client.
    pub fn new(provider: Arc<dyn StatusProvider>) -> Result<Self, GatewayError> {
        let client = http_client().map_err(|err| GatewayError::Client(err.to_string()))?;
        Ok(Self::with_client(provider, client))
    }

    /// Build state reusing an existing client (the supervisor's, so the
    /// connection pool is shared).
    pub fn with_client(provider: Arc<dyn StatusProvider>, client: reqwest::Client) -> Self {
        Self {
            provider,
            client,
            // Resolved once, here, so the whole process agrees on which brain it
            // is reading; whether that file exists is re-checked per request,
            // because a brain can appear at any time.
            db_path: lattice_core::graph_db_path(),
            agent_root: mounts::default_agent_root(),
            agent_runs_dir: None,
            jobs: None,
        }
    }

    /// Point the native search routes at a specific store.
    ///
    /// The default (`LATTICEAI_DATA_DIR`, else `~/.ltcai`) is what the product
    /// uses; this exists for tests and for a caller that supervises more than
    /// one brain.
    pub fn with_db_path(mut self, path: impl Into<PathBuf>) -> Self {
        self.db_path = path.into();
        self
    }

    /// Point the agent kernel routes at a specific workspace root.
    ///
    /// The default is the root the supervisor hands the worker
    /// (`LATTICEAI_AGENT_ROOT`, else `~/.ltcai/desktop-runtime/agent_workspace`),
    /// so a path the host judges is a path the worker would actually touch.
    pub fn with_agent_root(mut self, path: impl Into<PathBuf>) -> Self {
        self.agent_root = path.into();
        self
    }

    /// Mount `/host/jobs` around this scheduler.
    ///
    /// Mounting is not starting: the routes answer `enabled: false` until the
    /// caller spawns the timer, which is the honest reading of "the button
    /// exists, nothing is on a clock".
    pub fn with_jobs(mut self, scheduler: Arc<Scheduler>) -> Self {
        self.jobs = Some(scheduler);
        self
    }

    /// The workspace root the agent kernel routes judge paths against.
    pub fn agent_root(&self) -> PathBuf {
        self.agent_root.clone()
    }

    /// The scheduler behind `/host/jobs`, when one is wired.
    pub fn jobs(&self) -> Option<Arc<Scheduler>> {
        self.jobs.clone()
    }

    /// Where paused `/rust/agent/run` approvals are stored.
    ///
    /// Defaults to `lattice_agent::runs::default_runs_dir`; a caller (a test, or
    /// a host supervising more than one brain) can point it elsewhere.
    pub fn with_agent_runs_dir(mut self, path: impl Into<PathBuf>) -> Self {
        self.agent_runs_dir = Some(path.into());
        self
    }

    /// The loop orchestrator's configuration for this gateway.
    pub fn agent_loop_config(&self) -> lattice_agent::LoopConfig {
        let mut config = mounts::agent_loop_config(&self.worker_origin(), self.client.clone());
        if let Some(dir) = &self.agent_runs_dir {
            config.runs_dir = dir.clone();
        }
        config
    }

    /// The knowledge graph the native search routes read.
    pub fn db_path(&self) -> PathBuf {
        self.db_path.clone()
    }

    /// The knowledge graph the native search routes read, borrowed.
    pub fn db(&self) -> &Path {
        &self.db_path
    }

    /// Current worker snapshot.
    pub fn status(&self) -> WorkerStatus {
        self.provider.status()
    }

    /// Worker origin.
    pub fn worker_origin(&self) -> String {
        self.provider.worker_origin()
    }

    /// The HTTP client used for probes and proxying.
    pub fn client(&self) -> &reqwest::Client {
        &self.client
    }

    /// A live `GET /health` against the worker.
    pub async fn probe_worker_health(&self) -> bool {
        check_health(&self.client, &self.worker_origin()).await
    }
}

impl fmt::Debug for GatewayState {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("GatewayState")
            .field("worker_origin", &self.worker_origin())
            .field("db_path", &self.db_path)
            .field("agent_root", &self.agent_root)
            .field("jobs", &self.jobs.is_some())
            .finish()
    }
}

/// Gateway failures.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum GatewayError {
    /// Someone asked for a non-loopback bind. Refused.
    NonLoopbackBind(SocketAddr),
    /// The listener could not be bound.
    Bind(String),
    /// The server stopped with an error.
    Serve(String),
    /// The HTTP client could not be built.
    Client(String),
}

impl fmt::Display for GatewayError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            GatewayError::NonLoopbackBind(addr) => write!(
                f,
                "refusing to bind the lattice-host gateway to {addr}: loopback only"
            ),
            GatewayError::Bind(message)
            | GatewayError::Serve(message)
            | GatewayError::Client(message) => f.write_str(message),
        }
    }
}

impl std::error::Error for GatewayError {}

/// Reject any address that is not on the loopback interface.
pub fn ensure_loopback(addr: &SocketAddr) -> Result<(), GatewayError> {
    if addr.ip().is_loopback() {
        Ok(())
    } else {
        Err(GatewayError::NonLoopbackBind(*addr))
    }
}

/// The full router: the host's own routes, every mounted native crate, and the
/// proxy fallback.
///
/// `/host/*` and `/rust/*` are host namespaces the same way: an unknown path
/// under either is a 404 from here, never a request quietly handed to the
/// worker under a name that promised a native answer. That guard used to be
/// three catch-all routes per namespace; with four crates mounting their own
/// paths underneath, a catch-all would collide with them, so
/// [`routes::gateway_fallback`] makes the same decision at the one place axum
/// reaches when nothing matched.
///
/// Order of assembly is deliberate: the host's routes and the mounted crates
/// are real routes, and the proxy is only the fallback, so nothing native can
/// be shadowed by the reverse proxy.
pub fn build_router(state: Arc<GatewayState>) -> Router {
    let db = state.db_path();
    let agent_root = state.agent_root();
    let jobs = state.jobs();
    // The loop orchestrator talks to the worker this host supervises, over the
    // client it already pools.
    let agent = state.agent_loop_config();
    Router::new()
        .route("/host/health", get(routes::host_health))
        .route("/host/status", get(routes::host_status))
        .route(
            "/rust/search/hybrid",
            get(search::hybrid).post(search::hybrid),
        )
        .route(
            "/rust/search/keyword",
            get(search::keyword).post(search::keyword),
        )
        .route(
            "/rust/search/vector",
            get(search::vector).post(search::vector),
        )
        .fallback(routes::gateway_fallback)
        .with_state(state)
        .merge(mounts::native_router(db, &agent_root, agent, jobs))
}

/// Bind a loopback listener, refusing anything else.
pub async fn bind_loopback(addr: SocketAddr) -> Result<TcpListener, GatewayError> {
    ensure_loopback(&addr)?;
    TcpListener::bind(addr)
        .await
        .map_err(|err| GatewayError::Bind(format!("cannot bind gateway on {addr}: {err}")))
}

/// Serve the gateway on an already-bound listener until `shutdown` resolves.
pub async fn serve_gateway<F>(
    listener: TcpListener,
    state: Arc<GatewayState>,
    shutdown: F,
) -> Result<(), GatewayError>
where
    F: Future<Output = ()> + Send + 'static,
{
    if let Ok(addr) = listener.local_addr() {
        ensure_loopback(&addr)?;
    }
    axum::serve(listener, build_router(state))
        .with_graceful_shutdown(shutdown)
        .await
        .map_err(|err| GatewayError::Serve(err.to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::supervisor::status::WorkerStatus;

    struct Stub;

    impl StatusProvider for Stub {
        fn status(&self) -> WorkerStatus {
            WorkerStatus::idle(4825, true)
        }

        fn worker_origin(&self) -> String {
            "http://127.0.0.1:4825".into()
        }
    }

    #[test]
    fn loopback_addresses_are_accepted() {
        for addr in ["127.0.0.1:4825", "[::1]:4825", "127.9.9.9:1"] {
            let parsed: SocketAddr = addr.parse().expect("addr");
            assert!(ensure_loopback(&parsed).is_ok(), "{addr} must be allowed");
        }
    }

    #[test]
    fn public_addresses_are_refused() {
        for addr in ["0.0.0.0:4825", "192.168.1.10:4825", "[::]:4825"] {
            let parsed: SocketAddr = addr.parse().expect("addr");
            let err = ensure_loopback(&parsed).expect_err("must refuse");
            assert!(err.to_string().contains("loopback only"));
        }
    }

    #[tokio::test]
    async fn binding_off_loopback_is_refused_before_touching_the_socket() {
        let err = bind_loopback("0.0.0.0:0".parse().expect("addr"))
            .await
            .expect_err("must refuse");
        assert!(matches!(err, GatewayError::NonLoopbackBind(_)));
    }

    #[tokio::test]
    async fn binding_loopback_works_and_reports_the_chosen_port() {
        let listener = bind_loopback("127.0.0.1:0".parse().expect("addr"))
            .await
            .expect("bind");
        assert!(listener.local_addr().expect("addr").ip().is_loopback());
    }

    #[test]
    fn state_exposes_the_provider() {
        let state = GatewayState::new(Arc::new(Stub)).expect("state");
        assert_eq!(state.worker_origin(), "http://127.0.0.1:4825");
        assert_eq!(state.status().port, 4825);
        assert!(format!("{state:?}").contains("127.0.0.1:4825"));
    }

    #[test]
    fn the_store_defaults_to_the_configured_data_dir_and_can_be_overridden() {
        let state = GatewayState::new(Arc::new(Stub)).expect("state");
        assert_eq!(state.db_path(), lattice_core::graph_db_path());
        assert!(state.db_path().ends_with(lattice_core::DB_FILE_NAME));
        assert!(format!("{state:?}").contains(lattice_core::DB_FILE_NAME));

        let pinned = GatewayState::new(Arc::new(Stub))
            .expect("state")
            .with_db_path("/tmp/elsewhere/knowledge_graph.sqlite");
        assert_eq!(
            pinned.db(),
            Path::new("/tmp/elsewhere/knowledge_graph.sqlite")
        );
    }

    #[test]
    fn the_router_builds_without_route_conflicts() {
        // Pinned at a temporary root: building the router creates the agent
        // workspace, and a unit test has no business writing to the home
        // directory of whoever runs it.
        let dir = tempfile::tempdir().expect("tempdir");
        let state = GatewayState::new(Arc::new(Stub))
            .expect("state")
            .with_db_path(dir.path().join("knowledge_graph.sqlite"))
            .with_agent_root(dir.path().join("agent_workspace"));
        assert!(state.jobs().is_none(), "no scheduler unless one is wired");
        let _router = build_router(Arc::new(state));

        // …and again with the jobs routes mounted.
        let state = GatewayState::new(Arc::new(Stub))
            .expect("state")
            .with_db_path(dir.path().join("knowledge_graph.sqlite"))
            .with_agent_root(dir.path().join("agent_workspace"))
            .with_jobs(mounts::scheduler(
                "http://127.0.0.1:1",
                http_client().expect("client"),
            ));
        assert!(state.jobs().is_some());
        assert!(format!("{state:?}").contains("jobs: true"));
        let _router = build_router(Arc::new(state));
    }
}
