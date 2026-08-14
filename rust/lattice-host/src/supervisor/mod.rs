//! Python worker supervision: command resolution, spawning, HTTP health
//! gating, crash restart with exponential backoff, and graceful shutdown.
//!
//! The desktop shell does all of this ad hoc today; this module is the single
//! implementation both `src-tauri` and the `lattice-host` binary use.

pub mod backoff;
pub mod command;
pub mod health;
mod monitor;
pub mod port;
pub mod process;
pub mod status;
pub mod worker_env;

use std::fmt;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant};

use tokio::sync::{oneshot, watch, Mutex as AsyncMutex};
use tokio::task::JoinHandle;

pub use backoff::BackoffPolicy;
pub use command::{
    resolve_worker_command, CommandOrigin, HostProbe, ResolveError, StaticProbe, SystemProbe,
    WorkerCommand,
};
pub use health::{
    check_health, http_client, probe_health, proxy_client, wait_for_health, HealthReport,
    HealthTimeout,
};
pub use port::{
    find_free_port, is_port_free, preferred_port, PortError, DEFAULT_PORT, DEFAULT_SCAN_ATTEMPTS,
};
pub use process::SignalOutcome;
pub use status::{origin_for, WorkerStatus};
pub use worker_env::{
    cors_allowed_origins, csrf_trusted_origins, worker_env, CORS_ALLOWED_ORIGINS_ENV,
    CSRF_TRUSTED_ORIGINS_ENV,
};

use monitor::{run_health_poller, run_monitor, Shared};

/// How the supervisor should behave.
#[derive(Debug, Clone)]
pub struct SupervisorConfig {
    /// The chosen worker port. Authoritative — this is what the worker is told
    /// to bind and what every consumer reads back.
    pub port: u16,
    /// The gateway port this worker sits behind, when it sits behind one.
    ///
    /// Set it and the worker is told to trust the gateway's origin for
    /// cookie-authenticated writes (`worker_env::csrf_trusted_origins`); leave
    /// it `None` for the direct topology, where the browser talks to the
    /// worker's own port and no second origin exists.
    pub gateway_port: Option<u16>,
    /// `false` fronts an already-running worker without spawning anything.
    pub supervise: bool,
    /// Worker working directory / agent root parent (`~/.ltcai/desktop-runtime`).
    pub runtime_dir: Option<PathBuf>,
    /// Where `desktop-sidecar.log` / `.err.log` are appended (`~/.ltcai`).
    pub log_dir: Option<PathBuf>,
    /// Delay between health probes while the start gate is closed.
    pub health_interval: Duration,
    /// How long the start gate waits before declaring the boot failed.
    pub health_deadline: Duration,
    /// Interval of the background liveness poller.
    pub health_poll_interval: Duration,
    /// Restart schedule.
    pub backoff: BackoffPolicy,
    /// How long SIGTERM gets before SIGKILL.
    pub stop_grace: Duration,
    /// Extra environment applied after the pinned worker environment.
    pub extra_env: Vec<(String, String)>,
}

impl SupervisorConfig {
    /// Defaults for `port`: supervised, 250 ms health interval, 45 s boot
    /// deadline (the desktop shell's 45×500 ms budget), 5 s liveness poll.
    pub fn new(port: u16) -> Self {
        Self {
            port,
            gateway_port: None,
            supervise: true,
            runtime_dir: None,
            log_dir: None,
            health_interval: Duration::from_millis(250),
            health_deadline: Duration::from_secs(45),
            health_poll_interval: Duration::from_secs(5),
            backoff: BackoffPolicy::default(),
            stop_grace: Duration::from_secs(5),
            extra_env: Vec::new(),
        }
    }

    /// Fill `log_dir` (`~/.ltcai`) and `runtime_dir`
    /// (`~/.ltcai/desktop-runtime`) from the probe's `HOME`.
    pub fn with_system_dirs(mut self, probe: &dyn HostProbe) -> Self {
        self.log_dir = worker_env::ltcai_home(probe);
        self.runtime_dir = worker_env::desktop_runtime_dir(probe);
        self
    }

    /// Declare that this worker is fronted by a gateway on `port`.
    pub fn behind_gateway(mut self, port: u16) -> Self {
        self.gateway_port = Some(port);
        self
    }

    /// The agent workspace the worker will be given, when a runtime dir is set.
    ///
    /// The host's native kernel routes judge paths against the same directory,
    /// so a preflight verdict is about the files the worker would really touch.
    pub fn agent_root(&self) -> Option<PathBuf> {
        self.runtime_dir
            .as_ref()
            .map(|dir| dir.join("agent_workspace"))
    }
}

