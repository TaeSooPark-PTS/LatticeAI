//! What the desktop shell asks of `lattice-host`.
//!
//! Through 11.3.0 this file's job was done inline in `main.rs`: four hand-rolled
//! command-resolution rules, a `Command::spawn`, a TCP-connect probe, and a
//! restart that killed the child and spawned another one. All four moved into
//! the `lattice-host` supervisor in 11.4.0, which the `lattice-host` binary uses
//! too — so the desktop and the CLI front door cannot drift apart.
//!
//! 11.5.0 finishes the job: the shell no longer only *supervises* a worker, it
//! **is** the front door. In the default topology it serves `lattice-host`'s
//! gateway on the public port — the `/host/*` status routes, the native
//! `/rust/*` retrieval, ingest and agent-kernel surfaces, the `/host/jobs`
//! scheduler — and the worker runs on an internal port behind it. The webview
//! navigates to the gateway, and the worker is told to trust that origin so
//! cookie-authenticated writes still work through the proxy hop.
//!
//! 11.6.0 One Door: the Python-direct topologies are gone. The shell always
//! boots or attaches through lattice-host. The window health-gates on
//! `GET {origin}/health` — the **host** surface at the same origin the window
//! loads, not a bare worker port.
//!
//! Which arrangement is chosen lives in [`crate::topology`]; what this module
//! adds is the running of it, and the five IPC contracts on top (unchanged in
//! name, type and meaning — the new status fields are additive).

use std::sync::{Arc, Mutex};
use std::time::Duration;

use lattice_host::gateway::{bind_loopback, mounts, serve_gateway, GatewayState};
use lattice_host::supervisor::{
    wait_for_health, HostProbe, Supervisor, SupervisorConfig, SupervisorError, SystemProbe,
    WorkerStatus,
};

use serde::Serialize;

use crate::topology::{self, Plan, Topology};

/// The module the host supervises. I6's launch string is
/// `uvicorn latticeai.worker_app:create_worker_app --factory`; the module form
/// reads `LATTICEAI_PORT` from the supervisor, so we do not have to know the
/// internal port at pin time.
const WORKER_MODULE: &str = "latticeai.worker_app";
const BACKEND_CMD_ENV: &str = "LATTICEAI_DESKTOP_BACKEND_CMD";

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
    /// `false` when the shell only fronts a host it did not start.
    pub supervised: bool,
    /// How many times the supervisor restarted the worker after a crash.
    pub restarts: u32,
    /// Seconds since the current worker process started.
    pub uptime_seconds: Option<u64>,
    /// Which arrangement this shell is running: `gateway` or `external`
    /// (11.5.0, additive; `direct`/`disabled` retired in 11.6.0).
    pub topology: String,
    /// Where the worker itself answers. Equal to `origin` unless a gateway is
    /// in front of it (11.5.0, additive).
    pub worker_origin: String,
    /// The gateway's origin when one is served here, else `null`
    /// (11.5.0, additive).
    pub gateway_origin: Option<String>,
    /// Whether the background jobs timer is running in this process
    /// (11.5.0, additive).
    pub jobs_running: bool,
    /// Why the front door is not there, when this shell was supposed to serve
    /// one and could not bind it (11.5.2, additive). `null` normally.
    pub gateway_error: Option<String>,
    /// Non-silent warning when the Python AI worker runtime is unavailable or
    /// degraded while the native Rust Brain surface remains active (additive).
    pub worker_warning: Option<String>,
}

/// The gateway this shell is serving, and how to stop it.
struct RunningGateway {
    shutdown: Option<tokio::sync::oneshot::Sender<()>>,
}

