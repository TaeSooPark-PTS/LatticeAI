//! Host probe + worker catalog merge used by recommendations and setup.

use std::collections::BTreeMap;
use std::path::Path;
use std::time::Duration;

use lattice_auth::pyjson::OrderedMap;
use lattice_core::worker::WorkerSeamClient;
use serde_json::{json, Map, Value};

/// What a native host probe can say without asking the worker.
#[derive(Debug, Clone)]
pub struct HostProbe {
    pub os: String,
    pub os_version: String,
    pub arch: String,
    pub apple_silicon: bool,
    pub ram_bytes: Option<u64>,
    pub ram_gb: u64,
    pub ram_mb: u64,
    pub cpu_model: String,
    pub cpu_cores: u64,
    pub cpu_logical_cores: u64,
    pub disk_free_bytes: Option<u64>,
    pub disk_free_gb: u64,
    pub disk_free_mb: u64,
    pub package_manager: String,
    pub has_internet: bool,
}

impl HostProbe {
    pub(crate) fn profile_map(&self) -> OrderedMap {
        let mut profile = OrderedMap::new();
        profile.insert("os", json!(self.os));
        profile.insert("os_version", json!(self.os_version));
        profile.insert("arch", json!(self.arch));
        profile.insert("apple_silicon", json!(self.apple_silicon));
        profile.insert("ram_gb", json!(self.ram_gb));
        profile.insert("ram_mb", json!(self.ram_mb));
        if self.ram_bytes.is_none() {
            profile.insert("ram_reason", json!("could not read installed memory"));
        }
        profile
    }
}

/// Worker answers used by recommendations and the setup probes.
#[derive(Debug, Clone)]
pub struct WorkerCatalog {
    pub reachable: bool,
    pub reason: Option<String>,
    pub models: Value,
    pub sysinfo: Value,
}

/// RAM / chip / disk from this process. `data_dir` is the volume whose free
/// space we report; `None` uses the current directory.
pub fn probe_host(data_dir: Option<&Path>) -> HostProbe {
    let ram_bytes = read_ram_bytes();
    let ram_gb = ram_bytes.map(|b| b / (1024 * 1024 * 1024)).unwrap_or(0);
    let ram_mb = ram_bytes.map(|b| b / (1024 * 1024)).unwrap_or(0);
    let disk = disk_free_bytes(data_dir.unwrap_or_else(|| Path::new(".")));
    let logical = std::thread::available_parallelism()
        .map(|n| n.get() as u64)
        .unwrap_or(1);
    HostProbe {
        os: std::env::consts::OS.to_string(),
        os_version: os_version(),
        arch: std::env::consts::ARCH.to_string(),
        apple_silicon: detect_apple_silicon(),
        ram_bytes,
        ram_gb,
        ram_mb,
        cpu_model: cpu_model(),
        cpu_cores: logical,
        cpu_logical_cores: logical,
        disk_free_bytes: disk,
        disk_free_gb: disk.map(|b| b / (1024 * 1024 * 1024)).unwrap_or(0),
        disk_free_mb: disk.map(|b| b / (1024 * 1024)).unwrap_or(0),
        package_manager: detect_package_manager(),
        has_internet: detect_internet(),
    }
}

/// `GET /models` + `GET /worker/sysinfo`. Either miss is named, never faked.
pub async fn fetch_worker_catalog(worker: Option<&WorkerSeamClient>) -> WorkerCatalog {
    let Some(worker) = worker else {
        return WorkerCatalog {
            reachable: false,
            reason: Some("worker is not configured".into()),
            models: json!({}),
            sysinfo: json!({}),
        };
    };
    let client = worker.clone().with_timeout(Duration::from_secs(5));
    let (models, sysinfo) = tokio::join!(
        client.get_json("/models"),
        client.get_json("/worker/sysinfo"),
    );
    match (models, sysinfo) {
        (Ok(models), Ok(sysinfo)) => WorkerCatalog {
            reachable: true,
            reason: None,
            models,
            sysinfo,
        },
        (Ok(models), Err(err)) => WorkerCatalog {
            reachable: true,
            reason: Some(format!("worker sysinfo unreachable: {err}")),
            models,
            sysinfo: json!({}),
        },
        (Err(err), Ok(sysinfo)) => WorkerCatalog {
            reachable: false,
            reason: Some(format!("worker catalog unreachable: {err}")),
            models: json!({}),
            sysinfo,
        },
        (Err(err), Err(_)) => WorkerCatalog {
            reachable: false,
            reason: Some(format!("worker catalog unreachable: {err}")),
            models: json!({}),
            sysinfo: json!({}),
        },
    }
}

