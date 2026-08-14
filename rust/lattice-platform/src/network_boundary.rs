//! Network boundary dial + hybrid policy — native port of
//! `latticeai/api/network_boundary.py`.
//!
//! The dial is stored in `<data_dir>/network_boundary.json` and the policy in
//! `<data_dir>/hybrid_policy.json` — the same files Python reads and writes.
//! Graph writes (`set_node_sensitivity`) go through
//! `POST /worker/graph/mutate`.

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
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use axum::body::Bytes;
use axum::extract::{Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_auth::{AuthState, OrderedMap};
use lattice_core::db::RuntimeConfig;
use lattice_core::worker::WorkerSeamClient;
use serde_json::{json, Value};

use crate::project_sessions::{detail, json_ok, missing_fields, parse_json_object};

/// Mounted (method, path) pairs.
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

const HARD_BLOCK_NODE_TYPES: &[&str] = &[
    "ApiKey",
    "Credential",
    "Password",
    "PrivateKey",
    "Secret",
    "Token",
];
const HARD_BLOCK_METADATA_FLAGS: &[&str] = &["do_not_share", "local_only", "private", "sensitive"];

/// Router state.
#[derive(Clone)]
pub struct NetworkBoundaryState {
    pub auth: Arc<AuthState>,
    pub config: Arc<RuntimeConfig>,
    pub boundary: Arc<BoundaryStore>,
    pub policy: Arc<PolicyStore>,
    pub budgets: Arc<Mutex<HashMap<String, TokenBudget>>>,
    pub seam: Option<WorkerSeamClient>,
    pub graph: Option<lattice_core::graph_write::GraphWriter>,
}

impl NetworkBoundaryState {
    pub fn new(
        auth: Arc<AuthState>,
        config: RuntimeConfig,
        seam: Option<WorkerSeamClient>,
    ) -> Self {
        let data = config.data_dir().to_path_buf();
        Self {
            auth,
            boundary: Arc::new(BoundaryStore::open(data.join("network_boundary.json"))),
            policy: Arc::new(PolicyStore::open(data.join("hybrid_policy.json"))),
            budgets: Arc::new(Mutex::new(HashMap::new())),
            config: Arc::new(config),
            seam,
            graph: None,
        }
    }
}

/// Build the network-boundary router.
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
    let ws = scope(&headers, query.get("workspace_id").map(String::as_str));
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
    let ws = scope(&headers, query.get("workspace_id").map(String::as_str));
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
    let ws = scope(&headers, object.get("workspace_id").and_then(Value::as_str));
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
    let ws = scope(&headers, object.get("workspace_id").and_then(Value::as_str));
    let mode = state.boundary.resolve(user.as_deref(), ws.as_deref());
    let keywords: Vec<String> = message
        .split_whitespace()
        .filter(|w| !w.is_empty())
        .map(str::to_string)
        .collect();
    let nodes = preview_nodes(&state.config, &keywords, top_k);
    let token_estimate = nodes
        .iter()
        .map(|n| n.title.chars().count() / 4 + 8)
        .sum::<usize>()
        .max(1);
    let key = budget_key(user.as_deref(), ws.as_deref());
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
    let compact: String = nodes
        .iter()
        .map(|n| {
            if n.summary.is_empty() || n.summary == n.title {
                format!("- [{}] {}", n.node_type, n.title)
            } else {
                format!("- [{}] {}: {}", n.node_type, n.title, n.summary)
            }
        })
        .collect::<Vec<_>>()
        .join("\n");
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
    let _ = identity;
    if let Some(graph) = state.graph.clone() {
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
                return json_ok(result);
            }
            Ok(Err(err)) => {
                return detail(StatusCode::BAD_REQUEST, &err.to_string());
            }
            Err(err) => return detail(StatusCode::BAD_GATEWAY, &err.to_string()),
        }
    } else if let Some(seam) = &state.seam {
        let mut args = serde_json::Map::new();
        args.insert("node_id".into(), json!(node_id));
        args.insert("local_only".into(), json!(local_only));
        if let Some(reason) = reason {
            args.insert("reason".into(), json!(reason));
        }
        match seam
            .post_json(
                "/worker/graph/mutate",
                &json!({"op":"set_node_sensitivity","args": args}),
            )
            .await
        {
            Ok(value) => {
                let result = value.get("result").cloned().unwrap_or(value);
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
            Err(err) => detail(
                StatusCode::from_u16(err.status().unwrap_or(502))
                    .unwrap_or(StatusCode::BAD_GATEWAY),
                &err.to_string(),
            ),
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
    let ws = scope(&headers, query.get("workspace_id").map(String::as_str));
    json_ok(state.policy.resolve(user.as_deref(), ws.as_deref()))
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
    let ws = scope(&headers, object.get("workspace_id").and_then(Value::as_str));
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

#[derive(Clone)]
struct PreviewNode {
    id: String,
    title: String,
    summary: String,
    node_type: String,
}

fn preview_nodes(config: &RuntimeConfig, keywords: &[String], top_k: usize) -> Vec<PreviewNode> {
    let path = config.graph_db_path();
    if !path.exists() {
        return Vec::new();
    }
    let Ok(conn) = lattice_core::db::open_read_only(&path) else {
        return Vec::new();
    };
    let Ok(mut stmt) = conn.prepare("SELECT id, type, title, summary FROM nodes LIMIT 200") else {
        return Vec::new();
    };
    let mut scored = Vec::new();
    if let Ok(rows) = stmt.query_map([], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, String>(2)?,
            row.get::<_, String>(3).unwrap_or_default(),
        ))
    }) {
        for row in rows.flatten() {
            let (id, node_type, title, summary) = row;
            let hay = format!("{title} {summary}").to_lowercase();
            let score = if keywords.is_empty() {
                1
            } else {
                keywords
                    .iter()
                    .filter(|k| hay.contains(&k.to_lowercase()))
                    .count()
            };
            if score > 0 {
                scored.push((
                    score,
                    PreviewNode {
                        id,
                        title,
                        summary,
                        node_type,
                    },
                ));
            }
        }
    }
    scored.sort_by(|a, b| b.0.cmp(&a.0));
    scored.into_iter().take(top_k).map(|(_, n)| n).collect()
}

