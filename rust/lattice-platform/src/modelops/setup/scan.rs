//! What this machine looks like, and what it would take to make it work.
//!
//! The read-only half of the setup wizard: probe the host, decide what it can
//! run, and turn that into the environment block, the zero-config state, the
//! recommendations, the install plan and the verification checks that
//! `/setup/scan`, `/setup/auto` and the workspace onboarding surface all
//! render. Nothing here writes, installs, or opens anything — that is
//! `install.rs` and the handlers in [`super`].
//!
//! [`scan_environment`] is also called from outside this family
//! (`crate::workspaceos::workspace::handlers_core`), which is why the machine
//! description is derived here once rather than in each caller: two probes that
//! disagree about whether this host has a GPU produce two different pieces of
//! advice for the same machine.

use std::path::PathBuf;

use lattice_auth::OrderedMap;
use serde_json::{json, Map, Value};

use crate::adminops::admin::json_from_ordered;
use crate::modelops::models_catalog::{
    command_exists, recommend_from_catalog, HostProbe, WorkerCatalog,
};

pub(crate) fn scan_environment(probe: &HostProbe, catalog: &WorkerCatalog) -> OrderedMap {
    let mlx = catalog
        .sysinfo
        .get("mlx_available")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let gpu = gpu_label(probe, catalog);
    let mut env = OrderedMap::new();
    env.insert("os", json!(probe.os));
    env.insert("os_version", json!(probe.os_version));
    env.insert("chip", json!(probe.cpu_model));
    env.insert("cpu", json!(probe.cpu_model));
    env.insert("gpu", json!(gpu));
    env.insert("cuda", json!(false));
    env.insert("wsl", json!(is_wsl()));
    env.insert("ram_gb", json!(probe.ram_gb));
    env.insert("disk_free_gb", json!(probe.disk_free_gb));
    env.insert("tools", json!(host_tools()));
    env.insert("components", json!({}));
    env.insert("path", json!(std::env::var("PATH").unwrap_or_default()));
    env.insert("mlx", json!(mlx));
    env.insert("api_keys", json!({}));
    if probe.ram_bytes.is_none() {
        env.insert("ram_reason", json!("could not read installed memory"));
    }
    env
}

