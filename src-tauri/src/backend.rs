//! What the desktop shell asks of `lattice-host`.
//!
//! Through 11.3.0 this file's job was done inline in `main.rs`: four hand-rolled
//! command-resolution rules, a `Command::spawn`, a TCP-connect probe, and a
//! restart that killed the child and spawned another one. All four now live in
//! the `lattice-host` supervisor, which the `lattice-host` binary uses too — so
//! the desktop and the CLI front door cannot drift apart, and the desktop
//! inherits an HTTP `/health` gate, crash restart with backoff, graceful
//! SIGTERM shutdown, and a port that is scanned rather than assumed.
//!
//! What this module adds on top is the two things only the desktop has: the
//! `LATTICEAI_DESKTOP_BACKEND_ORIGIN` override (front an existing worker,
//! spawn nothing) and the `LATTICEAI_DESKTOP_NO_BACKEND` kill switch.

use std::sync::Arc;
use std::time::Duration;

use lattice_host::supervisor::{
    find_free_port, wait_for_health, HostProbe, Supervisor, SupervisorConfig, SupervisorError,
    SystemProbe, WorkerStatus, DEFAULT_PORT, DEFAULT_SCAN_ATTEMPTS,
};
use serde::Serialize;

/// Point the shell at an already-running worker. Nothing is spawned, and this
/// exact string is what `backend_origin` returns and the webview navigates to.
pub const ORIGIN_ENV: &str = "LATTICEAI_DESKTOP_BACKEND_ORIGIN";
/// Kill switch: open the window with no worker at all.
pub const NO_BACKEND_ENV: &str = "LATTICEAI_DESKTOP_NO_BACKEND";
/// An explicit worker port, honoured verbatim.
pub const PORT_ENV: &str = "LATTICEAI_PORT";

/// The desktop shell's view of the worker.
///
/// The first six fields are the contract `latticeApi.desktopBackendStatus`
/// has consumed since the first shell and are unchanged in name, type and
/// meaning. The rest are additive: facts the ad-hoc shell could not know and
/// the supervisor does.
#[derive(Debug, Clone, Serialize)]
pub struct BackendStatus {
    /// Where the frontend should send its requests.
    pub origin: String,
    /// The resolved command line, or why there is not one.
    pub command: String,
    /// Working directory of the worker, when the resolution rule set one.
    pub cwd: Option<String>,
    /// Whether a worker process is alive right now.
    pub running: bool,
    /// Its OS process id, when running.
    pub pid: Option<u32>,
    /// The last failure observed, if any.
    pub last_error: Option<String>,
    /// Whether `GET /health` answered 2xx on the most recent probe.
    pub healthy: bool,
    /// The chosen port — authoritative, not the configured preference.
    pub port: u16,
    /// `false` when the shell only fronts a worker it did not start.
    pub supervised: bool,
    /// How many times the supervisor restarted the worker after a crash.
    pub restarts: u32,
    /// Seconds since the current worker process started.
    pub uptime_seconds: Option<u64>,
}

/// The worker port: an explicit `LATTICEAI_PORT` verbatim, else 4825 scanning
/// upward.
///
/// An explicit port is an instruction, not a preference — if it is busy the
/// shell must fail loudly on it rather than quietly answer somewhere else,
/// because the whole point of setting it is that something else knows the
/// number.
pub fn resolve_port(probe: &dyn HostProbe) -> u16 {
    let pinned = probe
        .env_var(PORT_ENV)
        .and_then(|raw| raw.trim().parse::<u16>().ok())
        .filter(|port| *port != 0);
    match pinned {
        Some(port) => port,
        None => find_free_port(DEFAULT_PORT, DEFAULT_SCAN_ATTEMPTS, &[]).unwrap_or(DEFAULT_PORT),
    }
}

/// The port named by an origin, when it names one.
pub fn origin_port(origin: &str) -> Option<u16> {
    let authority = origin
        .split_once("://")
        .map(|(_, rest)| rest)
        .unwrap_or(origin)
        .split('/')
        .next()
        .unwrap_or_default();
    authority
        .rsplit_once(':')
        .and_then(|(_, port)| port.trim().parse::<u16>().ok())
        .filter(|port| *port != 0)
}

/// The supervisor plus the origin every surface is told about.
pub struct DesktopBackend {
    origin: String,
    supervisor: Supervisor,
    supervised: bool,
    /// The kill switch was set: there is deliberately no worker to wait for.
    disabled: bool,
    health_deadline: Duration,
}

