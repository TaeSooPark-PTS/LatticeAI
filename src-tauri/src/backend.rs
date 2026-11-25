use std::sync::{Arc, Mutex};
use std::time::Duration;

use lattice_host::gateway::{bind_loopback, mounts, serve_gateway, GatewayState};
use lattice_host::supervisor::{
    wait_for_health, EnvOverlayProbe, HostProbe, Supervisor, SupervisorConfig, SystemProbe,
    WorkerStatus,
};

use serde::Serialize;

use crate::topology::{self, Plan, PortPolicy, Topology};

const WORKER_MODULE: &str = "latticeai.worker_app";
const BACKEND_CMD_ENV: &str = "LATTICEAI_DESKTOP_BACKEND_CMD";

#[derive(Debug, Clone, Serialize)]
pub struct BackendStatus {
    pub origin: String,
    pub command: String,
    pub cwd: Option<String>,
    pub running: bool,
    pub pid: Option<u32>,
    pub last_error: Option<String>,
    pub healthy: bool,
    pub port: u16,
    pub supervised: bool,
    pub restarts: u32,
    pub uptime_seconds: Option<u64>,
    pub topology: String,
    pub worker_origin: String,
    pub gateway_origin: Option<String>,
    pub jobs_running: bool,
    pub gateway_error: Option<String>,
    pub worker_warning: Option<String>,
}

struct RunningGateway {
    shutdown: Option<tokio::sync::oneshot::Sender<()>>,
    task: tauri::async_runtime::JoinHandle<()>,
}

pub struct DesktopBackend {
    plan: Plan,
    port_policy: PortPolicy,
    supervisor: Supervisor,
    health_deadline: Duration,
    gateway_lifecycle: tokio::sync::Mutex<()>,
    gateway: Mutex<Option<RunningGateway>>,
    jobs: Mutex<Option<Arc<lattice_jobs::Scheduler>>>,
    gateway_error: Mutex<Option<String>>,
}

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

fn resolve_launch_env_overrides(probe: &dyn HostProbe) -> Vec<(String, String)> {
    let mut overrides = Vec::new();
    if probe
        .env_var("LTCAI_PYTHON")
        .filter(|v| !v.trim().is_empty())
        .is_none()
    {
        if let Some(python) = resolve_worker_python(probe) {
            overrides.push(("LTCAI_PYTHON".to_string(), python));
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
            overrides.push((
                "LATTICEAI_HOST_BIN".to_string(),
                host_bin.to_string_lossy().into_owned(),
            ));
        }
    }
    overrides
}

impl DesktopBackend {
    pub fn resolve() -> Result<Self, String> {
        let overrides = resolve_launch_env_overrides(&SystemProbe);
        let probe: Arc<dyn HostProbe> = if overrides.is_empty() {
            Arc::new(SystemProbe)
        } else {
            Arc::new(EnvOverlayProbe::new(
                Arc::new(SystemProbe),
                overrides.clone(),
            ))
        };
        Self::with_probe_policy_and_env(probe, PortPolicy::for_build(), overrides)
    }

    #[cfg(test)]
    pub fn with_probe(probe: Arc<dyn HostProbe>) -> Result<Self, String> {
        Self::with_probe_policy_and_env(probe, PortPolicy::AllowDynamicFallback, Vec::new())
    }

    #[cfg(test)]
    pub fn with_probe_and_policy(
        probe: Arc<dyn HostProbe>,
        port_policy: PortPolicy,
    ) -> Result<Self, String> {
        Self::with_probe_policy_and_env(probe, port_policy, Vec::new())
    }

    fn with_probe_policy_and_env(
        probe: Arc<dyn HostProbe>,
        port_policy: PortPolicy,
        extra_env: Vec<(String, String)>,
    ) -> Result<Self, String> {
        let plan = topology::resolve_with_policy(&*probe, port_policy)?;
        let mut config = SupervisorConfig::new(plan.worker_port).with_system_dirs(&*probe);
        config.supervise = plan.supervised();
        config.gateway_port = plan.gateway_port;
        config.extra_env.extend(extra_env);
        let health_deadline = config.health_deadline;
        let supervisor =
            Supervisor::with_probe(config, Arc::clone(&probe)).map_err(|err| err.to_string())?;
        let jobs = (plan.topology == Topology::Gateway)
            .then(|| mounts::scheduler(&plan.worker_origin, supervisor.client()));
        Ok(Self {
            plan,
            port_policy,
            supervisor,
            health_deadline,
            gateway_lifecycle: tokio::sync::Mutex::new(()),
            gateway: Mutex::new(None),
            jobs: Mutex::new(jobs),
            gateway_error: Mutex::new(None),
        })
    }

    pub fn origin(&self) -> &str {
        &self.plan.origin
    }

    pub fn app_url(&self) -> String {
        self.plan.app_url()
    }

    pub fn supervised(&self) -> bool {
        self.plan.supervised()
    }

    pub fn topology(&self) -> Topology {
        self.plan.topology
    }

    pub fn gateway_error(&self) -> Option<String> {
        self.gateway_error
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone()
    }

    pub async fn start(&self) -> BackendStatus {
        self.start_gateway().await;
        if self.gateway_error().is_some() {
            return self.status().await;
        }
        let outcome = self.supervisor.start().await;
        if let Err(err) = &outcome {
            eprintln!("lattice-ai-desktop: worker did not come up: {err}");
        }
        self.status().await
    }

