//! Probe / recommendation / eval surfaces — worker-up and worker-down.

use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;

use axum::extract::Json;
use axum::routing::get;
use axum::Router;
use lattice_core::worker::WorkerSeamClient;
use lattice_platform::agents::{self, AgentsState};
use lattice_platform::computer_use::{self, ComputerUseState};
use lattice_platform::models_catalog::{self, ModelsCatalogState};
use lattice_platform::setup::{self, SetupState};
use lattice_platform::workspace::{self, WorkspaceState};
use serde_json::{json, Value};

#[allow(dead_code)]
#[path = "models_catalog_support/mod.rs"]
mod support;

async fn serve(app: Router) -> (String, tokio::task::JoinHandle<()>) {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind");
    let addr: SocketAddr = listener.local_addr().expect("addr");
    let handle = tokio::spawn(async move {
        let _ = axum::serve(listener, app.into_make_service()).await;
    });
    (format!("http://{addr}"), handle)
}

async fn fake_worker(pointer_tools: bool) -> (WorkerSeamClient, tokio::task::JoinHandle<()>) {
    let app = Router::new()
        .route(
            "/models",
            get(|| async {
                Json(json!({
                    "current": "mlx-community/gemma-4-e2b-it-4bit",
                    "loaded": ["mlx-community/gemma-4-e2b-it-4bit"],
                    "engines": [{"id": "local_mlx", "installed": true}],
                    "recommended": [{
                        "id": "mlx-community/gemma-4-e2b-it-4bit",
                        "name": "Gemma 4 E2B",
                        "family": "Gemma 4",
                        "recommended_default": true,
                        "display_priority": 10,
                        "hardware": {"min_ram_gb": 8.0, "recommended_ram_gb": 8.0}
                    }],
                    "registry": {"version": "5.2.0", "verified_count": 1, "verified": []}
                }))
            }),
        )
        .route(
            "/worker/sysinfo",
            get(move || async move {
                Json(json!({
                    "mlx_available": true,
                    "gpu_mem_gb": 1.0,
                    "gpu_mem_pct": 6.0,
                    "total_bytes": 16u64 * 1024 * 1024 * 1024,
                    "detail": null,
                    "capabilities": {"pointer_tools": pointer_tools},
                    "python_version": "3.12.0"
                }))
            }),
        );
    let (origin, handle) = serve(app).await;
    (
        WorkerSeamClient::new(&origin).expect("worker client"),
        handle,
    )
}

#[tokio::test(flavor = "multi_thread")]
async fn onboarding_model_recommendations_fill_from_the_probe() {
    let install = support::Install::start();
    let (worker, handle) = fake_worker(false).await;
    let state = WorkspaceState::new(install.auth.clone(), install.data_dir()).with_worker(worker);
    let (origin, app) = serve(workspace::router(state)).await;
    let answer = support::issue(
        &origin,
        "GET",
        "/workspace/onboarding/model-recommendations",
        &json!({}),
        &json!({"cookie": "session:owner", "origin": "http://127.0.0.1:4825"}),
        &Value::Null,
        &install,
    )
    .await;
    assert_eq!(answer.status, 200, "{}", answer.body);
    let body: Value = serde_json::from_str(&answer.body).unwrap();
    assert_ne!(
        body["environment"]["os"].as_str().unwrap_or("unknown"),
        "unknown",
        "{}",
        answer.body
    );
    assert!(
        body["environment"]["ram_gb"].as_u64().is_some(),
        "{}",
        answer.body
    );
    assert_eq!(
        body["catalog"]["top_pick"]["id"], "mlx-community/gemma-4-e2b-it-4bit",
        "{}",
        answer.body
    );
    assert_eq!(body["catalog"]["engine_available"], true, "{}", answer.body);
    handle.abort();
    app.abort();
}

#[tokio::test(flavor = "multi_thread")]
async fn recommendations_fill_from_the_worker_catalog() {
    let install = support::Install::start();
    let (worker, handle) = fake_worker(false).await;
    let state =
        ModelsCatalogState::new(install.auth.clone(), install.data_dir()).with_worker(worker);
    let (origin, app) = serve(models_catalog::router(state)).await;
    let answer = support::issue(
        &origin,
        "GET",
        "/models/recommendations",
        &json!({"engine": "local_mlx"}),
        &json!({"cookie": "session:owner", "origin": "http://127.0.0.1:4825"}),
        &Value::Null,
        &install,
    )
    .await;
    assert_eq!(answer.status, 200, "{}", answer.body);
    let body: Value = serde_json::from_str(&answer.body).unwrap();
    assert!(body["recommendations"]["engine_available"]
        .as_bool()
        .unwrap());
    assert_eq!(
        body["recommendations"]["top_pick"]["id"],
        "mlx-community/gemma-4-e2b-it-4bit"
    );
    assert!(body["profile"]["ram_gb"].as_u64().is_some());
    handle.abort();
    app.abort();
}

