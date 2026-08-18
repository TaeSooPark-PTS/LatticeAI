//! Multi-agent runtime + single-agent HTTP loop (v11.6.0, WP-R8).
//!
//! `/agents/api/*` is the product AgentRuntime surface. `/agent`,
//! `/agent/resume` and `/agent/approvals` reuse `lattice-agent` types
//! (`LoopConfig`, `Workspace`, `AgentRunStore`) and speak the original
//! Python paths and request/response protocol.

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use axum::extract::{Path as AxumPath, Query, State};
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_agent::agentloop::{default_file_create_actions, Runtime};
use lattice_agent::runbody::{finish_payload, RunBody};
use lattice_agent::sandbox::Workspace;
use lattice_agent::state::AgentRunContext;
use lattice_agent::worker::WorkerClient;
use lattice_agent::{loop_router, LoopConfig};
use lattice_auth::response::json_response;
use lattice_auth::{AuthState, OrderedMap};
use lattice_core::worker::WorkerSeamClient;
use serde_json::{json, Value};

use crate::toolsurface::mcp::{
    detail, json_status, missing_fields, parse_json_object, requested_scope, require_user,
    PlatformStore,
};

mod eval;

/// Contract table for `mcp_market.json` (excludes GET /agents — I4/ui_redirects,
/// and the /agent loop — chat family).
///
/// `GET /agents` is not merely absent from this table, it is no longer mounted
/// by this router at all. It is a 308 into the SPA's hash route, it is filed
/// under `ui_redirects.json` in the committed contract, and `ui_redirects`
/// serves it with the query string preserved the way `app_redirect(request)`
/// does — which the copy here did not. Two owners for one path is an axum panic
/// at boot, so the gateway integration picked the one the contract names.
pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/agents/api/runtime/status"),
    ("GET", "/agents/api/runtime/health"),
    ("GET", "/agents/api/runtime/config"),
    ("GET", "/agents/api/runs/:run_id/events"),
    ("POST", "/agents/api/runs/:run_id/stop"),
    ("GET", "/agents/api/roles"),
    ("GET", "/agents/api/runs"),
    ("GET", "/agents/api/handoffs"),
    ("GET", "/agents/api/runs/:run_id"),
    ("GET", "/agents/api/runs/:run_id/replay"),
    ("GET", "/agents/api/memory/snapshots"),
    ("POST", "/agents/api/memory/snapshots"),
    ("POST", "/agents/api/run"),
    ("POST", "/agents/api/run/preview"),
];

/// Extra routes this module serves that live in the chat OpenAPI fragment.
pub const AGENT_LOOP_MOUNTED: &[(&str, &str)] = &[
    ("POST", "/agent"),
    ("POST", "/agent/resume"),
    ("GET", "/agent/approvals"),
    ("POST", "/agent/eval"),
];

const AGENT_ROLES: &[&str] = &["researcher", "planner", "executor", "reviewer", "release"];
const CORE_PIPELINE: &[&str] = &["planner", "executor", "reviewer"];
const MULTI_AGENT_VERSION: &str = env!("CARGO_PKG_VERSION");
const UNAVAILABLE: &str =
    "No LLM-backed model is loaded; product execution API refuses simulation runs.";

const ROLE_DESCRIPTIONS: &[(&str, &str)] = &[
    (
        "researcher",
        "Gathers workspace context and memory for the goal.",
    ),
    (
        "planner",
        "Decomposes the goal into an ordered, bounded plan.",
    ),
    (
        "executor",
        "Executes each planned step, invoking tools and workflows.",
    ),
    (
        "reviewer",
        "Reviews the executed work and approves, rejects, or retries.",
    ),
    ("release", "Finalizes and summarizes the approved outcome."),
];

#[derive(Clone)]
pub struct AgentsState {
    pub auth: Arc<AuthState>,
    pub(crate) store: PlatformStore,
    pub model_loaded: bool,
    pub workspace: Option<Workspace>,
    pub loop_config: Option<LoopConfig>,
    pub skills_root: PathBuf,
    pub worker: Option<WorkerSeamClient>,
    data_dir: PathBuf,
}

