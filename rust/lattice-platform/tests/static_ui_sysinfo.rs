//! `/local/sysinfo` — the half this machine can answer, and the half it asks for.
//!
//! The Python route reads CPU and RAM out of `top` and `vm_stat` and the GPU out
//! of MLX. Only the last of those is Python-shaped, so the port keeps the
//! parsing (proven here against recorded `top` / `vm_stat` output, so it runs on
//! a Linux CI box that has neither) and delegates the GPU to the worker seam
//! (`GET /worker/sysinfo`, WP-I6), proven here against a fake worker.
//!
//! The readiness bucket is the reason any of this is a contract rather than a
//! number: basic-mode System copy says "roomy" / "tight" / "low" and must not
//! re-derive that from percentages on the client.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
mod static_ui_harness;

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

use lattice_platform::static_ui::{
    host_capacity_readiness, parse_cpu_percent, parse_ram_percent, probe_host_capacity,
    sysinfo_router, GpuFuture, GpuReading, GpuSource, NoGpu, SysinfoState, WorkerGpuSource,
    SYSINFO_READINESS_ROOMY_MAX, SYSINFO_READINESS_TIGHT_MAX, WORKER_SYSINFO_PATH,
};
use serde_json::Value;
use static_ui_harness::{fixture, Install};

/// A GPU that answers whatever the test wants, without a worker in the way.
struct StubGpu(Option<GpuReading>);

impl GpuSource for StubGpu {
    fn read(&self) -> GpuFuture<'_> {
        let reading = self.0;
        Box::pin(async move { reading })
    }
}

#[test]
fn the_recorded_probes_parse_to_the_recorded_numbers() {
    let probes = fixture()["sysinfo"]["probes"].as_array().expect("probes");
    assert!(
        probes.len() >= 4,
        "the recording covers the parser's branches"
    );
    for probe in probes {
        let name = probe["name"].as_str().expect("name");
        let expected = &probe["expect"];
        let cpu = parse_cpu_percent(probe["top"].as_str().expect("top")).unwrap_or(0.0);
        let ram = parse_ram_percent(probe["vm_stat"].as_str().expect("vm_stat"));
        assert_eq!(
            cpu,
            expected["cpu_pct"].as_f64().expect("cpu"),
            "{name}: cpu"
        );
        assert_eq!(
            ram,
            expected["ram_pct"].as_f64().expect("ram"),
            "{name}: ram"
        );
        // The recording was made with MLX hidden, so its GPU is zero — which is
        // exactly the state this port is in before the seam answers.
        assert_eq!(
            host_capacity_readiness(cpu, ram, 0.0),
            expected["readiness"].as_str().expect("readiness"),
            "{name}: readiness"
        );
    }
}

#[test]
fn the_readiness_grid_is_pythons() {
    let grid = fixture()["sysinfo"]["readiness_grid"]
        .as_array()
        .expect("grid");
    assert_eq!(grid.len(), 54, "the recorded grid");
    for row in grid {
        let cpu = row["cpu_pct"].as_f64().expect("cpu");
        let ram = row["ram_pct"].as_f64().expect("ram");
        let gpu = row["gpu_mem_pct"].as_f64().expect("gpu");
        assert_eq!(
            host_capacity_readiness(cpu, ram, gpu),
            row["readiness"].as_str().expect("readiness"),
            "cpu={cpu} ram={ram} gpu={gpu}"
        );
    }
}

#[test]
fn the_thresholds_are_the_recorded_ones() {
    let thresholds = &fixture()["sysinfo"]["thresholds"];
    assert_eq!(
        SYSINFO_READINESS_ROOMY_MAX,
        thresholds["roomy_max"].as_f64().expect("roomy")
    );
    assert_eq!(
        SYSINFO_READINESS_TIGHT_MAX,
        thresholds["tight_max"].as_f64().expect("tight")
    );
    assert_eq!(
        WORKER_SYSINFO_PATH,
        fixture()["sysinfo"]["worker_gpu_path"]
            .as_str()
            .expect("path")
    );
    let schema = fixture()["sysinfo"]["worker_gpu_schema"]
        .as_object()
        .expect("WP-I6 schema recorded in the fixture");
    for key in [
        "mlx_available",
        "gpu_mem_gb",
        "gpu_mem_pct",
        "total_bytes",
        "detail",
    ] {
        assert!(schema.contains_key(key), "schema missing {key}");
    }
}

/// A CPU line the parser must not half-read: Python's regex takes the number
/// before `% user` and then the *next* one before `% sys`.
#[test]
fn the_cpu_parser_reads_the_pair_or_nothing() {
    assert_eq!(
        parse_cpu_percent("CPU usage: 4.34% user, 10.86% sys, 84.80% idle"),
        Some(15.2)
    );
    assert_eq!(parse_cpu_percent("CPU usage: nothing numeric here"), None);
    assert_eq!(parse_cpu_percent("no such line"), None);
    // Two samples in one capture: the last one wins, as the Python loop's
    // assignment does.
    assert_eq!(
        parse_cpu_percent("CPU usage: 1.00% user, 1.00% sys\nCPU usage: 2.00% user, 3.00% sys"),
        Some(5.0)
    );
}

