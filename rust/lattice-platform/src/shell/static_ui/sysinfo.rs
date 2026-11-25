use std::collections::BTreeMap;
use std::pin::Pin;
use std::sync::Arc;

use axum::body::Body;
use axum::extract::State;
use axum::http::{header, HeaderValue, Response};
use axum::Router;
use serde_json::{json, Map, Value};

use lattice_core::worker::{WorkerSeamClient, WorkerSeamError};

use super::*;

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct GpuReading {
    pub gpu_mem_gb: f64,
    pub gpu_mem_pct: f64,
}

pub type GpuFuture<'a> = Pin<Box<dyn std::future::Future<Output = Option<GpuReading>> + Send + 'a>>;

pub trait GpuSource: Send + Sync + 'static {
    fn read(&self) -> GpuFuture<'_>;
}

#[derive(Debug, Default, Clone, Copy)]
pub struct NoGpu;

impl GpuSource for NoGpu {
    fn read(&self) -> GpuFuture<'_> {
        Box::pin(async { None })
    }
}

#[derive(Debug, Clone)]
pub struct WorkerGpuSource {
    client: WorkerSeamClient,
}

impl WorkerGpuSource {
    pub fn new(origin: impl AsRef<str>) -> Result<Self, WorkerSeamError> {
        Ok(Self {
            client: WorkerSeamClient::new(origin)?.with_timeout(GPU_SEAM_TIMEOUT),
        })
    }

    pub fn with_client(client: WorkerSeamClient) -> Self {
        Self { client }
    }
}

impl GpuSource for WorkerGpuSource {
    fn read(&self) -> GpuFuture<'_> {
        Box::pin(async move {
            let payload = self.client.get_json(WORKER_SYSINFO_PATH).await.ok()?;
            gpu_from_worker_payload(&payload)
        })
    }
}

pub fn gpu_from_worker_payload(payload: &Value) -> Option<GpuReading> {
    Some(GpuReading {
        gpu_mem_gb: payload.get("gpu_mem_gb")?.as_f64()?,
        gpu_mem_pct: payload.get("gpu_mem_pct")?.as_f64()?,
    })
}

pub struct SysinfoState {
    pub gpu: Arc<dyn GpuSource>,
}

impl std::fmt::Debug for SysinfoState {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("SysinfoState { gpu: <seam> }")
    }
}

pub fn sysinfo_router(state: Arc<SysinfoState>) -> Router {
    Router::new()
        .route("/local/sysinfo", get_only(local_sysinfo))
        .with_state(state)
}

async fn local_sysinfo(State(state): State<Arc<SysinfoState>>) -> Response<Body> {
    let payload = probe_host_capacity(state.gpu.as_ref()).await;
    let body = serde_json::to_vec(&payload).unwrap_or_else(|_| b"{}".to_vec());
    let mut response = Response::new(Body::from(body));
    response.headers_mut().insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("application/json"),
    );
    response
}

pub async fn probe_host_capacity(gpu: &dyn GpuSource) -> Value {
    let mut cpu_pct = 0.0;
    let mut ram_pct = 0.0;
    let mut gpu_mem_gb = 0.0;
    let mut gpu_mem_pct = 0.0;
    let mut error: Option<String> = None;

    match capture_stdout("top", &["-l", "1", "-n", "0"]).await {
        Ok(output) => cpu_pct = parse_cpu_percent(&output).unwrap_or(0.0),
        Err(message) => error = Some(message),
    }
    if error.is_none() {
        match capture_stdout("vm_stat", &[]).await {
            Ok(output) => ram_pct = parse_ram_percent(&output),
            Err(message) => error = Some(message),
        }
    }
    if error.is_none() {
        if let Some(reading) = gpu.read().await {
            gpu_mem_gb = reading.gpu_mem_gb;
            gpu_mem_pct = reading.gpu_mem_pct;
        }
    }

    let mut payload = Map::new();
    payload.insert("cpu_pct".into(), json!(cpu_pct));
    payload.insert("ram_pct".into(), json!(ram_pct));
    payload.insert("gpu_mem_pct".into(), json!(gpu_mem_pct));
    payload.insert("gpu_mem_gb".into(), json!(gpu_mem_gb));
    if let Some(message) = error {
        payload.insert("error".into(), json!(message));
    }
    payload.insert(
        "readiness".into(),
        json!(host_capacity_readiness(cpu_pct, ram_pct, gpu_mem_pct)),
    );
    Value::Object(payload)
}

async fn capture_stdout(program: &str, args: &[&str]) -> Result<String, String> {
    let mut command = tokio::process::Command::new(program);
    command.args(args);
    let run = command.output();
    match tokio::time::timeout(PROBE_TIMEOUT, run).await {
        Ok(Ok(output)) => Ok(String::from_utf8_lossy(&output.stdout).into_owned()),
        Ok(Err(err)) => Err(err.to_string()),
        Err(_) => Err(format!(
            "Command '{program}' timed out after {} seconds",
            PROBE_TIMEOUT.as_secs()
        )),
    }
}

pub fn parse_cpu_percent(top_output: &str) -> Option<f64> {
    let mut latest = None;
    for line in top_output.lines() {
        if !line.contains("CPU usage") {
            continue;
        }
        let Some(user_at) = line.find("% user") else {
            continue;
        };
        let Some(user) = number_before(line, user_at) else {
            continue;
        };
        let tail_from = user_at + "% user".len();
        let Some(sys_at) = line[tail_from..].find("% sys").map(|at| at + tail_from) else {
            continue;
        };
        let Some(sys) = number_before(line, sys_at) else {
            continue;
        };
        latest = Some(lattice_core::pytext::round_to(user + sys, 1));
    }
    latest
}

fn number_before(line: &str, end: usize) -> Option<f64> {
    let bytes = line.as_bytes();
    let mut start = end;
    while start > 0 && (bytes[start - 1].is_ascii_digit() || bytes[start - 1] == b'.') {
        start -= 1;
    }
    if start == end {
        return None;
    }
    line[start..end].parse().ok()
}

pub fn parse_ram_percent(vm_stat_output: &str) -> f64 {
    const KEYS: [&str; 5] = [
        "Pages free",
        "Pages active",
        "Pages inactive",
        "Pages wired down",
        "Pages occupied by compressor",
    ];
    let mut pages: BTreeMap<&str, u64> = BTreeMap::new();
    for line in vm_stat_output.lines() {
        for key in KEYS {
            if line.starts_with(key) {
                if let Some(count) = first_integer(line) {
                    pages.insert(key, count);
                }
            }
        }
    }
    let total: u64 = pages.values().sum();
    if total == 0 {
        return 0.0;
    }
    let used = total - pages.get("Pages free").copied().unwrap_or(0);
    lattice_core::pytext::round_to(used as f64 / total as f64 * 100.0, 1)
}

fn first_integer(line: &str) -> Option<u64> {
    let bytes = line.as_bytes();
    let start = bytes.iter().position(u8::is_ascii_digit)?;
    let end = bytes[start..]
        .iter()
        .position(|byte| !byte.is_ascii_digit())
        .map(|offset| start + offset)
        .unwrap_or(bytes.len());
    line[start..end].parse().ok()
}

pub fn host_capacity_readiness(cpu_pct: f64, ram_pct: f64, gpu_mem_pct: f64) -> &'static str {
    let load = cpu_pct.max(ram_pct).max(gpu_mem_pct);
    if load <= SYSINFO_READINESS_ROOMY_MAX {
        "roomy"
    } else if load <= SYSINFO_READINESS_TIGHT_MAX {
        "tight"
    } else {
        "low"
    }
}
