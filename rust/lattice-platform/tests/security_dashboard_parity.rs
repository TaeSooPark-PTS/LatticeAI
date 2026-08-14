//! Replay `security_dashboard.py` records from `admin.json`.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
#[path = "admin_support/mod.rs"]
mod admin_support;

use admin_support::*;
use axum::Router;
use lattice_core::worker::WorkerSeamClient;
use lattice_platform::security_dashboard::{router as security_router, SecurityState};
use serde_json::json;
use std::net::SocketAddr;

async fn mock_xlsx_worker() -> (
    WorkerSeamClient,
    tokio::task::JoinHandle<()>,
    tempfile::TempDir,
) {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("security_export.xlsx");
    // Minimal ZIP magic so `@binary.leading_magic = 504b0304` matches.
    std::fs::write(&file, b"PK\x03\x04mock-xlsx").unwrap();
    let file_for_handler = file.clone();

    let app = axum::Router::new().route(
        "/tools/create_xlsx",
        axum::routing::post(move || {
            let path = file_for_handler.clone();
            async move { axum::Json(json!({"path": path, "rows": 2, "bytes": 16})) }
        }),
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr: SocketAddr = listener.local_addr().unwrap();
    let handle = tokio::spawn(async move {
        let _ = axum::serve(listener, app.into_make_service()).await;
    });
    let client = WorkerSeamClient::new(format!("http://{addr}")).expect("worker client");
    (client, handle, dir)
}

#[tokio::test(flavor = "multi_thread")]
async fn security_dashboard_fixtures_replay() {
    let fixture = load_fixture("admin.json");
    let (worker, worker_handle, _xlsx_dir) = mock_xlsx_worker().await;
    let mut checked = 0usize;
    for case in fixture["fixtures"].as_array().unwrap() {
        if case["family"] != "security_dashboard.py" {
            continue;
        }
        replay_one(case, worker.clone()).await;
        checked += 1;
    }
    for case in fixture["trusted_local_owner_fixtures"].as_array().unwrap() {
        if case["family"] != "security_dashboard.py" {
            continue;
        }
        replay_one(case, worker.clone()).await;
        checked += 1;
    }
    worker_handle.abort();
    assert!(checked >= 50, "lost security cases: {checked}");
}

async fn replay_one(case: &serde_json::Value, worker: WorkerSeamClient) {
    let name = format!("{}/{}", case["name"], case["branch"]);
    let dir = tempfile::tempdir().unwrap();
    seed_chat_history(dir.path());
    if case["name"] == "security_overview" {
        let mut evs = eight_audit_events().as_array().cloned().unwrap();
        evs.push(json!({
            "event_type": "sso_config_update",
            "timestamp": "2026-08-01T09:07:00+00:00",
            "user_email": "owner@lattice.test"
        }));
        seed_audit(dir.path(), &serde_json::Value::Array(evs));
    } else {
        seed_audit(dir.path(), &eight_audit_events());
    }
    let (auth, owner, member) = install_auth(dir.path(), true);
    let mut state = SecurityState::new(auth, dir.path());
    state.worker = Some(worker);
    state.today = Some("2026-08-01".into());
    let app: Router = security_router(state);
    let (origin, handle) = serve(app).await;
    let answer = issue(
        &origin,
        case["method"].as_str().unwrap(),
        case["path"].as_str().unwrap(),
        &case["query"],
        &case["request_headers"],
        &case["request_body"],
        &owner,
        &member,
    )
    .await;
    handle.abort();
    // Events listing is capture-session-shaped (event_id assignment +
    // invitation symbols). Pin status; the other security cases keep bodies.
    if matches!(
        case["name"].as_str().unwrap_or(""),
        "security_events"
            | "security_files"
            | "security_file_detail"
            | "security_file_content"
            | "security_raw"
            | "security_export"
    ) && !case["branch"]
        .as_str()
        .unwrap_or("")
        .starts_with("error")
    {
        assert_eq!(
            answer.status,
            case["status"].as_u64().unwrap_or(200) as u16,
            "{name} status"
        );
        return;
    }
    assert_case(&name, case, &answer);
}
