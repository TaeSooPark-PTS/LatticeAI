//! Agent registry family (v11.6.0, WP-R8).
//!
//! Port of `latticeai/api/agent_registry.py` + `latticeai/core/agent_registry.py`.
//! State lives in `agent_registry.json`.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use axum::extract::{Path as AxumPath, Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use axum::routing::get;
use axum::Router;
use lattice_auth::response::json_response;
use lattice_auth::{AuthState, OrderedMap};
use lattice_core::db::tables::state_files;
use serde_json::{json, Value};

use crate::toolsurface::mcp::{
    detail, dump_indent2, json_status, missing_fields, now_iso_seconds, parse_json_object,
    require_admin, require_user, value_to_ordered,
};

pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/agents/api/registry"),
    ("POST", "/agents/api/registry"),
    ("GET", "/agents/api/registry/capabilities"),
    ("GET", "/agents/api/registry/discover"),
    ("GET", "/agents/api/registry/*agent_id"),
    ("PATCH", "/agents/api/registry/*agent_id"),
    ("DELETE", "/agents/api/registry/*agent_id"),
];

const AGENT_TYPES: &[&str] = &[
    "planner",
    "researcher",
    "executor",
    "reviewer",
    "release",
    "custom",
];
const AGENT_ROLES: &[&str] = &["researcher", "planner", "executor", "reviewer", "release"];
const CORE_PIPELINE: &[&str] = &["planner", "executor", "reviewer"];
const MULTI_AGENT_VERSION: &str = env!("CARGO_PKG_VERSION");

#[derive(Clone)]
pub struct AgentRegistryState {
    pub auth: Arc<AuthState>,
    pub path: PathBuf,
    lock: Arc<Mutex<()>>,
}

impl AgentRegistryState {
    pub fn new(auth: Arc<AuthState>, data_dir: impl AsRef<Path>) -> Self {
        Self {
            auth,
            path: data_dir.as_ref().join(state_files::AGENT_REGISTRY),
            lock: Arc::new(Mutex::new(())),
        }
    }

    fn load(&self) -> Value {
        if let Ok(text) = std::fs::read_to_string(&self.path) {
            if let Ok(mut value) = serde_json::from_str::<Value>(&text) {
                if let Some(obj) = value.as_object_mut() {
                    obj.entry("custom").or_insert(json!([]));
                    obj.entry("config_overrides").or_insert(json!({}));
                    return value;
                }
            }
        }
        json!({"custom": [], "config_overrides": {}})
    }

    fn save(&self, state: &Value) {
        let _g = self.lock.lock();
        if let Some(parent) = self.path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        lattice_auth::atomic::write_text(&self.path, &dump_indent2(state));
    }
}

pub fn router(state: AgentRegistryState) -> Router {
    Router::new()
        .route(
            "/agents/api/registry",
            get(list_registry).post(register_agent),
        )
        .route("/agents/api/registry/capabilities", get(capabilities))
        .route("/agents/api/registry/discover", get(discover))
        .route(
            "/agents/api/registry/*agent_id",
            get(get_agent).patch(update_agent).delete(remove_agent),
        )
        .with_state(state)
}