impl AgentsState {
    pub fn new(auth: Arc<AuthState>, data_dir: impl AsRef<Path>) -> Self {
        let data_dir = data_dir.as_ref();
        Self {
            auth,
            store: PlatformStore::new(data_dir),
            model_loaded: false,
            workspace: None,
            loop_config: None,
            skills_root: resolve_skills_dir(data_dir),
            worker: None,
            data_dir: data_dir.to_path_buf(),
        }
    }

    pub fn with_agent_loop(mut self, workspace: Workspace, config: LoopConfig) -> Self {
        self.workspace = Some(workspace);
        self.loop_config = Some(config);
        self
    }

    pub fn with_worker(mut self, worker: WorkerSeamClient) -> Self {
        self.worker = Some(worker);
        self
    }

    pub fn with_skills_root(mut self, root: impl Into<PathBuf>) -> Self {
        self.skills_root = root.into();
        self
    }
}

pub fn router(state: AgentsState) -> Router {
    let loop_part = match (state.workspace.clone(), state.loop_config.clone()) {
        (Some(ws), Some(cfg)) => Some(loop_router(ws, cfg)),
        _ => None,
    };
    let mut app = Router::new()
        .route("/agents/api/runtime/status", get(runtime_status))
        .route("/agents/api/runtime/health", get(runtime_health))
        .route("/agents/api/runtime/config", get(runtime_config))
        .route("/agents/api/runs/:run_id/events", get(run_events))
        .route("/agents/api/runs/:run_id/stop", post(run_stop))
        .route("/agents/api/roles", get(agent_roles))
        .route("/agents/api/runs", get(agent_runs))
        .route("/agents/api/handoffs", get(agent_handoffs))
        .route("/agents/api/runs/:run_id", get(run_detail))
        .route("/agents/api/runs/:run_id/replay", get(run_replay))
        .route(
            "/agents/api/memory/snapshots",
            get(list_snapshots).post(create_snapshot),
        )
        .route("/agents/api/run", post(agent_run))
        .route("/agents/api/run/preview", post(agent_run_preview))
        .route("/agent", post(agent_http))
        .route("/agent/resume", post(agent_resume))
        .route("/agent/approvals", get(agent_approvals))
        .route("/agent/eval", post(eval::agent_eval))
        .with_state(state);
    if let Some(loop_r) = loop_part {
        app = app.merge(loop_r);
    }
    app
}

const LOOP_SURFACE: &str = "POST /agents/api/run";

async fn health_body(state: &AgentsState) -> OrderedMap {
    let (ready, detail, current) = loop_readiness(state).await;
    let mut checks = OrderedMap::new();
    checks.insert("run_store", json!({"status": "ok"}));
    if ready {
        checks.insert(
            "orchestrator",
            json!({
                "status": "ok",
                "mode": "live",
                "detail": format!("model loaded; loop surface is {LOOP_SURFACE}"),
                "loop_surface": LOOP_SURFACE,
                "current_model": current,
            }),
        );
    } else {
        checks.insert(
            "orchestrator",
            json!({"status": "unavailable", "mode": "simulation", "detail": detail}),
        );
    }
    let mut h = OrderedMap::new();
    h.insert("status", json!(if ready { "ok" } else { "unavailable" }));
    h.insert("ready", json!(ready));
    if ready {
        h.insert("loop_surface", json!(LOOP_SURFACE));
        h.insert("current_model", json!(current));
    }
    h.insert("checks", serde_json::to_value(&checks).unwrap_or(json!({})));
    h
}

async fn loop_readiness(state: &AgentsState) -> (bool, String, Option<String>) {
    if state.model_loaded {
        let current = probe_current_model(state).await;
        return (true, LOOP_SURFACE.into(), current);
    }
    if let Some(current) = probe_current_model(state).await {
        return (true, LOOP_SURFACE.into(), Some(current));
    }
    if let Some(worker) = &state.worker {
        match worker
            .clone()
            .with_timeout(Duration::from_secs(5))
            .get_json("/models")
            .await
        {
            Ok(_) => (false, UNAVAILABLE.into(), None),
            Err(err) => (false, format!("worker is unreachable: {err}"), None),
        }
    } else {
        (false, UNAVAILABLE.into(), None)
    }
}