/// The supervisor, the front door, and the origin every surface is told about.
pub struct DesktopBackend {
    plan: Plan,
    supervisor: Supervisor,
    health_deadline: Duration,
    /// Present once the gateway is serving. `Mutex` because the Tauri commands
    /// touch it from whichever thread the runtime hands them.
    gateway: Mutex<Option<RunningGateway>>,
    /// The jobs scheduler, mounted on `/host/jobs` in the gateway topology.
    /// Upgraded to a graph-bound native drain once `open_product` succeeds.
    jobs: Mutex<Option<Arc<lattice_jobs::Scheduler>>>,
    /// Why the front door is not there, when it could not be bound.
    ///
    /// `None` means "nothing went wrong" — including in the topology that
    /// attaches to someone else's host, where this process serves no gateway.
    gateway_error: Mutex<Option<String>>,
}

/// True when the packaged Resources tree already carries a worker.
///
/// Leave `LATTICEAI_DESKTOP_BACKEND_CMD` unset in that case so the supervisor
/// can point `PYTHONPATH` at the tree (its bundled-tree rule). Integrator
/// must flip `WORKER_MODULE` from `latticeai.cli.entrypoint` to
/// `latticeai.worker_app`.
fn bundled_worker_present(probe: &dyn HostProbe) -> bool {
    let Some(resources) = probe.resource_dir() else {
        return false;
    };
    for root in [resources.join("_up_"), resources] {
        if probe.is_file(&root.join("latticeai").join("worker_app.py"))
            || probe.is_file(&root.join("latticeai").join("cli").join("entrypoint.py"))
        {
            return true;
        }
    }
    false
}

/// Discover a packaged `lattice-host` sidecar/sibling binary before falling
/// back to a developer workspace build directory.
pub fn resolve_packaged_host_binary(
    probe: &dyn HostProbe,
    exe_dir: Option<&std::path::Path>,
    workspace_root: Option<&std::path::Path>,
) -> Option<std::path::PathBuf> {
    let bin_name = if cfg!(windows) {
        "lattice-host.exe"
    } else {
        "lattice-host"
    };
    for env_key in ["LATTICEAI_HOST_BIN", "LTCAI_HOST"] {
        if let Some(pinned) = probe.env_var(env_key).filter(|v| !v.trim().is_empty()) {
            let path = std::path::PathBuf::from(pinned);
            if probe.is_file(&path) {
                return Some(path);
            }
        }
    }
    if let Some(dir) = exe_dir {
        let sidecar_arch = format!(
            "lattice-host-{}-{}{}",
            std::env::consts::ARCH,
            std::env::consts::OS,
            if cfg!(windows) { ".exe" } else { "" }
        );
        for candidate in [dir.join(bin_name), dir.join(sidecar_arch)] {
            if probe.is_file(&candidate) {
                return Some(candidate);
            }
        }
    }
    if let Some(resources) = probe.resource_dir() {
        for candidate in [
            resources.join("bin").join(bin_name),
            resources.join(bin_name),
        ] {
            if probe.is_file(&candidate) {
                return Some(candidate);
            }
        }
    }
    if let Some(home) = probe
        .env_var("HOME")
        .or_else(|| probe.env_var("USERPROFILE"))
        .filter(|v| !v.trim().is_empty())
    {
        let managed = std::path::PathBuf::from(home)
            .join(".ltcai")
            .join("bin")
            .join(bin_name);
        if probe.is_file(&managed) {
            return Some(managed);
        }
    }
    if let Some(ws) = workspace_root {
        for profile in ["release", "debug"] {
            let built = ws.join("rust").join("target").join(profile).join(bin_name);
            if probe.is_file(&built) {
                return Some(built);
            }
        }
    }
    None
}

