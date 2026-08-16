//! The static / UI surface, against the recorded Python contract (WP-I4/I6):
//! capture-replay for the static family, the legacy page redirects, and
//! `/local/sysinfo`.
//!
//! ## Capture-replay parity for the static/UI family
//!
//! Every case in `rust/fixtures/http/static_ui.json` was recorded against the
//! live Python router; every case is replayed here against the Rust one, over
//! real HTTP, with the same static tree and the same invite posture. A
//! difference in status, in any pinned header, or in one byte of the body fails.
//!
//! The replay is split by install rather than by route, because the install *is*
//! the branch: "the build output is missing" and "the gate is armed" are not
//! variations on a request, they are different machines.
//!
//! ## The legacy page redirects
//!
//! Three things are proven, and the third is the one that rots silently:
//!
//! 1. every route in [`lattice_platform::ui_redirects::REDIRECTS`] answers 308
//!    to the SPA hash route, over real HTTP, with the query where Python puts it;
//! 2. the table *is* the Python map — same paths, same fragments, same
//!    `require_user` flags — asserted against `redirects.routes` in the fixture;
//! 3. a path that only one of the two routers claims cannot be added twice: the
//!    static router and this one are merged in the same host, and axum panics on
//!    a duplicate, so the merge itself is a test.
//!
//! ## `/local/sysinfo` — the half this machine can answer, and the half it asks for
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
//!
//! Three test binaries collapsed into one; all three recompiled the same
//! harness to stand up the same install.

// The shared harness is written for every suite that includes it, so a
// helper this one does not call still reads as dead in this binary.
#[allow(dead_code)]
mod static_ui_harness;

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

use lattice_platform::static_ui::{
    asset_content_type, host_capacity_readiness, parse_cpu_percent, parse_ram_percent,
    probe_host_capacity, sign_invite_cookie, sysinfo_router, verify_invite_cookie, GpuFuture,
    GpuReading, GpuSource, NoGpu, SysinfoState, WorkerGpuSource, INVITE_DENIED_HTML,
    PRODUCTION_CSP, SYSINFO_READINESS_ROOMY_MAX, SYSINFO_READINESS_TIGHT_MAX, WORKER_SYSINFO_PATH,
};
use lattice_platform::ui_redirects::{
    app_redirect, authenticated_router, public_router, router, REDIRECTS, REDIRECT_STATUS,
};
use serde_json::Value;
use static_ui_harness::{cases_for, fixture, sha256, Install};

// ── the static family, install by install ──

#[tokio::test]
async fn the_default_install_answers_as_python_answered() {
    let install = Install::start("gate_off").await;
    install.replay_all("gate_off").await;
}

#[tokio::test]
async fn an_install_without_build_output_answers_as_python_answered() {
    let install = Install::start("shell_missing").await;
    install.replay_all("shell_missing").await;
}

#[tokio::test]
async fn an_install_without_icons_answers_as_python_answered() {
    let install = Install::start("iconless").await;
    install.replay_all("iconless").await;
}

#[tokio::test]
async fn an_invitation_only_install_answers_as_python_answered() {
    let install = Install::start("gate_on").await;
    install.replay_all("gate_on").await;
}

#[tokio::test]
async fn a_reachable_invitation_only_install_marks_its_cookie_secure() {
    let install = Install::start("gate_on_secure").await;
    install.replay_all("gate_on_secure").await;
}

/// The wall is a literal in two languages' source files; only a digest can say
/// they are the same wall.
#[test]
fn the_invitation_wall_is_byte_identical() {
    let recorded = cases_for("gate_on")
        .into_iter()
        .find(|case| case["name"] == "gate_root_denied")
        .expect("the denied case");
    assert_eq!(
        sha256(INVITE_DENIED_HTML.as_bytes()),
        recorded["body_sha256"].as_str().expect("digest"),
        "the invitation wall drifted from the Python literal"
    );
    assert_eq!(
        INVITE_DENIED_HTML.len(),
        recorded["body_bytes"].as_u64().expect("length") as usize
    );
}

/// The CSP is one long string that nobody reads and every browser enforces.
#[test]
fn the_production_csp_is_the_recorded_one() {
    let recorded = cases_for("gate_off")
        .into_iter()
        .find(|case| case["name"] == "app_shell")
        .expect("the shell case");
    assert_eq!(
        recorded["headers"]["content-security-policy"]
            .as_str()
            .expect("csp"),
        PRODUCTION_CSP
    );
}

