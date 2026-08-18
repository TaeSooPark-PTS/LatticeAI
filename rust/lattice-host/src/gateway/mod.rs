//! The loopback-only IPC/API gateway.
//!
//! Binding anywhere other than 127.0.0.1 is refused outright: this front door
//! exposes the worker's whole API surface, and the product's promise is that
//! the brain never leaves the machine.

pub mod agent_bind;
pub mod agent_catalog;
pub mod allowlist;
pub mod identity;
pub mod mounts;
pub mod onedoor;
pub mod params;
pub mod posture;
pub mod product;
pub mod proxy;
pub mod routes;
pub mod scopes;
pub mod search;
pub mod sinks;

use std::fmt;
use std::future::Future;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::Instant;

use axum::routing::get;
use axum::Router;
use lattice_jobs::Scheduler;
use tokio::net::TcpListener;

use crate::supervisor::{http_client, probe_health, proxy_client, Supervisor, WorkerStatus};
use posture::{Posture, POSTURE_TTL};

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
    proxy_client: reqwest::Client,
    db_path: PathBuf,
    agent_root: PathBuf,
    agent_runs_dir: Option<PathBuf>,
    jobs: Option<Arc<Scheduler>>,
    pinned_posture: Option<Posture>,
    observed_posture: Mutex<Option<(Instant, Posture)>>,
    product: Option<Arc<onedoor::OneDoorState>>,
    allowlist: Option<Arc<allowlist::Allowlist>>,
}

impl GatewayState {
    /// Build state with a fresh HTTP client.
    pub fn new(provider: Arc<dyn StatusProvider>) -> Result<Self, GatewayError> {
        let client = http_client().map_err(|err| GatewayError::Client(err.to_string()))?;
        Ok(Self::with_client(provider, client))
    }

    /// Build state reusing an existing client (the supervisor's, so the
    /// connection pool is shared).
    ///
    /// The *proxy* client is always this state's own: the shared one follows
    /// redirects, which is right for an API caller and destroys a proxied
    /// `Set-Cookie` (see [`proxy`]). Should that builder ever fail, the shared
    /// client is a worse front door but still a working one.
    pub fn with_client(provider: Arc<dyn StatusProvider>, client: reqwest::Client) -> Self {
        let proxy_client = proxy_client().unwrap_or_else(|_| client.clone());
        Self {
            provider,
            client,
            proxy_client,
            // Resolved once, here, so the whole process agrees on which brain it
            // is reading; whether that file exists is re-checked per request,
            // because a brain can appear at any time.
            db_path: lattice_core::graph_db_path(),
            agent_root: mounts::default_agent_root(),
            agent_runs_dir: None,
            jobs: None,
            pinned_posture: None,
            observed_posture: Mutex::new(None),
            product: None,
            allowlist: None,
        }
    }

    /// Front a worker whose surface is not the product's.
    ///
    /// The default is [`allowlist::Allowlist::shared`] — the committed
    /// projection of `worker_route_keys()`, which is the only one a drift gate
    /// watches. This exists for a harness whose fake worker serves fixture
    /// paths, and for a host supervising a worker built from another profile.
    pub fn with_allowlist(mut self, allowlist: Arc<allowlist::Allowlist>) -> Self {
        self.allowlist = Some(allowlist);
        self
    }

    /// The allowlist this gateway forwards by.
    pub fn allowlist(&self) -> &allowlist::Allowlist {
        match &self.allowlist {
            Some(custom) => custom,
            None => allowlist::Allowlist::shared(),
        }
    }

    /// Assemble the product surface and mount it on this gateway.
    ///
    /// Separate from [`GatewayState::new`] and fallible on purpose. Everything
    /// below is a real dependency on the machine — a data directory that can be
    /// opened, a schema that can be bootstrapped, an agent workspace that can
    /// be created — and a front door that half-exists is worse than one that
    /// says why it cannot open. Callers that only want the supervisor and the
    /// `/rust/*` lanes (`src-tauri`'s smoke, the test suites) simply do not call
    /// this, and the router is the pre-v11.6.0 one.
    pub fn open_product(mut self) -> Result<Self, GatewayError> {
        let product = onedoor::OneDoorState::open(
            &self.worker_origin(),
            self.client.clone(),
            &self.agent_root.clone(),
            self.agent_loop_config(),
        )?;
        self.product = Some(Arc::new(product));
        Ok(self)
    }