/// Resolve the Python interpreter for the supervised AI worker (`--factory`),
/// or `None` when an explicit command override is already active.
///
/// Pinning `LTCAI_PYTHON` (rather than setting `LATTICEAI_DESKTOP_BACKEND_CMD`
/// to a bare `-m latticeai.worker_app`) lets [`Supervisor`] construct its
/// canonical `python -m uvicorn latticeai.worker_app:create_worker_app --factory
/// --host 127.0.0.1 --port <chosen>` command for both unpackaged and bundled `.app` builds.
fn resolve_worker_python(probe: &dyn HostProbe) -> Option<String> {
    if probe
        .env_var(BACKEND_CMD_ENV)
        .filter(|value| !value.trim().is_empty())
        .is_some()
    {
        return None;
    }
    for env_key in ["LATTICEAI_WORKER_PYTHON", "LTCAI_PYTHON"] {
        if let Some(pinned) = probe.env_var(env_key).filter(|v| !v.trim().is_empty()) {
            return Some(pinned);
        }
    }
    let candidates = lattice_host::supervisor::command::python_candidates(probe);
    if bundled_worker_present(probe) {
        return candidates
            .iter()
            .find(|program| probe.module_importable(program, "uvicorn"))
            .or_else(|| {
                candidates
                    .iter()
                    .find(|program| probe.is_file(std::path::Path::new(program)))
            })
            .cloned();
    }
    candidates
        .iter()
        .find(|program| probe.module_importable(program, WORKER_MODULE))
        .cloned()
        .or_else(|| candidates.first().cloned())
}

/// Pin `LTCAI_PYTHON` (and `LATTICEAI_HOST_BIN` when a packaged sidecar exists)
/// on the process environment when resolved, once.
fn pin_worker_launch_if_needed(probe: &dyn HostProbe) {
    if probe
        .env_var("LTCAI_PYTHON")
        .filter(|v| !v.trim().is_empty())
        .is_none()
    {
        if let Some(python) = resolve_worker_python(probe) {
            std::env::set_var("LTCAI_PYTHON", python);
        }
    }
    if probe
        .env_var("LATTICEAI_HOST_BIN")
        .filter(|v| !v.trim().is_empty())
        .is_none()
    {
        let exe_dir = std::env::current_exe()
            .ok()
            .and_then(|p| p.parent().map(std::path::Path::to_path_buf));
        let workspace_root = std::env::current_dir().ok();
        if let Some(host_bin) =
            resolve_packaged_host_binary(probe, exe_dir.as_deref(), workspace_root.as_deref())
        {
            std::env::set_var("LATTICEAI_HOST_BIN", host_bin);
        }
    }
}

impl DesktopBackend {
    /// Resolve against the real machine.
    pub fn resolve() -> Result<Self, SupervisorError> {
        pin_worker_launch_if_needed(&SystemProbe);
        Self::with_probe(Arc::new(SystemProbe))
    }

    /// Resolve against an injected probe.
    pub fn with_probe(probe: Arc<dyn HostProbe>) -> Result<Self, SupervisorError> {
        let plan = topology::resolve(&*probe);
        let mut config = SupervisorConfig::new(plan.worker_port).with_system_dirs(&*probe);
        config.supervise = plan.supervised();
        // Behind a gateway the worker's own CSRF default knows only its
        // internal port; naming the front door here is what makes a
        // cookie-authenticated POST through the proxy work at all.
        config.gateway_port = plan.gateway_port;
        let health_deadline = config.health_deadline;
        let supervisor = Supervisor::with_probe(config, Arc::clone(&probe))?;
        let jobs = (plan.topology == Topology::Gateway)
            .then(|| mounts::scheduler(&plan.worker_origin, supervisor.client()));
        Ok(Self {
            plan,
            supervisor,
            health_deadline,
            gateway: Mutex::new(None),
            jobs: Mutex::new(jobs),
            gateway_error: Mutex::new(None),
        })
    }

    /// Where the frontend and the webview should go.
    pub fn origin(&self) -> &str {
        &self.plan.origin
    }

    /// `{origin}/app` — the URL the window navigates to once health answers.
    pub fn app_url(&self) -> String {
        self.plan.app_url()
    }

    /// Whether this shell owns the worker process.
    pub fn supervised(&self) -> bool {
        self.plan.supervised()
    }

    /// Which arrangement this shell resolved.
    pub fn topology(&self) -> Topology {
        self.plan.topology
    }