async fn probe_current_model(state: &AgentsState) -> Option<String> {
    let worker = state.worker.as_ref()?;
    let body = worker
        .clone()
        .with_timeout(Duration::from_secs(5))
        .get_json("/models")
        .await
        .ok()?;
    if let Some(current) = body.get("current").and_then(Value::as_str) {
        if !current.is_empty() {
            return Some(current.to_string());
        }
    }
    body.get("loaded")
        .and_then(Value::as_array)
        .and_then(|rows| rows.first())
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
}

fn roles_body() -> Vec<OrderedMap> {
    AGENT_ROLES
        .iter()
        .map(|role| {
            let desc = ROLE_DESCRIPTIONS
                .iter()
                .find(|(r, _)| r == role)
                .map(|(_, d)| *d)
                .unwrap_or("");
            let mut m = OrderedMap::new();
            m.insert("role", json!(role));
            m.insert("agent_id", json!(format!("agent:{role}")));
            m.insert("description", json!(desc));
            m.insert(
                "terminal",
                json!(!matches!(
                    *role,
                    "researcher" | "planner" | "executor" | "reviewer"
                )),
            );
            m
        })
        .collect()
}

fn roster() -> Vec<OrderedMap> {
    let order = ["planner", "executor", "reviewer", "researcher", "release"];
    order
        .iter()
        .map(|role| {
            let desc = ROLE_DESCRIPTIONS
                .iter()
                .find(|(r, _)| r == role)
                .map(|(_, d)| *d)
                .unwrap_or("");
            let handoffs: Vec<String> = match *role {
                "planner" => vec!["agent:executor".into()],
                "executor" => vec!["agent:reviewer".into()],
                _ => vec![],
            };
            let mut m = OrderedMap::new();
            m.insert("id", json!(format!("agent:{role}")));
            let mut name = role.to_string();
            if let Some(c) = name.get_mut(0..1) {
                c.make_ascii_uppercase();
            }
            m.insert("name", json!(name));
            m.insert("role", json!(desc));
            m.insert(
                "state",
                json!(if *role == "release" {
                    "idle"
                } else {
                    "available"
                }),
            );
            m.insert("runs", json!(0));
            m.insert("last_status", Value::Null);
            m.insert("last_at", Value::Null);
            m.insert("handoffs", json!(handoffs));
            m
        })
        .collect()
}

fn ground_roles(roles: &[String]) -> Vec<String> {
    let requested: Vec<String> = if roles.is_empty() {
        CORE_PIPELINE.iter().map(|s| (*s).to_string()).collect()
    } else {
        roles.to_vec()
    };
    if requested == ["planner", "executor", "reviewer"] {
        vec![
            "researcher".into(),
            "planner".into(),
            "executor".into(),
            "reviewer".into(),
        ]
    } else {
        requested
    }
}

async fn runtime_status(State(state): State<AgentsState>, headers: HeaderMap) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let listing = state.store.list_agents();
    let runs = listing
        .get("runs")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let health = health_body(&state).await;
    let ready = health
        .get("ready")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let mut runtime = OrderedMap::new();
    runtime.insert("ready", json!(ready));
    runtime.insert("version", json!(MULTI_AGENT_VERSION));
    runtime.insert("execution_mode", json!("async"));
    runtime.insert("mode", json!(if ready { "live" } else { "simulation" }));
    if ready {
        runtime.insert("loop_surface", json!(LOOP_SURFACE));
        if let Some(current) = health.get("current_model") {
            runtime.insert("current_model", current.clone());
        }
    } else {
        runtime.insert(
            "unavailable_reason",
            health
                .get("checks")
                .and_then(|checks| checks.get("orchestrator"))
                .and_then(|orch| orch.get("detail"))
                .cloned()
                .unwrap_or_else(|| json!(UNAVAILABLE)),
        );
    }
    runtime.insert("default_pipeline", json!(CORE_PIPELINE));
    runtime.insert("total_runs", json!(runs.len()));
    runtime.insert("active_runs", json!(0));
    let mut body = OrderedMap::new();
    body.insert(
        "runtime",
        serde_json::to_value(&runtime).unwrap_or(json!({})),
    );
    body.insert("health", serde_json::to_value(&health).unwrap_or(json!({})));
    body.insert(
        "roles",
        json!(roles_body()
            .iter()
            .map(|m| serde_json::to_value(m).unwrap_or(json!({})))
            .collect::<Vec<_>>()),
    );
    body.insert(
        "agents",
        json!(roster()
            .iter()
            .map(|m| serde_json::to_value(m).unwrap_or(json!({})))
            .collect::<Vec<_>>()),
    );
    body.insert("runs", json!(runs.into_iter().take(25).collect::<Vec<_>>()));
    body.insert("contracts", json!([]));
    // Rebuild with ordered serialization
    let text = format!(
        "{{\"runtime\":{},\"health\":{},\"roles\":[{}],\"agents\":[{}],\"runs\":{},\"contracts\":[]}}",
        serde_json::to_string(&runtime).unwrap_or_else(|_| "{}".into()),
        serde_json::to_string(&health).unwrap_or_else(|_| "{}".into()),
        roles_body()
            .iter()
            .map(|m| serde_json::to_string(m).unwrap_or_else(|_| "{}".into()))
            .collect::<Vec<_>>()
            .join(","),
        roster()
            .iter()
            .map(|m| serde_json::to_string(m).unwrap_or_else(|_| "{}".into()))
            .collect::<Vec<_>>()
            .join(","),
        serde_json::to_string(&listing.get("runs").cloned().unwrap_or(json!([])))
            .unwrap_or_else(|_| "[]".into()),
    );
    let _ = body;
    json_response(StatusCode::OK, &text, None)
}