    /// Mount an already-built product state (tests, and a caller that wants to
    /// pin the data directory before anything is created).
    pub fn with_product(mut self, product: Arc<onedoor::OneDoorState>) -> Self {
        self.product = Some(product);
        self
    }

    /// The product state, when the One Door surface is mounted.
    pub fn product(&self) -> Option<&Arc<onedoor::OneDoorState>> {
        self.product.as_ref()
    }

    /// Pin the worker's access posture instead of reading it from `/health`.
    ///
    /// For tests, and for a caller that owns the worker's environment outright.
    /// The product leaves it unset: the worker is the single source of truth
    /// about who may talk to it, and a pinned "open" is a promise this process
    /// cannot keep on its own.
    pub fn with_pinned_posture(mut self, posture: Posture) -> Self {
        self.pinned_posture = Some(posture);
        self
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

    /// Where a paused run stages its change proposal.
    ///
    /// The product's own `GovernanceState` when the One Door surface is mounted
    /// — the Review Center routes were built from that same handle, so a staged
    /// proposal is visible to them immediately. With no product mounted there is
    /// no Review Center in this process to disagree with, and the standalone
    /// JSON store is the documented writer for that case.
    fn proposal_store(&self) -> Arc<dyn lattice_agent::proposals::ProposalStore> {
        match self.product() {
            Some(product) => Arc::new(product.governance.clone()),
            None => Arc::new(lattice_agent::proposals::JsonProposalStore::from_env()),
        }
    }

    /// The loop orchestrator's configuration for this gateway.
    pub fn agent_loop_config(&self) -> lattice_agent::LoopConfig {
        let mut config = mounts::agent_loop_config(
            &self.worker_origin(),
            self.client.clone(),
            self.proposal_store(),
        );
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

    /// The HTTP client used for health probes and for the mounted crates.
    pub fn client(&self) -> &reqwest::Client {
        &self.client
    }

    /// The client the reverse proxy forwards with — redirects **not** followed.
    pub fn proxy_client(&self) -> &reqwest::Client {
        &self.proxy_client
    }

    /// A live `GET /health` against the worker.
    ///
    /// The same answer carries the worker's access posture, so the probe
    /// `/host/health` already makes on every call keeps the posture cache warm
    /// and the guard below rarely has to ask for itself.
    pub async fn probe_worker_health(&self) -> bool {
        let report = probe_health(&self.client, &self.worker_origin()).await;
        self.remember_posture(Posture::from_report(&report));
        report.healthy
    }

    /// The worker's access posture, from the cache or from the worker.
    ///
    /// Fails closed in every direction: an unreachable worker, an answer with
    /// no posture in it, and a worker that says "authentication required" all
    /// come back not-open, and the native lanes refuse.
    pub async fn worker_posture(&self) -> Posture {
        if let Some(pinned) = self.pinned_posture {
            return pinned;
        }
        if let Some(fresh) = self.cached_posture() {
            return fresh;
        }
        let report = probe_health(&self.client, &self.worker_origin()).await;
        let posture = Posture::from_report(&report);
        self.remember_posture(posture);
        posture
    }

    /// The observed posture while it is still within [`POSTURE_TTL`].
    fn cached_posture(&self) -> Option<Posture> {
        let cache = self.observed_posture.lock().ok()?;
        cache
            .filter(|(at, _)| at.elapsed() < POSTURE_TTL)
            .map(|(_, posture)| posture)
    }

    fn remember_posture(&self, posture: Posture) {
        if let Ok(mut cache) = self.observed_posture.lock() {
            *cache = Some((Instant::now(), posture));
        }
    }
}

impl fmt::Debug for GatewayState {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("GatewayState")
            .field("worker_origin", &self.worker_origin())
            .field("db_path", &self.db_path)
            .field("agent_root", &self.agent_root)
            .field("jobs", &self.jobs.is_some())
            .field("product", &self.product.is_some())
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
    /// The product state could not be assembled — no brain, no workspace, no
    /// schema. Named separately from [`GatewayError::Bind`] because it is the
    /// one failure that means "this machine cannot host the product", and the
    /// caller's answer to it is different from "that port is taken".
    State(String),
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
            | GatewayError::Client(message)
            | GatewayError::State(message) => f.write_str(message),
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
///
/// Everything answered *natively* — every `/rust/*` lane, `/host/status` and
/// `/host/jobs` — sits behind [`posture::require_open_posture`], because those
/// handlers read the store with no credential at all. `/host/health` and the
/// proxy fallback stay outside it: liveness is not a secret, and a proxied
/// request is the worker's own decision to make.
pub fn build_router(state: Arc<GatewayState>) -> Router {
    let db = state.db_path();
    let agent_root = state.agent_root();
    let jobs = state.jobs();
    // The loop orchestrator talks to the worker this host supervises, over the
    // client it already pools.
    let agent = state.agent_loop_config();
    let mut native = Router::new()
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
        .with_state(Arc::clone(&state))
        .merge(mounts::native_router_parts(
            db,
            &agent_root,
            agent,
            jobs,
            state.product().is_none(),
        ));
    if let Some(product) = state.product() {
        // `RunBody.user_role` is the server's to state, not the caller's
        // (§4c). Applied *inside* the posture gate below, so an unauthorised
        // request is refused before this layer buffers its body.
        native = native.layer(axum::middleware::from_fn_with_state(
            Arc::clone(&product.auth),
            identity::inject_user_role,
        ));
    }
    let native = native.layer(axum::middleware::from_fn_with_state(
        Arc::clone(&state),
        posture::require_open_posture,
    ));

    let mut app = Router::new()
        .route("/host/health", get(routes::host_health))
        .fallback(routes::gateway_fallback)
        .with_state(Arc::clone(&state))
        .merge(native);
    if let Some(product) = state.product() {
        // The Origin guard goes outside everything the front door serves
        // (WP-I2 §1), including the proxy: a browser write that reaches the
        // worker through this hop was let through *here*, and the worker's own
        // guard is defence in depth rather than the decision.
        let bind = agent_bind::AgentBindState::new(
            Arc::clone(&product.auth),
            product.workspace.clone(),
            product.loop_config.clone(),
        );
        app = app
            .merge(product::product_router(product))
            .layer(axum::middleware::from_fn_with_state(
                bind,
                agent_bind::bind_agent_run,
            ))
            .layer(axum::middleware::from_fn_with_state(
                Arc::clone(&product.auth),
                lattice_auth::csrf_guard,
            ));
    }
    app
}

/// Bind a loopback listener, refusing anything else.
pub async fn bind_loopback(addr: SocketAddr) -> Result<TcpListener, GatewayError> {
    ensure_loopback(&addr)?;
    TcpListener::bind(addr)
        .await
        .map_err(|err| GatewayError::Bind(format!("cannot bind gateway on {addr}: {err}")))
}

/// Serve the gateway on an already-bound listener until `shutdown` resolves.
///
/// Served *with connect info*: the proxy states the peer's address in
/// `X-Forwarded-For`, and a peer it was never told about is one it would have
/// to invent.
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
    axum::serve(
        listener,
        build_router(state).into_make_service_with_connect_info::<SocketAddr>(),
    )
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

    #[tokio::test]
    async fn an_unreachable_worker_leaves_the_posture_unknown_and_caches_it() {
        struct Dead;
        impl StatusProvider for Dead {
            fn status(&self) -> WorkerStatus {
                WorkerStatus::idle(1, true)
            }
            fn worker_origin(&self) -> String {
                // Port 1 on loopback: reserved, nothing listens there.
                "http://127.0.0.1:1".into()
            }
        }
        let state = GatewayState::new(Arc::new(Dead)).expect("state");
        assert_eq!(
            state.cached_posture(),
            None,
            "nothing is known before anything is asked"
        );
        assert_eq!(state.worker_posture().await, Posture::Unknown);
        assert_eq!(
            state.cached_posture(),
            Some(Posture::Unknown),
            "the failure is cached too, so a dead worker is not re-probed per request"
        );
        assert!(!state.probe_worker_health().await);
    }

    #[tokio::test]
    async fn a_pinned_posture_short_circuits_the_probe() {
        let state = GatewayState::new(Arc::new(Stub))
            .expect("state")
            .with_pinned_posture(Posture::Open);
        assert_eq!(state.worker_posture().await, Posture::Open);
        assert_eq!(
            state.cached_posture(),
            None,
            "a pinned posture never consults the worker, so nothing is observed"
        );
    }

    #[test]
    fn the_proxy_client_is_this_state_s_own() {
        let shared = http_client().expect("client");
        let state = GatewayState::with_client(Arc::new(Stub), shared);
        // Distinct handles: the shared one follows redirects, this one must not.
        assert!(!std::ptr::eq(state.client(), state.proxy_client()));
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