    /// Why the front door is unavailable, when it is.
    ///
    /// `Some` only when this shell was supposed to serve the gateway and could
    /// not bind it — in which case [`Self::app_url`] names a port nothing will
    /// ever listen on, and `main::boot` must not navigate the window into it.
    pub fn gateway_error(&self) -> Option<String> {
        self.gateway_error
            .lock()
            .expect("gateway error lock")
            .clone()
    }

    /// Start the front door, then the worker.
    ///
    /// The gateway comes up first on purpose: it is what the webview polls, and
    /// a front door that answers "worker not up yet" is more use than a
    /// connection refused.
    pub async fn start(&self) -> BackendStatus {
        self.start_gateway().await;
        let outcome = self.supervisor.start().await;
        if let Err(err) = &outcome {
            // Not fatal: the supervisor keeps retrying in the background, and
            // the window must open either way so the person can read why.
            eprintln!("lattice-ai-desktop: worker did not come up: {err}");
        }
        self.status().await
    }

    /// Serve the gateway on the public port, once.
    ///
    /// A failure here is logged rather than fatal — the window still opens, and
    /// the browser's own "cannot connect" plus this line is a better failure
    /// than a shell that exits. Attach to a running host with
    /// `LATTICEAI_DESKTOP_BACKEND_ORIGIN` if this process cannot bind.
    async fn start_gateway(&self) {
        let Some(port) = self.plan.gateway_port else {
            return;
        };
        if self.gateway.lock().expect("gateway lock").is_some() {
            return;
        }
        let addr = std::net::SocketAddr::from(([127, 0, 0, 1], port));
        let listener = match bind_loopback(addr).await {
            Ok(listener) => listener,
            Err(err) => {
                let message = format!(
                    "the gateway could not bind {addr}: {err} — \
                     set LATTICEAI_PORT to a free port, or \
                     LATTICEAI_DESKTOP_BACKEND_ORIGIN to an already-running lattice-host"
                );
                eprintln!("lattice-ai-desktop: {message}");
                // Recorded, not only logged: `plan.origin` names the port that
                // just failed to bind, so navigating the window there would
                // land on a connection refused with no explanation. The window
                // stays on the bundled shell and reads this instead.
                *self.gateway_error.lock().expect("gateway error lock") = Some(message);
                return;
            }
        };

        let mut state =
            GatewayState::with_client(Arc::new(self.supervisor.clone()), self.supervisor.client());
        if let Some(root) = self.agent_root() {
            state = state.with_agent_root(root);
        }
        let mut state = match state.open_product() {
            Ok(state) => state,
            Err(err) => {
                let message = format!("the gateway could not open the product surface: {err}");
                eprintln!("lattice-ai-desktop: {message}");
                *self.gateway_error.lock().expect("gateway error lock") = Some(message);
                return;
            }
        };
        state = self.attach_product_scheduler(state);

        let (tx, rx) = tokio::sync::oneshot::channel::<()>();
        tauri::async_runtime::spawn(async move {
            if let Err(err) = serve_gateway(listener, Arc::new(state), async {
                let _ = rx.await;
            })
            .await
            {
                eprintln!("lattice-ai-desktop: the gateway stopped: {err}");
            }
        });
        *self.gateway.lock().expect("gateway lock") = Some(RunningGateway { shutdown: Some(tx) });
        eprintln!("lattice-ai-desktop: gateway listening on {}", self.origin());

        self.start_jobs();
    }

    /// Start the background jobs timer, unless it is switched off.
    ///
    /// It keeps running across `shutdown_backend`/`restart_backend`: a tick
    /// against a stopped worker fails, backs off, and is picked up again when
    /// the worker returns — which is exactly what a scheduler should do, and
    /// cheaper than a second lifecycle to keep in step.
    fn attach_product_scheduler(&self, mut state: GatewayState) -> GatewayState {
        if self.plan.topology == Topology::Gateway {
            if let Some(product) = state.product() {
                let scheduler = mounts::scheduler_with_graph(
                    &self.plan.worker_origin,
                    self.supervisor.client(),
                    product.graph.clone(),
                );
                state = state.with_jobs(Arc::clone(&scheduler));
                *self.jobs.lock().expect("jobs lock") = Some(scheduler);
                return state;
            }
        }
        if let Some(scheduler) = self.jobs.lock().expect("jobs lock").clone() {
            state = state.with_jobs(scheduler);
        }
        state
    }