async fn runtime_health(State(state): State<AgentsState>, headers: HeaderMap) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    json_status(StatusCode::OK, &health_body(&state).await)
}

async fn runtime_config(State(state): State<AgentsState>, headers: HeaderMap) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let mut boundary = OrderedMap::new();
    boundary.insert("schema_version", json!("runtime-boundary/v1"));
    boundary.insert("name", json!("AgentRuntime"));
    boundary.insert("runtime", json!("multi_agent"));
    boundary.insert(
        "entrypoint",
        json!("lattice_brain.runtime.agent_runtime.AgentRuntime"),
    );
    boundary.insert("surface", json!("/agents"));
    boundary.insert(
        "owns",
        json!("product agent execution, observability, status, health, events, replay, and stop"),
    );
    boundary.insert("compatibility_aliases", json!([]));
    let mut body = OrderedMap::new();
    body.insert("version", json!(MULTI_AGENT_VERSION));
    body.insert(
        "boundary",
        serde_json::to_value(&boundary).unwrap_or(json!({})),
    );
    body.insert("roles", json!(AGENT_ROLES));
    body.insert("default_pipeline", json!(CORE_PIPELINE));
    body.insert("max_retries_cap", json!(5));
    body.insert("execution_mode", json!("async"));
    body.insert("simulation_runs_allowed", json!(false));
    body.insert(
        "cancellation",
        json!("cooperative; running synchronous model/tool calls finish their current step before a cancelled status is persisted"),
    );
    let text = format!(
        "{{\"version\":\"{MULTI_AGENT_VERSION}\",\"boundary\":{},\"roles\":{},\"default_pipeline\":{},\"max_retries_cap\":5,\"execution_mode\":\"async\",\"simulation_runs_allowed\":false,\"cancellation\":\"cooperative; running synchronous model/tool calls finish their current step before a cancelled status is persisted\"}}",
        serde_json::to_string(&boundary).unwrap_or_else(|_| "{}".into()),
        serde_json::to_string(&AGENT_ROLES).unwrap_or_else(|_| "[]".into()),
        serde_json::to_string(&CORE_PIPELINE).unwrap_or_else(|_| "[]".into()),
    );
    let _ = body;
    json_response(StatusCode::OK, &text, None)
}

async fn agent_roles(State(state): State<AgentsState>, headers: HeaderMap) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let roles: Vec<String> = AGENT_ROLES
        .iter()
        .map(|role| {
            let mut m = OrderedMap::new();
            m.insert("role", json!(role));
            m.insert("agent_id", json!(format!("agent:{role}")));
            serde_json::to_string(&m).unwrap_or_else(|_| "{}".into())
        })
        .collect();
    json_response(
        StatusCode::OK,
        &format!(
            "{{\"roles\":[{}],\"default_pipeline\":{}}}",
            roles.join(","),
            serde_json::to_string(&CORE_PIPELINE).unwrap_or_else(|_| "[]".into())
        ),
        None,
    )
}