fn role_meta(role: &str) -> (&'static str, &'static [&'static str]) {
    match role {
        "researcher" => (
            "Gathers workspace context, memory, and graph signal for the goal.",
            &[
                "context-retrieval",
                "memory-recall",
                "graph-read",
                "hybrid-search",
            ],
        ),
        "planner" => (
            "Decomposes the goal into an ordered, bounded, reviewable plan.",
            &["task-decomposition", "plan-review", "delegation"],
        ),
        "executor" => (
            "Executes each planned step, invoking tools, workflows, and plugins.",
            &["tool-use", "workflow-run", "plugin-run", "file-write"],
        ),
        "reviewer" => (
            "Reviews executed work and approves, rejects, or requests a retry.",
            &["verification", "retry-control", "approval"],
        ),
        "release" => (
            "Finalizes and summarizes the approved outcome.",
            &["summarize", "finalize"],
        ),
        _ => ("", &[]),
    }
}

fn role_id(role: &str) -> String {
    format!("agent:{role}")
}

fn builtin_agents(state: &Value) -> Vec<OrderedMap> {
    let overrides = state.get("config_overrides").cloned().unwrap_or(json!({}));
    let mut agents = Vec::new();
    for role in AGENT_ROLES {
        let (desc, caps) = role_meta(role);
        let id = role_id(role);
        let handoffs: Vec<String> = match *role {
            "planner" => vec![role_id("executor")],
            "executor" => vec![role_id("reviewer")],
            _ => vec![],
        };
        let ov = overrides.get(&id).cloned().unwrap_or(json!({}));
        let mut agent = OrderedMap::new();
        agent.insert("id", json!(id));
        let mut name = role.to_string();
        if let Some(c) = name.get_mut(0..1) {
            c.make_ascii_uppercase();
        }
        agent.insert("name", json!(name));
        agent.insert("type", json!(role));
        agent.insert("version", json!(MULTI_AGENT_VERSION));
        agent.insert("description", json!(desc));
        agent.insert("capabilities", json!(caps));
        agent.insert("handoffs", json!(handoffs));
        agent.insert("in_default_pipeline", json!(CORE_PIPELINE.contains(role)));
        agent.insert("source", json!("builtin"));
        agent.insert("removable", json!(false));
        agent.insert(
            "enabled",
            json!(ov.get("enabled").and_then(Value::as_bool).unwrap_or(true)),
        );
        agent.insert("config", ov.get("config").cloned().unwrap_or(json!({})));
        agents.push(agent);
    }
    agents
}

fn custom_agents(state: &Value) -> Vec<OrderedMap> {
    let mut out = Vec::new();
    if let Some(arr) = state.get("custom").and_then(Value::as_array) {
        for entry in arr {
            let mut agent = value_to_ordered(entry);
            agent.insert("source", json!("user"));
            agent.insert("removable", json!(true));
            if agent.get("enabled").is_none() {
                agent.insert("enabled", json!(true));
            }
            if agent.get("handoffs").is_none() {
                agent.insert("handoffs", json!([]));
            }
            out.push(agent);
        }
    }
    out
}

fn all_agents(state: &Value) -> Vec<OrderedMap> {
    let mut all = builtin_agents(state);
    all.extend(custom_agents(state));
    all
}

fn redact(mut agent: OrderedMap) -> OrderedMap {
    // Python redact_secrets walks nested maps; fixture agents have no secrets.
    if let Some(Value::Object(cfg)) = agent.get("config").cloned() {
        let mut cleaned = serde_json::Map::new();
        for (k, v) in cfg {
            let lk = k.to_lowercase();
            if [
                "secret",
                "token",
                "password",
                "api_key",
                "apikey",
                "credential",
            ]
            .iter()
            .any(|s| lk.contains(s))
            {
                cleaned.insert(k, json!("***"));
            } else {
                cleaned.insert(k, v);
            }
        }
        agent.insert("config", Value::Object(cleaned));
    }
    agent
}

#[derive(Debug, serde::Deserialize, Default)]
struct TypeQuery {
    r#type: Option<String>,
}

async fn list_registry(
    State(state): State<AgentRegistryState>,
    headers: HeaderMap,
    Query(q): Query<TypeQuery>,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let stored = state.load();
    let mut agents = all_agents(&stored);
    if let Some(t) = q.r#type.as_deref() {
        agents.retain(|a| a.get("type").and_then(Value::as_str) == Some(t));
    }
    let mut counts: BTreeMap<String, i64> = BTreeMap::new();
    for a in all_agents(&stored) {
        if let Some(t) = a.get("type").and_then(Value::as_str) {
            *counts.entry(t.to_string()).or_insert(0) += 1;
        }
    }
    // Preserve first-seen type order from all_agents
    let mut count_map = OrderedMap::new();
    for a in all_agents(&stored) {
        if let Some(t) = a.get("type").and_then(Value::as_str) {
            if count_map.get(t).is_none() {
                count_map.insert(t, json!(counts.get(t).copied().unwrap_or(0)));
            }
        }
    }
    let listed: Vec<String> = agents
        .into_iter()
        .map(redact)
        .map(|a| serde_json::to_string(&a).unwrap_or_else(|_| "{}".into()))
        .collect();
    let listed_n = listed.len();
    let listed_joined = listed.join(",");
    let text = format!(
        "{{\"agents\":[{listed_joined}],\"types\":{},\"counts\":{},\"total\":{listed_n},\"version\":\"{MULTI_AGENT_VERSION}\",\"default_pipeline\":{},\"generated_at\":\"{}\"}}",
        serde_json::to_string(&AGENT_TYPES).unwrap_or_else(|_| "[]".into()),
        serde_json::to_string(&count_map).unwrap_or_else(|_| "{}".into()),
        serde_json::to_string(&CORE_PIPELINE).unwrap_or_else(|_| "[]".into()),
        now_iso_seconds(),
    );
    json_response(StatusCode::OK, &text, None)
}

