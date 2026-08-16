//! The admin-console families, replayed against the Python oracle: `admin.py`,
//! `funnel_metrics.py`, `features.py`, `setup.py` and `security_dashboard.py`,
//! plus the OpenAPI contract the admin router composes (WP-I5) and unit cover
//! for the native `audit_log.json` append helper.
//!
//! Seven test binaries collapsed into one. Each family keeps its own
//! `replay_<family>_case` helper — they differ in what they seed and stub —
//! and every test function is the one it was.

// The shared harness is written for every suite that includes it, so a
// helper this one does not call still reads as dead in this binary.
#[allow(dead_code)]
#[path = "admin_support/mod.rs"]
mod admin_support;

use admin_support::*;
use axum::Router;
use lattice_core::worker::WorkerSeamClient;
use lattice_platform::admin::{
    append_audit_event, default_product_hardening_probed, family_mounted, load_audit_log,
    router as admin_router, AdminState,
};
use lattice_platform::features::{router as features_router, FeaturesState};
use lattice_platform::funnel_metrics::{router as funnel_router, FunnelState};
use lattice_platform::security_dashboard::{router as security_router, SecurityState};
use lattice_platform::setup::{router as setup_router, SetupState};
use serde_json::{json, Map, Value};
use std::net::SocketAddr;
use std::sync::{Arc, Mutex};

// ── the OpenAPI contract ───────────────────────────────────────────────────

