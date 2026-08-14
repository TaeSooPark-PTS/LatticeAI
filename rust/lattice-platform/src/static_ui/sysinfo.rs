#![allow(
    dead_code,
    unused_imports,
    unused_variables,
    unused_assignments,
    unused_mut,
    private_interfaces,
    clippy::result_large_err,
    clippy::needless_lifetimes,
    clippy::too_many_arguments,
    clippy::type_complexity,
    clippy::collapsible_if,
    clippy::needless_as_bytes,
    clippy::redundant_closure,
    clippy::needless_return,
    clippy::manual_clamp,
    clippy::ptr_arg,
    clippy::unnecessary_sort_by,
    clippy::result_unit_err,
    clippy::useless_vec,
    clippy::uninlined_format_args,
    clippy::manual_contains,
    clippy::needless_borrows_for_generic_args,
    clippy::implicit_clone,
    clippy::unnecessary_map_or,
    clippy::match_like_matches_macro,
    clippy::manual_range_contains,
    clippy::derivable_impls,
    clippy::needless_pass_by_ref_mut,
    clippy::redundant_guards,
    clippy::map_identity,
    clippy::iter_overeager_cloned,
    clippy::explicit_auto_deref,
    clippy::bool_comparison,
    clippy::nonminimal_bool,
    clippy::if_same_then_else,
    clippy::question_mark,
    clippy::single_char_pattern,
    clippy::manual_pattern_char_comparison,
    clippy::manual_is_ascii_check,
    clippy::repeat_once,
    clippy::unused_self,
    clippy::module_inception
)]

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::pin::Pin;
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use axum::body::Body;
use axum::extract::{RawQuery, State};
use axum::http::{header, HeaderMap, HeaderValue, Response, StatusCode};
use axum::middleware::{from_fn, Next};
use axum::routing::{get, MethodRouter};
use axum::Router;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use tower_http::services::ServeDir;

use lattice_core::worker::{WorkerSeamClient, WorkerSeamError};

use crate::ui_redirects::app_redirect;

use super::*;

/// The GPU half of the host probe, as the worker reports it.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct GpuReading {
    /// Unified memory held by the ML runtime, in GiB.
    pub gpu_mem_gb: f64,
    /// The same as a percentage of the machine's memory.
    pub gpu_mem_pct: f64,
}

/// A future returning what the machine's GPU is holding, or nothing.
pub type GpuFuture<'a> = Pin<Box<dyn std::future::Future<Output = Option<GpuReading>> + Send + 'a>>;

/// Where the GPU numbers come from.
///
/// The seam exists because MLX is Python-only: `api/static_routes.py` imports
/// `mlx.core` purely for these two numbers, which is why a static-files module
/// carried an ML dependency. Here the numbers are asked for
/// ([`WORKER_SYSINFO_PATH`], added by WP-I6) and their absence is the same
/// non-event it is in Python, where the import failure is swallowed and the
/// fields stay zero.
pub trait GpuSource: Send + Sync + 'static {
    /// Read the current GPU memory, or `None` if this machine cannot say.
    fn read(&self) -> GpuFuture<'_>;
}

/// A machine with no ML runtime to ask: the fields stay zero.
#[derive(Debug, Default, Clone, Copy)]
pub struct NoGpu;

impl GpuSource for NoGpu {
    fn read(&self) -> GpuFuture<'_> {
        Box::pin(async { None })
    }
}

/// The worker seam: `GET {origin}/worker/sysinfo`.
#[derive(Debug, Clone)]
pub struct WorkerGpuSource {
    client: WorkerSeamClient,
}

impl WorkerGpuSource {
    /// A source pointed at the worker this host supervises.
    ///
    /// Builds its own HTTP client; prefer [`Self::with_client`] from a host that
    /// already has a seam client, so the loopback connection pool is shared and
    /// any credential the seam needs is configured in exactly one place.
    pub fn new(origin: impl AsRef<str>) -> Result<Self, WorkerSeamError> {
        Ok(Self {
            client: WorkerSeamClient::new(origin)?.with_timeout(GPU_SEAM_TIMEOUT),
        })
    }