async fn agent_runs(State(state): State<AgentsState>, headers: HeaderMap) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let listing = state.store.list_agents();
    let agents = listing.get("agents").cloned().unwrap_or(json!([]));
    let runs = listing.get("runs").cloned().unwrap_or(json!([]));
    json_response(
        StatusCode::OK,
        &format!(
            "{{\"agents\":{},\"runs\":{},\"contracts\":[]}}",
            serde_json::to_string(&agents).unwrap_or_else(|_| "[]".into()),
            serde_json::to_string(&runs).unwrap_or_else(|_| "[]".into()),
        ),
        None,
    )
}

#[derive(Debug, serde::Deserialize, Default)]
struct HandoffQuery {
    run_id: Option<String>,
}

async fn agent_handoffs(
    State(state): State<AgentsState>,
    headers: HeaderMap,
    Query(q): Query<HandoffQuery>,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let listing = state.store.list_handoffs(q.run_id.as_deref());
    json_response(
        StatusCode::OK,
        &serde_json::to_string(&listing).unwrap_or_else(|_| "{\"handoffs\":[]}".into()),
        None,
    )
}

async fn run_detail(
    State(state): State<AgentsState>,
    headers: HeaderMap,
    AxumPath(run_id): AxumPath<String>,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    match state.store.get_agent_run(&run_id) {
        Some(run) => json_response(
            StatusCode::OK,
            &format!(
                "{{\"run\":{}}}",
                serde_json::to_string(&run).unwrap_or_else(|_| "{}".into())
            ),
            None,
        ),
        None => detail(
            StatusCode::NOT_FOUND,
            &format!("Agent run not found: {run_id}"),
        ),
    }
}

async fn run_events(
    State(state): State<AgentsState>,
    headers: HeaderMap,
    AxumPath(run_id): AxumPath<String>,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    if state.store.get_agent_run(&run_id).is_none() {
        return detail(
            StatusCode::NOT_FOUND,
            &format!("Agent run not found: {run_id}"),
        );
    }
    json_response(StatusCode::OK, "{}", None)
}

async fn run_replay(
    State(state): State<AgentsState>,
    headers: HeaderMap,
    AxumPath(run_id): AxumPath<String>,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    if state.store.get_agent_run(&run_id).is_none() {
        return detail(
            StatusCode::NOT_FOUND,
            &format!("Agent run not found: {run_id}"),
        );
    }
    json_response(StatusCode::OK, "{}", None)
}

async fn run_stop(
    State(state): State<AgentsState>,
    headers: HeaderMap,
    AxumPath(run_id): AxumPath<String>,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let mut body = OrderedMap::new();
    body.insert("stopped", json!(false));
    body.insert("reason", json!("run not found"));
    body.insert("run_id", json!(run_id));
    json_status(StatusCode::OK, &body)
}

#[derive(Debug, serde::Deserialize, Default)]
struct SnapQuery {
    limit: Option<usize>,
}

async fn list_snapshots(
    State(state): State<AgentsState>,
    headers: HeaderMap,
    Query(q): Query<SnapQuery>,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let listing = state.store.list_memory_snapshots(q.limit.unwrap_or(50));
    json_response(
        StatusCode::OK,
        &serde_json::to_string(&listing).unwrap_or_else(|_| "{\"snapshots\":[]}".into()),
        None,
    )
}

async fn create_snapshot(
    State(state): State<AgentsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    let identity = match require_user(&state.auth, &headers) {
        Ok(id) => id,
        Err(r) => return r,
    };
    let parsed = if body.is_empty() {
        json!({})
    } else {
        match parse_json_object(&body) {
            Ok(v) => v,
            Err(r) => return r,
        }
    };
    let label = parsed
        .get("label")
        .and_then(Value::as_str)
        .unwrap_or("agent memory snapshot");
    let reason = parsed.get("reason").and_then(Value::as_str).unwrap_or("");
    let ids: Option<Vec<String>> = parsed.get("memory_ids").and_then(Value::as_array).map(|a| {
        a.iter()
            .filter_map(Value::as_str)
            .map(str::to_string)
            .collect()
    });
    let scope = requested_scope(&headers, None);
    let snapshot = state.store.create_memory_snapshot(
        label,
        reason,
        if identity.email.is_empty() {
            None
        } else {
            Some(identity.email.as_str())
        },
        Some(&scope),
        ids.as_deref(),
    );
    json_response(
        StatusCode::OK,
        &format!(
            "{{\"snapshot\":{}}}",
            serde_json::to_string(&snapshot).unwrap_or_else(|_| "{}".into())
        ),
        None,
    )
}

