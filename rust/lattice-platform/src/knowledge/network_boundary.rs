use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use axum::body::Bytes;
use axum::extract::{Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_auth::{AuthState, OrderedMap};
use lattice_core::db::RuntimeConfig;
use serde_json::{json, Value};

use crate::http_reply::{detail, json_ok, missing_fields, parse_json_object};

#[path = "network_boundary_stores.rs"]
mod stores;
use stores::network_mode_catalog;
pub(crate) use stores::TokenBudget;
pub use stores::{BoundaryStore, PolicyStore};

pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/api/network-boundary"),
    ("POST", "/api/network-boundary"),
    ("GET", "/api/network-boundary/catalog"),
    ("GET", "/api/network-boundary/ui-state"),
    ("POST", "/api/network-boundary/preview"),
    ("POST", "/api/network-boundary/node-sensitivity"),
    ("GET", "/api/network-boundary/policy"),
    ("POST", "/api/network-boundary/policy"),
];

pub(crate) const HARD_BLOCK_NODE_TYPES: &[&str] = &[
    "ApiKey",
    "Credential",
    "Password",
    "PrivateKey",
    "Secret",
    "Token",
];
pub(crate) const HARD_BLOCK_METADATA_FLAGS: &[&str] =
    &["do_not_share", "local_only", "private", "sensitive"];

#[derive(Clone)]
pub struct NetworkBoundaryState {
    pub auth: Arc<AuthState>,
    pub config: Arc<RuntimeConfig>,
    pub boundary: Arc<BoundaryStore>,
    pub policy: Arc<PolicyStore>,
    pub(crate) budgets: Arc<Mutex<HashMap<String, TokenBudget>>>,
    pub graph: Option<lattice_core::graph_write::GraphWriter>,
    pub workspaces: Option<Arc<crate::workspaceos::workspace::WorkspaceService>>,
}

impl NetworkBoundaryState {
    pub fn new(auth: Arc<AuthState>, config: RuntimeConfig) -> Self {
        let data = config.data_dir().to_path_buf();
        Self {
            auth,
            boundary: Arc::new(BoundaryStore::open(data.join("network_boundary.json"))),
            policy: Arc::new(PolicyStore::open(data.join("hybrid_policy.json"))),
            budgets: Arc::new(Mutex::new(HashMap::new())),
            config: Arc::new(config),
            graph: None,
            workspaces: None,
        }
    }

    pub fn with_workspaces(
        mut self,
        workspaces: Arc<crate::workspaceos::workspace::WorkspaceService>,
    ) -> Self {
        self.workspaces = Some(workspaces);
        self
    }
}

pub fn router(state: NetworkBoundaryState) -> Router {
    Router::new()
        .route(
            "/api/network-boundary",
            get(get_network_boundary).post(set_network_boundary),
        )
        .route("/api/network-boundary/catalog", get(catalog))
        .route("/api/network-boundary/ui-state", get(ui_state))
        .route("/api/network-boundary/preview", post(preview))
        .route(
            "/api/network-boundary/node-sensitivity",
            post(set_node_sensitivity),
        )
        .route(
            "/api/network-boundary/policy",
            get(get_policy).post(set_policy),
        )
        .with_state(state)
}

fn scope(headers: &HeaderMap, workspace_id: Option<&str>) -> Option<String> {
    if let Some(ws) = workspace_id.map(str::trim).filter(|s| !s.is_empty()) {
        return Some(ws.to_string());
    }
    lattice_auth::workspace_scope_from_request(headers, None)
}

fn email_of(identity: &lattice_auth::Identity) -> Option<String> {
    if identity.email.is_empty() {
        None
    } else {
        Some(identity.email.clone())
    }
}

fn budget_key(user: Option<&str>, workspace: Option<&str>) -> String {
    format!(
        "{}|{}",
        user.unwrap_or("anon"),
        workspace.unwrap_or("global")
    )
}

