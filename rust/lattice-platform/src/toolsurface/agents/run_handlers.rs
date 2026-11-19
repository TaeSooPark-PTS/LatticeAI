//! Execution, preview, single-agent HTTP loop, and resume handlers for [`super`].

use std::path::{Path, PathBuf};

use axum::extract::State;
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::Response;
use lattice_agent::agentloop::{default_file_create_actions, Runtime};
use lattice_agent::runbody::{finish_payload, RunBody};
use lattice_agent::sandbox::Workspace;
use lattice_agent::state::AgentRunContext;
use lattice_agent::worker::WorkerClient;
use lattice_auth::response::json_response;
use lattice_auth::OrderedMap;
use serde_json::{json, Value};

use super::{
    ground_roles, health_body, loop_readiness, AgentsState, AGENT_ROLES, CORE_PIPELINE,
    MULTI_AGENT_VERSION, UNAVAILABLE,
};
use crate::toolsurface::mcp::{
    detail, missing_fields, parse_json_object, requested_scope, require_user,
};

pub(super) async fn agent_run(
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

pub(super) async fn agent_run_preview(
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

pub(super) async fn agent_http(
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

pub(super) async fn agent_resume(
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

pub(super) async fn agent_approvals(
    State(state): State<AgentsState>,
    headers: HeaderMap,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    json_response(StatusCode::OK, "{\"pending\":[]}", None)
}

pub(super) fn resolve_skills_dir(data_dir: &Path) -> PathBuf {
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