async fn agent_run(
    State(state): State<AgentsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    if !parsed
        .as_object()
        .map(|o| o.contains_key("goal"))
        .unwrap_or(false)
    {
        return missing_fields(&parsed, &["goal"]);
    }
    let goal = parsed
        .get("goal")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim();
    if goal.is_empty() {
        return detail(StatusCode::BAD_REQUEST, "goal is required");
    }
    let (ready, detail_msg, current) = loop_readiness(&state).await;
    if !ready {
        return detail(StatusCode::CONFLICT, &detail_msg);
    }
    run_single_agent_pass(&state, goal, current.as_deref()).await
}

async fn run_single_agent_pass(
    state: &AgentsState,
    goal: &str,
    current_model: Option<&str>,
) -> Response {
    let workspace = match state
        .workspace
        .clone()
        .or_else(|| Workspace::new(state.data_dir.join("agent")).ok())
    {
        Some(ws) => ws,
        None => {
            return detail(
                StatusCode::CONFLICT,
                "agent workspace is not configured; POST /agent is the working loop surface when the host wires LoopConfig",
            );
        }
    };
    let origin = state
        .loop_config
        .as_ref()
        .map(|cfg| cfg.worker_origin.clone())
        .or_else(|| state.worker.as_ref().map(|w| w.origin().to_string()));
    let Some(origin) = origin else {
        return detail(
            StatusCode::CONFLICT,
            "worker origin is not configured; POST /agent is the working loop surface when the host wires LoopConfig",
        );
    };
    let body = RunBody {
        message: goal.to_string(),
        executing_model: current_model.map(str::to_string),
        planning_model: current_model.map(str::to_string),
        reviewing_model: current_model.map(str::to_string),
        source: Some("agents.api.run".into()),
        ..RunBody::default()
    };
    let request = body.to_request();
    let worker = WorkerClient::new(&origin);
    let deps = body.to_deps(worker, workspace.clone());
    let mut runtime = Runtime::new(deps);
    let mut ctx = AgentRunContext::new();
    ctx.executing_model = body.executing_model.clone();
    ctx.reviewing_model = body.reviewing_model.clone();
    ctx.state = lattice_agent::state::AgentState::Planning;
    ctx.state_history.push(ctx.state.as_str().to_string());
    if let Err(err) = runtime.plan(&mut ctx, &request).await {
        return detail(
            StatusCode::BAD_GATEWAY,
            &format!("agent loop could not reach the reasoner: {}", err.0),
        );
    }
    let requirements = runtime.approval_requirements(&ctx, &request);
    if requirements
        .get("requires_approval")
        .and_then(Value::as_bool)
        == Some(true)
    {
        runtime.approve(&mut ctx, &request, false);
    } else {
        runtime.approve(&mut ctx, &request, false);
        if let Err(err) = runtime.run_to_completion(&mut ctx, &request).await {
            return detail(
                StatusCode::BAD_GATEWAY,
                &format!("agent loop could not reach the reasoner: {}", err.0),
            );
        }
    }
    let payload = finish_payload(
        &ctx,
        &body,
        &workspace,
        &default_file_create_actions(),
        &runtime.audit,
    );
    json_response(
        StatusCode::OK,
        &serde_json::to_string(&payload).unwrap_or_else(|_| "{}".into()),
        None,
    )
}

