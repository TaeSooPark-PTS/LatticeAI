//! Setup wizard + demo corpus — native (v11.6.0, WP-R2).
//!
//! Port of `latticeai/api/setup.py`. `/setup/scan` is client-critical
//! onboarding; `/setup/install` is SSE with **no Accept negotiation**. Demo
//! corpus deletes are native: `GraphWriter::delete_document_tree`.
//!
//! ## Structure
//!
//! Four files along one seam — *look*, *act*, *seed*, *route*:
//!
//! | module | what it owns |
//! |---|---|
//! | [`scan`] | reading the host: the environment block, zero-config state, recommendations, the install plan, the verification checks |
//! | `install` | performing a consented install step, and the SSE frames it emits |
//! | [`demo`] | the three demo documents, their questions, and their exact removal |
//! | this file | [`SetupState`], the router, and the eight handlers |
//!
//! The split is by *effect*, not by size: [`scan`] answers questions and
//! `install` changes the machine, so a change to what onboarding *reports* can
//! never accidentally change what it *runs*.

use std::collections::HashSet;

use axum::extract::{Path, State};
use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_auth::pyjson::dumps_spaced;
use lattice_auth::{AuthState, OrderedMap};
use lattice_core::worker::WorkerSeamClient;
use serde_json::{json, Value};
use std::path::PathBuf;
use std::sync::Arc;

use crate::adminops::admin;
use crate::adminops::admin::{json_ok, language_from, message_error};
use crate::modelops::models_catalog::{fetch_worker_catalog, probe_host};

mod demo;
mod install;
mod scan;

pub use demo::{
    demo_documents, suggested_questions, DemoStore, IngestResult, DEMO_METADATA_FLAG,
    DEMO_URI_PREFIX,
};
pub(crate) use scan::scan_environment;

use demo::{demo_install, demo_remove, demo_status};
use scan::{auto_state, recommendations_from_zero};

pub const MOUNTED: &[(&str, &str)] = &[
    ("DELETE", "/api/setup/demo-corpus"),
    ("GET", "/api/setup/demo-corpus"),
    ("POST", "/api/setup/demo-corpus"),
    ("POST", "/permissions/open/:permission_id"),
    ("GET", "/setup/auto"),
    ("POST", "/setup/install"),
    ("POST", "/setup/open-auth/:mcp_id"),
    ("GET", "/setup/scan"),
];

const AUTH_URLS: &[(&str, &str)] = &[
    ("github", "https://github.com/apps"),
    ("google-drive", "https://chatgpt.com/connectors"),
    ("slack", "https://chatgpt.com/connectors"),
    ("chrome", "https://chatgpt.com/connectors"),
    ("computer-use", "https://chatgpt.com/connectors"),
    ("figma", "https://chatgpt.com/connectors"),
    ("notion", "https://chatgpt.com/connectors"),
    ("linear", "https://chatgpt.com/connectors"),
    ("gmail", "https://chatgpt.com/connectors"),
    ("google-calendar", "https://chatgpt.com/connectors"),
    ("outlook-email", "https://chatgpt.com/connectors"),
    ("outlook-calendar", "https://chatgpt.com/connectors"),
    ("teams", "https://chatgpt.com/connectors"),
    ("sharepoint", "https://chatgpt.com/connectors"),
    ("canva", "https://chatgpt.com/connectors"),
];