/// Why the supervisor could not bring the worker up.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SupervisorError {
    /// No worker command could be resolved on this machine.
    Resolve(ResolveError),
    /// The process could not be spawned.
    Spawn(String),
    /// The worker never answered `GET /health` in time.
    Health(String),
    /// A stop was requested while starting.
    Stopped(String),
    /// The shared HTTP client could not be built.
    Client(String),
}

impl fmt::Display for SupervisorError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            SupervisorError::Resolve(err) => write!(f, "{err}"),
            SupervisorError::Spawn(message)
            | SupervisorError::Health(message)
            | SupervisorError::Stopped(message)
            | SupervisorError::Client(message) => f.write_str(message),
        }
    }
}

impl std::error::Error for SupervisorError {}

#[derive(Default)]
struct Tasks {
    monitor: Option<JoinHandle<()>>,
    health: Option<JoinHandle<()>>,
}

/// Owns the worker process and answers "what is its state right now".
///
/// Cheap to clone (everything is behind an `Arc`).
#[derive(Clone)]
pub struct Supervisor {
    shared: Arc<Shared>,
    stop_tx: watch::Sender<bool>,
    tasks: Arc<AsyncMutex<Tasks>>,
}

impl Supervisor {
    /// Supervisor talking to the real machine.
    pub fn new(config: SupervisorConfig) -> Result<Self, SupervisorError> {
        Self::with_probe(config, Arc::new(SystemProbe))
    }

    /// Supervisor with an injected probe (tests, or a caller that wants to
    /// pin the resolution inputs).
    pub fn with_probe(
        config: SupervisorConfig,
        probe: Arc<dyn HostProbe>,
    ) -> Result<Self, SupervisorError> {
        let client = http_client().map_err(|err| SupervisorError::Client(err.to_string()))?;
        Ok(Self {
            shared: Arc::new(Shared::new(config, probe, client)),
            stop_tx: watch::channel(false).0,
            tasks: Arc::new(AsyncMutex::new(Tasks::default())),
        })
    }

    /// The chosen worker port.
    pub fn port(&self) -> u16 {
        self.shared.config.port
    }

    /// `http://127.0.0.1:{port}` — the worker origin the gateway proxies to.
    pub fn worker_origin(&self) -> String {
        self.shared.origin()
    }

    /// The shared HTTP client (reused by the gateway so connections pool).
    pub fn client(&self) -> reqwest::Client {
        self.shared.client.clone()
    }

    /// Current snapshot.
    pub fn status(&self) -> WorkerStatus {
        self.shared.snapshot()
    }

    /// A live `GET /health` probe, folded into the cached state.
    pub async fn probe_health(&self) -> bool {
        let healthy = check_health(&self.shared.client, &self.shared.origin()).await;
        self.shared.observe_health(healthy);
        self.shared.snapshot().healthy
    }

    /// Start the worker and wait for the health gate.
    ///
    /// In `supervise: false` mode nothing is spawned: the health poller starts
    /// and one live probe decides the initial `healthy` flag.
    ///
    /// An `Err` here does not mean the supervisor gave up — a spawn failure or
    /// a missed health deadline leaves the crash monitor running (and
    /// retrying); the error is what the *first* attempt did.
    pub async fn start(&self) -> Result<WorkerStatus, SupervisorError> {
        // The guard must not be held across `snapshot()` — the state mutex is
        // not reentrant.
        let already_started = {
            let mut state = self.shared.state();
            let started = state.started;
            if !started {
                state.started = true;
                state.last_error = None;
            }
            started
        };
        if already_started {
            return Ok(self.shared.snapshot());
        }
        // `send` fails once every receiver is gone (i.e. after a stop), which
        // would leave the flag stuck at "stopping"; `send_replace` always
        // writes the value.
        self.stop_tx.send_replace(false);

        if !self.shared.config.supervise {
            let mut tasks = self.tasks.lock().await;
            tasks.health = Some(tokio::spawn(run_health_poller(
                Arc::clone(&self.shared),
                self.stop_tx.subscribe(),
            )));
            drop(tasks);
            self.probe_health().await;
            return Ok(self.shared.snapshot());
        }

        let (gate_tx, gate_rx) = oneshot::channel();
        {
            let mut tasks = self.tasks.lock().await;
            tasks.monitor = Some(tokio::spawn(run_monitor(
                Arc::clone(&self.shared),
                self.stop_tx.subscribe(),
                gate_tx,
            )));
            tasks.health = Some(tokio::spawn(run_health_poller(
                Arc::clone(&self.shared),
                self.stop_tx.subscribe(),
            )));
        }

        match gate_rx.await {
            Ok(Ok(())) => Ok(self.shared.snapshot()),
            Ok(Err(err)) => Err(err),
            Err(_) => Err(SupervisorError::Stopped(
                "supervisor monitor ended before the worker came up".into(),
            )),
        }
    }

