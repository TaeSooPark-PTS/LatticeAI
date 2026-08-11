//! Shared supervisor state and the crash-monitor / health-poller tasks.
//!
//! The monitor owns the child process. Everything else (the gateway, the Tauri
//! commands) reads the state snapshot, so there is exactly one writer per
//! field and no second source of truth about "is the worker up".

use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant};

use reqwest::Client;
use tokio::sync::{oneshot, watch};

use super::command::{resolve_worker_command, HostProbe, WorkerCommand};
use super::health::{check_health, wait_for_health};
use super::process::spawn_worker;
use super::status::{origin_for, WorkerStatus};
use super::{SupervisorConfig, SupervisorError};

/// Mutable supervisor state — written by the monitor, read by everyone.
#[derive(Debug, Default)]
pub(crate) struct State {
    pub running: bool,
    pub healthy: bool,
    pub pid: Option<u32>,
    pub command: Option<WorkerCommand>,
    pub restarts: u32,
    pub last_error: Option<String>,
    pub started_at: Option<Instant>,
    pub started: bool,
}

/// Everything the supervisor tasks share.
pub(crate) struct Shared {
    pub config: SupervisorConfig,
    pub probe: Arc<dyn HostProbe>,
    pub client: Client,
    state: Mutex<State>,
}

impl Shared {
    pub fn new(config: SupervisorConfig, probe: Arc<dyn HostProbe>, client: Client) -> Self {
        Self {
            config,
            probe,
            client,
            state: Mutex::new(State::default()),
        }
    }

    /// Lock the state, tolerating a poisoned mutex (a panicking reader must not
    /// take the supervisor down with it).
    pub fn state(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(|err| err.into_inner())
    }

    pub fn origin(&self) -> String {
        origin_for(self.config.port)
    }

    pub fn snapshot(&self) -> WorkerStatus {
        let state = self.state();
        WorkerStatus {
            running: state.running,
            healthy: state.healthy,
            pid: state.pid,
            port: self.config.port,
            origin: origin_for(self.config.port),
            command: state.command.as_ref().map(WorkerCommand::display),
            command_origin: state.command.as_ref().map(|cmd| cmd.origin),
            cwd: state
                .command
                .as_ref()
                .and_then(|cmd| cmd.cwd.as_ref())
                .map(|cwd| cwd.to_string_lossy().into_owned()),
            restarts: state.restarts,
            last_error: state.last_error.clone(),
            supervised: self.config.supervise,
            uptime_seconds: state
                .running
                .then(|| state.started_at.map(|at| at.elapsed().as_secs()))
                .flatten(),
        }
    }

    pub fn record_error(&self, message: String) {
        self.state().last_error = Some(message);
    }

    /// Fold a live health observation into the state. A worker that is not
    /// running is never reported healthy, whatever answers on that port.
    pub fn observe_health(&self, healthy: bool) {
        let mut state = self.state();
        state.healthy = healthy && (state.running || !self.config.supervise);
    }
}

/// Sleep, unless a stop is requested first. Returns `true` when stopping.
pub(crate) async fn sleep_or_stop(stop_rx: &mut watch::Receiver<bool>, delay: Duration) -> bool {
    let signalled = tokio::select! {
        _ = tokio::time::sleep(delay) => false,
        result = stop_rx.changed() => result.is_ok(),
    };
    signalled || *stop_rx.borrow()
}

/// Background liveness poller: keeps `healthy` fresh for `/host/status`.
pub(crate) async fn run_health_poller(shared: Arc<Shared>, mut stop_rx: watch::Receiver<bool>) {
    let origin = shared.origin();
    let interval = shared.config.health_poll_interval;
    loop {
        if sleep_or_stop(&mut stop_rx, interval).await {
            return;
        }
        let healthy = check_health(&shared.client, &origin).await;
        shared.observe_health(healthy);
    }
}

type Gate = Option<oneshot::Sender<Result<(), SupervisorError>>>;

fn open_gate(gate: &mut Gate, result: Result<(), SupervisorError>) {
    if let Some(sender) = gate.take() {
        let _ = sender.send(result);
    }
}