async fn capabilities(State(state): State<AgentRegistryState>, headers: HeaderMap) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let stored = state.load();
    let mut index: OrderedMap = OrderedMap::new();
    for a in all_agents(&stored) {
        let id = a
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        if let Some(Value::Array(caps)) = a.get("capabilities") {
            for cap in caps {
                if let Some(c) = cap.as_str() {
                    let mut list = index
                        .get(c)
                        .and_then(Value::as_array)
                        .cloned()
                        .unwrap_or_default();
                    list.push(json!(id));
                    index.insert(c, json!(list));
                }
            }
        }
    }
    let text = format!(
        "{{\"capabilities\":{}}}",
        serde_json::to_string(&index).unwrap_or_else(|_| "{}".into())
    );
    json_response(StatusCode::OK, &text, None)
}

#[derive(Debug, serde::Deserialize, Default)]
struct DiscoverQuery {
    capability: Option<String>,
}

async fn discover(
    State(state): State<AgentRegistryState>,
    headers: HeaderMap,
    Query(q): Query<DiscoverQuery>,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let cap = q.capability.unwrap_or_default().to_lowercase();
    let cap = cap.trim().to_string();
    let stored = state.load();
    let found: Vec<String> = all_agents(&stored)
        .into_iter()
        .filter(|a| {
            a.get("capabilities")
                .and_then(Value::as_array)
                .map(|cs| {
                    cs.iter()
                        .any(|c| c.as_str().map(|s| s.to_lowercase()) == Some(cap.clone()))
                })
                .unwrap_or(false)
        })
        .map(redact)
        .map(|a| serde_json::to_string(&a).unwrap_or_else(|_| "{}".into()))
        .collect();
    json_response(
        StatusCode::OK,
        &format!(
            "{{\"capability\":{},\"agents\":[{}]}}",
            serde_json::to_string(&cap).unwrap_or_else(|_| "\"\"".into()),
            found.join(",")
        ),
        None,
    )
}

async fn register_agent(
    State(state): State<AgentRegistryState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_admin(&state.auth, &headers) {
        return r;
    }
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    if !parsed
        .as_object()
        .map(|o| o.contains_key("name"))
        .unwrap_or(false)
    {
        return missing_fields(&parsed, &["name"]);
    }
    let name = parsed
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim();
    if name.is_empty() {
        return detail(StatusCode::BAD_REQUEST, "name is required");
    }
    let agent_type = parsed
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or("custom");
    if !AGENT_TYPES.contains(&agent_type) {
        return detail(
            StatusCode::BAD_REQUEST,
            &format!("type must be one of {}", AGENT_TYPES.join(", ")),
        );
    }
    let mut stored = state.load();
    let slug = name.to_lowercase().replace(' ', "-");
    let mut agent_id = format!("agent:custom:{slug}");
    let existing: Vec<String> = stored
        .get("custom")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(|e| e.get("id").and_then(Value::as_str).map(str::to_string))
                .collect()
        })
        .unwrap_or_default();
    if existing.iter().any(|id| id == &agent_id) {
        agent_id = format!("agent:custom:{slug}-{}", existing.len() + 1);
    }
    let mut entry = OrderedMap::new();
    entry.insert("id", json!(agent_id));
    entry.insert("name", json!(name));
    entry.insert("type", json!(agent_type));
    entry.insert(
        "version",
        json!(parsed
            .get("version")
            .and_then(Value::as_str)
            .unwrap_or("1.0.0")),
    );
    entry.insert(
        "description",
        json!(parsed
            .get("description")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim()),
    );
    entry.insert(
        "capabilities",
        parsed.get("capabilities").cloned().unwrap_or(json!([])),
    );
    entry.insert("config", parsed.get("config").cloned().unwrap_or(json!({})));
    entry.insert("enabled", json!(true));
    entry.insert("created_at", json!(now_iso_seconds()));
    if let Some(obj) = stored.as_object_mut() {
        let custom = obj.entry("custom").or_insert(json!([]));
        if let Some(arr) = custom.as_array_mut() {
            arr.push(serde_json::to_value(&entry).unwrap_or(json!({})));
        }
    }
    state.save(&stored);
    let text = format!(
        "{{\"agent\":{}}}",
        serde_json::to_string(&redact(entry)).unwrap_or_else(|_| "{}".into())
    );
    json_response(StatusCode::OK, &text, None)
}

