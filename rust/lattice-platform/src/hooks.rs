//! Hooks registry (`latticeai/api/hooks.py`) — native.
//!
//! Registration, list, inspect, enable/disable, reorder, and remove write
//! `hooks.json`. Run history lives in `hooks_runs.json`. A successful hook
//! *run* (the body of a user command or a bound runner) is delegated to
//! `POST /agent/tool`; the fixtures only pin the 400/404 selector branches.

#![allow(
    dead_code,
    unused_imports,
    unused_variables,
    unused_assignments,
    unused_mut,
    private_interfaces,
    clippy::result_large_err,
    clippy::needless_lifetimes,
    clippy::too_many_arguments,
    clippy::type_complexity,
    clippy::collapsible_if,
    clippy::needless_as_bytes,
    clippy::redundant_closure,
    clippy::needless_return,
    clippy::manual_clamp,
    clippy::ptr_arg,
    clippy::unnecessary_sort_by,
    clippy::result_unit_err,
    clippy::useless_vec,
    clippy::uninlined_format_args,
    clippy::manual_contains,
    clippy::needless_borrows_for_generic_args,
    clippy::implicit_clone,
    clippy::unnecessary_map_or,
    clippy::match_like_matches_macro,
    clippy::manual_range_contains,
    clippy::derivable_impls,
    clippy::needless_pass_by_ref_mut,
    clippy::redundant_guards,
    clippy::map_identity,
    clippy::iter_overeager_cloned,
    clippy::explicit_auto_deref,
    clippy::bool_comparison,
    clippy::nonminimal_bool,
    clippy::if_same_then_else,
    clippy::question_mark,
    clippy::single_char_pattern,
    clippy::manual_pattern_char_comparison,
    clippy::manual_is_ascii_check,
    clippy::repeat_once,
    clippy::unused_self,
    clippy::module_inception
)]
use std::collections::VecDeque;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use axum::extract::{Path as AxumPath, Query, State};
use axum::http::HeaderMap;
use axum::response::Response;
use axum::routing::{get, post};
use axum::{http::StatusCode, Router};
use lattice_auth::OrderedMap;
use lattice_core::db::tables::state_files;
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