/// A cookie Python signed, verified by Rust — the point of the exercise. If the
/// two HMACs ever disagree, every browser holding a live invitation is locked
/// out at the moment of the cutover, which is not something to discover in
/// production.
#[test]
fn python_signed_cookies_verify_here_and_vice_versa() {
    let invite = &fixture()["invite"];
    let secret = invite["secret"].as_str().expect("secret");
    let signed = invite["signed_cookie"].as_str().expect("cookie");
    let frozen = invite["frozen_now"].as_i64().expect("now");
    let nonce = invite["frozen_nonce"].as_str().expect("nonce");
    let ttl = invite["ttl_seconds"].as_i64().expect("ttl");

    assert!(verify_invite_cookie(Some(signed), secret, frozen));
    assert_eq!(
        sign_invite_cookie(secret, frozen + ttl, nonce),
        signed,
        "the Rust signature is not the Python one"
    );
}

/// Every accept/reject the Python verifier makes, made again here.
#[test]
fn the_invite_verifier_agrees_branch_for_branch() {
    let invite = &fixture()["invite"];
    let default_secret = invite["secret"].as_str().expect("secret");
    for vector in invite["vectors"].as_array().expect("vectors") {
        let value = vector["value"].as_str().expect("value");
        let secret = vector
            .get("secret")
            .and_then(Value::as_str)
            .unwrap_or(default_secret);
        let now = vector["now"].as_i64().expect("now");
        assert_eq!(
            verify_invite_cookie(Some(value), secret, now),
            vector["valid"].as_bool().expect("verdict"),
            "vector: {}",
            vector["why"]
        );
    }
    assert!(
        !verify_invite_cookie(None, default_secret, 0),
        "no cookie at all"
    );
}

/// The content-type table, against what Starlette actually served.
///
/// Two extensions are machine-dependent in Python — CPython's `mimetypes` also
/// reads the system's `mime.types` — so they are asserted against the choice the
/// port documents rather than against the recording.
#[test]
fn the_content_type_table_matches_the_recording() {
    let mut machine_dependent = 0;
    for row in fixture()["mimetypes"]["rows"].as_array().expect("rows") {
        let extension = row["extension"].as_str().expect("extension");
        let served = row["served"].as_str().expect("served");
        let ours = asset_content_type(&format!("probe{extension}"));
        let live = row["live_guess"].as_str();
        let builtin = row["builtin_guess"].as_str();
        if live != builtin {
            machine_dependent += 1;
            assert_eq!(
                ours, served,
                "{extension}: the port follows this machine's answer"
            );
            continue;
        }
        assert_eq!(ours, served, "{extension}");
    }
    assert_eq!(
        machine_dependent, 2,
        "the recording knows of exactly two machine-dependent extensions (.ico, .xml)"
    );
}

/// The tree the goldens were recorded against is the tree they are replayed
/// against — a fixture that carries its own inputs cannot drift from them.
#[test]
fn the_recorded_tree_carries_its_own_digests() {
    use base64::engine::general_purpose::STANDARD as BASE64;
    use base64::Engine;

    let tree = fixture()["tree"].as_object().expect("tree");
    assert!(
        tree.contains_key("app/index.html"),
        "the SPA shell is the point"
    );
    for (path, entry) in tree {
        let bytes = BASE64
            .decode(entry["b64"].as_str().expect("b64"))
            .expect("base64");
        assert_eq!(
            sha256(&bytes),
            entry["sha256"].as_str().expect("digest"),
            "{path}"
        );
        assert_eq!(
            bytes.len(),
            entry["bytes"].as_u64().expect("length") as usize,
            "{path}"
        );
    }
}

/// Nothing in the recording is left unreplayed: a case added to the fixture and
/// not to a suite would otherwise pass by being ignored.
#[test]
fn every_recorded_case_belongs_to_a_replayed_install() {
    let replayed = [
        "gate_off",
        "shell_missing",
        "iconless",
        "gate_on",
        "gate_on_secure",
    ];
    let configs = fixture()["configs"].as_object().expect("configs");
    for config in configs.keys() {
        assert!(replayed.contains(&config.as_str()), "{config} has no suite");
    }
    for case in fixture()["cases"].as_array().expect("cases") {
        let config = case["config"].as_str().expect("config");
        assert!(
            configs.contains_key(config),
            "{} names an install the fixture does not describe",
            case["name"]
        );
    }
    assert_eq!(
        fixture()["cases"].as_array().expect("cases").len(),
        replayed
            .iter()
            .map(|config| cases_for(config).len())
            .sum::<usize>()
    );
}

// ── the legacy page redirects ──