pub(super) fn auto_state(probe: &HostProbe, catalog: &WorkerCatalog) -> OrderedMap {
    let (recs, _) = recommend_from_catalog(probe, "local_mlx", catalog);
    let top = recs.get("top_pick").cloned().unwrap_or(Value::Null);
    let top_id = top
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let quantization = top
        .get("quantization")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let mlx = catalog
        .sysinfo
        .get("mlx_available")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let python_version = catalog
        .sysinfo
        .get("python_version")
        .and_then(Value::as_str)
        .map(str::to_string)
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "unknown".into());
    let runtime = if mlx {
        "local_mlx"
    } else if catalog.reachable {
        "worker"
    } else {
        "none"
    };

    let mut hardware_probe = OrderedMap::new();
    hardware_probe.insert("os", json!(probe.os));
    hardware_probe.insert("os_version", json!(probe.os_version));
    hardware_probe.insert("arch", json!(probe.arch));
    hardware_probe.insert("cpu_model", json!(probe.cpu_model));
    hardware_probe.insert("cpu_cores", json!(probe.cpu_cores));
    hardware_probe.insert("cpu_logical_cores", json!(probe.cpu_logical_cores));
    hardware_probe.insert("cpu_instructions", json!([]));
    hardware_probe.insert("ram_mb", json!(probe.ram_mb));
    hardware_probe.insert("disk_free_mb", json!(probe.disk_free_mb));
    hardware_probe.insert("gpu", json!(gpu_label(probe, catalog)));
    hardware_probe.insert("package_manager", json!(probe.package_manager));
    hardware_probe.insert("has_internet", json!(probe.has_internet));
    hardware_probe.insert("python_version", json!(python_version));
    hardware_probe.insert("is_wsl", json!(is_wsl()));
    hardware_probe.insert("wsl_version", Value::Null);
    hardware_probe.insert("cuda_available", json!(false));
    hardware_probe.insert("cuda_version", Value::Null);
    hardware_probe.insert("tools", json!(host_tools()));
    hardware_probe.insert("score", json!(setup_score(probe, catalog)));
    if let Some(reason) = &catalog.reason {
        hardware_probe.insert("worker_reason", json!(reason));
    }

    let mut rationale = Vec::new();
    if probe.apple_silicon {
        rationale.push(json!("Apple Silicon — local MLX is the preferred runtime"));
    }
    if !top_id.is_empty() {
        rationale.push(json!(format!(
            "top pick for {} GB RAM: {top_id}",
            probe.ram_gb
        )));
    } else if let Some(reason) = &catalog.reason {
        rationale.push(json!(reason));
    } else {
        rationale.push(json!("no model fits this machine from the live catalog"));
    }

    let mut recommend = OrderedMap::new();
    recommend.insert(
        "runtime",
        json!(if top_id.is_empty() { "none" } else { runtime }),
    );
    recommend.insert(
        "backend",
        json!(if mlx {
            "mlx"
        } else if catalog.reachable {
            "worker"
        } else {
            "none"
        }),
    );
    recommend.insert("model_id", json!(top_id));
    recommend.insert("quantization", json!(quantization));
    recommend.insert("rationale", json!(rationale));
    recommend.insert("estimated_tokens_per_sec", json!(0));
    recommend.insert("top_pick", top.clone());

    let steps = plan_steps(probe, catalog, &top_id);
    let mut plan = OrderedMap::new();
    plan.insert("package_manager", json!(probe.package_manager));
    plan.insert("steps", json!(steps));
    plan.insert("notes", json!(plan_notes(catalog)));
    plan.insert("command_plan", Value::Null);
    plan.insert("confirmation_token", Value::Null);

    let checks = verify_checks(probe, catalog);
    let all_pass = checks.iter().all(|check| check["pass"] == json!(true));
    let mut verify = OrderedMap::new();
    verify.insert("checks", json!(checks));
    verify.insert("all_pass", json!(all_pass));

    let mut model = OrderedMap::new();
    model.insert("id", json!(top.get("id").cloned().unwrap_or(json!(""))));
    model.insert(
        "runtime",
        json!(if top_id.is_empty() { "none" } else { runtime }),
    );
    let mut preset = OrderedMap::new();
    preset.insert("mode", json!("local"));
    preset.insert("model", json_from_ordered(&model));
    preset.insert("shortcuts", json!([]));
    preset.insert("mcp", json!([]));
    preset.insert("theme", json!("system"));
    preset.insert("language", json!("ko"));
    preset.insert("tips", json!([]));

    let mut out = OrderedMap::new();
    out.insert("probe", json_from_ordered(&hardware_probe));
    out.insert("recommend", json_from_ordered(&recommend));
    out.insert("plan", json_from_ordered(&plan));
    out.insert("verify", json_from_ordered(&verify));
    out.insert("preset", json_from_ordered(&preset));
    out
}

pub(super) fn recommendations_from_zero(zero: &OrderedMap, catalog: &WorkerCatalog) -> OrderedMap {
    let mut recs = OrderedMap::new();
    recs.insert("components", json!([]));
    recs.insert(
        "engines",
        catalog.models.get("engines").cloned().unwrap_or(json!([])),
    );
    recs.insert(
        "models",
        catalog
            .models
            .get("recommended")
            .cloned()
            .unwrap_or(json!([])),
    );
    recs.insert("mcps", json!([]));
    recs.insert("summary", json!({}));
    if let Some(recommend) = zero.get("recommend") {
        let mut summary = OrderedMap::new();
        summary.insert("zero_config", recommend.clone());
        recs.insert("summary", json_from_ordered(&summary));
    }
    recs.insert(
        "install_plan",
        zero.get("plan").cloned().unwrap_or(json!({})),
    );
    recs.insert("preset", zero.get("preset").cloned().unwrap_or(json!({})));
    recs
}

fn gpu_label(probe: &HostProbe, catalog: &WorkerCatalog) -> String {
    if catalog
        .sysinfo
        .get("mlx_available")
        .and_then(Value::as_bool)
        == Some(true)
    {
        return "Apple Silicon unified memory".into();
    }
    if probe.apple_silicon {
        return "Apple Silicon".into();
    }
    if catalog
        .sysinfo
        .get("detail")
        .and_then(Value::as_str)
        .is_some()
    {
        return "unknown".into();
    }
    "unknown".into()
}

fn host_tools() -> Map<String, Value> {
    let mut tools = Map::new();
    for bin in ["brew", "python3", "pip3", "git", "node"] {
        tools.insert(bin.into(), json!(command_exists(bin)));
    }
    tools
}

fn is_wsl() -> bool {
    std::fs::read_to_string("/proc/version")
        .map(|text| text.to_ascii_lowercase().contains("microsoft"))
        .unwrap_or(false)
}

