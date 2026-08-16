//! Hooks registry (`latticeai/api/hooks.py`) — native.
//!
//! Registration, list, inspect, enable/disable, reorder, and remove write
//! `hooks.json`. Run history lives in `hooks_runs.json`. A successful hook
//! *run* (the body of a user command or a bound runner) is delegated to
//! `POST /agent/tool`; the fixtures only pin the 400/404 selector branches.

use axum::extract::{Path as AxumPath, Query, State};
use axum::http::HeaderMap;
use axum::response::Response;
use axum::routing::{get, post};
use axum::{http::StatusCode, Router};
use lattice_auth::OrderedMap;
use serde_json::{json, Value};

use crate::review_queue::{
    http_detail, json_ok, language, now_iso, parse_object, require_admin, require_field,
    require_user, string_field, string_field_or, GovernanceState,
};

/// Mounted (method, axum-path) pairs. `{hook_id:path}` is greedy.
pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/api/hooks"),
    ("POST", "/api/hooks/disable"),
    ("POST", "/api/hooks/enable"),
    ("POST", "/api/hooks/fire"),
    ("POST", "/api/hooks/register"),
    ("POST", "/api/hooks/reorder"),
    ("POST", "/api/hooks/run"),
    ("GET", "/api/hooks/runs"),
    ("DELETE", "/api/hooks/*hook_id"),
    ("GET", "/api/hooks/*hook_id"),
];

pub(crate) const HOOK_KINDS: &[&str] = &[
    "pre_run",
    "post_run",
    "pre_tool",
    "post_tool",
    "pre_workflow",
    "post_workflow",
    "pre_upload",
    "post_upload",
    "pre_index",
    "post_index",
    "agent",
];

pub(crate) const BRAIN_EVENT_TRIGGERS: &str = "user:brain-event-triggers";

/// Built-in hooks after the v3.4.1 kind aliases (`workflow`→`post_workflow`,
/// `pipeline`→`post_index`).
pub(crate) fn builtin_hooks() -> Vec<Value> {
    vec![
        json!({
            "id": "builtin:redact-secrets",
            "name": "Redact secrets",
            "kind": "pre_run",
            "order": 10,
            "description": "Strip secret-like fields (token, password, api_key…) from agent context packets before a run.",
            "binding": "lattice_brain.runtime.multi_agent._redact",
            "managed": "platform"
        }),
        json!({
            "id": "builtin:research-memory-snapshot",
            "name": "Research memory snapshot",
            "kind": "agent",
            "order": 20,
            "description": "Capture a short-term memory snapshot after the researcher stage gathers context.",
            "binding": "lattice_brain.runtime.multi_agent.default_role_runner",
            "managed": "platform"
        }),
        json!({
            "id": "builtin:tool-permission-gate",
            "name": "Tool permission gate",
            "kind": "pre_tool",
            "order": 10,
            "description": "Require explicit approval for tools whose governance policy is not auto-approve.",
            "binding": "latticeai.core.tool_registry.ToolRegistry.permission",
            "managed": "platform"
        }),
        json!({
            "id": "builtin:sensitive-data-guard",
            "name": "Sensitive-data guard",
            "kind": "pre_tool",
            "order": 20,
            "description": "Classify outgoing content for sensitive data before tool execution.",
            "binding": "server_app.classify_sensitive_message",
            "managed": "platform"
        }),
        json!({
            "id": "builtin:audit-agent-run",
            "name": "Audit agent run",
            "kind": "post_run",
            "order": 10,
            "description": "Append every completed agent run to the workspace audit log.",
            "binding": "lattice_brain.runtime.agent_runtime.AgentRuntime.start",
            "managed": "platform"
        }),
        json!({
            "id": "builtin:workflow-replay-log",
            "name": "Workflow replay log",
            "kind": "post_workflow",
            "order": 10,
            "description": "Record each workflow run's timeline so it can be replayed step by step.",
            "binding": "latticeai.api.workflow_designer",
            "managed": "platform"
        }),
        json!({
            "id": "builtin:pipeline-index-status",
            "name": "Pipeline index status",
            "kind": "post_index",
            "order": 10,
            "description": "Publish ingest / embed / graph-build pipeline state to the retrieval index status.",
            "binding": "latticeai.api.search",
            "managed": "platform"
        }),
    ]
}

mod sink;
mod store;
pub use sink::NativeHookSink;
pub use store::HooksStore;
use store::{alias_kind, HooksError};

/// The hooks router. `hooks` is stored on [`HooksState`].
pub fn router(state: HooksState) -> Router {
    Router::new()
        .route("/api/hooks", get(list_hooks))
        .route("/api/hooks/runs", get(hook_runs))
        .route("/api/hooks/run", post(run_hooks))
        .route("/api/hooks/fire", post(fire_hooks))
        .route("/api/hooks/enable", post(enable_hook))
        .route("/api/hooks/disable", post(disable_hook))
        .route("/api/hooks/reorder", post(reorder_hooks))
        .route("/api/hooks/register", post(register_hook))
        .route("/api/hooks/*hook_id", get(inspect_hook).delete(remove_hook))
        .with_state(state)
}