#[test]
fn the_ram_parser_sums_only_the_five_counters() {
    let sample = "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n\
                  Pages free:                         100.\n\
                  Pages active:                       300.\n\
                  Pages inactive:                     200.\n\
                  Pages wired down:                   400.\n\
                  Pages occupied by compressor:       0.\n\
                  Pageins:                            999999.\n";
    assert_eq!(parse_ram_percent(sample), 90.0);
    assert_eq!(
        parse_ram_percent("Pageins: 12345.\n"),
        0.0,
        "no counters, no claim"
    );
    assert_eq!(parse_ram_percent(""), 0.0);
}

#[tokio::test]
async fn the_route_answers_the_recorded_shape() {
    let state = Arc::new(SysinfoState {
        gpu: Arc::new(StubGpu(Some(GpuReading {
            gpu_mem_gb: 2.5,
            gpu_mem_pct: 15.6,
        }))),
    });
    let install = Install::serve(sysinfo_router(state), std::path::PathBuf::from(".")).await;
    let response = install
        .client
        .get(format!("{}/local/sysinfo", install.origin))
        .send()
        .await
        .expect("request");
    assert_eq!(response.status().as_u16(), 200);
    assert_eq!(
        response.headers()["content-type"].to_str().expect("ascii"),
        "application/json"
    );
    let payload: Value =
        serde_json::from_slice(&response.bytes().await.expect("body")).expect("json");
    let object = payload.as_object().expect("object");

    for key in fixture()["sysinfo"]["keys"].as_array().expect("keys") {
        let key = key.as_str().expect("key");
        assert!(object.contains_key(key), "missing {key}");
    }
    for key in object.keys() {
        assert!(
            fixture()["sysinfo"]["keys"]
                .as_array()
                .expect("keys")
                .iter()
                .any(|known| known == key)
                || key == "error",
            "unexpected key {key}"
        );
    }

    let cpu = object["cpu_pct"].as_f64().expect("cpu");
    let ram = object["ram_pct"].as_f64().expect("ram");
    let gpu = object["gpu_mem_pct"].as_f64().expect("gpu");
    if object.contains_key("error") {
        // A box without `top` / `vm_stat` — a Linux CI runner. Python reports
        // zeros beside the message and never reaches the GPU, and so does this.
        assert_eq!((cpu, ram, gpu), (0.0, 0.0, 0.0));
        assert_eq!(object["gpu_mem_gb"].as_f64(), Some(0.0));
        assert_eq!(object["readiness"], "roomy");
    } else {
        assert_eq!(gpu, 15.6, "the seam's number, not a locally invented one");
        assert_eq!(object["gpu_mem_gb"].as_f64(), Some(2.5));
        assert_eq!(
            object["readiness"].as_str().expect("readiness"),
            host_capacity_readiness(cpu, ram, gpu)
        );
        assert!((0.0..=100.0).contains(&cpu) && (0.0..=100.0).contains(&ram));
    }
}

#[tokio::test]
async fn a_machine_with_no_gpu_to_ask_reports_zero_rather_than_failing() {
    let state = Arc::new(SysinfoState {
        gpu: Arc::new(NoGpu),
    });
    let payload = probe_host_capacity(state.gpu.as_ref()).await;
    assert_eq!(payload["gpu_mem_gb"].as_f64(), Some(0.0));
    assert_eq!(payload["gpu_mem_pct"].as_f64(), Some(0.0));
}

/// The seam itself: one GET to `/worker/sysinfo`, and a refusal to invent
/// numbers when the worker cannot supply them.
#[tokio::test]
async fn the_gpu_comes_from_the_worker_seam() {
    let calls = Arc::new(AtomicUsize::new(0));
    let counter = Arc::clone(&calls);
    let worker = axum::Router::new().route(
        WORKER_SYSINFO_PATH,
        axum::routing::get(move || {
            let counter = Arc::clone(&counter);
            async move {
                counter.fetch_add(1, Ordering::SeqCst);
                axum::Json(serde_json::json!({
                    "mlx_available": true,
                    "gpu_mem_gb": 3.25,
                    "gpu_mem_pct": 20.3,
                    "total_bytes": 17_179_869_184u64,
                    "detail": null,
                }))
            }
        }),
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind");
    let origin = format!("http://{}", listener.local_addr().expect("addr"));
    tokio::spawn(async move {
        let _ = axum::serve(listener, worker).await;
    });

    let source = WorkerGpuSource::new(&origin).expect("seam client");
    assert_eq!(
        source.read().await,
        Some(GpuReading {
            gpu_mem_gb: 3.25,
            gpu_mem_pct: 20.3
        })
    );
    assert_eq!(calls.load(Ordering::SeqCst), 1, "one call, one reading");

    // A worker that does not answer the seam is "cannot say", not "idle GPU".
    let silent = WorkerGpuSource::new(format!("{origin}/nowhere")).expect("seam client");
    assert_eq!(silent.read().await, None);
}