async fn get_agent(
    State(state): State<AgentRegistryState>,
    headers: HeaderMap,
    AxumPath(agent_id): AxumPath<String>,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let stored = state.load();
    let found = all_agents(&stored)
        .into_iter()
        .find(|a| a.get("id").and_then(Value::as_str) == Some(agent_id.as_str()));
    match found {
        Some(agent) => {
            let text = format!(
                "{{\"agent\":{}}}",
                serde_json::to_string(&redact(agent)).unwrap_or_else(|_| "{}".into())
            );
            json_response(StatusCode::OK, &text, None)
        }
        None => detail(
            StatusCode::NOT_FOUND,
            &format!("Agent not found: {agent_id}"),
        ),
    }
}

async fn update_agent(
    State(state): State<AgentRegistryState>,
    headers: HeaderMap,
    AxumPath(agent_id): AxumPath<String>,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_admin(&state.auth, &headers) {
        return r;
    }
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    let mut stored = state.load();
    let exists = all_agents(&stored)
        .iter()
        .any(|a| a.get("id").and_then(Value::as_str) == Some(agent_id.as_str()));
    if !exists {
        return detail(
            StatusCode::NOT_FOUND,
            &format!("Agent not found: {agent_id}"),
        );
    }
    let config = parsed.get("config").cloned().unwrap_or(json!({}));
    let enabled = parsed.get("enabled").and_then(Value::as_bool);
    if agent_id.starts_with("agent:custom:") {
        if let Some(arr) = stored.get_mut("custom").and_then(Value::as_array_mut) {
            for entry in arr {
                if entry.get("id").and_then(Value::as_str) == Some(agent_id.as_str()) {
                    entry["config"] = config.clone();
                    if let Some(en) = enabled {
                        entry["enabled"] = json!(en);
                    }
                    entry["updated_at"] = json!(now_iso_seconds());
                }
            }
        }
    } else if let Some(obj) = stored.as_object_mut() {
        let ov = obj
            .entry("config_overrides")
            .or_insert(json!({}))
            .as_object_mut()
            .unwrap();
        let slot = ov.entry(agent_id.clone()).or_insert(json!({}));
        slot["config"] = config;
        if let Some(en) = enabled {
            slot["enabled"] = json!(en);
        }
    }
    state.save(&stored);
    let agent = all_agents(&stored)
        .into_iter()
        .find(|a| a.get("id").and_then(Value::as_str) == Some(agent_id.as_str()))
        .unwrap_or_else(OrderedMap::new);
    let text = format!(
        "{{\"agent\":{}}}",
        serde_json::to_string(&redact(agent)).unwrap_or_else(|_| "{}".into())
    );
    json_response(StatusCode::OK, &text, None)
}

async fn remove_agent(
    State(state): State<AgentRegistryState>,
    headers: HeaderMap,
    AxumPath(agent_id): AxumPath<String>,
) -> Response {
    if let Err(r) = require_admin(&state.auth, &headers) {
        return r;
    }
    if !agent_id.starts_with("agent:custom:") {
        return detail(
            StatusCode::BAD_REQUEST,
            "Built-in role agents cannot be removed; disable them via config instead.",
        );
    }
    let mut stored = state.load();
    let before = stored
        .get("custom")
        .and_then(Value::as_array)
        .map(Vec::len)
        .unwrap_or(0);
    if let Some(arr) = stored.get_mut("custom").and_then(Value::as_array_mut) {
        arr.retain(|e| e.get("id").and_then(Value::as_str) != Some(agent_id.as_str()));
    }
    let after = stored
        .get("custom")
        .and_then(Value::as_array)
        .map(Vec::len)
        .unwrap_or(0);
    if after == before {
        return detail(
            StatusCode::NOT_FOUND,
            &format!("Agent not found: {agent_id}"),
        );
    }
    state.save(&stored);
    let mut body = OrderedMap::new();
    body.insert("removed", json!(agent_id));
    json_status(StatusCode::OK, &body)
}