/// Router state: governance (auth / worker) plus the hooks store.
#[derive(Clone)]
pub struct HooksState {
    /// Shared auth + worker + data dir.
    pub gov: GovernanceState,
    /// Persisted registry.
    pub hooks: HooksStore,
}

impl HooksState {
    /// Build from an existing governance state.
    pub fn new(gov: GovernanceState) -> Self {
        let hooks = HooksStore::open(&gov.data_dir);
        Self { gov, hooks }
    }

    /// Build over a registry somebody else opened.
    ///
    /// The host opens **one** [`HooksStore`] and hands it both to these routes
    /// and to [`NativeHookSink`], for the reason `GovernanceState` is opened
    /// once: the store keeps `hooks.json` and the run log in memory, so a
    /// second instance over the same directory would not see the first one's
    /// writes and would overwrite them on its next save.
    pub fn with_store(gov: GovernanceState, hooks: HooksStore) -> Self {
        Self { gov, hooks }
    }
}

#[derive(Debug, Default, serde::Deserialize)]
struct ListQuery {
    kind: Option<String>,
}

#[derive(Debug, Default, serde::Deserialize)]
struct RunsQuery {
    limit: Option<i64>,
    kind: Option<String>,
}

async fn list_hooks(
    State(state): State<HooksState>,
    headers: HeaderMap,
    Query(query): Query<ListQuery>,
) -> Result<Response, Response> {
    require_user(&state.gov, &headers)?;
    let _ = language(&headers);
    Ok(json_ok(&state.hooks.list(query.kind.as_deref())))
}

async fn hook_runs(
    State(state): State<HooksState>,
    headers: HeaderMap,
    Query(query): Query<RunsQuery>,
) -> Result<Response, Response> {
    require_user(&state.gov, &headers)?;
    let limit = query.limit.unwrap_or(50);
    Ok(json_ok(
        &state.hooks.recent_runs(limit, query.kind.as_deref()),
    ))
}

async fn inspect_hook(
    State(state): State<HooksState>,
    headers: HeaderMap,
    AxumPath(hook_id): AxumPath<String>,
) -> Result<Response, Response> {
    require_user(&state.gov, &headers)?;
    match state.hooks.inspect(&hook_id) {
        Some(hook) => {
            let mut body = OrderedMap::new();
            body.insert("hook", hook);
            Ok(json_ok(&body))
        }
        None => Err(http_detail(
            StatusCode::NOT_FOUND,
            &format!("Hook not found: {hook_id}"),
        )),
    }
}