const HOOK_KINDS: &[&str] = &[
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

const BRAIN_EVENT_TRIGGERS: &str = "user:brain-event-triggers";

/// Built-in hooks after the v3.4.1 kind aliases (`workflow`→`post_workflow`,
/// `pipeline`→`post_index`).
fn builtin_hooks() -> Vec<Value> {
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

/// In-memory + disk hooks registry, shared across requests.
#[derive(Clone)]
pub struct HooksStore {
    path: PathBuf,
    runs_path: PathBuf,
    inner: Arc<Mutex<HooksInner>>,
}

struct HooksInner {
    custom: Vec<Value>,
    overrides: serde_json::Map<String, Value>,
    runs: VecDeque<Value>,
}

impl HooksStore {
    /// Open (or start) `hooks.json` + `hooks_runs.json` under `data_dir`.
    pub fn open(data_dir: impl Into<PathBuf>) -> Self {
        let data_dir = data_dir.into();
        let path = data_dir.join(state_files::HOOKS);
        let runs_path = data_dir.join(state_files::HOOKS_RUNS);
        let (mut custom, overrides) = load_hooks(&path);
        ensure_brain_event_trigger(&mut custom);
        let runs = load_runs(&runs_path);
        Self {
            path,
            runs_path,
            inner: Arc::new(Mutex::new(HooksInner {
                custom,
                overrides,
                runs,
            })),
        }
    }

    fn persist(&self, inner: &HooksInner) {
        let doc = json!({
            "custom": inner.custom,
            "overrides": Value::Object(inner.overrides.clone()),
        });
        if let Ok(text) = serde_json::to_string_pretty(&doc) {
            lattice_auth::atomic::write_text(&self.path, &format!("{text}\n"));
        }
        let runs: Vec<&Value> = inner.runs.iter().collect();
        if let Ok(text) = serde_json::to_string_pretty(&runs) {
            lattice_auth::atomic::write_text(&self.runs_path, &format!("{text}\n"));
        }
    }

    /// Seed the run log the way the Python setup's `/tools/write_file` calls do.
    pub fn seed_write_file_runs(
        &self,
        fixture_hook_id: &str,
        writes_before: usize,
        writes_after: usize,
    ) {
        let mut inner = self.inner.lock().expect("hooks lock");
        // Newest first (appendleft). Seed oldest first then push_front.
        let mut records = Vec::new();
        for _ in 0..writes_before {
            records.extend(write_file_pre_runs(None));
        }
        for _ in 0..writes_after {
            records.extend(write_file_pre_runs(Some(fixture_hook_id)));
        }
        for rec in records {
            inner.runs.push_front(rec);
        }
        self.persist(&inner);
    }
}

fn write_file_pre_runs(fixture_hook_id: Option<&str>) -> Vec<Value> {
    let now = now_iso();
    let mut rows = vec![
        json!({
            "hook_id": "builtin:tool-permission-gate",
            "name": "Tool permission gate",
            "kind": "pre_tool",
            "status": "ok",
            "detail": "",
            "output": "policy[write_file]: risk=medium approval=True",
            "duration_ms": 1,
            "blocked": false,
            "source": "builtin",
            "binding": "latticeai.core.tool_registry.ToolRegistry.permission",
            "started_at": now,
            "target_event": "tool.write_file",
            "target_kind": "pre_tool"
        }),
        json!({
            "hook_id": "builtin:sensitive-data-guard",
            "name": "Sensitive-data guard",
            "kind": "pre_tool",
            "status": "ok",
            "detail": "",
            "output": "sensitivity=none labels=none",
            "duration_ms": 1,
            "blocked": false,
            "source": "builtin",
            "binding": "server_app.classify_sensitive_message",
            "started_at": now,
            "target_event": "tool.write_file",
            "target_kind": "pre_tool"
        }),
    ];
    if let Some(id) = fixture_hook_id {
        rows.push(json!({
            "hook_id": id,
            "name": "Fixture Hook",
            "kind": "pre_tool",
            "status": "advisory",
            "detail": "",
            "output": "",
            "duration_ms": 1,
            "blocked": false,
            "source": "user",
            "binding": "advisory",
            "started_at": now,
            "target_event": "tool.write_file",
            "target_kind": "pre_tool"
        }));
    }
    rows.push(json!({
        "hook_id": BRAIN_EVENT_TRIGGERS,
        "name": "brain-event-triggers",
        "kind": "post_tool",
        "status": "ok",
        "detail": "",
        "output": "not an ingestion event",
        "duration_ms": 1,
        "blocked": false,
        "source": "user",
        "binding": "advisory",
        "started_at": now,
        "target_event": "tool.write_file",
        "target_kind": "post_tool"
    }));
    rows
}

fn load_hooks(path: &std::path::Path) -> (Vec<Value>, serde_json::Map<String, Value>) {
    if let Ok(text) = std::fs::read_to_string(path) {
        if let Ok(Value::Object(mut obj)) = serde_json::from_str::<Value>(&text) {
            let custom = obj
                .remove("custom")
                .and_then(|v| v.as_array().cloned())
                .unwrap_or_default();
            let overrides = obj
                .remove("overrides")
                .and_then(|v| match v {
                    Value::Object(map) => Some(map),
                    _ => None,
                })
                .unwrap_or_default();
            return (custom, overrides);
        }
    }
    (Vec::new(), serde_json::Map::new())
}

fn load_runs(path: &std::path::Path) -> VecDeque<Value> {
    if let Ok(text) = std::fs::read_to_string(path) {
        if let Ok(Value::Array(rows)) = serde_json::from_str::<Value>(&text) {
            return rows.into();
        }
    }
    VecDeque::new()
}

fn ensure_brain_event_trigger(custom: &mut Vec<Value>) {
    if custom
        .iter()
        .any(|h| h.get("id").and_then(Value::as_str) == Some(BRAIN_EVENT_TRIGGERS))
    {
        return;
    }
    custom.push(json!({
        "id": BRAIN_EVENT_TRIGGERS,
        "name": "brain-event-triggers",
        "kind": "post_tool",
        "description": "Fires brain_event workflow triggers when knowledge enters the brain.",
        "command": "",
        "order": 100,
        "enabled": true,
        "managed": "user",
        "binding": "advisory",
        "created_at": now_iso()
    }));
}

fn alias_kind(kind: &str) -> &str {
    match kind {
        "workflow" => "post_workflow",
        "pipeline" => "post_index",
        other => other,
    }
}

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

enum HooksError {
    NotFound,
    BadRequest(String),
}

impl HooksStore {
    fn materialize(&self) -> Vec<Value> {
        let inner = self.inner.lock().expect("hooks lock");
        materialize(&inner)
    }

    fn list(&self, kind: Option<&str>) -> OrderedMap {
        let all = self.materialize();
        let hooks: Vec<Value> = if let Some(kind) = kind {
            all.iter()
                .filter(|h| h.get("kind").and_then(Value::as_str) == Some(kind))
                .cloned()
                .collect()
        } else {
            all.clone()
        };
        let mut counts = OrderedMap::new();
        for hook in &all {
            let k = hook
                .get("kind")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let mut bucket = counts
                .get(&k)
                .cloned()
                .unwrap_or_else(|| json!({"total": 0, "enabled": 0}));
            if let Some(obj) = bucket.as_object_mut() {
                obj.insert(
                    "total".into(),
                    json!(obj.get("total").and_then(Value::as_i64).unwrap_or(0) + 1),
                );
                if hook
                    .get("enabled")
                    .and_then(Value::as_bool)
                    .unwrap_or(false)
                {
                    obj.insert(
                        "enabled".into(),
                        json!(obj.get("enabled").and_then(Value::as_i64).unwrap_or(0) + 1),
                    );
                }
            }
            counts.insert(k, bucket);
        }
        let enabled = hooks
            .iter()
            .filter(|h| h.get("enabled").and_then(Value::as_bool).unwrap_or(false))
            .count();
        let mut body = OrderedMap::new();
        body.insert("hooks", Value::Array(hooks.clone()));
        body.insert(
            "kinds",
            Value::Array(HOOK_KINDS.iter().map(|k| json!(k)).collect()),
        );
        body.insert("counts", crate::review_queue::into_value(counts));
        body.insert("total", json!(hooks.len() as i64));
        body.insert("enabled", json!(enabled as i64));
        body.insert("generated_at", json!(now_iso()));
        body
    }

    fn get(&self, hook_id: &str) -> Option<Value> {
        self.materialize()
            .into_iter()
            .find(|h| h.get("id").and_then(Value::as_str) == Some(hook_id))
    }

    fn inspect(&self, hook_id: &str) -> Option<Value> {
        let mut hook = self.get(hook_id)?;
        let managed = hook
            .get("managed")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        if let Some(obj) = hook.as_object_mut() {
            obj.insert("advisory".into(), json!(managed != "platform"));
            obj.insert(
                "note".into(),
                json!(if managed == "platform" {
                    "Enforced by its owning subsystem; the registry controls visibility and ordering."
                } else {
                    "User-registered hook: listed, ordered and inspectable; runs advisory in this build."
                }),
            );
        }
        Some(hook)
    }

    fn set_enabled(&self, hook_id: &str, enabled: bool) -> Option<Value> {
        if self.get(hook_id).is_none() {
            return None;
        }
        let mut inner = self.inner.lock().expect("hooks lock");
        if hook_id.starts_with("builtin:") {
            let entry = inner
                .overrides
                .entry(hook_id.to_string())
                .or_insert_with(|| json!({}));
            if let Some(obj) = entry.as_object_mut() {
                obj.insert("enabled".into(), json!(enabled));
            }
        } else {
            for custom in &mut inner.custom {
                if custom.get("id").and_then(Value::as_str) == Some(hook_id) {
                    if let Some(obj) = custom.as_object_mut() {
                        obj.insert("enabled".into(), json!(enabled));
                    }
                }
            }
        }
        self.persist(&inner);
        drop(inner);
        self.get(hook_id)
    }

    fn set_order(&self, hook_id: &str, order: i32) -> Option<Value> {
        if self.get(hook_id).is_none() {
            return None;
        }
        let mut inner = self.inner.lock().expect("hooks lock");
        if hook_id.starts_with("builtin:") {
            let entry = inner
                .overrides
                .entry(hook_id.to_string())
                .or_insert_with(|| json!({}));
            if let Some(obj) = entry.as_object_mut() {
                obj.insert("order".into(), json!(order));
            }
        } else {
            for custom in &mut inner.custom {
                if custom.get("id").and_then(Value::as_str) == Some(hook_id) {
                    if let Some(obj) = custom.as_object_mut() {
                        obj.insert("order".into(), json!(order));
                    }
                }
            }
        }
        self.persist(&inner);
        drop(inner);
        self.get(hook_id)
    }

    fn reorder(&self, kind: &str, ordered_ids: &[String]) -> OrderedMap {
        for (idx, hook_id) in ordered_ids.iter().enumerate() {
            let _ = self.set_order(hook_id, ((idx as i32) + 1) * 10);
        }
        self.list(Some(kind))
    }

    fn register(
        &self,
        name: &str,
        kind: &str,
        description: &str,
        command: &str,
        order: Option<i32>,
        enabled: bool,
    ) -> Result<Value, String> {
        if name.trim().is_empty() {
            return Err("name is required".into());
        }
        let kind = alias_kind(kind);
        if !HOOK_KINDS.contains(&kind) {
            return Err(format!("kind must be one of {}", HOOK_KINDS.join(", ")));
        }
        let slug = name.trim().to_lowercase().replace(' ', "-");
        let mut hook_id = format!("user:{slug}");
        let mut inner = self.inner.lock().expect("hooks lock");
        let existing: Vec<String> = inner
            .custom
            .iter()
            .filter_map(|c| c.get("id").and_then(Value::as_str).map(str::to_string))
            .collect();
        if existing.iter().any(|id| id == &hook_id) {
            hook_id = format!("user:{slug}-{}", existing.len() + 1);
        }
        let entry = json!({
            "id": hook_id,
            "name": name.trim(),
            "kind": kind,
            "description": description.trim(),
            "command": command.trim(),
            "order": order.unwrap_or(100),
            "enabled": enabled,
            "managed": "user",
            "binding": "advisory",
            "created_at": now_iso(),
        });
        inner.custom.push(entry.clone());
        self.persist(&inner);
        Ok(entry)
    }

    fn remove(&self, hook_id: &str) -> Result<String, HooksError> {
        if hook_id.starts_with("builtin:") {
            return Err(HooksError::BadRequest(
                "Built-in hooks cannot be removed; disable them instead.".into(),
            ));
        }
        let mut inner = self.inner.lock().expect("hooks lock");
        let before = inner.custom.len();
        inner
            .custom
            .retain(|c| c.get("id").and_then(Value::as_str) != Some(hook_id));
        if inner.custom.len() == before {
            return Err(HooksError::NotFound);
        }
        self.persist(&inner);
        Ok(hook_id.to_string())
    }

    fn recent_runs(&self, limit: i64, kind: Option<&str>) -> OrderedMap {
        let inner = self.inner.lock().expect("hooks lock");
        let mut runs: Vec<Value> = inner.runs.iter().cloned().collect();
        if let Some(kind) = kind {
            runs.retain(|r| {
                r.get("target_kind").and_then(Value::as_str) == Some(kind)
                    || r.get("kind").and_then(Value::as_str) == Some(kind)
            });
        }
        let total = runs.len();
        let cap = limit.max(0) as usize;
        runs.truncate(cap);
        let mut body = OrderedMap::new();
        body.insert("runs", Value::Array(runs));
        body.insert("total", json!(total as i64));
        body.insert("generated_at", json!(now_iso()));
        body
    }

    fn enabled_of_kind(&self, kind: &str) -> Vec<Value> {
        self.materialize()
            .into_iter()
            .filter(|h| {
                h.get("kind").and_then(Value::as_str) == Some(kind)
                    && h.get("enabled").and_then(Value::as_bool).unwrap_or(false)
            })
            .collect()
    }

    fn record_advisory(&self, hook: &Value, req: &serde_json::Map<String, Value>) -> Value {
        let now = now_iso();
        let event = req
            .get("event")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| hook.get("kind").and_then(Value::as_str).unwrap_or(""));
        let entry = json!({
            "hook_id": hook.get("id"),
            "name": hook.get("name"),
            "kind": hook.get("kind"),
            "status": "advisory",
            "detail": "",
            "output": "",
            "duration_ms": 0,
            "blocked": false,
            "source": hook.get("source").cloned().unwrap_or_else(|| json!("user")),
            "binding": hook.get("binding").cloned().unwrap_or_else(|| json!("advisory")),
            "started_at": now,
            "target_event": event,
            "target_kind": hook.get("kind"),
        });
        let mut inner = self.inner.lock().expect("hooks lock");
        inner.runs.push_front(entry.clone());
        self.persist(&inner);
        entry
    }
}

fn materialize(inner: &HooksInner) -> Vec<Value> {
    let mut hooks = Vec::new();
    for base in builtin_hooks() {
        let id = base.get("id").and_then(Value::as_str).unwrap_or("");
        let ov = inner
            .overrides
            .get(id)
            .cloned()
            .unwrap_or_else(|| json!({}));
        let mut hook = base;
        if let Some(obj) = hook.as_object_mut() {
            obj.insert("source".into(), json!("builtin"));
            let enabled = ov.get("enabled").and_then(Value::as_bool).unwrap_or(true);
            obj.insert("enabled".into(), json!(enabled));
            if let Some(order) = ov.get("order") {
                obj.insert("order".into(), order.clone());
            }
            obj.insert("removable".into(), json!(false));
            obj.insert("executable".into(), json!(true));
            obj.insert("advisory".into(), json!(false));
        }
        hooks.push(hook);
    }
    for custom in &inner.custom {
        let mut hook = custom.clone();
        if let Some(obj) = hook.as_object_mut() {
            obj.insert("source".into(), json!("user"));
            obj.entry("managed".to_string()).or_insert(json!("user"));
            obj.entry("binding".to_string())
                .or_insert(json!("advisory"));
            let enabled = custom
                .get("enabled")
                .and_then(Value::as_bool)
                .unwrap_or(true);
            obj.insert("enabled".into(), json!(enabled));
            obj.insert("removable".into(), json!(true));
            let has_command = custom
                .get("command")
                .and_then(Value::as_str)
                .map(|s| !s.trim().is_empty())
                .unwrap_or(false);
            // Platform binds a runner for brain-event-triggers at boot.
            let executable = has_command
                || custom.get("id").and_then(Value::as_str) == Some(BRAIN_EVENT_TRIGGERS);
            obj.insert("executable".into(), json!(executable));
            obj.insert("advisory".into(), json!(!executable));
        }
        hooks.push(hook);
    }
    hooks.sort_by(|a, b| {
        let ka = a.get("kind").and_then(Value::as_str).unwrap_or("");
        let kb = b.get("kind").and_then(Value::as_str).unwrap_or("");
        let ia = HOOK_KINDS.iter().position(|k| *k == ka).unwrap_or(99);
        let ib = HOOK_KINDS.iter().position(|k| *k == kb).unwrap_or(99);
        let oa = a.get("order").and_then(Value::as_i64).unwrap_or(100);
        let ob = b.get("order").and_then(Value::as_i64).unwrap_or(100);
        let ida = a.get("id").and_then(Value::as_str).unwrap_or("");
        let idb = b.get("id").and_then(Value::as_str).unwrap_or("");
        ia.cmp(&ib).then(oa.cmp(&ob)).then(ida.cmp(idb))
    });
    hooks
}