fn network_mode_catalog() -> Vec<OrderedMap> {
    let mut local = OrderedMap::new();
    local.insert("id", json!("local_only"));
    local.insert("label", json!("Local only"));
    local.insert("label_ko", json!("로컬만"));
    local.insert(
        "summary",
        json!("Nothing leaves this machine. Answers use local models and the local Brain only."),
    );
    local.insert(
        "summary_ko",
        json!("이 컴퓨터를 벗어나지 않습니다. 로컬 모델과 로컬 Brain만 사용합니다."),
    );
    local.insert("risk", json!("low"));
    local.insert("requires_ack", json!(false));

    let mut cloud = OrderedMap::new();
    cloud.insert("id", json!("cloud_allowed"));
    cloud.insert("label", json!("Cloud streaming allowed"));
    cloud.insert("label_ko", json!("클라우드 스트리밍 허용"));
    cloud.insert(
        "summary",
        json!("Minimal related Knowledge Graph nodes may be sent to a cloud LLM. The streamed answer is written back into the local Brain with provenance."),
    );
    cloud.insert(
        "summary_ko",
        json!("관련된 최소 Knowledge Graph 노드만 클라우드 LLM으로 전송될 수 있습니다. 스트리밍 답변은 provenance와 함께 로컬 Brain에 다시 기록됩니다."),
    );
    cloud.insert("risk", json!("medium"));
    cloud.insert("requires_ack", json!(true));
    cloud.insert(
        "warning",
        json!("Cloud mode sends a compact summary of selected local nodes to an external provider. Sensitive nodes remain blocked. You can switch back to Local only at any time."),
    );
    cloud.insert(
        "warning_ko",
        json!("클라우드 모드는 선택된 로컬 노드의 압축 요약을 외부 제공자에게 전송합니다. 민감 노드는 계속 차단됩니다. 언제든지 로컬만으로 되돌릴 수 있습니다."),
    );
    vec![local, cloud]
}

fn normalize_mode(value: &str) -> &'static str {
    match value.trim().to_ascii_lowercase().as_str() {
        "cloud_allowed" | "cloud" | "cloud-allowed" | "hybrid" | "online" => "cloud_allowed",
        _ => "local_only",
    }
}

fn mode_contract(mode: &str) -> OrderedMap {
    let mode = normalize_mode(mode);
    let catalog = network_mode_catalog();
    let entry = catalog
        .iter()
        .find(|m| m.get("id").and_then(Value::as_str) == Some(mode))
        .cloned()
        .unwrap_or_else(|| catalog[0].clone());
    let mut map = OrderedMap::new();
    map.insert("mode", json!(mode));
    map.insert("label", entry.get("label").cloned().unwrap_or(Value::Null));
    map.insert(
        "label_ko",
        entry.get("label_ko").cloned().unwrap_or(Value::Null),
    );
    map.insert("risk", entry.get("risk").cloned().unwrap_or(Value::Null));
    map.insert(
        "requires_ack",
        entry.get("requires_ack").cloned().unwrap_or(json!(false)),
    );
    map.insert("allows_cloud", json!(mode == "cloud_allowed"));
    map.insert("hard_block_node_types", json!(HARD_BLOCK_NODE_TYPES));
    map.insert(
        "hard_block_metadata_flags",
        json!(HARD_BLOCK_METADATA_FLAGS),
    );
    map
}