async fn agent_run_preview(
    State(state): State<AgentsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    if !parsed
        .as_object()
        .map(|o| o.contains_key("goal"))
        .unwrap_or(false)
    {
        return missing_fields(&parsed, &["goal"]);
    }
    let goal = parsed
        .get("goal")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim();
    let roles_in: Vec<String> = parsed
        .get("roles")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default();
    let roles = ground_roles(&roles_in);
    let unknown: Vec<String> = roles
        .iter()
        .filter(|r| !AGENT_ROLES.contains(&r.as_str()))
        .cloned()
        .collect();
    let max_retries = parsed
        .get("max_retries")
        .and_then(Value::as_i64)
        .unwrap_or(2);
    let cap = 5;
    let retry = max_retries.clamp(0, cap);
    let mut blocking = Vec::new();
    if goal.is_empty() {
        blocking.push("goal is required");
    }
    if !unknown.is_empty() {
        blocking.push("unknown roles");
    }
    let health = health_body(&state).await;
    let ready = health
        .get("ready")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    if !ready {
        blocking.push(UNAVAILABLE);
    }
    let scope = requested_scope(&headers, None);
    let mut runtime = OrderedMap::new();
    runtime.insert("version", json!(MULTI_AGENT_VERSION));
    runtime.insert("default_pipeline", json!(CORE_PIPELINE));
    runtime.insert("max_retries_cap", json!(cap));
    runtime.insert("simulation_runs_allowed", json!(false));
    let inputs_keys: Vec<String> = parsed
        .get("inputs")
        .and_then(Value::as_object)
        .map(|o| {
            let mut k: Vec<_> = o.keys().cloned().collect();
            k.sort();
            k
        })
        .unwrap_or_default();
    let text = format!(
        "{{\"ready\":{ready},\"can_start\":{ready},\"blocking_reasons\":{},\"goal\":{},\"roles\":{},\"unknown_roles\":{},\"inputs_keys\":{},\"max_retries\":{},\"max_retries_requested\":{},\"scope\":{},\"execution_mode\":\"async\",\"runtime\":{},\"health\":{}}}",
        serde_json::to_string(&blocking).unwrap_or_else(|_| "[]".into()),
        serde_json::to_string(&goal).unwrap_or_else(|_| "\"\"".into()),
        serde_json::to_string(&roles).unwrap_or_else(|_| "[]".into()),
        serde_json::to_string(&unknown).unwrap_or_else(|_| "[]".into()),
        serde_json::to_string(&inputs_keys).unwrap_or_else(|_| "[]".into()),
        retry,
        max_retries,
        serde_json::to_string(&scope).unwrap_or_else(|_| "\"personal\"".into()),
        serde_json::to_string(&runtime).unwrap_or_else(|_| "{}".into()),
        serde_json::to_string(&health).unwrap_or_else(|_| "{}".into()),
    );
    json_response(StatusCode::OK, &text, None)
}

// ── /agent loop (original paths; reuse lattice-agent when a model is loaded)

async fn agent_http(
    State(state): State<AgentsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    if !parsed
        .as_object()
        .map(|o| o.contains_key("message"))
        .unwrap_or(false)
    {
        return missing_fields(&parsed, &["message"]);
    }
    let stream = parsed
        .get("stream")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    if !state.model_loaded {
        if stream {
            let frames = "data: {\"error\":\"No model loaded. Call /models/load first.\",\"model\":null}\n\ndata: [DONE]\n\n";
            let mut response = Response::new(axum::body::Body::from(frames));
            *response.status_mut() = StatusCode::OK;
            response.headers_mut().insert(
                header::CONTENT_TYPE,
                header::HeaderValue::from_static("text/event-stream; charset=utf-8"),
            );
            return response;
        }
        return detail(
            StatusCode::BAD_REQUEST,
            "No model loaded. Call /models/load first.",
        );
    }
    detail(
        StatusCode::BAD_REQUEST,
        "No model loaded. Call /models/load first.",
    )
}

async fn agent_resume(
    State(state): State<AgentsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    let run_id = parsed.get("run_id").and_then(Value::as_str).unwrap_or("");
    let context_id = parsed
        .get("context_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    if run_id.is_empty() && context_id.is_empty() {
        return detail(
            StatusCode::BAD_REQUEST,
            "run_id (with approval_token) or context_id is required.",
        );
    }
    detail(
        StatusCode::NOT_FOUND,
        "Agent run not found. It may have expired — start a new request.",
    )
}

async fn agent_approvals(State(state): State<AgentsState>, headers: HeaderMap) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    json_response(StatusCode::OK, "{\"pending\":[]}", None)
}

fn resolve_skills_dir(data_dir: &Path) -> PathBuf {
    if let Ok(configured) = std::env::var("LATTICEAI_SKILLS_DIR") {
        let trimmed = configured.trim();
        if !trimmed.is_empty() {
            return PathBuf::from(trimmed);
        }
    }
    let under_data = data_dir.join("skills");
    if under_data.is_dir() {
        return under_data;
    }
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../skills");
    if repo.is_dir() {
        return repo;
    }
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../latticeai/core/skills")
}