    fn start_jobs(&self) {
        let Some(scheduler) = self.jobs.lock().expect("jobs lock").clone() else {
            return;
        };
        if !mounts::jobs_enabled_from_env() {
            eprintln!("lattice-ai-desktop: jobs scheduler off (LATTICEAI_JOBS); /host/jobs is manual only");
            return;
        }
        let interval = scheduler.config().interval.as_secs();
        scheduler.spawn(std::future::pending::<()>());
        eprintln!("lattice-ai-desktop: jobs scheduler every {interval}s");
    }

    /// The agent workspace the worker will be given, so the host's native
    /// kernel judges the same directory.
    fn agent_root(&self) -> Option<std::path::PathBuf> {
        SupervisorConfig::new(self.plan.worker_port)
            .with_system_dirs(&SystemProbe)
            .agent_root()
    }

    /// Wait for `GET {origin}/health` on the **host** origin the window loads.
    ///
    /// Replaces the old TCP-connect probe, which proved only that *something*
    /// had bound the port. After One Door this is never a bare worker port:
    /// `plan.origin` is the in-process gateway or the attached lattice-host.
    pub async fn wait_until_serving(&self) -> bool {
        wait_for_health(
            &self.supervisor.client(),
            &self.plan.origin,
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

    /// Stop the worker and suppress restarts. The gateway keeps serving, so
    /// `/host/status` still answers and the window can say what happened.
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

    /// Stop the gateway too. Called on the way out of the process.
    pub fn stop_gateway(&self) {
        if let Some(running) = self.gateway.lock().expect("gateway lock").as_mut() {
            if let Some(tx) = running.shutdown.take() {
                let _ = tx.send(());
            }
        }
    }

    fn snapshot(&self) -> BackendStatus {
        self.render(&self.supervisor.status())
    }

    fn render(&self, status: &WorkerStatus) -> BackendStatus {
        BackendStatus {
            origin: self.plan.origin.clone(),
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
            topology: self.plan.topology.as_str().to_string(),
            worker_origin: self.plan.worker_origin.clone(),
            gateway_origin: self.plan.gateway_port.map(|_| self.plan.origin.clone()),
            jobs_running: self
                .jobs
                .lock()
                .expect("jobs lock")
                .as_ref()
                .map(|scheduler| scheduler.is_running())
                .unwrap_or(false),
            gateway_error: self.gateway_error(),
            worker_warning: self.worker_warning(status),
        }
    }

    fn worker_warning(&self, status: &WorkerStatus) -> Option<String> {
        if !self.supervised() || status.running {
            return None;
        }
        status.last_error.as_ref().map(|err| {
            format!(
                "AI worker degraded ({err}). Local Brain graph/search/notes remain active; \
                 install Python 3.11+ with Lattice AI dependencies or set LTCAI_PYTHON for \
                 local embeddings and model inference."
            )
        })
    }

    /// `command` has always been a plain string, never null, so an unresolved
    /// worker says *why* rather than disappearing from the panel.
    fn command_text(&self, status: &WorkerStatus) -> String {
        if let Some(command) = &status.command {
            return command.clone();
        }
        if !self.supervised() {
            return format!(
                "external lattice-host at {} (not started here)",
                self.plan.origin
            );
        }
        match &status.last_error {
            Some(error) => format!("unavailable: {error}"),
            None => "not resolved yet".to_string(),
        }
    }
}

#[cfg(test)]
#[path = "backend_tests.rs"]
mod tests;