/// `network_boundary.json`.
pub struct BoundaryStore {
    path: PathBuf,
}

impl BoundaryStore {
    pub fn open(path: PathBuf) -> Self {
        Self { path }
    }

    fn read(&self) -> Value {
        std::fs::read_to_string(&self.path)
            .ok()
            .and_then(|t| serde_json::from_str(&t).ok())
            .unwrap_or_else(|| json!({"default":"local_only","users":{},"workspaces":{}}))
    }

    fn write(&self, data: &Value) {
        if let Some(parent) = self.path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        if let Ok(text) = serde_json::to_string_pretty(data) {
            lattice_auth::atomic::write_text(&self.path, &text);
        }
    }

    pub fn resolve(&self, user_email: Option<&str>, workspace_id: Option<&str>) -> String {
        let data = self.read();
        if let Some(ws) = workspace_id {
            if let Some(mode) = data
                .get("workspaces")
                .and_then(|w| w.get(ws))
                .and_then(Value::as_str)
            {
                return normalize_mode(mode).to_string();
            }
        }
        if let Some(email) = user_email {
            if let Some(mode) = data
                .get("users")
                .and_then(|u| u.get(email.to_ascii_lowercase()))
                .and_then(Value::as_str)
            {
                return normalize_mode(mode).to_string();
            }
        }
        normalize_mode(
            data.get("default")
                .and_then(Value::as_str)
                .unwrap_or("local_only"),
        )
        .to_string()
    }

    pub fn get(&self, user_email: Option<&str>, workspace_id: Option<&str>) -> OrderedMap {
        let mode = self.resolve(user_email, workspace_id);
        let mut contract = mode_contract(&mode);
        contract.insert("catalog", json!(network_mode_catalog()));
        let mut scope = OrderedMap::new();
        scope.insert("user_email", json!(user_email));
        scope.insert("workspace_id", json!(workspace_id));
        contract.insert("scope", json!(scope));
        contract
    }

    pub fn set_mode(
        &self,
        mode: &str,
        user_email: Option<&str>,
        workspace_id: Option<&str>,
        acknowledge_risk: bool,
    ) -> Result<OrderedMap, String> {
        let mode = normalize_mode(mode);
        if mode == "cloud_allowed" && !acknowledge_risk {
            return Err(
                "cloud_allowed mode requires acknowledge_risk=true (minimal related Knowledge Graph nodes may leave this machine)"
                    .into(),
            );
        }
        let mut data = self.read();
        if let Some(obj) = data.as_object_mut() {
            if let Some(ws) = workspace_id {
                let workspaces = obj.entry("workspaces").or_insert_with(|| json!({}));
                if let Some(map) = workspaces.as_object_mut() {
                    map.insert(ws.to_string(), json!(mode));
                }
            } else if let Some(email) = user_email {
                let users = obj.entry("users").or_insert_with(|| json!({}));
                if let Some(map) = users.as_object_mut() {
                    map.insert(email.to_ascii_lowercase(), json!(mode));
                }
            } else {
                obj.insert("default".into(), json!(mode));
            }
        }
        self.write(&data);
        Ok(self.get(user_email, workspace_id))
    }
}

/// `hybrid_policy.json`.
pub struct PolicyStore {
    path: PathBuf,
}

impl PolicyStore {
    pub fn open(path: PathBuf) -> Self {
        Self { path }
    }

    fn default_policy() -> OrderedMap {
        let mut map = OrderedMap::new();
        map.insert("blocked_node_types", json!(Vec::<String>::new()));
        map.insert("blocked_metadata_flags", json!(HARD_BLOCK_METADATA_FLAGS));
        map.insert("auto_commit", json!(false));
        map.insert("allow_multimodal", json!(false));
        map.insert("min_extraction_confidence", json!(0.55));
        map
    }

    fn read(&self) -> Value {
        std::fs::read_to_string(&self.path)
            .ok()
            .and_then(|t| serde_json::from_str(&t).ok())
            .unwrap_or_else(|| {
                json!({"default": serde_json::to_value(Self::default_policy()).unwrap_or(json!({})), "users":{}, "workspaces":{}})
            })
    }

    fn write(&self, data: &Value) {
        if let Some(parent) = self.path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        if let Ok(text) = serde_json::to_string_pretty(data) {
            lattice_auth::atomic::write_text(&self.path, &text);
        }
    }