/// Build the `/models/recommendations` body from a probe + worker payload.
pub fn recommend_from_catalog(
    probe: &HostProbe,
    engine: &str,
    catalog: &WorkerCatalog,
) -> (OrderedMap, OrderedMap) {
    let rows = merge_catalog_rows(&catalog.models);
    let engine_available = engine_is_available(&catalog.models, engine);
    let mut models = Vec::new();
    let mut families: BTreeMap<String, Vec<Value>> = BTreeMap::new();
    let mut recommended = 0u64;
    let mut compatible = 0u64;
    let mut not_recommended = 0u64;
    let mut scored: Vec<(i64, i64, i64, Value)> = Vec::new();

    for row in rows {
        let id = row_id(&row);
        if id.is_empty() {
            continue;
        }
        let hints = catalog_hints(&id);
        let family = row_str(&row, "family")
            .or_else(|| hints.map(|h| h.family.to_string()))
            .unwrap_or_else(|| "unknown".into());
        let rec_ram = row_ram(&row, "recommended_ram_gb")
            .or_else(|| hints.map(|h| h.recommended_ram_gb))
            .unwrap_or(0.0);
        let min_ram = row_ram(&row, "min_ram_gb")
            .or_else(|| hints.map(|h| h.min_ram_gb))
            .unwrap_or(rec_ram);
        let display_priority = row
            .get("display_priority")
            .and_then(Value::as_i64)
            .unwrap_or_else(|| hints.map(|h| h.display_priority).unwrap_or(100));
        let recommended_default = row
            .get("recommended_default")
            .and_then(Value::as_bool)
            .unwrap_or_else(|| hints.is_some_and(|h| h.recommended_default));
        let load_status = row_str(&row, "load_status").unwrap_or_default();
        let runtime_supported = row
            .pointer("/runtime_compatibility/supported")
            .and_then(Value::as_bool)
            != Some(false);
        let ram_gb = probe.ram_gb as f64;
        let status = if !runtime_supported
            || load_status == "not_recommended"
            || (min_ram > 0.0 && ram_gb + 0.01 < min_ram)
        {
            "not_recommended"
        } else if rec_ram > 0.0 && ram_gb + 0.01 >= rec_ram {
            "recommended"
        } else if probe.ram_bytes.is_none() || (min_ram > 0.0 && ram_gb + 0.01 >= min_ram) {
            "compatible"
        } else {
            "not_recommended"
        };
        match status {
            "recommended" => recommended += 1,
            "compatible" => compatible += 1,
            _ => not_recommended += 1,
        }
        let mut enriched = match row {
            Value::Object(map) => map,
            _ => Map::new(),
        };
        enriched.entry("id").or_insert_with(|| json!(id.clone()));
        enriched
            .entry("family")
            .or_insert_with(|| json!(family.clone()));
        enriched.insert("status".into(), json!(status));
        enriched
            .entry("recommended_default")
            .or_insert_with(|| json!(recommended_default));
        enriched
            .entry("display_priority")
            .or_insert_with(|| json!(display_priority));
        if !enriched.contains_key("hardware") {
            if let Some(hints) = hints {
                enriched.insert(
                    "hardware".into(),
                    json!({
                        "min_ram_gb": hints.min_ram_gb,
                        "recommended_ram_gb": hints.recommended_ram_gb,
                    }),
                );
            }
        }
        let value = Value::Object(enriched);
        let default_rank = if recommended_default { 0 } else { 1 };
        let ram_rank = if status == "not_recommended" { 1 } else { 0 };
        scored.push((ram_rank, default_rank, display_priority, value.clone()));
        families.entry(family).or_default().push(value.clone());
        models.push(value);
    }

    scored.sort_by(|a, b| a.0.cmp(&b.0).then(a.1.cmp(&b.1)).then(a.2.cmp(&b.2)));
    let top_pick = scored
        .into_iter()
        .find(|(ram_rank, _, _, _)| *ram_rank == 0)
        .or_else(|| models.first().cloned().map(|value| (1, 1, 100, value)))
        .map(|(_, _, _, value)| value);

    let family_rows: Vec<Value> = families
        .into_iter()
        .map(|(family, members)| {
            json!({
                "family": family,
                "count": members.len(),
                "models": members,
            })
        })
        .collect();

    let mut counts = OrderedMap::new();
    counts.insert("recommended", json!(recommended));
    counts.insert("compatible", json!(compatible));
    counts.insert("not_recommended", json!(not_recommended));

    let mut recs = OrderedMap::new();
    recs.insert("engine", json!(engine));
    recs.insert(
        "engine_available",
        json!(engine_available && catalog.reachable),
    );
    recs.insert("apple_silicon", json!(probe.apple_silicon));
    recs.insert("ram_gb", json!(probe.ram_gb));
    recs.insert("counts", serde_json::to_value(&counts).unwrap_or(json!({})));
    recs.insert("top_pick", top_pick.unwrap_or(Value::Null));
    recs.insert("families", json!(family_rows));
    recs.insert("models", json!(models));
    if let Some(reason) = &catalog.reason {
        recs.insert("reason", json!(reason));
    }
    if !catalog.reachable {
        recs.insert("top_pick", Value::Null);
        recs.insert("families", json!([]));
        recs.insert("models", json!([]));
        recs.insert(
            "counts",
            json!({
                "recommended": 0,
                "compatible": 0,
                "not_recommended": 0
            }),
        );
        recs.insert("engine_available", json!(false));
    }

    let verified = catalog
        .models
        .pointer("/registry/verified")
        .and_then(Value::as_array)
        .map(Vec::len)
        .unwrap_or(0);
    let verified_count = catalog
        .models
        .pointer("/registry/verified_count")
        .and_then(Value::as_u64)
        .unwrap_or(verified as u64);
    let version = catalog
        .models
        .pointer("/registry/version")
        .and_then(Value::as_str)
        .unwrap_or("5.2.0");
    let mut registry = OrderedMap::new();
    registry.insert("version", json!(version));
    registry.insert("verified_total", json!(verified_count));
    (recs, registry)
}