impl DesktopBackend {
    /// Resolve against the real machine.
    pub fn resolve() -> Result<Self, SupervisorError> {
        Self::with_probe(Arc::new(SystemProbe))
    }

    /// Resolve against an injected probe.
    pub fn with_probe(probe: Arc<dyn HostProbe>) -> Result<Self, SupervisorError> {
        let override_origin = probe
            .env_var(ORIGIN_ENV)
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty());
        let disabled = probe.env_var(NO_BACKEND_ENV).is_some();
        let port = override_origin
            .as_deref()
            .and_then(origin_port)
            .unwrap_or_else(|| resolve_port(&*probe));

        // Either override means "do not start anything": one because a worker
        // already exists, the other because the operator asked for none. Only
        // the second means there is nothing to wait for, so an explicit origin
        // wins over the kill switch when both are set.
        let override_origin_absent = override_origin.is_none();
        let supervised = override_origin_absent && !disabled;
        let mut config = SupervisorConfig::new(port).with_system_dirs(&*probe);
        config.supervise = supervised;
        let health_deadline = config.health_deadline;
        let supervisor = Supervisor::with_probe(config, Arc::clone(&probe))?;
        let origin = override_origin.unwrap_or_else(|| supervisor.worker_origin());
        Ok(Self {
            origin,
            supervisor,
            supervised,
            disabled: disabled && override_origin_absent,
            health_deadline,
        })
    }

    /// Where the frontend and the webview should go.
    pub fn origin(&self) -> &str {
        &self.origin
    }

    /// `{origin}/app` — the URL the window navigates to once the worker answers.
    pub fn app_url(&self) -> String {
        format!("{}/app", self.origin.trim_end_matches('/'))
    }

    /// Whether this shell owns the worker process.
    pub fn supervised(&self) -> bool {
        self.supervised
    }

    /// Start the worker (or, in override mode, adopt the one already running).
    pub async fn start(&self) -> BackendStatus {
        let outcome = self.supervisor.start().await;
        if let Err(err) = &outcome {
            // Not fatal: the supervisor keeps retrying in the background, and
            // the window must open either way so the person can read why.
            eprintln!("lattice-ai-desktop: worker did not come up: {err}");
        }
        self.status().await
    }

    /// Wait for `GET {origin}/health`, so the webview never lands on a page the
    /// worker has not started serving yet.
    ///
    /// Replaces the old TCP-connect probe, which proved only that *something*
    /// had bound the port. With the kill switch on there is nothing to wait for
    /// and this returns immediately.
    pub async fn wait_until_serving(&self) -> bool {
        if self.disabled {
            return false;
        }
        wait_for_health(
            &self.supervisor.client(),
            &self.origin,
            Duration::from_millis(250),
            self.health_deadline,
        )
        .await
        .is_ok()
    }

    /// A live snapshot, including a fresh health probe.
    pub async fn status(&self) -> BackendStatus {
        let healthy = self.supervisor.probe_health().await;
        let mut status = self.snapshot();
        status.healthy = healthy;
        status
    }

    /// Stop the worker and suppress restarts.
    pub async fn stop(&self) -> BackendStatus {
        let status = self.supervisor.stop().await;
        self.render(&status)
    }

    /// Stop then start again, preserving the restart counter.
    pub async fn restart(&self) -> BackendStatus {
        if let Err(err) = self.supervisor.restart().await {
            eprintln!("lattice-ai-desktop: restart failed: {err}");
        }
        self.status().await
    }

    fn snapshot(&self) -> BackendStatus {
        self.render(&self.supervisor.status())
    }

    fn render(&self, status: &WorkerStatus) -> BackendStatus {
        BackendStatus {
            origin: self.origin.clone(),
            command: self.command_text(status),
            cwd: status.cwd.clone(),
            running: status.running,
            pid: status.pid,
            last_error: status.last_error.clone(),
            healthy: status.healthy,
            port: status.port,
            supervised: status.supervised,
            restarts: status.restarts,
            uptime_seconds: status.uptime_seconds,
        }
    }

    /// `command` has always been a plain string, never null, so an unresolved
    /// worker says *why* rather than disappearing from the panel.
    fn command_text(&self, status: &WorkerStatus) -> String {
        if let Some(command) = &status.command {
            return command.clone();
        }
        if !self.supervised {
            return format!("external worker at {} (not started here)", self.origin);
        }
        match &status.last_error {
            Some(error) => format!("unavailable: {error}"),
            None => "not resolved yet".to_string(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use lattice_host::supervisor::StaticProbe;

    #[test]
    fn an_origin_names_its_port_or_admits_it_does_not() {
        assert_eq!(origin_port("http://127.0.0.1:8765"), Some(8765));
        assert_eq!(origin_port("http://127.0.0.1:8765/app"), Some(8765));
        assert_eq!(origin_port("https://[::1]:4825"), Some(4825));
        assert_eq!(origin_port("127.0.0.1:4899"), Some(4899));
        assert_eq!(origin_port("http://localhost"), None);
        assert_eq!(origin_port("http://[::1]"), None);
        assert_eq!(origin_port("http://127.0.0.1:0"), None);
        assert_eq!(origin_port(""), None);
    }

    #[test]
    fn an_explicit_port_is_honoured_verbatim() {
        let probe = StaticProbe::new().with_env(PORT_ENV, " 8765 ");
        assert_eq!(resolve_port(&probe), 8765);
    }

    #[test]
    fn without_a_pinned_port_the_scan_starts_at_the_unified_default() {
        let port = resolve_port(&StaticProbe::new());
        assert!(
            (DEFAULT_PORT..DEFAULT_PORT + DEFAULT_SCAN_ATTEMPTS).contains(&port),
            "expected a port scanned upward from {DEFAULT_PORT}, got {port}"
        );
    }

    #[test]
    fn the_origin_override_pins_the_origin_and_spawns_nothing() {
        let probe = StaticProbe::new().with_env(ORIGIN_ENV, "http://127.0.0.1:9100/");
        let backend = DesktopBackend::with_probe(Arc::new(probe)).expect("backend");
        assert_eq!(backend.origin(), "http://127.0.0.1:9100/");
        assert_eq!(backend.app_url(), "http://127.0.0.1:9100/app");
        assert!(!backend.supervised());
    }

    #[test]
    fn the_kill_switch_spawns_nothing_but_still_names_an_origin() {
        let probe = StaticProbe::new().with_env(NO_BACKEND_ENV, "1");
        let backend = DesktopBackend::with_probe(Arc::new(probe)).expect("backend");
        assert!(!backend.supervised());
        assert!(
            backend.disabled,
            "nothing will ever answer, so nothing waits"
        );
        assert!(backend.origin().starts_with("http://127.0.0.1:"));
    }

    #[test]
    fn an_explicit_origin_outranks_the_kill_switch() {
        let probe = StaticProbe::new()
            .with_env(NO_BACKEND_ENV, "1")
            .with_env(ORIGIN_ENV, "http://127.0.0.1:9100");
        let backend = DesktopBackend::with_probe(Arc::new(probe)).expect("backend");
        assert!(!backend.supervised(), "still nothing is spawned");
        assert!(
            !backend.disabled,
            "a worker was named, so the shell waits for it"
        );
    }

    #[test]
    fn the_default_shell_supervises_its_own_worker() {
        let backend = DesktopBackend::with_probe(Arc::new(StaticProbe::new())).expect("backend");
        assert!(backend.supervised());
        assert!(backend.app_url().ends_with("/app"));
    }

    #[test]
    fn an_unresolved_command_says_why_instead_of_going_blank() {
        let backend = DesktopBackend::with_probe(Arc::new(StaticProbe::new())).expect("backend");
        let mut status = WorkerStatus::idle(4825, true);
        assert_eq!(backend.command_text(&status), "not resolved yet");
        status.last_error = Some("worker unavailable".into());
        assert_eq!(
            backend.command_text(&status),
            "unavailable: worker unavailable"
        );
        status.command = Some("/usr/bin/python3 -m latticeai.cli.entrypoint".into());
        assert_eq!(
            backend.command_text(&status),
            "/usr/bin/python3 -m latticeai.cli.entrypoint"
        );
    }

    #[test]
    fn the_status_keeps_every_field_the_frontend_has_ever_read() {
        let backend = DesktopBackend::with_probe(Arc::new(StaticProbe::new())).expect("backend");
        let rendered = backend.render(&WorkerStatus::idle(4825, true));
        let value = serde_json::to_value(&rendered).expect("serialise");
        for key in ["origin", "command", "cwd", "running", "pid", "last_error"] {
            assert!(value.get(key).is_some(), "missing legacy field {key}");
        }
        assert!(value["command"].is_string(), "command is never null");
    }
}