async fn get_network_boundary(
    State(state): State<NetworkBoundaryState>,
    headers: HeaderMap,
    Query(query): Query<HashMap<String, String>>,
) -> Response {
    let identity = match state.auth.require_user(&headers) {
        Ok(id) => id,
        Err(refusal) => return refusal,
    };
    let user = email_of(&identity);
    let requested_ws = scope(&headers, query.get("workspace_id").map(String::as_str));
    if requested_ws.is_some() {
        if let Err(err) = super::network_boundary_guard::resolve_read_scope(
            state.workspaces.as_deref(),
            user.as_deref(),
            requested_ws.as_deref(),
        ) {
            return detail(StatusCode::FORBIDDEN, &err);
        }
    }
    let ws = requested_ws;
    let mut payload = state.boundary.get(user.as_deref(), ws.as_deref());
    let key = budget_key(user.as_deref(), ws.as_deref());
    payload.insert("token_budget", json!(budget_snapshot(&state, &key)));
    payload.insert(
        "policy",
        json!(state.policy.resolve(user.as_deref(), ws.as_deref())),
    );
    json_ok(payload)
}

async fn catalog(State(state): State<NetworkBoundaryState>, headers: HeaderMap) -> Response {
    if let Err(refusal) = state.auth.require_user(&headers) {
        return refusal;
    }
    let mut map = OrderedMap::new();
    map.insert("modes", json!(network_mode_catalog()));
    json_ok(map)
}

async fn ui_state(
    State(state): State<NetworkBoundaryState>,
    headers: HeaderMap,
    Query(query): Query<HashMap<String, String>>,
) -> Response {
    let identity = match state.auth.require_user(&headers) {
        Ok(id) => id,
        Err(refusal) => return refusal,
    };
    let user = email_of(&identity);
    let requested_ws = scope(&headers, query.get("workspace_id").map(String::as_str));
    if requested_ws.is_some() {
        if let Err(err) = super::network_boundary_guard::resolve_read_scope(
            state.workspaces.as_deref(),
            user.as_deref(),
            requested_ws.as_deref(),
        ) {
            return detail(StatusCode::FORBIDDEN, &err);
        }
    }
    let ws = requested_ws;
    let mode_payload = state.boundary.get(user.as_deref(), ws.as_deref());
    let policy = state.policy.resolve(user.as_deref(), ws.as_deref());
    let key = budget_key(user.as_deref(), ws.as_deref());
    let mode = mode_payload
        .get("mode")
        .and_then(Value::as_str)
        .unwrap_or("local_only");
    let warning_ko = network_mode_catalog()
        .into_iter()
        .find(|m| m.get("id").and_then(Value::as_str) == Some(mode))
        .and_then(|m| m.get("warning_ko").cloned());
    let mut map = OrderedMap::new();
    map.insert("mode", json!(mode));
    map.insert(
        "label",
        mode_payload.get("label").cloned().unwrap_or(Value::Null),
    );
    map.insert(
        "label_ko",
        mode_payload.get("label_ko").cloned().unwrap_or(Value::Null),
    );
    map.insert(
        "allows_cloud",
        mode_payload
            .get("allows_cloud")
            .cloned()
            .unwrap_or(json!(false)),
    );
    map.insert(
        "requires_ack",
        mode_payload
            .get("requires_ack")
            .cloned()
            .unwrap_or(json!(false)),
    );
    map.insert("warning_ko", warning_ko.unwrap_or(Value::Null));
    map.insert("policy", json!(policy));
    map.insert("token_budget", json!(budget_snapshot(&state, &key)));
    map.insert("catalog", json!(network_mode_catalog()));
    json_ok(map)
}

async fn set_network_boundary(
    State(state): State<NetworkBoundaryState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let identity = match state.auth.require_user(&headers) {
        Ok(id) => id,
        Err(refusal) => return refusal,
    };
    let object = match parse_json_object(&body) {
        Ok(v) => v,
        Err(refusal) => return refusal,
    };
    if !object.contains_key("mode") {
        return missing_fields(&object, &["mode"]);
    }
    let mode = object.get("mode").and_then(Value::as_str).unwrap_or("");
    let ack = object
        .get("acknowledge_risk")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let user = email_of(&identity);
    let requested_ws = scope(&headers, object.get("workspace_id").and_then(Value::as_str));
    let ws = match super::network_boundary_guard::authorize_scope_write(
        state.workspaces.as_deref(),
        user.as_deref(),
        requested_ws.as_deref(),
    ) {
        Ok(ws) => ws,
        Err(err) => return detail(StatusCode::FORBIDDEN, &err),
    };
    match state
        .boundary
        .set_mode(mode, user.as_deref(), ws.as_deref(), ack)
    {
        Ok(payload) => json_ok(payload),
        Err(message) => detail(StatusCode::BAD_REQUEST, &message),
    }
}