/// The crash monitor: spawn → health gate → wait → backoff → repeat.
///
/// A manual stop (the `stop_rx` flag) suppresses every restart; a resolution
/// failure is terminal (the machine is missing the worker, retrying cannot fix
/// that); a spawn failure or a crash is retried with exponential backoff up to
/// `backoff.max_attempts`.
pub(crate) async fn run_monitor(
    shared: Arc<Shared>,
    mut stop_rx: watch::Receiver<bool>,
    gate: oneshot::Sender<Result<(), SupervisorError>>,
) {
    let mut gate: Gate = Some(gate);
    let mut attempt: u32 = 0;
    let origin = shared.origin();

    loop {
        if *stop_rx.borrow() {
            break;
        }
        let command = match resolve_worker_command(shared.probe.as_ref(), shared.config.port) {
            Ok(command) => command,
            Err(err) => {
                let error = SupervisorError::Resolve(err);
                shared.record_error(error.to_string());
                open_gate(&mut gate, Err(error));
                break;
            }
        };
        {
            let mut state = shared.state();
            state.command = Some(command.clone());
        }

        let child = spawn_worker(
            &command,
            shared.config.port,
            shared.config.gateway_port,
            shared.config.runtime_dir.as_deref(),
            shared.config.log_dir.as_deref(),
            shared.probe.as_ref(),
            &shared.config.extra_env,
        );
        let mut child = match child {
            Ok(child) => child,
            Err(err) => {
                let error = SupervisorError::Spawn(format!(
                    "failed to start worker '{}': {err}",
                    command.display()
                ));
                shared.record_error(error.to_string());
                open_gate(&mut gate, Err(error));
                attempt += 1;
                if !shared.config.backoff.may_retry(attempt) {
                    break;
                }
                shared.state().restarts += 1;
                if sleep_or_stop(&mut stop_rx, shared.config.backoff.delay_for(attempt)).await {
                    break;
                }
                continue;
            }
        };

        let pid = child.id();
        {
            let mut state = shared.state();
            state.running = true;
            state.healthy = false;
            state.pid = pid;
            state.started_at = Some(Instant::now());
        }
        let run_started = Instant::now();

        // Health gate — cancellable, so a stop during boot is immediate.
        let gate_result = tokio::select! {
            result = wait_for_health(
                &shared.client,
                &origin,
                shared.config.health_interval,
                shared.config.health_deadline,
            ) => Some(result),
            changed = stop_rx.changed() => {
                let _ = changed;
                None
            }
        };
        match gate_result {
            Some(Ok(_)) => {
                shared.state().healthy = true;
                open_gate(&mut gate, Ok(()));
            }
            Some(Err(timeout)) => {
                let error = SupervisorError::Health(timeout.to_string());
                shared.record_error(error.to_string());
                open_gate(&mut gate, Err(error));
            }
            None => {
                open_gate(
                    &mut gate,
                    Err(SupervisorError::Stopped(
                        "supervisor stopped while waiting for the worker health gate".into(),
                    )),
                );
            }
        }

        let exit = child.wait().await;
        let stopping = *stop_rx.borrow();
        {
            let mut state = shared.state();
            state.running = false;
            state.healthy = false;
            state.pid = None;
            state.started_at = None;
            match &exit {
                Ok(status) if status.success() && stopping => {}
                Ok(status) => {
                    state.last_error = Some(format!("worker exited with {status}"));
                }
                Err(err) => {
                    state.last_error = Some(format!("unable to reap worker: {err}"));
                }
            }
        }

        if stopping {
            break;
        }
        if shared.config.backoff.resets(run_started.elapsed()) {
            attempt = 0;
        }
        attempt += 1;
        if !shared.config.backoff.may_retry(attempt) {
            shared.record_error(format!(
                "worker crashed {attempt} times; giving up after {} restart attempts",
                shared.config.backoff.max_attempts
            ));
            break;
        }
        shared.state().restarts += 1;
        if sleep_or_stop(&mut stop_rx, shared.config.backoff.delay_for(attempt)).await {
            break;
        }
    }

    let mut state = shared.state();
    state.running = false;
    state.healthy = false;
    state.pid = None;
    state.started_at = None;
}

#[cfg(test)]
mod tests {
    use super::super::command::{CommandOrigin, StaticProbe};
    use super::super::health::http_client;
    use super::*;

    fn shared(supervise: bool) -> Arc<Shared> {
        let mut config = SupervisorConfig::new(4825);
        config.supervise = supervise;
        Arc::new(Shared::new(
            config,
            Arc::new(StaticProbe::new()),
            http_client().expect("client"),
        ))
    }

    #[test]
    fn snapshot_reports_the_resolved_command_and_origin() {
        let shared = shared(true);
        {
            let mut state = shared.state();
            state.command = Some(WorkerCommand {
                program: "/usr/bin/python3".into(),
                args: vec!["-m".into(), "latticeai.cli.entrypoint".into()],
                cwd: Some("/work".into()),
                python_root: None,
                origin: CommandOrigin::PythonModule,
            });
            state.restarts = 3;
        }
        let snapshot = shared.snapshot();
        assert_eq!(
            snapshot.command.as_deref(),
            Some("/usr/bin/python3 -m latticeai.cli.entrypoint")
        );
        assert_eq!(snapshot.command_origin, Some(CommandOrigin::PythonModule));
        assert_eq!(snapshot.cwd.as_deref(), Some("/work"));
        assert_eq!(snapshot.restarts, 3);
        assert_eq!(snapshot.uptime_seconds, None);
    }

    #[test]
    fn a_stopped_worker_is_never_reported_healthy() {
        let shared = shared(true);
        shared.observe_health(true);
        assert!(!shared.state().healthy, "not running ⇒ not healthy");
        shared.state().running = true;
        shared.observe_health(true);
        assert!(shared.state().healthy);
    }

    #[test]
    fn unsupervised_mode_trusts_the_live_probe() {
        let shared = shared(false);
        shared.observe_health(true);
        assert!(shared.state().healthy);
    }

    #[test]
    fn uptime_is_reported_only_while_running() {
        let shared = shared(true);
        {
            let mut state = shared.state();
            state.running = true;
            state.started_at = Some(Instant::now());
        }
        assert_eq!(shared.snapshot().uptime_seconds, Some(0));
    }

    #[tokio::test]
    async fn sleep_or_stop_returns_early_when_stopped() {
        let (tx, mut rx) = watch::channel(false);
        let handle = tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(20)).await;
            let _ = tx.send(true);
        });
        let started = Instant::now();
        assert!(sleep_or_stop(&mut rx, Duration::from_secs(30)).await);
        assert!(started.elapsed() < Duration::from_secs(5));
        handle.await.expect("signal task");
    }

    #[tokio::test]
    async fn sleep_or_stop_returns_false_after_a_quiet_sleep() {
        let (_tx, mut rx) = watch::channel(false);
        assert!(!sleep_or_stop(&mut rx, Duration::from_millis(5)).await);
    }
}