#[derive(Clone, Copy)]
struct CatalogHint {
    family: &'static str,
    min_ram_gb: f64,
    recommended_ram_gb: f64,
    recommended_default: bool,
    display_priority: i64,
}

/// Display priority / RAM floors the worker `to_legacy_dict` omits.
const CATALOG_HINTS: &[(&str, CatalogHint)] = &[
    (
        "mlx-community/LFM2.5-2.6B-4bit",
        CatalogHint {
            family: "LFM2.5",
            min_ram_gb: 6.0,
            recommended_ram_gb: 8.0,
            recommended_default: true,
            display_priority: 5,
        },
    ),
    (
        "mlx-community/gemma-4-e2b-it-4bit",
        CatalogHint {
            family: "Gemma 4",
            min_ram_gb: 8.0,
            recommended_ram_gb: 8.0,
            recommended_default: true,
            display_priority: 10,
        },
    ),
    (
        "mlx-community/gemma-4-e4b-it-4bit",
        CatalogHint {
            family: "Gemma 4",
            min_ram_gb: 10.0,
            recommended_ram_gb: 16.0,
            recommended_default: false,
            display_priority: 15,
        },
    ),
    (
        "mlx-community/Qwen3.5-9B-MLX-4bit",
        CatalogHint {
            family: "Qwen3.5",
            min_ram_gb: 12.0,
            recommended_ram_gb: 16.0,
            recommended_default: true,
            display_priority: 20,
        },
    ),
    (
        "mlx-community/gemma-4-12B-it-4bit",
        CatalogHint {
            family: "Gemma 4",
            min_ram_gb: 12.0,
            recommended_ram_gb: 16.0,
            recommended_default: true,
            display_priority: 25,
        },
    ),
    (
        "mlx-community/gpt-oss-20b-MXFP4-Q8",
        CatalogHint {
            family: "GPT-OSS",
            min_ram_gb: 18.0,
            recommended_ram_gb: 24.0,
            recommended_default: false,
            display_priority: 30,
        },
    ),
    (
        "mlx-community/gemma-4-26b-a4b-it-4bit",
        CatalogHint {
            family: "Gemma 4",
            min_ram_gb: 22.0,
            recommended_ram_gb: 32.0,
            recommended_default: false,
            display_priority: 50,
        },
    ),
    (
        "mlx-community/Qwen3.6-27B-4bit",
        CatalogHint {
            family: "Qwen3.6",
            min_ram_gb: 24.0,
            recommended_ram_gb: 48.0,
            recommended_default: false,
            display_priority: 55,
        },
    ),
    (
        "mlx-community/gemma-4-31b-it-4bit",
        CatalogHint {
            family: "Gemma 4",
            min_ram_gb: 26.0,
            recommended_ram_gb: 48.0,
            recommended_default: false,
            display_priority: 60,
        },
    ),
    (
        "mlx-community/Qwen3.6-35B-A3B-4bit",
        CatalogHint {
            family: "Qwen3.6",
            min_ram_gb: 28.0,
            recommended_ram_gb: 48.0,
            recommended_default: false,
            display_priority: 65,
        },
    ),
];