    async fn start_gateway(&self) {
        let Some(port) = self.plan.gateway_port else {
            return;
        };
        let _lifecycle = self.gateway_lifecycle.lock().await;
        if self
            .gateway
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .is_some()
        {
            return;
        }
        let addr = std::net::SocketAddr::from(([127, 0, 0, 1], port));
        let listener = match bind_loopback(addr).await {
            Ok(listener) => listener,
            Err(err) => {
                let message = match self.port_policy {
                    PortPolicy::PinnedProduction => format!(
                        "the gateway could not bind {addr}: {err} — production Lattice AI \
                         pins http://127.0.0.1:{port} to match desktop CSP and IPC capabilities \
                         and will not silently fall back to an unreachable port. Free port \
                         {port} or set LATTICEAI_DESKTOP_BACKEND_ORIGIN=http://127.0.0.1:{port} \
                         to attach to an already-running lattice-host"
                    ),
                    PortPolicy::AllowDynamicFallback => format!(
                        "the gateway could not bind {addr}: {err} — \
                         set LATTICEAI_PORT to a free port, or \
                         LATTICEAI_DESKTOP_BACKEND_ORIGIN to an already-running lattice-host"
                    ),
                };
                eprintln!("lattice-ai-desktop: {message}");
                *self.gateway_error.lock().unwrap_or_else(|e| e.into_inner()) = Some(message);
                return;
            }
        };

        let mut state = match GatewayState::with_client(
            Arc::new(self.supervisor.clone()),
            self.supervisor.client(),
        ) {
            Ok(state) => state,
            Err(err) => {
                let message = format!("the gateway could not initialize HTTP client: {err}");
                eprintln!("lattice-ai-desktop: {message}");
                *self.gateway_error.lock().unwrap_or_else(|e| e.into_inner()) = Some(message);
                return;
            }
        };
        if let Some(root) = self.agent_root() {
            state = state.with_agent_root(root);
        }
        let mut state = match state.open_product() {
            Ok(state) => state,
            Err(err) => {
                let message = format!("the gateway could not open the product surface: {err}");
                eprintln!("lattice-ai-desktop: {message}");
                *self.gateway_error.lock().unwrap_or_else(|e| e.into_inner()) = Some(message);
                return;
            }
        };
        state = self.attach_product_scheduler(state);

        let (tx, rx) = tokio::sync::oneshot::channel::<()>();
        let task = tauri::async_runtime::spawn(async move {
            if let Err(err) = serve_gateway(listener, Arc::new(state), async {
                let _ = rx.await;
            })
            .await
            {
                eprintln!("lattice-ai-desktop: the gateway stopped: {err}");
            }
        });
        *self.gateway_error.lock().unwrap_or_else(|e| e.into_inner()) = None;
        *self.gateway.lock().unwrap_or_else(|e| e.into_inner()) = Some(RunningGateway {
            shutdown: Some(tx),
            task,
        });
        eprintln!("lattice-ai-desktop: gateway listening on {}", self.origin());

        self.start_jobs();
    }

    fn attach_product_scheduler(&self, mut state: GatewayState) -> GatewayState {
        if self.plan.topology == Topology::Gateway {
            if let Some(product) = state.product() {
                let scheduler = mounts::scheduler_with_graph(
                    &self.plan.worker_origin,
                    self.supervisor.client(),
                    product.graph.clone(),
                );
                state = state.with_jobs(Arc::clone(&scheduler));
                *self.jobs.lock().unwrap_or_else(|e| e.into_inner()) = Some(scheduler);
                return state;
            }
        }
        if let Some(scheduler) = self.jobs.lock().unwrap_or_else(|e| e.into_inner()).clone() {
            state = state.with_jobs(scheduler);
        }
        state
    }

    fn start_jobs(&self) {
        let Some(scheduler) = self.jobs.lock().unwrap_or_else(|e| e.into_inner()).clone() else {
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

    fn agent_root(&self) -> Option<std::path::PathBuf> {
        SupervisorConfig::new(self.plan.worker_port)
            .with_system_dirs(&SystemProbe)
            .agent_root()
    }

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

    pub async fn status(&self) -> BackendStatus {
        let healthy = self.supervisor.probe_health().await;
        let mut status = self.snapshot();
        status.healthy = healthy;
        status
    }

    pub async fn stop(&self) -> BackendStatus {
        let status = self.supervisor.stop().await;
        self.render(&status)
    }

    pub async fn restart(&self) -> Result<BackendStatus, String> {
        if let Err(err) = self.supervisor.restart().await {
            eprintln!("lattice-ai-desktop: restart failed: {err}");
            return Err(format!("worker restart failed: {err}"));
        }
        Ok(self.status().await)
    }

    pub async fn stop_gateway(&self) {
        let _lifecycle = self.gateway_lifecycle.lock().await;
        let running = {
            let mut guard = self.gateway.lock().unwrap_or_else(|e| e.into_inner());
            guard.take()
        };
        if let Some(mut gw) = running {
            if let Some(tx) = gw.shutdown.take() {
                let _ = tx.send(());
            }
            let _ = tokio::time::timeout(Duration::from_secs(5), gw.task).await;
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
                .unwrap_or_else(|e| e.into_inner())
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
