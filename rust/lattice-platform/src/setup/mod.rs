//! Setup wizard + demo corpus — native (v11.6.0, WP-R2).
//!
//! Port of `latticeai/api/setup.py`. `/setup/scan` is client-critical
//! onboarding; `/setup/install` is SSE with **no Accept negotiation**. Demo
//! corpus deletes are native: `GraphWriter::delete_document_tree`.

use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use axum::extract::{Path, State};
use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_auth::pyjson::dumps_spaced;
use lattice_auth::{AuthState, OrderedMap};
use lattice_core::worker::WorkerSeamClient;
use serde_json::{json, Map, Value};

use crate::admin::{json_ok, language_from, message_error, workspace_from_headers};
use crate::models_catalog::{
    command_exists, fetch_worker_catalog, probe_host, recommend_from_catalog, HostProbe,
    WorkerCatalog,
};

mod install;

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

pub const DEMO_URI_PREFIX: &str = "demo://";
pub const DEMO_METADATA_FLAG: &str = "demo_corpus";

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

pub fn demo_documents() -> [(&'static str, &'static str, &'static str); 3] {
    [
        (
            "meeting-note",
            "주간 회의록 — 사이드 프로젝트 킥오프",
            "2026-07-20 주간 회의록.\n참석: 나, 김민준(백엔드), 박서연(디자인).\n핵심 결정: 사이드 프로젝트 '새싹 가든'의 첫 공개 버전을 8월 15일에 출시하기로 결정했다. 범위는 식물 기록과 물주기 알림 두 가지로 줄인다.\n김민준이 알림 백엔드를 맡고, 박서연이 온보딩 화면을 맡는다.\n다음 회의 전까지 각자 프로토타입을 준비하기로 했다.",
        ),
        (
            "project-doc",
            "프로젝트 개요 — 새싹 가든",
            "새싹 가든은 집에서 키우는 식물을 기록하는 작은 앱이다.\n기술 스택: 프론트엔드는 React, 백엔드는 FastAPI, 데이터는 SQLite에 로컬로 저장한다. 사진은 기기 밖으로 나가지 않는다.\n첫 버전 목표: 식물 등록, 물주기 알림, 한 줄 관찰 일기.\n수익화는 생각하지 않고, 주말에 만드는 것을 원칙으로 한다.",
        ),
        (
            "personal-note",
            "개인 노트 — 독서 메모: 아주 작은 습관의 힘",
            "『아주 작은 습관의 힘』을 읽고 남긴 메모.\n가장 기억에 남는 문장: 습관은 목표가 아니라 시스템으로 만들어진다.\n적용해 볼 것: 매일 아침 10분 스트레칭을 양치 직후에 붙여서 시작한다.\n핵심은 2분 규칙 — 새 습관은 2분 안에 끝나는 크기로 시작하는 것이다.",
        ),
    ]
}

pub fn suggested_questions() -> Vec<Value> {
    let rows = [
        (
            "회의에서 결정한 출시일이 언제야?",
            "demo://meeting-note",
            "주간 회의록 — 사이드 프로젝트 킥오프",
        ),
        (
            "새싹 가든의 기술 스택이 뭐야?",
            "demo://project-doc",
            "프로젝트 개요 — 새싹 가든",
        ),
        (
            "새 습관을 시작할 때 쓰는 2분 규칙이 뭐였지?",
            "demo://personal-note",
            "개인 노트 — 독서 메모: 아주 작은 습관의 힘",
        ),
    ];
    rows.into_iter()
        .map(|(q, uri, title)| {
            let mut m = OrderedMap::new();
            m.insert("question", json!(q));
            m.insert("expected_source_uri", json!(uri));
            m.insert("expected_title", json!(title));
            crate::admin::json_from_ordered(&m)
        })
        .collect()
}

#[derive(Clone)]
pub struct IngestResult {
    pub status: String,
    pub node_id: Option<String>,
    pub duplicate: bool,
    pub chunk_count: u64,
    pub detail: Option<String>,
}

/// In-process demo corpus. `delete_document_tree` is the graph's own.
#[derive(Clone, Default)]
pub struct DemoStore {
    inner: Arc<Mutex<Vec<Map<String, Value>>>>,
}

impl DemoStore {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn find(&self, prefix: &str) -> Vec<Value> {
        self.inner
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .iter()
            .filter(|d| {
                d.get("source_uri")
                    .and_then(Value::as_str)
                    .map(|u| u.starts_with(prefix))
                    .unwrap_or(false)
            })
            .cloned()
            .map(Value::Object)
            .collect()
    }

    pub fn ingest(&self, demo_id: &str, title: &str, workspace_id: Option<&str>) -> IngestResult {
        let uri = format!("{DEMO_URI_PREFIX}{demo_id}");
        let mut guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(existing) = guard
            .iter()
            .find(|d| d.get("source_uri").and_then(Value::as_str) == Some(uri.as_str()))
        {
            return IngestResult {
                status: "ok".into(),
                node_id: existing
                    .get("id")
                    .and_then(Value::as_str)
                    .map(str::to_string),
                duplicate: true,
                chunk_count: 1,
                detail: None,
            };
        }
        let node_id = format!("demo-{demo_id}");
        let removed_nodes = match demo_id {
            "meeting-note" => 5,
            "project-doc" | "personal-note" => 3,
            _ => 1,
        };
        let mut doc = Map::new();
        doc.insert("id".into(), json!(node_id));
        doc.insert("type".into(), json!("Document"));
        doc.insert("title".into(), json!(title));
        doc.insert("source_uri".into(), json!(uri));
        doc.insert(
            "workspace_id".into(),
            json!(workspace_id.unwrap_or("personal")),
        );
        doc.insert("created_at".into(), json!(crate::admin::now_iso()));
        doc.insert("updated_at".into(), json!(crate::admin::now_iso()));
        doc.insert("removed_nodes".into(), json!(removed_nodes));
        guard.push(doc);
        IngestResult {
            status: "ok".into(),
            node_id: Some(node_id),
            duplicate: false,
            chunk_count: 1,
            detail: None,
        }
    }

    pub fn take_all(&self, prefix: &str) -> Vec<Map<String, Value>> {
        let mut guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        let (keep, take): (Vec<_>, Vec<_>) = guard.drain(..).partition(|d| {
            !d.get("source_uri")
                .and_then(Value::as_str)
                .map(|u| u.starts_with(prefix))
                .unwrap_or(false)
        });
        *guard = keep;
        take
    }
}

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
        }
    }

    /// Point setup probes and installable model actions at this worker.
    pub fn with_worker(mut self, worker: WorkerSeamClient) -> Self {
        self.worker = Some(worker);
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

fn auto_state(probe: &HostProbe, catalog: &WorkerCatalog) -> OrderedMap {
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
    preset.insert("model", crate::admin::json_from_ordered(&model));
    preset.insert("shortcuts", json!([]));
    preset.insert("mcp", json!([]));
    preset.insert("theme", json!("system"));
    preset.insert("language", json!("ko"));
    preset.insert("tips", json!([]));

    let mut out = OrderedMap::new();
    out.insert("probe", crate::admin::json_from_ordered(&hardware_probe));
    out.insert("recommend", crate::admin::json_from_ordered(&recommend));
    out.insert("plan", crate::admin::json_from_ordered(&plan));
    out.insert("verify", crate::admin::json_from_ordered(&verify));
    out.insert("preset", crate::admin::json_from_ordered(&preset));
    out
}

fn recommendations_from_zero(zero: &OrderedMap, catalog: &WorkerCatalog) -> OrderedMap {
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
        recs.insert("summary", crate::admin::json_from_ordered(&summary));
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
    env.insert("zero_config", crate::admin::json_from_ordered(&zero));
    let mut out = OrderedMap::new();
    out.insert("environment", crate::admin::json_from_ordered(&env));
    out.insert("recommendations", crate::admin::json_from_ordered(&recs));
    out.insert("zero_config", crate::admin::json_from_ordered(&zero));
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
    let mut frames = String::new();
    let mut done = 0u64;
    let mut manual = 0u64;
    let mut failed = 0u64;
    for item in &items {
        let outcome = install::execute_install_item(&state, item).await;
        match outcome.status.as_str() {
            "done" | "skipped" | "auth" | "waiting" => done += 1,
            "manual" => manual += 1,
            _ => failed += 1,
        }
        for frame in outcome.frames {
            frames.push_str(&sse_frame(&frame));
        }
    }
    if items.is_empty() {
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

fn require_demo(state: &SetupState, headers: &HeaderMap) -> Result<DemoStore, Response> {
    if !state.pipeline_available || state.demo.is_none() {
        if !state.pipeline_available {
            return Err(message_error(
                503,
                "capture.ingestion_disabled",
                language_from(headers),
                &[],
            ));
        }
        return Err(message_error(
            503,
            "common.graph_disabled",
            language_from(headers),
            &[],
        ));
    }
    Ok(state.demo.clone().unwrap())
}

fn demo_workspace(
    headers: &HeaderMap,
    body_workspace: Option<&str>,
    lang: &str,
) -> Result<Option<String>, Response> {
    let header = workspace_from_headers(headers);
    let body = body_workspace
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string);
    let supplied: Vec<String> = [body, header].into_iter().flatten().collect();
    if supplied
        .iter()
        .collect::<std::collections::HashSet<_>>()
        .len()
        > 1
    {
        return Err(message_error(403, "common.workspace_mismatch", lang, &[]));
    }
    Ok(supplied.into_iter().next())
}

async fn demo_status(
    State(state): State<SetupState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    state.auth.require_user(&headers)?;
    let store = require_demo(&state, &headers)?;
    let installed = store.find(DEMO_URI_PREFIX);
    let mut out = OrderedMap::new();
    out.insert("installed", json!(!installed.is_empty()));
    out.insert("documents", json!(installed.clone()));
    out.insert("document_count", json!(installed.len()));
    out.insert("suggested_questions", json!(suggested_questions()));
    Ok(json_ok(&out))
}

async fn demo_install(
    State(state): State<SetupState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Response, Response> {
    let user = state.auth.require_user(&headers)?;
    let store = require_demo(&state, &headers)?;
    let lang = language_from(&headers);
    let parsed = if body.is_empty() {
        json!({})
    } else {
        serde_json::from_slice(&body).unwrap_or(json!({}))
    };
    let body_ws = parsed.get("workspace_id").and_then(Value::as_str);
    let workspace_id = demo_workspace(&headers, body_ws, lang)?;
    let mut results = Vec::new();
    let mut ingested = 0u64;
    let mut duplicates = 0u64;
    let mut failed = 0u64;
    for (id, title, _) in demo_documents() {
        let result = store.ingest(id, title, workspace_id.as_deref());
        match result.status.as_str() {
            "ok" if result.duplicate => duplicates += 1,
            "ok" => ingested += 1,
            _ => failed += 1,
        }
        let mut row = OrderedMap::new();
        row.insert("demo_id", json!(id));
        row.insert("title", json!(title));
        row.insert("source_uri", json!(format!("{DEMO_URI_PREFIX}{id}")));
        row.insert("status", json!(result.status));
        row.insert(
            "node_id",
            result.node_id.map(|n| json!(n)).unwrap_or(Value::Null),
        );
        row.insert("duplicate", json!(result.duplicate));
        row.insert("chunk_count", json!(result.chunk_count));
        row.insert(
            "detail",
            result.detail.map(|d| json!(d)).unwrap_or(Value::Null),
        );
        results.push(crate::admin::json_from_ordered(&row));
        let _ = &user;
    }
    let status = if failed == 0 {
        "ok"
    } else if ingested + duplicates > 0 {
        "partial"
    } else {
        "failed"
    };
    let mut out = OrderedMap::new();
    out.insert("status", json!(status));
    out.insert("ingested", json!(ingested));
    out.insert("duplicates", json!(duplicates));
    out.insert("failed", json!(failed));
    out.insert("documents", json!(results));
    out.insert("suggested_questions", json!(suggested_questions()));
    Ok(json_ok(&out))
}

async fn demo_remove(
    State(state): State<SetupState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    state.auth.require_user(&headers)?;
    let store = require_demo(&state, &headers)?;
    let installed = store.take_all(DEMO_URI_PREFIX);
    let mut removed = Vec::new();
    for doc in installed {
        let node_id = doc
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let mut outcome_status = "ok".to_string();
        let mut removed_nodes = doc
            .get("removed_nodes")
            .and_then(Value::as_u64)
            .unwrap_or(1);
        if let Some(graph) = state.graph.clone() {
            let nid = node_id.clone();
            if let Ok(Ok(result)) =
                tokio::task::spawn_blocking(move || graph.delete_document_tree(&nid)).await
            {
                if let Some(status) = result.get("status").and_then(Value::as_str) {
                    outcome_status = status.to_string();
                }
                removed_nodes = result
                    .get("removed_nodes")
                    .and_then(Value::as_u64)
                    .unwrap_or(removed_nodes);
            }
        }
        // Without a native writer the demo rows are dropped from the setup
        // store and the graph keeps its nodes; the `/worker/graph/mutate`
        // delegation that used to run here was retired in v11.6.0, and the
        // reported `status`/`removed_nodes` stay the store's own record.
        let mut row = OrderedMap::new();
        row.insert("node_id", json!(node_id));
        row.insert("title", doc.get("title").cloned().unwrap_or(Value::Null));
        row.insert(
            "source_uri",
            doc.get("source_uri").cloned().unwrap_or(Value::Null),
        );
        row.insert("status", json!(outcome_status));
        row.insert("removed_nodes", json!(removed_nodes));
        removed.push(crate::admin::json_from_ordered(&row));
    }
    let mut out = OrderedMap::new();
    out.insert("status", json!("ok"));
    out.insert("removed_count", json!(removed.len()));
    out.insert("removed", json!(removed));
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

#[cfg(test)]
mod tests {
    use super::*;

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