    /// Stop the worker and suppress every restart: SIGTERM, then SIGKILL once
    /// `stop_grace` has elapsed.
    pub async fn stop(&self) -> WorkerStatus {
        self.stop_tx.send_replace(true);
        let pid = self.shared.state().pid;
        if let Some(pid) = pid {
            process::terminate(pid);
        }
        let deadline = Instant::now() + self.shared.config.stop_grace;
        while Instant::now() < deadline && self.shared.state().running {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        if self.shared.state().running {
            if let Some(pid) = pid {
                process::force_kill(pid);
            }
        }

        let mut tasks = self.tasks.lock().await;
        if let Some(mut handle) = tasks.monitor.take() {
            let grace = self.shared.config.stop_grace + Duration::from_secs(1);
            if tokio::time::timeout(grace, &mut handle).await.is_err() {
                handle.abort();
            }
        }
        if let Some(handle) = tasks.health.take() {
            handle.abort();
        }
        drop(tasks);

        {
            let mut state = self.shared.state();
            state.started = false;
            state.running = false;
            state.healthy = false;
            state.pid = None;
        }
        self.shared.snapshot()
    }

    /// Stop then start again, preserving the cumulative restart counter.
    pub async fn restart(&self) -> Result<WorkerStatus, SupervisorError> {
        self.stop().await;
        self.start().await
    }
}

impl fmt::Debug for Supervisor {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Supervisor")
            .field("port", &self.shared.config.port)
            .field("supervised", &self.shared.config.supervise)
            .finish()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn config() -> SupervisorConfig {
        let mut config = SupervisorConfig::new(4825);
        config.health_deadline = Duration::from_millis(30);
        config.health_interval = Duration::from_millis(5);
        config.health_poll_interval = Duration::from_millis(20);
        config.stop_grace = Duration::from_millis(200);
        config
    }

    #[test]
    fn defaults_are_the_documented_ones() {
        let config = SupervisorConfig::new(DEFAULT_PORT);
        assert_eq!(config.port, 4825);
        assert!(config.supervise);
        assert_eq!(config.backoff.base, Duration::from_millis(500));
        assert_eq!(config.backoff.cap, Duration::from_secs(30));
    }

    #[test]
    fn system_dirs_come_from_home() {
        let dir = tempfile::tempdir().expect("tempdir");
        let probe = StaticProbe::new().with_env("HOME", &dir.path().to_string_lossy());
        let config = SupervisorConfig::new(4825).with_system_dirs(&probe);
        assert_eq!(config.log_dir, Some(dir.path().join(".ltcai")));
        assert_eq!(
            config.runtime_dir,
            Some(dir.path().join(".ltcai").join("desktop-runtime"))
        );
    }

    #[tokio::test]
    async fn an_unresolvable_worker_fails_start_with_a_clear_error() {
        let supervisor =
            Supervisor::with_probe(config(), Arc::new(StaticProbe::new())).expect("supervisor");
        let err = supervisor.start().await.expect_err("nothing to run");
        assert!(matches!(err, SupervisorError::Resolve(_)));
        let status = supervisor.status();
        assert!(!status.running);
        assert!(status
            .last_error
            .unwrap_or_default()
            .contains("latticeai.worker_app"));
        supervisor.stop().await;
    }

    #[tokio::test]
    async fn unsupervised_mode_never_spawns_anything() {
        let mut config = config();
        config.supervise = false;
        config.port = 1; // nothing listens on loopback port 1
        let supervisor =
            Supervisor::with_probe(config, Arc::new(StaticProbe::new())).expect("supervisor");
        let status = supervisor.start().await.expect("no spawn, no failure");
        assert!(!status.supervised);
        assert!(!status.healthy);
        assert_eq!(status.pid, None);
        assert_eq!(supervisor.worker_origin(), "http://127.0.0.1:1");
        supervisor.stop().await;
    }

    #[tokio::test]
    async fn start_is_idempotent() {
        let mut config = config();
        config.supervise = false;
        let supervisor =
            Supervisor::with_probe(config, Arc::new(StaticProbe::new())).expect("supervisor");
        supervisor.start().await.expect("first start");
        supervisor.start().await.expect("second start is a no-op");
        supervisor.stop().await;
    }

    #[test]
    fn debug_does_not_leak_internals() {
        let supervisor = Supervisor::new(config()).expect("supervisor");
        let rendered = format!("{supervisor:?}");
        assert!(rendered.contains("port: 4825"));
        assert!(rendered.contains("supervised: true"));
    }

    #[test]
    fn errors_render_their_message() {
        assert_eq!(
            SupervisorError::Spawn("boom".into()).to_string(),
            "boom".to_string()
        );
        assert!(SupervisorError::Resolve(ResolveError::NotFound)
            .to_string()
            .contains("worker unavailable"));
    }
}