fn catalog_hints(id: &str) -> Option<&'static CatalogHint> {
    CATALOG_HINTS
        .iter()
        .find(|(known, _)| *known == id)
        .map(|(_, hint)| hint)
}

fn merge_catalog_rows(payload: &Value) -> Vec<Value> {
    let mut by_id: BTreeMap<String, Map<String, Value>> = BTreeMap::new();
    for key in ["recommended", "catalog"] {
        if let Some(rows) = payload.get(key).and_then(Value::as_array) {
            for row in rows {
                let id = row_id(row);
                if id.is_empty() {
                    continue;
                }
                let mut map = match row {
                    Value::Object(obj) => obj.clone(),
                    _ => continue,
                };
                map.entry("id").or_insert_with(|| json!(id.clone()));
                by_id.entry(id).or_insert(map);
            }
        }
    }
    if let Some(verified) = payload
        .pointer("/registry/verified")
        .and_then(Value::as_array)
    {
        for row in verified {
            let id = row_id(row);
            if id.is_empty() {
                continue;
            }
            let src = match row {
                Value::Object(obj) => obj,
                _ => continue,
            };
            let entry = by_id.entry(id).or_default();
            for (key, value) in src {
                entry.entry(key.clone()).or_insert(value.clone());
            }
        }
    }
    by_id.into_values().map(Value::Object).collect()
}

