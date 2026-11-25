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
    resolve_worker_command, CommandOrigin, EnvOverlayProbe, HostProbe, ResolveError, StaticProbe,
    SystemProbe, WorkerCommand,
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

#[derive(Debug, Clone)]
pub struct SupervisorConfig {
    pub port: u16,
    pub gateway_port: Option<u16>,
    pub supervise: bool,
    pub runtime_dir: Option<PathBuf>,
    pub log_dir: Option<PathBuf>,
    pub health_interval: Duration,
    pub health_deadline: Duration,
    pub health_poll_interval: Duration,
    pub backoff: BackoffPolicy,
    pub stop_grace: Duration,
    pub extra_env: Vec<(String, String)>,
}

impl SupervisorConfig {
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

    pub fn with_system_dirs(mut self, probe: &dyn HostProbe) -> Self {
        self.log_dir = worker_env::ltcai_home(probe);
        self.runtime_dir = worker_env::desktop_runtime_dir(probe);
        self.inherit_agent_root(probe);
        self
    }

    fn inherit_agent_root(&mut self, probe: &dyn HostProbe) {
        if self
            .extra_env
            .iter()
            .any(|(key, _)| key == worker_env::AGENT_ROOT_ENV)
        {
            return;
        }
        if let Some(root) = probe.env_var(worker_env::AGENT_ROOT_ENV) {
            let trimmed = root.trim();
            if !trimmed.is_empty() {
                self.extra_env
                    .push((worker_env::AGENT_ROOT_ENV.into(), trimmed.to_string()));
            }
        }
    }

    pub fn agent_root(&self) -> Option<PathBuf> {
        if let Some((_, value)) = self
            .extra_env
            .iter()
            .rev()
            .find(|(key, _)| key == worker_env::AGENT_ROOT_ENV)
        {
            let trimmed = value.trim();
            if !trimmed.is_empty() {
                return Some(PathBuf::from(trimmed));
            }
        }
        self.runtime_dir
            .as_ref()
            .map(|dir| dir.join("agent_workspace"))
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SupervisorError {
    Resolve(ResolveError),
    Spawn(String),
    Health(String),
    Stopped(String),
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

#[derive(Clone)]
pub struct Supervisor {
    shared: Arc<Shared>,
    stop_tx: watch::Sender<bool>,
    tasks: Arc<AsyncMutex<Tasks>>,
}

impl Supervisor {
    pub fn new(config: SupervisorConfig) -> Result<Self, SupervisorError> {
        Self::with_probe(config, Arc::new(SystemProbe))
    }

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

    pub fn port(&self) -> u16 {
        self.shared.config.port
    }

    pub fn worker_origin(&self) -> String {
        self.shared.origin()
    }

    pub fn client(&self) -> reqwest::Client {
        self.shared.client.clone()
    }

    pub fn status(&self) -> WorkerStatus {
        self.shared.snapshot()
    }

    pub async fn probe_health(&self) -> bool {
        let healthy = check_health(&self.shared.client, &self.shared.origin()).await;
        self.shared.observe_health(healthy);
        self.shared.snapshot().healthy
    }

    pub async fn start(&self) -> Result<WorkerStatus, SupervisorError> {
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
        assert_eq!(
            config.agent_root(),
            Some(
                dir.path()
                    .join(".ltcai")
                    .join("desktop-runtime")
                    .join("agent_workspace")
            ),
            "without LATTICEAI_AGENT_ROOT the desktop runtime is the fallback"
        );
    }

    #[test]
    fn an_explicit_agent_root_wins_over_the_desktop_runtime() {
        let dir = tempfile::tempdir().expect("tempdir");
        let probe = StaticProbe::new()
            .with_env("HOME", &dir.path().to_string_lossy())
            .with_env("LATTICEAI_AGENT_ROOT", "/tmp/session/agent");
        let config = SupervisorConfig::new(4825).with_system_dirs(&probe);
        assert_eq!(
            config.agent_root(),
            Some(PathBuf::from("/tmp/session/agent"))
        );
        assert!(
            config
                .extra_env
                .iter()
                .any(|(key, value)| key == "LATTICEAI_AGENT_ROOT" && value == "/tmp/session/agent"),
            "the worker must inherit the same root via extra_env: {:?}",
            config.extra_env
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
        config.port = 1;
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
