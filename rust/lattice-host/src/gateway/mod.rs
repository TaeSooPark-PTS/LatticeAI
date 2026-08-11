//! The loopback-only IPC/API gateway.
//!
//! Binding anywhere other than 127.0.0.1 is refused outright: this front door
//! exposes the worker's whole API surface, and the product's promise is that
//! the brain never leaves the machine.

pub mod clock;
pub mod params;
pub mod proxy;
pub mod routes;
pub mod search;

use std::fmt;
use std::future::Future;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use axum::routing::{any, get};
use axum::Router;
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

/// The full router: host routes, the native search lanes, and the proxy
/// fallback.
///
/// `/rust/search/*` is the host's namespace the same way `/host/*` is — an
/// unknown path under it is a 404 from here, never a request quietly handed to
/// the worker under a name that promised native retrieval.
pub fn build_router(state: Arc<GatewayState>) -> Router {
    Router::new()
        .route("/host/health", get(routes::host_health))
        .route("/host/status", get(routes::host_status))
        .route("/host", any(routes::host_not_found))
        .route("/host/", any(routes::host_not_found))
        .route("/host/*rest", any(routes::host_not_found))
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
        .route("/rust/search", any(routes::unknown_search))
        .route("/rust/search/", any(routes::unknown_search))
        .route("/rust/search/*rest", any(routes::unknown_search))
        .fallback(proxy::proxy_handler)
        .with_state(state)
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
        let state = GatewayState::new(Arc::new(Stub)).expect("state");
        let _router = build_router(Arc::new(state));
    }
}