fn row_id(row: &Value) -> String {
    row.get("id")
        .or_else(|| row.get("model_id"))
        .or_else(|| row.get("recommended_load_id"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string()
}

fn row_str(row: &Value, key: &str) -> Option<String> {
    row.get(key)
        .and_then(Value::as_str)
        .map(str::to_string)
        .filter(|value| !value.is_empty())
}

fn row_ram(row: &Value, key: &str) -> Option<f64> {
    row.pointer(&format!("/hardware/{key}"))
        .and_then(Value::as_f64)
        .or_else(|| row.get(key).and_then(Value::as_f64))
        .filter(|value| *value > 0.0)
}

fn engine_is_available(payload: &Value, engine: &str) -> bool {
    payload
        .get("engines")
        .and_then(Value::as_array)
        .map(|engines| {
            engines.iter().any(|item| {
                let id = item.get("id").and_then(Value::as_str).unwrap_or("");
                let installed = item
                    .get("installed")
                    .and_then(Value::as_bool)
                    .unwrap_or(false);
                installed
                    && (id == engine
                        || (engine == "local_mlx" && matches!(id, "local_mlx" | "mlx")))
            })
        })
        .unwrap_or(false)
}

pub fn read_ram_bytes() -> Option<u64> {
    #[cfg(target_os = "macos")]
    {
        parse_u64_stdout(sysctl_n(&["hw.memsize"])?)
    }
    #[cfg(target_os = "linux")]
    {
        parse_meminfo_total(&std::fs::read_to_string("/proc/meminfo").ok()?)
    }
    #[cfg(not(any(target_os = "macos", target_os = "linux")))]
    {
        None
    }
}

pub fn parse_meminfo_total(text: &str) -> Option<u64> {
    for line in text.lines() {
        let Some(rest) = line.strip_prefix("MemTotal:") else {
            continue;
        };
        let kb: u64 = rest.split_whitespace().next()?.parse().ok()?;
        return Some(kb.saturating_mul(1024));
    }
    None
}

fn detect_apple_silicon() -> bool {
    #[cfg(target_os = "macos")]
    {
        if let Some(value) = sysctl_n(&["hw.optional.arm64"]) {
            if value.trim() == "1" {
                return true;
            }
        }
        cfg!(target_arch = "aarch64")
    }
    #[cfg(not(target_os = "macos"))]
    {
        false
    }
}

fn os_version() -> String {
    #[cfg(target_os = "macos")]
    {
        sysctl_n(&["kern.osproductversion"]).unwrap_or_else(|| std::env::consts::FAMILY.to_string())
    }
    #[cfg(target_os = "linux")]
    {
        std::fs::read_to_string("/etc/os-release")
            .ok()
            .and_then(|text| {
                text.lines().find_map(|line| {
                    line.strip_prefix("PRETTY_NAME=")
                        .map(|value| value.trim_matches('"').to_string())
                })
            })
            .unwrap_or_else(|| std::env::consts::FAMILY.to_string())
    }
    #[cfg(not(any(target_os = "macos", target_os = "linux")))]
    {
        std::env::consts::FAMILY.to_string()
    }
}

fn cpu_model() -> String {
    #[cfg(target_os = "macos")]
    {
        sysctl_n(&["machdep.cpu.brand_string"])
            .unwrap_or_else(|| std::env::consts::ARCH.to_string())
    }
    #[cfg(target_os = "linux")]
    {
        std::fs::read_to_string("/proc/cpuinfo")
            .ok()
            .and_then(|text| {
                text.lines().find_map(|line| {
                    line.strip_prefix("model name")
                        .and_then(|rest| rest.split(':').nth(1))
                        .map(|value| value.trim().to_string())
                })
            })
            .unwrap_or_else(|| std::env::consts::ARCH.to_string())
    }
    #[cfg(not(any(target_os = "macos", target_os = "linux")))]
    {
        std::env::consts::ARCH.to_string()
    }
}

fn disk_free_bytes(path: &Path) -> Option<u64> {
    #[cfg(unix)]
    {
        let cstr = std::ffi::CString::new(path.to_string_lossy().as_bytes()).ok()?;
        let mut buf = std::mem::MaybeUninit::<libc::statvfs>::uninit();
        let rc = unsafe { libc::statvfs(cstr.as_ptr(), buf.as_mut_ptr()) };
        if rc != 0 {
            return None;
        }
        let buf = unsafe { buf.assume_init() };
        Some((buf.f_bavail as u64).saturating_mul(buf.f_frsize))
    }
    #[cfg(not(unix))]
    {
        let _ = path;
        None
    }
}

fn detect_package_manager() -> String {
    for (bin, name) in [
        ("brew", "brew"),
        ("apt-get", "apt"),
        ("dnf", "dnf"),
        ("pacman", "pacman"),
    ] {
        if command_exists(bin) {
            return name.to_string();
        }
    }
    "none".into()
}

pub fn command_exists(bin: &str) -> bool {
    std::process::Command::new(bin)
        .arg("--version")
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|status| status.success())
        .unwrap_or(false)
}

fn detect_internet() -> bool {
    std::net::TcpStream::connect_timeout(
        &std::net::SocketAddr::from(([1, 1, 1, 1], 443)),
        Duration::from_millis(400),
    )
    .is_ok()
}

#[cfg(target_os = "macos")]
fn sysctl_n(args: &[&str]) -> Option<String> {
    let output = std::process::Command::new("sysctl")
        .arg("-n")
        .args(args)
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if text.is_empty() {
        None
    } else {
        Some(text)
    }
}

#[cfg(target_os = "macos")]
fn parse_u64_stdout(text: String) -> Option<u64> {
    text.trim().parse().ok()
}

// ── POST /setup/set-api-key ──────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn meminfo_total_is_kilobytes_times_1024() {
        let sample = "MemTotal:       16384000 kB\nMemFree:         1000 kB\n";
        assert_eq!(parse_meminfo_total(sample), Some(16_384_000 * 1024));
        assert_eq!(parse_meminfo_total("nope"), None);
    }

    #[test]
    fn worker_down_recommendations_are_empty_with_a_reason() {
        let probe = HostProbe {
            os: "macos".into(),
            os_version: "15.0".into(),
            arch: "aarch64".into(),
            apple_silicon: true,
            ram_bytes: Some(16 * 1024 * 1024 * 1024),
            ram_gb: 16,
            ram_mb: 16 * 1024,
            cpu_model: "Apple M-series".into(),
            cpu_cores: 8,
            cpu_logical_cores: 8,
            disk_free_bytes: Some(64 * 1024 * 1024 * 1024),
            disk_free_gb: 64,
            disk_free_mb: 64 * 1024,
            package_manager: "brew".into(),
            has_internet: false,
        };
        let catalog = WorkerCatalog {
            reachable: false,
            reason: Some("worker is not configured".into()),
            models: json!({}),
            sysinfo: json!({}),
        };
        let (recs, registry) = recommend_from_catalog(&probe, "local_mlx", &catalog);
        assert_eq!(recs.get("top_pick"), Some(&Value::Null));
        assert_eq!(recs.get("families"), Some(&json!([])));
        assert_eq!(recs.get("models"), Some(&json!([])));
        assert_eq!(recs.get("engine_available"), Some(&json!(false)));
        assert_eq!(recs.get("reason"), Some(&json!("worker is not configured")));
        assert_eq!(recs.get("ram_gb"), Some(&json!(16)));
        assert_eq!(recs.get("apple_silicon"), Some(&json!(true)));
        assert_eq!(registry.get("verified_total"), Some(&json!(0)));
    }

    #[test]
    fn top_pick_honors_ram_tier_then_recommended_default_then_priority() {
        let probe = HostProbe {
            os: "macos".into(),
            os_version: "15.0".into(),
            arch: "aarch64".into(),
            apple_silicon: true,
            ram_bytes: Some(16 * 1024 * 1024 * 1024),
            ram_gb: 16,
            ram_mb: 16 * 1024,
            cpu_model: "Apple M-series".into(),
            cpu_cores: 8,
            cpu_logical_cores: 8,
            disk_free_bytes: None,
            disk_free_gb: 0,
            disk_free_mb: 0,
            package_manager: "none".into(),
            has_internet: false,
        };
        let catalog = WorkerCatalog {
            reachable: true,
            reason: None,
            models: json!({
                "engines": [{"id": "local_mlx", "installed": true}],
                "recommended": [
                    {
                        "id": "mlx-community/gemma-4-12B-it-4bit",
                        "name": "Gemma 4 12B",
                        "family": "Gemma 4",
                        "recommended_default": true,
                        "display_priority": 25,
                        "hardware": {"min_ram_gb": 12.0, "recommended_ram_gb": 16.0}
                    },
                    {
                        "id": "mlx-community/Qwen3.5-9B-MLX-4bit",
                        "name": "Qwen3.5 9B",
                        "family": "Qwen3.5",
                        "recommended_default": true,
                        "display_priority": 20,
                        "hardware": {"min_ram_gb": 12.0, "recommended_ram_gb": 16.0}
                    },
                    {
                        "id": "mlx-community/gemma-4-31b-it-4bit",
                        "name": "Gemma 4 31B",
                        "family": "Gemma 4",
                        "recommended_default": false,
                        "display_priority": 60,
                        "hardware": {"min_ram_gb": 26.0, "recommended_ram_gb": 48.0}
                    }
                ],
                "registry": {"version": "5.2.0", "verified_count": 3, "verified": []}
            }),
            sysinfo: json!({}),
        };
        let (recs, registry) = recommend_from_catalog(&probe, "local_mlx", &catalog);
        assert_eq!(recs.get("engine_available"), Some(&json!(true)));
        let top = recs
            .get("top_pick")
            .and_then(Value::as_object)
            .expect("top");
        assert_eq!(
            top.get("id").and_then(Value::as_str),
            Some("mlx-community/Qwen3.5-9B-MLX-4bit")
        );
        assert_eq!(recs.get("counts").unwrap()["recommended"], json!(2));
        assert_eq!(recs.get("counts").unwrap()["not_recommended"], json!(1));
        assert_eq!(registry.get("verified_total"), Some(&json!(3)));
        let families = recs.get("families").and_then(Value::as_array).unwrap();
        assert!(families.iter().any(|row| row["family"] == "Gemma 4"));
    }
}