#[tokio::test(flavor = "multi_thread")]
async fn recommendations_degrade_when_the_worker_is_absent() {
    let install = support::Install::start();
    let state = ModelsCatalogState::new(install.auth.clone(), install.data_dir());
    let (origin, app) = serve(models_catalog::router(state)).await;
    let answer = support::issue(
        &origin,
        "GET",
        "/models/recommendations",
        &json!({}),
        &json!({"cookie": "session:owner", "origin": "http://127.0.0.1:4825"}),
        &Value::Null,
        &install,
    )
    .await;
    let body: Value = serde_json::from_str(&answer.body).unwrap();
    assert_eq!(body["recommendations"]["top_pick"], Value::Null);
    assert_eq!(
        body["recommendations"]["reason"],
        "worker is not configured"
    );
    app.abort();
}

#[tokio::test(flavor = "multi_thread")]
async fn setup_scan_verify_is_not_vacuous() {
    let install = support::Install::start();
    let state = SetupState::new(install.auth.clone(), install.data_dir());
    let (origin, app) = serve(setup::router(state)).await;
    let answer = support::issue(
        &origin,
        "GET",
        "/setup/scan",
        &json!({}),
        &json!({"cookie": "session:owner", "origin": "http://127.0.0.1:4825"}),
        &Value::Null,
        &install,
    )
    .await;
    let body: Value = serde_json::from_str(&answer.body).unwrap();
    assert_eq!(body["zero_config"]["verify"]["all_pass"], false);
    assert!(body["environment"]["ram_gb"].as_u64().is_some());
    app.abort();
}

#[tokio::test(flavor = "multi_thread")]
async fn setup_install_brew_is_manual_not_complete_theatre() {
    let install = support::Install::start();
    let state = SetupState::new(install.auth.clone(), install.data_dir());
    let (origin, app) = serve(setup::router(state)).await;
    let answer = support::issue(
        &origin,
        "POST",
        "/setup/install",
        &json!({}),
        &json!({
            "cookie": "session:owner",
            "origin": "http://127.0.0.1:4825",
            "content-type": "application/json"
        }),
        &json!({"items": [{"id": "mlx", "name": "MLX", "action": {"type": "brew"}}]}),
        &install,
    )
    .await;
    assert!(
        answer.body.contains("\"status\": \"manual\""),
        "{}",
        answer.body
    );
    assert!(answer.body.contains("brew install mlx"), "{}", answer.body);
    assert!(answer.body.contains("manual=1"), "{}", answer.body);
    app.abort();
}

#[tokio::test(flavor = "multi_thread")]
async fn setup_install_consented_pip_version_executes() {
    let install = support::Install::start();
    let state = SetupState::new(install.auth.clone(), install.data_dir());
    let (origin, app) = serve(setup::router(state)).await;
    let answer = support::issue(
        &origin,
        "POST",
        "/setup/install",
        &json!({}),
        &json!({
            "cookie": "session:owner",
            "origin": "http://127.0.0.1:4825",
            "content-type": "application/json"
        }),
        &json!({
            "execute": ["pip"],
            "items": [{
                "id": "pip",
                "name": "pip",
                "action": {"type": "pip", "verb": "version"}
            }]
        }),
        &install,
    )
    .await;
    assert!(
        answer.body.contains("\"status\": \"done\""),
        "{}",
        answer.body
    );
    assert!(
        answer.body.contains("\"status\": \"progress\""),
        "stdout must stream as progress frames: {}",
        answer.body
    );
    assert!(answer.body.contains("done=1"), "{}", answer.body);
    assert!(
        !answer.body.contains("\"status\": \"manual\""),
        "consented pip must not stay manual: {}",
        answer.body
    );
    app.abort();
}

#[tokio::test(flavor = "multi_thread")]
async fn setup_install_unconsented_pip_stays_manual() {
    let install = support::Install::start();
    let state = SetupState::new(install.auth.clone(), install.data_dir());
    let (origin, app) = serve(setup::router(state)).await;
    let answer = support::issue(
        &origin,
        "POST",
        "/setup/install",
        &json!({}),
        &json!({
            "cookie": "session:owner",
            "origin": "http://127.0.0.1:4825",
            "content-type": "application/json"
        }),
        &json!({"items": [{"id": "mlx", "name": "MLX", "action": {"type": "pip"}}]}),
        &install,
    )
    .await;
    assert!(
        answer.body.contains("\"status\": \"manual\""),
        "{}",
        answer.body
    );
    assert!(answer.body.contains("pip3 install mlx"), "{}", answer.body);
    assert!(answer.body.contains("manual=1"), "{}", answer.body);
    app.abort();
}