const PERMISSION_URLS: &[(&str, &str)] = &[
    (
        "accessibility",
        "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    ),
    (
        "automation",
        "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation",
    ),
    (
        "screen",
        "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
    ),
];

#[derive(Clone)]
pub struct SetupState {
    pub auth: Arc<AuthState>,
    pub demo: Option<DemoStore>,
    pub graph: Option<lattice_core::graph_write::GraphWriter>,
    /// When false, demo routes 503 (`capture.ingestion_disabled`).
    pub pipeline_available: bool,
    pub data_dir: PathBuf,
    /// Worker seam for `/models`, `/worker/sysinfo`, prepare-model.
    pub worker: Option<WorkerSeamClient>,
    /// `open_url` is a no-op by default (tests / headless).
    pub opener: Arc<dyn Fn(&str) + Send + Sync>,
    /// Test seam for consented brew/pip/uv execution. `None` runs the
    /// process. The production path never takes a command from the client.
    pub runner: Option<install::InstallRunner>,
}

impl SetupState {
    pub fn new(auth: Arc<AuthState>, data_dir: impl Into<PathBuf>) -> Self {
        Self {
            auth,
            demo: Some(DemoStore::new()),
            graph: None,
            pipeline_available: true,
            data_dir: data_dir.into(),
            worker: None,
            opener: Arc::new(|_| {}),
            runner: None,
        }
    }

    /// Point setup probes and installable model actions at this worker.
    pub fn with_worker(mut self, worker: WorkerSeamClient) -> Self {
        self.worker = Some(worker);
        self
    }

    /// Replace the process runner (tests).
    pub fn with_runner(mut self, runner: install::InstallRunner) -> Self {
        self.runner = Some(runner);
        self
    }
}

impl axum::extract::FromRef<SetupState> for Arc<AuthState> {
    fn from_ref(s: &SetupState) -> Self {
        Arc::clone(&s.auth)
    }
}

pub fn router(state: SetupState) -> Router {
    Router::new()
        .route("/setup/scan", get(setup_scan))
        .route("/setup/auto", get(setup_auto))
        .route("/setup/install", post(setup_install))
        .route("/setup/open-auth/:mcp_id", post(setup_open_auth))
        .route(
            "/api/setup/demo-corpus",
            get(demo_status).post(demo_install).delete(demo_remove),
        )
        .route("/permissions/open/:permission_id", post(open_permission))
        .with_state(state)
}

pub(crate) fn sse_frame(entries: &[(&str, Value)]) -> String {
    format!("data: {}\n\n", dumps_spaced(entries))
}

async fn setup_scan(
    State(state): State<SetupState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    state.auth.require_user(&headers)?;
    let probe = probe_host(Some(&state.data_dir));
    let catalog = fetch_worker_catalog(state.worker.as_ref()).await;
    let zero = auto_state(&probe, &catalog);
    let mut env = scan_environment(&probe, &catalog);
    let recs = recommendations_from_zero(&zero, &catalog);
    env.insert("zero_config", admin::json_from_ordered(&zero));
    let mut out = OrderedMap::new();
    out.insert("environment", admin::json_from_ordered(&env));
    out.insert("recommendations", admin::json_from_ordered(&recs));
    out.insert("zero_config", admin::json_from_ordered(&zero));
    Ok(json_ok(&out))
}

async fn setup_auto(
    State(state): State<SetupState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    state.auth.require_user(&headers)?;
    let probe = probe_host(Some(&state.data_dir));
    let catalog = fetch_worker_catalog(state.worker.as_ref()).await;
    Ok(json_ok(&auto_state(&probe, &catalog)))
}

async fn setup_install(
    State(state): State<SetupState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Response, Response> {
    state.auth.require_user(&headers)?;
    let parsed = if body.is_empty() {
        json!({})
    } else {
        serde_json::from_slice(&body).unwrap_or(json!({}))
    };
    let items = parsed
        .get("items")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let execute: HashSet<String> = parsed
        .get("execute")
        .and_then(Value::as_array)
        .map(|rows| {
            rows.iter()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default();
    let mut frames = String::new();
    let mut done = 0u64;
    let mut manual = 0u64;
    let mut failed = 0u64;
    let mut seen: HashSet<String> = HashSet::new();
    for item in &items {
        if let Some(id) = item.get("id").and_then(Value::as_str) {
            seen.insert(id.to_string());
        }
        let outcome = install::execute_install_item(&state, item, &execute).await;
        match outcome.status.as_str() {
            "done" | "skipped" | "auth" | "waiting" => done += 1,
            "manual" => manual += 1,
            _ => failed += 1,
        }
        for frame in outcome.frames {
            frames.push_str(&sse_frame(&frame));
        }
    }
    for unknown in execute.iter().filter(|id| !seen.contains(*id)) {
        let outcome = install::refused_unknown_id(unknown);
        failed += 1;
        for frame in outcome.frames {
            frames.push_str(&sse_frame(&frame));
        }
    }
    if items.is_empty() && execute.is_empty() {
        frames.push_str(&sse_frame(&[
            ("status", json!("complete")),
            ("msg", json!("모든 항목 처리 완료!")),
        ]));
    } else {
        frames.push_str(&sse_frame(&[
            ("status", json!("complete")),
            (
                "msg",
                json!(format!("done={done}, manual={manual}, failed={failed}")),
            ),
            ("done", json!(done)),
            ("manual", json!(manual)),
            ("failed", json!(failed)),
        ]));
    }
    Ok(sse_response(frames))
}

fn sse_response(body: String) -> Response {
    Response::builder()
        .status(StatusCode::OK)
        .header(
            header::CONTENT_TYPE,
            HeaderValue::from_static("text/event-stream; charset=utf-8"),
        )
        .header(header::CACHE_CONTROL, HeaderValue::from_static("no-cache"))
        .header("x-accel-buffering", HeaderValue::from_static("no"))
        .body(axum::body::Body::from(body))
        .unwrap_or_else(|_| Response::new(axum::body::Body::empty()))
}

async fn setup_open_auth(
    State(state): State<SetupState>,
    Path(mcp_id): Path<String>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    state.auth.require_user(&headers)?;
    let Some((_, url)) = AUTH_URLS.iter().find(|(id, _)| *id == mcp_id) else {
        return Err(message_error(
            404,
            "mcp.unknown_id",
            language_from(&headers),
            &[("mcp_id", mcp_id.as_str())],
        ));
    };
    (state.opener)(url);
    let mut out = OrderedMap::new();
    out.insert("status", json!("ok"));
    out.insert("opened", json!(*url));
    out.insert("mcp_id", json!(mcp_id));
    Ok(json_ok(&out))
}

async fn open_permission(
    State(state): State<SetupState>,
    Path(permission_id): Path<String>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    state.auth.require_user(&headers)?;
    let Some((_, url)) = PERMISSION_URLS.iter().find(|(id, _)| *id == permission_id) else {
        return Err(message_error(
            404,
            "setup.unknown_permission",
            language_from(&headers),
            &[],
        ));
    };
    (state.opener)(url);
    let mut out = OrderedMap::new();
    out.insert("status", json!("ok"));
    out.insert("opened", json!(*url));
    out.insert("permission", json!(permission_id));
    Ok(json_ok(&out))
}