async fn preview(
    State(state): State<NetworkBoundaryState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let identity = match state.auth.require_user(&headers) {
        Ok(id) => id,
        Err(refusal) => return refusal,
    };
    let object = match parse_json_object(&body) {
        Ok(v) => v,
        Err(refusal) => return refusal,
    };
    if !object.contains_key("message") {
        return missing_fields(&object, &["message"]);
    }
    let message = object.get("message").and_then(Value::as_str).unwrap_or("");
    let top_k = object
        .get("top_k")
        .and_then(Value::as_i64)
        .unwrap_or(6)
        .clamp(1, 12) as usize;
    let user = email_of(&identity);
    let requested_ws = scope(&headers, object.get("workspace_id").and_then(Value::as_str));
    let (resolved_ws, allowed_workspaces) = match super::network_boundary_guard::resolve_read_scope(
        state.workspaces.as_deref(),
        user.as_deref(),
        requested_ws.as_deref(),
    ) {
        Ok(pair) => pair,
        Err(err) => return detail(StatusCode::FORBIDDEN, &err),
    };
    let policy_ws = requested_ws.as_deref().or(resolved_ws.as_deref());
    let mode = state.boundary.resolve(user.as_deref(), policy_ws);
    let resolved_policy = state.policy.resolve(user.as_deref(), policy_ws);
    let keywords: Vec<String> = message
        .split_whitespace()
        .filter(|w| !w.is_empty())
        .map(str::to_string)
        .collect();
    let nodes = super::network_boundary_guard::preview_nodes_filtered(
        &state.config,
        &keywords,
        top_k,
        allowed_workspaces.as_ref(),
        &resolved_policy,
    );
    let compact: String = lattice_core::redact::redact_secret_text(
        &nodes
            .iter()
            .map(|n| {
                if n.summary.is_empty() || n.summary == n.title {
                    format!("- [{}] {}", n.node_type, n.title)
                } else {
                    format!("- [{}] {}: {}", n.node_type, n.title, n.summary)
                }
            })
            .collect::<Vec<_>>()
            .join("\n"),
    );
    let token_estimate = if compact.is_empty() {
        1
    } else {
        compact.chars().count().div_ceil(4).max(1)
    };
    let key = budget_key(user.as_deref(), policy_ws);
    let budget = budget_snapshot(&state, &key);
    let would_block = {
        let max = budget
            .get("max_tokens_per_turn")
            .and_then(Value::as_u64)
            .unwrap_or(2500) as usize;
        if token_estimate > max {
            Some(format!(
                "estimated input tokens {token_estimate} exceed per-turn limit {max}"
            ))
        } else {
            None
        }
    };
    let mut quality = OrderedMap::new();
    quality.insert("mode", json!("hybrid"));
    quality.insert("nodes", json!(nodes.len()));
    quality.insert("limited", json!(false));
    quality.insert("reason", Value::Null);
    let mut map = OrderedMap::new();
    map.insert("mode", json!(mode));
    map.insert("allows_cloud", json!(mode == "cloud_allowed"));
    map.insert(
        "node_ids",
        json!(nodes.iter().map(|n| &n.id).collect::<Vec<_>>()),
    );
    map.insert("keywords", json!(keywords));
    map.insert(
        "titles",
        json!(nodes.iter().map(|n| &n.title).collect::<Vec<_>>()),
    );
    map.insert(
        "types",
        json!(nodes.iter().map(|n| &n.node_type).collect::<Vec<_>>()),
    );
    map.insert("token_estimate", json!(token_estimate));
    map.insert("quality", json!(quality));
    map.insert(
        "compact_preview",
        json!(compact.chars().take(1200).collect::<String>()),
    );
    map.insert("token_budget", json!(budget));
    map.insert("would_block", json!(would_block));
    json_ok(map)
}

