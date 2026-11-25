pub mod agent_bind;
pub mod agent_catalog;
pub mod allowlist;
pub mod identity;
pub mod isolation;
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

pub trait StatusProvider: Send + Sync + 'static {
    fn status(&self) -> WorkerStatus;
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
    pub fn new(provider: Arc<dyn StatusProvider>) -> Result<Self, GatewayError> {
        let client = http_client().map_err(|err| GatewayError::Client(err.to_string()))?;
        Self::with_client(provider, client)
    }

    pub fn with_client(
        provider: Arc<dyn StatusProvider>,
        client: reqwest::Client,
    ) -> Result<Self, GatewayError> {
        let proxy_client = proxy_client().map_err(|err| {
            GatewayError::Client(format!(
                "failed to build no-redirect proxy HTTP client: {err}"
            ))
        })?;
        Ok(Self {
            provider,
            client,
            proxy_client,
            db_path: lattice_core::graph_db_path(),
            agent_root: mounts::default_agent_root(),
            agent_runs_dir: None,
            jobs: None,
            pinned_posture: None,
            observed_posture: Mutex::new(None),
            product: None,
            allowlist: None,
        })
    }

    pub fn with_allowlist(mut self, allowlist: Arc<allowlist::Allowlist>) -> Self {
        self.allowlist = Some(allowlist);
        self
    }

    pub fn allowlist(&self) -> &allowlist::Allowlist {
        match &self.allowlist {
            Some(custom) => custom,
            None => allowlist::Allowlist::shared(),
        }
    }

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

    pub fn with_product(mut self, product: Arc<onedoor::OneDoorState>) -> Self {
        self.product = Some(product);
        self
    }

    pub fn product(&self) -> Option<&Arc<onedoor::OneDoorState>> {
        self.product.as_ref()
    }

    pub fn with_pinned_posture(mut self, posture: Posture) -> Self {
        self.pinned_posture = Some(posture);
        self
    }

    pub fn with_db_path(mut self, path: impl Into<PathBuf>) -> Self {
        self.db_path = path.into();
        self
    }

    pub fn with_agent_root(mut self, path: impl Into<PathBuf>) -> Self {
        self.agent_root = path.into();
        self
    }

    pub fn with_jobs(mut self, scheduler: Arc<Scheduler>) -> Self {
        self.jobs = Some(scheduler);
        self
    }

    pub fn agent_root(&self) -> PathBuf {
        self.agent_root.clone()
    }

    pub fn jobs(&self) -> Option<Arc<Scheduler>> {
        self.jobs.clone()
    }

    pub fn with_agent_runs_dir(mut self, path: impl Into<PathBuf>) -> Self {
        self.agent_runs_dir = Some(path.into());
        self
    }

    fn proposal_store(&self) -> Arc<dyn lattice_agent::kernel::proposals::ProposalStore> {
        match self.product() {
            Some(product) => Arc::new(product.governance.clone()),
            None => Arc::new(lattice_agent::kernel::proposals::JsonProposalStore::from_env()),
        }
    }

    pub fn agent_loop_config(&self) -> lattice_agent::surface::looproutes::LoopConfig {
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

    pub fn db_path(&self) -> PathBuf {
        self.db_path.clone()
    }

    pub fn db(&self) -> &Path {
        &self.db_path
    }

    pub fn status(&self) -> WorkerStatus {
        self.provider.status()
    }

    pub fn worker_origin(&self) -> String {
        self.provider.worker_origin()
    }

    pub fn client(&self) -> &reqwest::Client {
        &self.client
    }

    pub fn proxy_client(&self) -> &reqwest::Client {
        &self.proxy_client
    }

    pub async fn probe_worker_health(&self) -> bool {
        let report = probe_health(&self.client, &self.worker_origin()).await;
        self.remember_posture(Posture::from_report(&report));
        report.healthy
    }

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

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum GatewayError {
    NonLoopbackBind(SocketAddr),
    UnauthenticatedPublicBind(SocketAddr),
    Bind(String),
    Serve(String),
    Client(String),
    State(String),
}

impl fmt::Display for GatewayError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            GatewayError::NonLoopbackBind(addr) => write!(
                f,
                "refusing to bind the lattice-host gateway to {addr}: loopback only"
            ),
            GatewayError::UnauthenticatedPublicBind(addr) => write!(
                f,
                "refusing non-loopback bind {addr} without LATTICEAI_REQUIRE_AUTH=true"
            ),
            GatewayError::Bind(message)
            | GatewayError::Serve(message)
            | GatewayError::Client(message)
            | GatewayError::State(message) => f.write_str(message),
        }
    }
}

impl std::error::Error for GatewayError {}

pub fn ensure_loopback(addr: &SocketAddr) -> Result<(), GatewayError> {
    if addr.ip().is_loopback() {
        Ok(())
    } else {
        Err(GatewayError::NonLoopbackBind(*addr))
    }
}

pub fn ensure_bind_allowed(addr: &SocketAddr, require_auth: bool) -> Result<(), GatewayError> {
    if addr.ip().is_loopback() || require_auth {
        Ok(())
    } else {
        Err(GatewayError::UnauthenticatedPublicBind(*addr))
    }
}

pub fn build_router(state: Arc<GatewayState>) -> Router {
    let db = state.db_path();
    let agent_root = state.agent_root();
    let jobs = state.jobs();
    let agent = state.agent_loop_config();
    let mut native = Router::new()
        .route("/host/status", get(routes::host_status))
        .route("/host/diagnostics", get(routes::host_diagnostics))
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
        let bind = agent_bind::AgentBindState::from_product(product);
        let iso = isolation::IsolationState::new(Arc::clone(product));
        app = app
            .merge(product::product_router(product))
            .layer(axum::middleware::from_fn_with_state(
                iso,
                isolation::enforce_agent_isolation,
            ))
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

pub async fn bind_loopback(addr: SocketAddr) -> Result<TcpListener, GatewayError> {
    ensure_loopback(&addr)?;
    TcpListener::bind(addr)
        .await
        .map_err(|err| GatewayError::Bind(format!("cannot bind gateway on {addr}: {err}")))
}

pub async fn bind_gateway(
    addr: SocketAddr,
    require_auth: bool,
) -> Result<TcpListener, GatewayError> {
    ensure_bind_allowed(&addr, require_auth)?;
    TcpListener::bind(addr)
        .await
        .map_err(|err| GatewayError::Bind(format!("cannot bind gateway on {addr}: {err}")))
}

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
    serve_gateway_inner(listener, state, shutdown).await
}

pub async fn serve_gateway_with_auth_guard<F>(
    listener: TcpListener,
    state: Arc<GatewayState>,
    require_auth: bool,
    shutdown: F,
) -> Result<(), GatewayError>
where
    F: Future<Output = ()> + Send + 'static,
{
    if let Ok(addr) = listener.local_addr() {
        ensure_bind_allowed(&addr, require_auth)?;
    }
    serve_gateway_inner(listener, state, shutdown).await
}

async fn serve_gateway_inner<F>(
    listener: TcpListener,
    state: Arc<GatewayState>,
    shutdown: F,
) -> Result<(), GatewayError>
where
    F: Future<Output = ()> + Send + 'static,
{
    axum::serve(
        listener,
        build_router(state).into_make_service_with_connect_info::<SocketAddr>(),
    )
    .with_graceful_shutdown(shutdown)
    .await
    .map_err(|err| GatewayError::Serve(err.to_string()))
}

#[cfg(test)]
mod tests;