    /// A source over a seam client the caller already configured.
    pub fn with_client(client: WorkerSeamClient) -> Self {
        Self { client }
    }
}

impl GpuSource for WorkerGpuSource {
    fn read(&self) -> GpuFuture<'_> {
        Box::pin(async move {
            // A worker that is down, refusing, or answering something else is
            // "cannot say" — the same non-event as MLX failing to import in
            // Python, which `quiet()` swallows. This route reports host load and
            // must not become a second place the product looks unhealthy.
            let payload = self.client.get_json(WORKER_SYSINFO_PATH).await.ok()?;
            gpu_from_worker_payload(&payload)
        })
    }
}

/// Read the GPU numbers out of WP-I6's `GET /worker/sysinfo` body.
///
/// Shipped schema (`latticeai/api/worker_seams.py::probe_gpu_memory`):
///
/// ```json
/// {"mlx_available": bool, "gpu_mem_gb": num, "gpu_mem_pct": num,
///  "total_bytes": int, "detail": str | null}
/// ```
///
/// Only the two numbers this route reports are taken. `mlx_available`,
/// `total_bytes` and `detail` stay worker-side: `/local/sysinfo` has never
/// exposed them, and inventing keys here would be a client-visible change.
/// A payload missing either number is "cannot say", not zero, so a seam that
/// regresses shows up as an absent GPU rather than an idle one. `false` plus
/// zeros (no MLX on this machine) still parses — those zeros are the reading.
pub fn gpu_from_worker_payload(payload: &Value) -> Option<GpuReading> {
    Some(GpuReading {
        gpu_mem_gb: payload.get("gpu_mem_gb")?.as_f64()?,
        gpu_mem_pct: payload.get("gpu_mem_pct")?.as_f64()?,
    })
}

/// What `/local/sysinfo` needs: somewhere to ask about the GPU.
pub struct SysinfoState {
    /// The GPU seam.
    pub gpu: Arc<dyn GpuSource>,
}

impl std::fmt::Debug for SysinfoState {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("SysinfoState { gpu: <seam> }")
    }
}

/// `GET /local/sysinfo`, on its own so it can be mounted behind the user gate.
///
/// The Python route calls `require_user`; this router does not, because
/// authentication in this release is a layer (`lattice-auth`, WP-I2) rather than
/// a line at the top of every handler. Mounting it bare is a difference from
/// Python, and a host that does so is publishing its CPU and RAM load to
/// anything that can reach the port.
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

/// `_probe_host_capacity` — CPU and RAM from this machine, GPU from the seam.
///
/// The Python original wraps all three samples in one `try`, so a failure while
/// reading CPU means RAM and GPU are never read and the payload carries an
/// `error` string with zeros beside it. That sequencing is reproduced rather
/// than improved: a caller reading `cpu_pct: 0.0` next to `error` knows it was
/// not sampled, whereas a partially-filled payload would look like an idle box.
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

/// `top -l 1 -n 0`'s user+sys percentage, rounded as CPython rounds.
///
/// `None` when no line carries the pair — the Python original simply leaves the
/// field at its default there, which is not the same as reading zero load, but
/// is what the product has always reported.
pub fn parse_cpu_percent(top_output: &str) -> Option<f64> {
    let mut latest = None;
    for line in top_output.lines() {
        if !line.contains("CPU usage") {
            continue;
        }
        // The Python regex is `([\d.]+)% user.*?([\d.]+)% sys`: the first number
        // before "% user", then the first before "% sys" *after* it.
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

/// The `[\d.]+` run ending at `end`, parsed as a float.
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

/// `vm_stat`'s used-pages percentage, rounded as CPython rounds.
///
/// The five counters Python sums are the whole of its idea of "memory in use";
/// anything else `vm_stat` prints (page-ins, faults) is not memory and is
/// ignored. A duplicate line overwrites, exactly as the Python dict does.
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

/// `host_capacity_readiness` — one plain-language bucket for three numbers.
///
/// The heaviest of the three decides, so a machine that is fine on CPU and out
/// of memory is not described as roomy. The thresholds live here, and only here,
/// so basic-mode copy and advanced-mode numbers cannot disagree.
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