async fn set_node_sensitivity(
    State(state): State<NetworkBoundaryState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let identity = match state.auth.require_user(&headers) {
        Ok(id) => id,
        Err(refusal) => return refusal,
    };
    let object = match parse_json_object(&body) {
        Ok(v) => v,
        Err(refusal) => return refusal,
    };
    if !object.contains_key("node_id") {
        return missing_fields(&object, &["node_id"]);
    }
    let node_id = object
        .get("node_id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let local_only = object
        .get("local_only")
        .and_then(Value::as_bool)
        .unwrap_or(true);
    let reason = object.get("reason").and_then(Value::as_str);
    let user = email_of(&identity);
    let requested_ws = scope(&headers, object.get("workspace_id").and_then(Value::as_str));
    if let Some(graph) = state.graph.clone() {
        if let Err((status, message)) =
            super::network_boundary_guard::authorize_node_sensitivity_write(
                &state.config,
                state.workspaces.as_deref(),
                user.as_deref(),
                requested_ws.as_deref(),
                &node_id,
            )
        {
            return detail(status, &message);
        }
        let nid = node_id.clone();
        let reason_owned = reason.map(str::to_string);
        match tokio::task::spawn_blocking(move || {
            graph.set_node_sensitivity(&nid, local_only, reason_owned.as_deref())
        })
        .await
        {
            Ok(Ok(result)) => {
                if result.get("ok").and_then(Value::as_bool) == Some(false) {
                    return detail(
                        StatusCode::NOT_FOUND,
                        result
                            .get("reason")
                            .and_then(Value::as_str)
                            .unwrap_or("node not found"),
                    );
                }
                json_ok(result)
            }
            Ok(Err(err)) => detail(StatusCode::BAD_REQUEST, &err.to_string()),
            Err(err) => detail(StatusCode::BAD_GATEWAY, &err.to_string()),
        }
    } else {
        detail(StatusCode::NOT_FOUND, "node not found")
    }
}

async fn get_policy(
    State(state): State<NetworkBoundaryState>,
    headers: HeaderMap,
    Query(query): Query<HashMap<String, String>>,
) -> Response {
    let identity = match state.auth.require_user(&headers) {
        Ok(id) => id,
        Err(refusal) => return refusal,
    };
    let user = email_of(&identity);
    let requested_ws = scope(&headers, query.get("workspace_id").map(String::as_str));
    if requested_ws.is_some() {
        if let Err(err) = super::network_boundary_guard::resolve_read_scope(
            state.workspaces.as_deref(),
            user.as_deref(),
            requested_ws.as_deref(),
        ) {
            return detail(StatusCode::FORBIDDEN, &err);
        }
    }
    json_ok(
        state
            .policy
            .resolve(user.as_deref(), requested_ws.as_deref()),
    )
}

async fn set_policy(
    State(state): State<NetworkBoundaryState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let identity = match state.auth.require_user(&headers) {
        Ok(id) => id,
        Err(refusal) => return refusal,
    };
    let object = if body.is_empty() {
        serde_json::Map::new()
    } else {
        match parse_json_object(&body) {
            Ok(v) => v,
            Err(refusal) => return refusal,
        }
    };
    let user = email_of(&identity);
    let requested_ws = scope(&headers, object.get("workspace_id").and_then(Value::as_str));
    let ws = match super::network_boundary_guard::authorize_scope_write(
        state.workspaces.as_deref(),
        user.as_deref(),
        requested_ws.as_deref(),
    ) {
        Ok(ws) => ws,
        Err(err) => return detail(StatusCode::FORBIDDEN, &err),
    };
    json_ok(
        state
            .policy
            .set_policy(&object, user.as_deref(), ws.as_deref()),
    )
}

fn budget_snapshot(state: &NetworkBoundaryState, key: &str) -> OrderedMap {
    let mut budgets = state.budgets.lock().expect("budget lock");
    let budget = budgets.entry(key.to_string()).or_default();
    budget.snapshot()
}