/// The fixture's table, minus `/account` — that one is invite-gated and is
/// served by `static_ui`, which is where its cases live.
fn recorded_routes() -> Vec<(&'static str, &'static str, bool)> {
    fixture()["redirects"]["routes"]
        .as_array()
        .expect("routes")
        .iter()
        .map(|route| {
            (
                route["path"].as_str().expect("path"),
                route["fragment"].as_str().expect("fragment"),
                route["requires_user"].as_bool().expect("flag"),
            )
        })
        .filter(|(path, _, _)| *path != "/account")
        .collect()
}

#[test]
fn the_table_is_the_python_map() {
    let recorded = recorded_routes();
    assert_eq!(
        recorded.len(),
        REDIRECTS.len(),
        "the fixture knows {} redirects, the table {}",
        recorded.len(),
        REDIRECTS.len()
    );
    for (path, fragment, requires_user) in recorded {
        let ours = REDIRECTS
            .iter()
            .find(|route| route.path == path)
            .unwrap_or_else(|| panic!("{path} is not in the Rust table"));
        assert_eq!(ours.fragment, fragment, "{path}: fragment");
        assert_eq!(ours.requires_user, requires_user, "{path}: require_user");
    }
}

#[test]
fn the_recorded_status_is_the_one_we_answer() {
    assert_eq!(
        REDIRECT_STATUS.as_u16(),
        fixture()["redirects"]["status"].as_u64().expect("status") as u16
    );
}

#[tokio::test]
async fn every_redirect_answers_over_http() {
    let install = Install::serve(router(), std::path::PathBuf::from(".")).await;
    for route in REDIRECTS {
        let response = install
            .client
            .get(format!("{}{}", install.origin, route.path))
            .send()
            .await
            .expect("request");
        assert_eq!(response.status().as_u16(), 308, "{}", route.path);
        assert_eq!(
            response
                .headers()
                .get("location")
                .expect("location")
                .to_str()
                .expect("ascii"),
            format!("/app#/{}", route.fragment),
            "{}",
            route.path
        );
        assert_eq!(response.bytes().await.expect("body").len(), 0);
    }
}

#[tokio::test]
async fn a_redirect_carries_the_query_across_the_hash() {
    let install = Install::serve(router(), std::path::PathBuf::from(".")).await;
    let response = install
        .client
        .get(format!(
            "{}/workspace?tab=members&q=%EA%B9%80",
            install.origin
        ))
        .send()
        .await
        .expect("request");
    assert_eq!(
        response.headers()["location"].to_str().expect("ascii"),
        "/app#/workspace-admin?tab=members&q=%EA%B9%80",
        "the query is copied byte-for-byte, still encoded"
    );
}

#[tokio::test]
async fn these_are_get_routes_and_say_so() {
    let install = Install::serve(router(), std::path::PathBuf::from(".")).await;
    for method in [reqwest::Method::HEAD, reqwest::Method::POST] {
        let response = install
            .client
            .request(method.clone(), format!("{}/chat", install.origin))
            .send()
            .await
            .expect("request");
        assert_eq!(response.status().as_u16(), 405, "{method}");
        assert_eq!(response.headers()["allow"].to_str().expect("ascii"), "GET");
        assert_eq!(
            response.headers()["content-type"].to_str().expect("ascii"),
            "application/json"
        );
        // `POST /chat` is the SSE stream in the product. This 405 is only what
        // *this* router says about a path it serves the page redirect for; the
        // chat crate mounts the POST and the two merge without colliding.
    }
}

#[test]
fn the_halves_add_up_to_the_whole() {
    // Building all three is the collision check; counting them is the coverage
    // check — a route in neither half would be served by `router` alone.
    let _ = router();
    let _ = public_router();
    let _ = authenticated_router();
    let public: Vec<_> = REDIRECTS
        .iter()
        .filter(|route| !route.requires_user)
        .collect();
    let gated: Vec<_> = REDIRECTS
        .iter()
        .filter(|route| route.requires_user)
        .collect();
    assert_eq!(public.len() + gated.len(), REDIRECTS.len());
    assert!(public
        .iter()
        .all(|route| route.path == "/chat" || route.path == "/admin"));
}

#[test]
fn the_helper_drops_an_empty_query_and_keeps_a_real_one() {
    let location = |response: axum::http::Response<axum::body::Body>| {
        response.headers()["location"]
            .to_str()
            .expect("ascii")
            .to_string()
    };
    assert_eq!(location(app_redirect("activity", None)), "/app#/activity");
    assert_eq!(
        location(app_redirect("activity", Some(""))),
        "/app#/activity"
    );
    assert_eq!(
        location(app_redirect("activity", Some("a=1"))),
        "/app#/activity?a=1"
    );
}

// ── /local/sysinfo ──

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
