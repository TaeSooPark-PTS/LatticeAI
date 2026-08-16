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
use serde_json::{json, Map, Value};

use crate::admin::{json_ok, language_from, message_error, workspace_from_headers};

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
    #[allow(dead_code)]
    pub data_dir: PathBuf,
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
            opener: Arc::new(|_| {}),
        }
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

fn sse_frame(entries: &[(&str, Value)]) -> String {
    format!("data: {}\n\n", dumps_spaced(entries))
}

fn scan_environment() -> OrderedMap {
    let mut env = OrderedMap::new();
    env.insert("os", json!(std::env::consts::OS));
    env.insert("os_version", json!(std::env::consts::FAMILY));
    env.insert("chip", json!(std::env::consts::ARCH));
    env.insert("cpu", json!(std::env::consts::ARCH));
    env.insert("gpu", json!("unknown"));
    env.insert("cuda", json!(false));
    env.insert("wsl", json!(false));
    env.insert("ram_gb", json!(0));
    env.insert("disk_free_gb", json!(0));
    env.insert("tools", json!({}));
    env.insert("components", json!({}));
    env.insert("path", json!(std::env::var("PATH").unwrap_or_default()));
    env.insert("mlx", json!(false));
    env.insert("api_keys", json!({}));
    env
}

fn auto_state() -> OrderedMap {
    let mut probe = OrderedMap::new();
    probe.insert("os", json!(std::env::consts::OS));
    probe.insert("os_version", json!(std::env::consts::FAMILY));
    probe.insert("arch", json!(std::env::consts::ARCH));
    probe.insert("cpu_model", json!(std::env::consts::ARCH));
    probe.insert("cpu_cores", json!(1));
    probe.insert("cpu_logical_cores", json!(1));
    probe.insert("cpu_instructions", json!([]));
    probe.insert("ram_mb", json!(0));
    probe.insert("disk_free_mb", json!(0));
    probe.insert("gpu", json!("unknown"));
    probe.insert("package_manager", json!("none"));
    probe.insert("has_internet", json!(false));
    probe.insert("python_version", json!(env!("CARGO_PKG_VERSION")));
    probe.insert("is_wsl", json!(false));
    probe.insert("wsl_version", json!(Value::Null));
    probe.insert("cuda_available", json!(false));
    probe.insert("cuda_version", json!(Value::Null));
    probe.insert("tools", json!({}));
    probe.insert("score", json!(0));

    let mut recommend = OrderedMap::new();
    recommend.insert("runtime", json!("none"));
    recommend.insert("backend", json!("none"));
    recommend.insert("model_id", json!(""));
    recommend.insert("quantization", json!(""));
    recommend.insert("rationale", json!([]));
    recommend.insert("estimated_tokens_per_sec", json!(0));

    let mut plan = OrderedMap::new();
    plan.insert("package_manager", json!("none"));
    plan.insert("steps", json!([]));
    plan.insert("notes", json!([]));
    plan.insert("command_plan", Value::Null);
    plan.insert("confirmation_token", Value::Null);

    let mut verify = OrderedMap::new();
    verify.insert("checks", json!([]));
    verify.insert("all_pass", json!(true));

    let mut model = OrderedMap::new();
    model.insert("id", json!(""));
    model.insert("runtime", json!("none"));
    let mut preset = OrderedMap::new();
    preset.insert("mode", json!("local"));
    preset.insert("model", crate::admin::json_from_ordered(&model));
    preset.insert("shortcuts", json!([]));
    preset.insert("mcp", json!([]));
    preset.insert("theme", json!("system"));
    preset.insert("language", json!("ko"));
    preset.insert("tips", json!([]));

    let mut out = OrderedMap::new();
    out.insert("probe", crate::admin::json_from_ordered(&probe));
    out.insert("recommend", crate::admin::json_from_ordered(&recommend));
    out.insert("plan", crate::admin::json_from_ordered(&plan));
    out.insert("verify", crate::admin::json_from_ordered(&verify));
    out.insert("preset", crate::admin::json_from_ordered(&preset));
    out
}

fn recommendations() -> OrderedMap {
    let mut recs = OrderedMap::new();
    recs.insert("components", json!([]));
    recs.insert("engines", json!([]));
    recs.insert("models", json!([]));
    recs.insert("mcps", json!([]));
    recs.insert("summary", json!({}));
    recs
}

async fn setup_scan(
    State(state): State<SetupState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    state.auth.require_user(&headers)?;
    let mut env = scan_environment();
    let mut recs = recommendations();
    let zero = auto_state();
    env.insert("zero_config", crate::admin::json_from_ordered(&zero));
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
    Ok(json_ok(&auto_state()))
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
    for item in &items {
        let item_id = item.get("id").and_then(Value::as_str).unwrap_or("unknown");
        let name = item.get("name").and_then(Value::as_str).unwrap_or(item_id);
        let action = item.get("action").and_then(Value::as_object);
        let atype = action.and_then(|a| a.get("type")).and_then(Value::as_str);
        match atype {
            None | Some("") => {
                frames.push_str(&sse_frame(&[
                    ("id", json!(item_id)),
                    ("status", json!("skipped")),
                    ("msg", json!(format!("{name} — 이미 준비됨"))),
                ]));
            }
            Some("auth") | Some("url") => {
                let url = action
                    .and_then(|a| a.get("url"))
                    .and_then(Value::as_str)
                    .unwrap_or("");
                (state.opener)(url);
                frames.push_str(&sse_frame(&[
                    ("id", json!(item_id)),
                    ("status", json!("auth")),
                    ("msg", json!("브라우저에서 인증 페이지를 엽니다...")),
                    ("auth_url", json!(url)),
                ]));
                frames.push_str(&sse_frame(&[
                    ("id", json!(item_id)),
                    ("status", json!("waiting")),
                    ("msg", json!("브라우저에서 인증 완료 후 계속하세요")),
                ]));
            }
            Some(other) => {
                frames.push_str(&sse_frame(&[
                    ("id", json!(item_id)),
                    ("status", json!("starting")),
                    ("msg", json!(format!("{name} 준비 중..."))),
                ]));
                frames.push_str(&sse_frame(&[
                    ("id", json!(item_id)),
                    ("status", json!("error")),
                    ("msg", json!(format!("알 수 없는 액션: {other}"))),
                ]));
            }
        }
    }
    frames.push_str(&sse_frame(&[
        ("status", json!("complete")),
        ("msg", json!("모든 항목 처리 완료!")),
    ]));
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