    pub fn resolve(&self, user_email: Option<&str>, workspace_id: Option<&str>) -> OrderedMap {
        let data = self.read();
        let mut policy = data
            .get("default")
            .cloned()
            .unwrap_or_else(|| serde_json::to_value(Self::default_policy()).unwrap_or(json!({})));
        if let Some(email) = user_email {
            if let Some(user) = data
                .get("users")
                .and_then(|u| u.get(email.to_ascii_lowercase()))
            {
                merge_obj(&mut policy, user);
            }
        }
        if let Some(ws) = workspace_id {
            if let Some(entry) = data.get("workspaces").and_then(|w| w.get(ws)) {
                merge_obj(&mut policy, entry);
            }
        }
        let mut types: Vec<String> = policy
            .get("blocked_node_types")
            .and_then(Value::as_array)
            .map(|a| {
                a.iter()
                    .filter_map(Value::as_str)
                    .map(str::to_string)
                    .collect()
            })
            .unwrap_or_default();
        for t in HARD_BLOCK_NODE_TYPES {
            if !types.iter().any(|x| x == t) {
                types.push((*t).into());
            }
        }
        types.sort();
        let mut flags: Vec<String> = policy
            .get("blocked_metadata_flags")
            .and_then(Value::as_array)
            .map(|a| {
                a.iter()
                    .filter_map(Value::as_str)
                    .map(str::to_string)
                    .collect()
            })
            .unwrap_or_default();
        for f in HARD_BLOCK_METADATA_FLAGS {
            if !flags.iter().any(|x| x == f) {
                flags.push((*f).into());
            }
        }
        flags.sort();
        let mut map = OrderedMap::new();
        map.insert("blocked_node_types", json!(types));
        map.insert("blocked_metadata_flags", json!(flags));
        map.insert(
            "auto_commit",
            json!(policy
                .get("auto_commit")
                .and_then(Value::as_bool)
                .unwrap_or(false)),
        );
        map.insert(
            "allow_multimodal",
            json!(policy
                .get("allow_multimodal")
                .and_then(Value::as_bool)
                .unwrap_or(false)),
        );
        map.insert(
            "min_extraction_confidence",
            json!(policy
                .get("min_extraction_confidence")
                .and_then(Value::as_f64)
                .unwrap_or(0.55)),
        );
        map
    }

    pub fn set_policy(
        &self,
        patch: &serde_json::Map<String, Value>,
        user_email: Option<&str>,
        workspace_id: Option<&str>,
    ) -> OrderedMap {
        let allowed = [
            "blocked_node_types",
            "blocked_metadata_flags",
            "auto_commit",
            "allow_multimodal",
            "min_extraction_confidence",
        ];
        let clean: serde_json::Map<String, Value> = patch
            .iter()
            .filter(|(k, _)| allowed.contains(&k.as_str()))
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect();
        let mut data = self.read();
        if let Some(obj) = data.as_object_mut() {
            if let Some(ws) = workspace_id {
                let bucket = obj.entry("workspaces").or_insert_with(|| json!({}));
                if let Some(map) = bucket.as_object_mut() {
                    let mut current = map.get(ws).cloned().unwrap_or(json!({}));
                    merge_obj(&mut current, &Value::Object(clean));
                    map.insert(ws.to_string(), current);
                }
            } else if let Some(email) = user_email {
                let bucket = obj.entry("users").or_insert_with(|| json!({}));
                if let Some(map) = bucket.as_object_mut() {
                    let key = email.to_ascii_lowercase();
                    let mut current = map.get(&key).cloned().unwrap_or(json!({}));
                    merge_obj(&mut current, &Value::Object(clean));
                    map.insert(key, current);
                }
            } else {
                let mut current = obj.get("default").cloned().unwrap_or(json!({}));
                merge_obj(&mut current, &Value::Object(clean));
                obj.insert("default".into(), current);
            }
        }
        self.write(&data);
        self.resolve(user_email, workspace_id)
    }
}

fn merge_obj(target: &mut Value, patch: &Value) {
    if let (Some(t), Some(p)) = (target.as_object_mut(), patch.as_object()) {
        for (k, v) in p {
            t.insert(k.clone(), v.clone());
        }
    }
}

struct TokenBudget {
    max_tokens_per_turn: u64,
    max_tokens_per_session: u64,
    session_used: u64,
}

impl Default for TokenBudget {
    fn default() -> Self {
        Self {
            max_tokens_per_turn: 2500,
            max_tokens_per_session: 50_000,
            session_used: 0,
        }
    }
}

impl TokenBudget {
    fn snapshot(&self) -> OrderedMap {
        let mut map = OrderedMap::new();
        map.insert("max_tokens_per_turn", json!(self.max_tokens_per_turn));
        map.insert("max_tokens_per_session", json!(self.max_tokens_per_session));
        map.insert("session_used", json!(self.session_used));
        map.insert(
            "session_remaining",
            json!(self
                .max_tokens_per_session
                .saturating_sub(self.session_used)),
        );
        map
    }
}
