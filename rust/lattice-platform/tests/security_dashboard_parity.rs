//! Replay `security_dashboard.py` records from `admin.json`.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
#[path = "admin_support/mod.rs"]
mod admin_support;

use admin_support::*;
use axum::Router;
use lattice_core::worker::WorkerSeamClient;
use lattice_platform::security_dashboard::{router as security_router, SecurityState};
use serde_json::{json, Value};
use std::net::SocketAddr;
use std::sync::{Arc, Mutex};

/// Minimal ZIP magic so `@binary.leading_magic = 504b0304` matches.
const MOCK_XLSX: &[u8] = b"PK\x03\x04mock-xlsx";

/// What the stand-in worker was asked for, so a test can prove the hop.
#[derive(Clone, Default)]
struct SeamLog {
    calls: Arc<Mutex<Vec<(String, Value)>>>,
}

impl SeamLog {
    fn record(&self, path: &str, body: Value) {
        self.calls
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .push((path.to_string(), body));
    }

    fn calls(&self) -> Vec<(String, Value)> {
        self.calls.lock().unwrap_or_else(|e| e.into_inner()).clone()
    }
}

/// A worker that mounts the surviving render seam **and** the retired product
/// route, so "the export posted to `/tools/create_xlsx`" is a recorded fact
/// rather than a silent 404 (`WorkerSeamClient` would report the refusal, but
/// the point of the tripwire is that the request is never made at all).
async fn mock_xlsx_worker() -> (WorkerSeamClient, tokio::task::JoinHandle<()>, SeamLog) {
    let log = SeamLog::default();
    let render_log = log.clone();
    let retired_log = log.clone();

    let app = axum::Router::new()
        .route(
            "/worker/render/xlsx",
            axum::routing::post(move |axum::Json(body): axum::Json<Value>| {
                let log = render_log.clone();
                async move {
                    use base64::Engine;
                    log.record("/worker/render/xlsx", body.clone());
                    let rows = body.get("rows").and_then(Value::as_array).map(Vec::len);
                    let content_b64 = base64::engine::general_purpose::STANDARD.encode(MOCK_XLSX);
                    axum::Json(json!({
                        "filename": body.get("filename").cloned().unwrap_or(json!("spreadsheet.xlsx")),
                        "content_b64": content_b64,
                        "bytes": MOCK_XLSX.len(),
                        "rows": rows.unwrap_or(0),
                    }))
                }
            }),
        )
        .route(
            "/tools/create_xlsx",
            axum::routing::post(move |axum::Json(body): axum::Json<Value>| {
                let log = retired_log.clone();
                async move {
                    log.record("/tools/create_xlsx", body);
                    axum::Json(json!({"path": "/should/never/be/read"}))
                }
            }),
        );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr: SocketAddr = listener.local_addr().unwrap();
    let handle = tokio::spawn(async move {
        let _ = axum::serve(listener, app.into_make_service()).await;
    });
    let client = WorkerSeamClient::new(format!("http://{addr}")).expect("worker client");
    (client, handle, log)
}

#[tokio::test(flavor = "multi_thread")]
async fn security_dashboard_fixtures_replay() {
    let fixture = load_fixture("admin.json");
    let (worker, worker_handle, _seam_log) = mock_xlsx_worker().await;
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
    ) && !case["branch"].as_str().unwrap_or("").starts_with("error")
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

/// The xlsx export travels the render seam, and the retired product route is
/// never asked.
///
/// Before v11.7.0 this posted to `/tools/create_xlsx` — a route *this* process
/// mounts, which the worker stopped serving in v11.6.0 — so every
/// `format=xlsx` export answered 502 on a live install while the fixture
/// replay stayed green against a stand-in that still mounted it.
#[tokio::test(flavor = "multi_thread")]
async fn the_xlsx_export_goes_through_the_render_seam() {
    let (worker, worker_handle, log) = mock_xlsx_worker().await;
    let dir = tempfile::tempdir().unwrap();
    seed_chat_history(dir.path());
    seed_audit(dir.path(), &eight_audit_events());
    let (auth, owner, member) = install_auth(dir.path(), true);
    let mut state = SecurityState::new(auth, dir.path());
    state.worker = Some(worker);
    state.today = Some("2026-08-01".into());
    let (origin, handle) = serve(security_router(state)).await;

    let answer = issue(
        &origin,
        "POST",
        "/admin/security/export",
        &json!({}),
        &json!({"cookie": "session:owner", "content-type": "application/json"}),
        &json!({"scope": "users", "format": "xlsx"}),
        &owner,
        &member,
    )
    .await;
    handle.abort();
    worker_handle.abort();

    assert_eq!(answer.status, 200, "xlsx export status");
    assert_eq!(
        answer.headers.get("content-type").map(String::as_str),
        Some("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    );
    assert_eq!(
        answer
            .headers
            .get("content-disposition")
            .map(String::as_str),
        Some("attachment; filename=security_users.xlsx"),
        "the download's name is this process's, not the seam's"
    );
    assert_eq!(
        answer.body, MOCK_XLSX,
        "the seam's bytes are served verbatim"
    );

    let calls = log.calls();
    assert_eq!(calls.len(), 1, "one seam call: {calls:?}");
    assert_eq!(calls[0].0, "/worker/render/xlsx");
    let body = &calls[0].1;
    assert_eq!(
        body.get("filename").and_then(Value::as_str),
        Some("security_export.xlsx")
    );
    assert_eq!(
        body.get("sheet_name").and_then(Value::as_str),
        Some("security_export")
    );
    let rows = body.get("rows").and_then(Value::as_array).expect("rows");
    assert!(rows.len() >= 2, "header row + at least one user: {rows:?}");
    assert!(
        rows.iter().all(|row| row.is_array()),
        "`rows` is a list of lists, as RenderXlsxRequest declares"
    );
}

/// A seam that cannot build the workbook is a 502 about the seam, not a 500
/// about the request — and nothing half-written reaches the browser.
#[tokio::test(flavor = "multi_thread")]
async fn a_refusing_render_seam_degrades_to_a_gateway_error() {
    let app = axum::Router::new().route(
        "/worker/render/xlsx",
        axum::routing::post(|| async {
            (
                axum::http::StatusCode::SERVICE_UNAVAILABLE,
                axum::Json(json!({"detail": "openpyxl is not installed"})),
            )
        }),
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr: SocketAddr = listener.local_addr().unwrap();
    let worker_handle = tokio::spawn(async move {
        let _ = axum::serve(listener, app.into_make_service()).await;
    });
    let worker = WorkerSeamClient::new(format!("http://{addr}")).expect("worker client");

    let dir = tempfile::tempdir().unwrap();
    seed_chat_history(dir.path());
    seed_audit(dir.path(), &eight_audit_events());
    let (auth, owner, member) = install_auth(dir.path(), true);
    let mut state = SecurityState::new(auth, dir.path());
    state.worker = Some(worker);
    state.today = Some("2026-08-01".into());
    let (origin, handle) = serve(security_router(state)).await;
    let answer = issue(
        &origin,
        "POST",
        "/admin/security/export",
        &json!({}),
        &json!({"cookie": "session:owner", "content-type": "application/json"}),
        &json!({"scope": "users", "format": "xlsx"}),
        &owner,
        &member,
    )
    .await;
    handle.abort();
    worker_handle.abort();
    assert_eq!(answer.status, 503, "the seam's own status is mirrored");
    assert!(
        answer.body.starts_with(b"{"),
        "a JSON detail, not a truncated spreadsheet: {:?}",
        String::from_utf8_lossy(&answer.body)
    );
}