#[tokio::test(flavor = "multi_thread")]
async fn setup_install_non_allowlisted_id_is_refused() {
    let install = support::Install::start();
    let state = SetupState::new(install.auth.clone(), install.data_dir());
    let (origin, app) = serve(setup::router(state)).await;
    let answer = support::issue(
        &origin,
        "POST",
        "/setup/install",
        &json!({}),
        &json!({
            "cookie": "session:owner",
            "origin": "http://127.0.0.1:4825",
            "content-type": "application/json"
        }),
        &json!({
            "execute": ["curl", "not-a-plan-item"],
            "items": [{"id": "curl", "name": "curl", "action": {"type": "apt"}}]
        }),
        &install,
    )
    .await;
    assert!(
        answer
            .body
            .contains("refused: not an allowlisted brew/pip/uv item"),
        "{}",
        answer.body
    );
    assert!(
        !answer.body.contains("\"status\": \"done\""),
        "apt must not execute: {}",
        answer.body
    );
    assert!(answer.body.contains("failed=2"), "{}", answer.body);
    app.abort();
}

#[tokio::test(flavor = "multi_thread")]
async fn computer_use_reads_pointer_capability() {
    let install = support::Install::start();
    let (worker, handle) = fake_worker(true).await;
    let state = ComputerUseState::new(
        Arc::clone(&install.auth),
        Some(worker),
        install.data_dir().to_path_buf(),
    );
    let (origin, app) = serve(computer_use::router(state)).await;
    let answer = support::issue(
        &origin,
        "GET",
        "/cu/status",
        &json!({}),
        &json!({"cookie": "session:owner", "origin": "http://127.0.0.1:4825"}),
        &Value::Null,
        &install,
    )
    .await;
    let body: Value = serde_json::from_str(&answer.body).unwrap();
    assert_eq!(body["available"], true);
    handle.abort();
    app.abort();
}

#[tokio::test(flavor = "multi_thread")]
async fn agent_eval_runs_static_cases_and_404s_unknown_skills() {
    let install = support::Install::start();
    let skills = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../skills");
    let state =
        AgentsState::new(Arc::clone(&install.auth), install.data_dir()).with_skills_root(skills);
    let (origin, app) = serve(agents::router(state)).await;
    let missing = support::issue(
        &origin,
        "POST",
        "/agent/eval",
        &json!({}),
        &json!({
            "cookie": "session:owner",
            "origin": "http://127.0.0.1:4825",
            "content-type": "application/json"
        }),
        &json!({"skill": "not-a-skill"}),
        &install,
    )
    .await;
    assert_eq!(missing.status, 404, "{}", missing.body);

    let found = support::issue(
        &origin,
        "POST",
        "/agent/eval",
        &json!({}),
        &json!({
            "cookie": "session:owner",
            "origin": "http://127.0.0.1:4825",
            "content-type": "application/json"
        }),
        &json!({"skill": "code_review"}),
        &install,
    )
    .await;
    assert_eq!(found.status, 200, "{}", found.body);
    let body: Value = serde_json::from_str(&found.body).unwrap();
    assert_eq!(body["skill"], "code_review");
    let cases = body["cases"].as_array().unwrap();
    assert!(cases
        .iter()
        .any(|row| row["id"] == "empty_target" && row["verdict"] == "pass"));
    assert!(cases
        .iter()
        .any(|row| row["id"] == "snippet_review" && row["verdict"] == "requires_model"));
    app.abort();
}

#[tokio::test(flavor = "multi_thread")]
async fn agent_health_is_ready_when_a_model_is_loaded() {
    let install = support::Install::start();
    let (worker, handle) = fake_worker(false).await;
    let mut state =
        AgentsState::new(Arc::clone(&install.auth), install.data_dir()).with_worker(worker);
    state.model_loaded = true;
    let (origin, app) = serve(agents::router(state)).await;
    let answer = support::issue(
        &origin,
        "GET",
        "/agents/api/runtime/health",
        &json!({}),
        &json!({"cookie": "session:owner", "origin": "http://127.0.0.1:4825"}),
        &Value::Null,
        &install,
    )
    .await;
    let body: Value = serde_json::from_str(&answer.body).unwrap();
    assert_eq!(body["ready"], true);
    assert_eq!(body["loop_surface"], "POST /agents/api/run");
    handle.abort();
    app.abort();
}