fn setup_score(probe: &HostProbe, catalog: &WorkerCatalog) -> u64 {
    let mut score = 0;
    if probe.ram_gb >= 8 {
        score += 20;
    }
    if probe.ram_gb >= 16 {
        score += 20;
    }
    if probe.apple_silicon {
        score += 20;
    }
    if catalog.reachable {
        score += 20;
    }
    if catalog
        .sysinfo
        .get("mlx_available")
        .and_then(Value::as_bool)
        == Some(true)
    {
        score += 20;
    }
    score
}

fn plan_steps(probe: &HostProbe, catalog: &WorkerCatalog, top_id: &str) -> Vec<Value> {
    let mut steps = Vec::new();
    if !catalog.reachable {
        steps.push(json!({
            "id": "worker",
            "kind": "manual",
            "action": "start_worker",
            "detail": catalog.reason.clone().unwrap_or_else(|| "worker is not configured".into()),
        }));
        return steps;
    }
    if !top_id.is_empty() {
        let loaded = catalog
            .models
            .get("current")
            .and_then(Value::as_str)
            .map(|value| value == top_id)
            .unwrap_or(false);
        if loaded {
            steps.push(json!({
                "id": "model",
                "kind": "ready",
                "action": "",
                "model_id": top_id,
                "detail": "recommended model is already loaded",
            }));
        } else {
            steps.push(json!({
                "id": "model",
                "kind": "prepare_model",
                "action": "prepare_model",
                "model_id": top_id,
                "engine": "local_mlx",
                "detail": format!("download and load {top_id} via /engines/prepare-model"),
            }));
        }
    }
    if probe.package_manager == "none" {
        steps.push(json!({
            "id": "package_manager",
            "kind": "manual",
            "action": "install_package_manager",
            "command": "install Homebrew from https://brew.sh",
            "detail": "no host package manager was found",
        }));
    }
    steps
}

fn plan_notes(catalog: &WorkerCatalog) -> Vec<Value> {
    match &catalog.reason {
        Some(reason) => vec![json!(reason)],
        None => vec![],
    }
}

fn verify_checks(_probe: &HostProbe, catalog: &WorkerCatalog) -> Vec<Value> {
    let worker_ok = catalog.reachable;
    let current = catalog
        .models
        .get("current")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty());
    let recommended = catalog
        .models
        .get("recommended")
        .and_then(Value::as_array)
        .map(|rows| !rows.is_empty())
        .unwrap_or(false);
    let model_ok = current.is_some() || recommended;
    let assets_ok = static_assets_present();
    vec![
        json!({
            "id": "worker_healthy",
            "pass": worker_ok,
            "detail": if worker_ok {
                "worker answered /models or /worker/sysinfo".to_string()
            } else {
                catalog
                    .reason
                    .clone()
                    .unwrap_or_else(|| "worker is not configured".into())
            },
        }),
        json!({
            "id": "model_present_or_downloadable",
            "pass": model_ok,
            "detail": if let Some(id) = current {
                format!("loaded {id}")
            } else if recommended {
                "catalog lists at least one downloadable model".to_string()
            } else {
                "no model is loaded and the catalog is empty".to_string()
            },
        }),
        json!({
            "id": "static_assets_present",
            "pass": assets_ok,
            "detail": if assets_ok {
                "static UI assets are on disk"
            } else {
                "no static/ or frontend/dist tree was found"
            },
        }),
    ]
}

fn static_assets_present() -> bool {
    if let Ok(dir) = std::env::var("LATTICEAI_STATIC_DIR") {
        let path = PathBuf::from(dir);
        if path.join("index.html").is_file() || path.is_dir() {
            return true;
        }
    }
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    for rel in ["../../static/index.html", "../../frontend/dist/index.html"] {
        if manifest.join(rel).is_file() {
            return true;
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::modelops::models_catalog::probe_host;

    #[test]
    fn worker_down_verify_does_not_vacuously_pass() {
        let probe = probe_host(None);
        let catalog = WorkerCatalog {
            reachable: false,
            reason: Some("worker is not configured".into()),
            models: json!({}),
            sysinfo: json!({}),
        };
        let checks = verify_checks(&probe, &catalog);
        assert!(checks
            .iter()
            .any(|c| c["id"] == "worker_healthy" && c["pass"] == false));
        assert!(!checks.iter().all(|c| c["pass"] == true));
    }
}