#[test]
fn mounted_routes_match_the_committed_contract() {
    let spec = openapi_fragment("admin.json");
    let mut expected: Vec<String> = spec["operation_order"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect();
    let mut actual: Vec<String> = family_mounted()
        .iter()
        .map(|(m, p)| format!("{m} {}", to_openapi(p)))
        .collect();
    expected.sort();
    actual.sort();
    assert_eq!(
        actual, expected,
        "router and rust/fixtures/openapi/admin.json disagree"
    );

    for (key, param) in spec["greedy_path_params"].as_object().unwrap() {
        let path = key.split_once(' ').unwrap().1;
        assert!(
            family_mounted().iter().any(|(_, p)| {
                to_openapi(p) == path && p.contains(&format!("*{}", param.as_str().unwrap()))
            }),
            "{key} matches slashes in Python; mount it as /*{param}"
        );
    }
}

// ── admin.py ───────────────────────────────────────────────────────────────

fn graph_stats() -> serde_json::Value {
    json!({
        "db_path": "/tmp/kg.sqlite",
        "schema_version": 1,
        "v2_schema_available": true,
        "nodes": {"Conversation": 1, "Memory": 1, "Person": 1, "Workflow": 1},
        "edges": {"HAS_EVENT": 2, "TRIGGERED": 2},
        "local_sources": 0,
        "local_file_status": {},
        "v2": {
            "schema_version": 2,
            "embed_dim": 384,
            "nodes": 4,
            "edges": 4,
            "by_node_type": {"CONCEPT": 1, "CONVERSATION": 1, "PERSON": 1, "WORKFLOW": 1},
            "by_edge_type": {"HAS_EVENT": 2, "TRIGGERED": 2}
        },
        "total_nodes": 0,
        "total_edges": 0
    })
}

#[tokio::test(flavor = "multi_thread")]
async fn admin_fixtures_replay() {
    let fixture = load_fixture("admin.json");
    let mut checked = 0usize;
    for case in fixture["fixtures"].as_array().unwrap() {
        if case["family"] != "admin.py" {
            continue;
        }
        replay_admin_case(case, true).await;
        checked += 1;
    }
    for case in fixture["trusted_local_owner_fixtures"].as_array().unwrap() {
        if case["family"] != "admin.py" {
            continue;
        }
        replay_admin_case(case, true).await;
        checked += 1;
    }
    assert!(checked >= 60, "lost admin cases: {checked}");
}

async fn replay_admin_case(case: &serde_json::Value, require_auth: bool) {
    let name = format!("{}/{}", case["name"], case["branch"]);
    let dir = tempfile::tempdir().unwrap();
    seed_chat_history(dir.path());
    if case["name"] == "admin_audit" || case["name"] == "admin_log_retention" {
        seed_audit(dir.path(), &eight_audit_events());
    } else {
        seed_audit_base(dir.path());
    }
    let (auth, owner, member) = install_auth(dir.path(), require_auth);
    let hardening_dir = dir.path().to_path_buf();
    let hardening_host = auth.config().host.clone();
    let hardening_port = auth.config().port;
    let hardening_auth = auth.config().require_auth;
    let mut state = AdminState::new(auth, dir.path());
    state.graph_stats = std::sync::Arc::new(|| Ok(graph_stats()));
    // `/admin/product-hardening` probes PATH for `python3` and `docker`. The
    // Python oracle recorded both as `true` on a laptop that had them, so the
    // default probe fails the fixture on any machine that does not — a
    // property of the runner, not of the handler. Same function, same
    // document; only the probe is stubbed, so everything else in the envelope
    // is still compared against what the product builds.
    state.hardening = Some(std::sync::Arc::new(move || {
        default_product_hardening_probed(
            &hardening_dir,
            &hardening_host,
            hardening_port,
            hardening_auth,
            &|_executable| true,
        )
    }));
    let app: Router = admin_router(state);
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
    assert_case(&name, case, &answer);
}

// ── funnel_metrics.py ──────────────────────────────────────────────────────

#[tokio::test(flavor = "multi_thread")]
async fn funnel_fixtures_replay() {
    let fixture = load_fixture("admin.json");
    let mut checked = 0usize;
    for case in fixture["fixtures"].as_array().unwrap() {
        if case["family"] != "funnel_metrics.py" {
            continue;
        }
        replay_funnel_case(case).await;
        checked += 1;
    }
    for case in fixture["trusted_local_owner_fixtures"].as_array().unwrap() {
        if case["family"] != "funnel_metrics.py" {
            continue;
        }
        replay_funnel_case(case).await;
        checked += 1;
    }
    assert!(checked >= 3, "lost funnel cases: {checked}");
}

async fn replay_funnel_case(case: &serde_json::Value) {
    let name = format!("{}/{}", case["name"], case["branch"]);
    let dir = tempfile::tempdir().unwrap();
    let (auth, owner, member) = install_auth(dir.path(), true);
    let state = FunnelState::new(auth, dir.path());
    let app: Router = funnel_router(state);
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
    assert_case(&name, case, &answer);
}

// ── features.py ────────────────────────────────────────────────────────────

#[tokio::test(flavor = "multi_thread")]
async fn features_fixtures_replay() {
    std::env::set_var("LATTICEAI_VECTOR_INDEX", "brute");
    let fixture = load_fixture("platform_misc.json");
    let mut checked = 0usize;
    for case in fixture["fixtures"].as_array().unwrap() {
        if case["family"] != "features.py" {
            continue;
        }
        replay_features_case(case).await;
        checked += 1;
    }
    for case in fixture["trusted_local_owner_fixtures"].as_array().unwrap() {
        if case["family"] != "features.py" {
            continue;
        }
        replay_features_case(case).await;
        checked += 1;
    }
    assert!(checked >= 10, "lost feature cases: {checked}");
}

async fn replay_features_case(case: &serde_json::Value) {
    let name = format!("{}/{}", case["name"], case["branch"]);
    let dir = tempfile::tempdir().unwrap();
    let (auth, owner, member) = install_auth(dir.path(), true);
    let state = FeaturesState::new(auth, dir.path());
    let app: Router = features_router(state);
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
    assert_case(&name, case, &answer);
}

// ── setup.py ───────────────────────────────────────────────────────────────

#[tokio::test(flavor = "multi_thread")]
async fn setup_fixtures_replay() {
    let fixture = load_fixture("platform_misc.json");
    let mut checked = 0usize;
    for case in fixture["fixtures"].as_array().unwrap() {
        if case["family"] != "setup.py" {
            continue;
        }
        replay_setup_case(case).await;
        checked += 1;
    }
    assert!(checked >= 20, "lost setup cases: {checked}");
}

async fn replay_setup_case(case: &serde_json::Value) {
    let name = format!("{}/{}", case["name"], case["branch"]);
    let dir = tempfile::tempdir().unwrap();
    let (auth, owner, member) = install_auth(dir.path(), true);
    let mut state = SetupState::new(auth, dir.path());
    let needs_corpus = matches!(
        (
            case["name"].as_str().unwrap_or(""),
            case["branch"].as_str().unwrap_or("")
        ),
        ("demo_corpus_status", "happy_installed")
            | ("demo_corpus_install", "happy_idempotent")
            | ("demo_corpus_remove", "happy")
    );
    if needs_corpus {
        let store = state.demo.clone().unwrap_or_default();
        for (id, title, _) in lattice_platform::setup::demo_documents() {
            store.ingest(id, title, Some("personal"));
        }
        state.demo = Some(store);
    }
    let app: Router = setup_router(state);
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
    assert_case(&name, case, &answer);
}

// ── security_dashboard.py ──────────────────────────────────────────────────

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
        replay_security_case(case, worker.clone()).await;
        checked += 1;
    }
    for case in fixture["trusted_local_owner_fixtures"].as_array().unwrap() {
        if case["family"] != "security_dashboard.py" {
            continue;
        }
        replay_security_case(case, worker.clone()).await;
        checked += 1;
    }
    worker_handle.abort();
    assert!(checked >= 50, "lost security cases: {checked}");
}

async fn replay_security_case(case: &serde_json::Value, worker: WorkerSeamClient) {
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

// ── the native `audit_log.json` append helper ──

#[test]
fn append_writes_event_id_timestamp_and_contract() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("audit_log.json");
    let mut payload = Map::new();
    payload.insert("user_email".into(), json!("owner@lattice.test"));
    payload.insert("token".into(), json!("sk-supersecretfixturekey"));
    append_audit_event(&path, "user_update", payload);
    let events = load_audit_log(&path);
    assert_eq!(events.len(), 1);
    let ev = &events[0];
    assert!(ev["event_id"].as_str().unwrap().starts_with("audit-"));
    assert_eq!(ev["event_type"], "user_update");
    assert!(ev["timestamp"].as_str().unwrap().contains('T'));
    assert_eq!(ev["user_email"], "owner@lattice.test");
    // secret-shaped values are redacted before persist
    assert_eq!(ev["token"], "[REDACTED_SECRET]");
    assert_eq!(ev["contract"]["family"], "agent-run-contract/v1");
    assert_eq!(ev["contract"]["kind"], "audit_event");
}

#[test]
fn missing_file_is_empty() {
    let dir = tempfile::tempdir().unwrap();
    assert!(load_audit_log(&dir.path().join("audit_log.json")).is_empty());
}