async fn enable_hook(
    State(state): State<HooksState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Response, Response> {
    require_admin(&state.gov, &headers)?;
    let parsed = parse_object(&body)?;
    require_field(&parsed, "hook_id")?;
    let hook_id = string_field(&parsed, "hook_id");
    let enabled = parsed
        .get("enabled")
        .and_then(Value::as_bool)
        .unwrap_or(true);
    match state.hooks.set_enabled(&hook_id, enabled) {
        Some(hook) => {
            let mut body = OrderedMap::new();
            body.insert("hook", hook);
            Ok(json_ok(&body))
        }
        None => Err(http_detail(
            StatusCode::NOT_FOUND,
            &format!("Hook not found: {hook_id}"),
        )),
    }
}

async fn disable_hook(
    State(state): State<HooksState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Response, Response> {
    require_admin(&state.gov, &headers)?;
    let parsed = parse_object(&body)?;
    require_field(&parsed, "hook_id")?;
    let hook_id = string_field(&parsed, "hook_id");
    match state.hooks.set_enabled(&hook_id, false) {
        Some(hook) => {
            let mut body = OrderedMap::new();
            body.insert("hook", hook);
            Ok(json_ok(&body))
        }
        None => Err(http_detail(
            StatusCode::NOT_FOUND,
            &format!("Hook not found: {hook_id}"),
        )),
    }
}

async fn reorder_hooks(
    State(state): State<HooksState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Response, Response> {
    require_admin(&state.gov, &headers)?;
    let parsed = parse_object(&body)?;
    require_field(&parsed, "kind")?;
    let kind = string_field(&parsed, "kind");
    let ids: Vec<String> = parsed
        .get("ordered_ids")
        .and_then(Value::as_array)
        .map(|rows| {
            rows.iter()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default();
    Ok(json_ok(&state.hooks.reorder(&kind, &ids)))
}

async fn register_hook(
    State(state): State<HooksState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Response, Response> {
    require_admin(&state.gov, &headers)?;
    let parsed = parse_object(&body)?;
    let name = string_field(&parsed, "name");
    let kind = string_field(&parsed, "kind");
    match state.hooks.register(
        &name,
        &kind,
        &string_field_or(&parsed, "description", ""),
        &string_field_or(&parsed, "command", ""),
        parsed
            .get("order")
            .and_then(Value::as_i64)
            .map(|n| n as i32),
        parsed
            .get("enabled")
            .and_then(Value::as_bool)
            .unwrap_or(true),
    ) {
        Ok(entry) => {
            let mut body = OrderedMap::new();
            body.insert("hook", entry);
            Ok(json_ok(&body))
        }
        Err(msg) => Err(http_detail(StatusCode::BAD_REQUEST, &msg)),
    }
}

async fn remove_hook(
    State(state): State<HooksState>,
    headers: HeaderMap,
    AxumPath(hook_id): AxumPath<String>,
) -> Result<Response, Response> {
    require_admin(&state.gov, &headers)?;
    match state.hooks.remove(&hook_id) {
        Ok(removed) => {
            let mut body = OrderedMap::new();
            body.insert("removed", json!(removed));
            Ok(json_ok(&body))
        }
        Err(HooksError::NotFound) => Err(http_detail(
            StatusCode::NOT_FOUND,
            &format!("Hook not found: {hook_id}"),
        )),
        Err(HooksError::BadRequest(msg)) => Err(http_detail(StatusCode::BAD_REQUEST, &msg)),
    }
}

async fn run_hooks(
    State(state): State<HooksState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Response, Response> {
    require_admin(&state.gov, &headers)?;
    let parsed = parse_object(&body)?;
    let hook_id = parsed
        .get("hook_id")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string);
    let kind = parsed
        .get("kind")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string);
    if hook_id.is_none() && kind.is_none() {
        return Err(http_detail(
            StatusCode::BAD_REQUEST,
            "Provide a 'kind' or a 'hook_id' to run.",
        ));
    }
    if let Some(id) = hook_id {
        match state.hooks.get(&id) {
            None => {
                return Err(http_detail(
                    StatusCode::NOT_FOUND,
                    &format!("Hook not found: '{id}'"),
                ));
            }
            Some(hook) => {
                return dispatch_one(&state, &hook, &parsed).await;
            }
        }
    }
    let kind = kind.expect("kind present");
    dispatch_kind(&state, &kind, &parsed).await
}

async fn fire_hooks(
    state: State<HooksState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Response, Response> {
    run_hooks(state, headers, body).await
}

async fn dispatch_one(
    state: &HooksState,
    hook: &Value,
    req: &serde_json::Map<String, Value>,
) -> Result<Response, Response> {
    let hook_id = hook.get("id").and_then(Value::as_str).unwrap_or("");
    let command = hook
        .get("command")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    if command.is_empty() {
        // Advisory / no-op — still recorded.
        let result = state.hooks.record_advisory(hook, req);
        return Ok(json_status_value(result));
    }
    // Actual execution goes through the worker tool seam.
    if let Some(worker) = state.gov.worker.clone() {
        let payload = json!({
            "tool": "run_hook",
            "args": {
                "hook_id": hook_id,
                "command": command,
                "event": req.get("event"),
                "payload": req.get("payload").cloned().unwrap_or_else(|| json!({})),
            }
        });
        match worker.post_json("/agent/tool", &payload).await {
            Ok(value) => return Ok(json_status_value(value)),
            Err(error) => {
                return Err(crate::review_queue::map_worker_error(error));
            }
        }
    }
    Ok(json_status_value(state.hooks.record_advisory(hook, req)))
}

async fn dispatch_kind(
    state: &HooksState,
    kind: &str,
    req: &serde_json::Map<String, Value>,
) -> Result<Response, Response> {
    let kind = alias_kind(kind);
    if !HOOK_KINDS.contains(&kind) {
        return Err(http_detail(
            StatusCode::BAD_REQUEST,
            &format!("kind must be one of {}", HOOK_KINDS.join(", ")),
        ));
    }
    let hooks = state.hooks.enabled_of_kind(kind);
    let mut results = Vec::new();
    for hook in hooks {
        let command = hook
            .get("command")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim();
        if command.is_empty() {
            results.push(state.hooks.record_advisory(&hook, req));
            continue;
        }
        if let Some(worker) = state.gov.worker.clone() {
            let payload = json!({
                "tool": "run_hook",
                "args": {
                    "hook_id": hook.get("id"),
                    "command": command,
                    "event": req.get("event"),
                    "payload": req.get("payload").cloned().unwrap_or_else(|| json!({})),
                }
            });
            match worker.post_json("/agent/tool", &payload).await {
                Ok(value) => results.push(value),
                Err(error) => return Err(crate::review_queue::map_worker_error(error)),
            }
        } else {
            results.push(state.hooks.record_advisory(&hook, req));
        }
    }
    let mut body = OrderedMap::new();
    body.insert("kind", json!(kind));
    body.insert(
        "event",
        json!(req.get("event").and_then(Value::as_str).unwrap_or(kind)),
    );
    body.insert("ran", json!(results.len() as i64));
    body.insert("blocked", json!(false));
    body.insert("block_reason", json!(""));
    body.insert("results", Value::Array(results));
    body.insert("generated_at", json!(now_iso()));
    Ok(json_ok(&body))
}

fn json_status_value(value: Value) -> Response {
    crate::review_queue::json_status(StatusCode::OK, &value)
}
