use std::collections::{BTreeMap, HashMap};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use axum::body::Body;
use axum::extract::{Request, State};
use axum::http::{Method, StatusCode};
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use axum::Router;
use lattice_agent::kernel::policy::{default_blocked_write_prefixes, PolicyTable, ToolPolicy};
use lattice_agent::surface::looproutes::{loop_router, LoopConfig};
use lattice_agent::tools::sandbox::Workspace;
use lattice_auth::{AuthState, WorkspaceResolver};
use serde_json::{json, Map, Value};
use tower::util::ServiceExt;

use super::identity::{MAX_REWRITTEN_BODY, ROLE_FIELD};
use super::isolation::{
    extract_requested_workspace, resolve_agent_scope, stamp_verified_headers, ResolvedAgentScope,
    ScopeResolveRequest,
};
use super::onedoor::OneDoorState;

const REWRITES: &[(&str, &str, &str)] = &[
    ("POST", "/agent", "/rust/agent/run"),
    ("POST", "/agent/resume", "/rust/agent/resume"),
    ("GET", "/agent/approvals", "/rust/agent/approvals"),
];

const NATIVE_LOOP_PATHS: &[(&str, &str)] = &[
    ("POST", "/rust/agent/run"),
    ("POST", "/rust/agent/resume"),
    ("GET", "/rust/agent/approvals"),
];

const POLICY_PATHS: &[&str] = &["/agent", "/rust/agent/run"];

#[derive(Clone)]
pub struct AgentBindState {
    pub auth: Arc<AuthState>,
    pub policies: Arc<PolicyTable>,
    pub loop_router: Router,
    base_root: PathBuf,
    data_dir: PathBuf,
    loop_config: LoopConfig,
    resolver: Option<Arc<dyn WorkspaceResolver>>,
    isolated_routers: Arc<Mutex<HashMap<PathBuf, Router>>>,
}

impl AgentBindState {
    pub fn new(auth: Arc<AuthState>, workspace: Workspace, config: LoopConfig) -> Self {
        let base_root = workspace.root().to_path_buf();
        Self {
            auth,
            policies: Arc::new(product_policy_table()),
            loop_router: loop_router(workspace, config.clone()),
            base_root,
            data_dir: std::env::temp_dir(),
            loop_config: config,
            resolver: None,
            isolated_routers: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    pub fn from_product(product: &OneDoorState) -> Self {
        Self {
            auth: Arc::clone(&product.auth),
            policies: Arc::new(product_policy_table()),
            loop_router: loop_router(product.workspace.clone(), product.loop_config.clone()),
            base_root: product.agent_root.clone(),
            data_dir: product.data_dir.clone(),
            loop_config: product.loop_config.clone(),
            resolver: Some(Arc::clone(&product.resolver) as Arc<dyn WorkspaceResolver>),
            isolated_routers: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    fn router_for(&self, scope: &ResolvedAgentScope) -> Result<Router, Box<Response>> {
        if !scope.isolated {
            return Ok(self.loop_router.clone());
        }
        let mut guard = self.isolated_routers.lock().expect("loop router cache");
        if let Some(router) = guard.get(&scope.agent_root) {
            return Ok(router.clone());
        }
        if guard.len() >= 128 {
            guard.clear();
        }
        let ws = scope.workspace()?;
        let router = loop_router(ws, self.loop_config.clone());
        guard.insert(scope.agent_root.clone(), router.clone());
        Ok(router)
    }
}

pub async fn bind_agent_run(
    State(bind): State<AgentBindState>,
    request: Request,
    next: Next,
) -> Response {
    let method = request.method().clone();
    let path = request.uri().path().to_string();
    let rewrite = REWRITES
        .iter()
        .find(|(want_method, from, _)| method == *want_method && path == *from)
        .map(|(_, _, to)| *to);
    let is_native_loop = NATIVE_LOOP_PATHS
        .iter()
        .any(|(want_method, p)| method == *want_method && path == *p);
    let inject = method == Method::POST && POLICY_PATHS.contains(&path.as_str());
    if rewrite.is_none() && !is_native_loop && !inject {
        return next.run(request).await;
    }

    let (mut parts, body) = request.into_parts();
    if let Some(target) = rewrite {
        if let Some(rewritten) = rewrite_uri(&parts.uri, target) {
            parts.uri = rewritten;
        }
    }

    if method != Method::POST {
        let requested_ws = match extract_requested_workspace(&parts.headers, &parts.uri, None) {
            Ok(ws) => ws,
            Err(resp) => return *resp,
        };
        let scope = match resolve_agent_scope(ScopeResolveRequest {
            auth: &bind.auth,
            resolver: bind.resolver.as_deref(),
            base_agent_root: &bind.base_root,
            data_dir: &bind.data_dir,
            headers: &parts.headers,
            uri: &parts.uri,
            requested_ws: requested_ws.as_deref(),
            write_intent: false,
        }) {
            Ok(scope) => scope,
            Err(refusal) => return *refusal,
        };
        stamp_verified_headers(&mut parts.headers, &scope);
        if !scope.identity.email.is_empty() {
            if let Some(forced) = force_user_query(&parts.uri, &scope.identity.email) {
                parts.uri = forced;
            }
        }
        let target_router = match bind.router_for(&scope) {
            Ok(router) => router,
            Err(resp) => return *resp,
        };
        let request = Request::from_parts(parts, body);
        if rewrite.is_some() || scope.isolated {
            return match target_router.oneshot(request).await {
                Ok(response) => response,
                Err(error) => match error {},
            };
        }
        return next.run(request).await;
    }

    let bytes = match axum::body::to_bytes(body, MAX_REWRITTEN_BODY).await {
        Ok(bytes) => bytes,
        Err(err) => {
            return (
                StatusCode::BAD_REQUEST,
                axum::Json(json!({
                    "error": "invalid_request",
                    "detail": format!("could not read the agent run request: {err}"),
                })),
            )
                .into_response()
        }
    };
    let parsed_json = serde_json::from_slice::<Value>(&bytes).ok();
    let requested_ws =
        match extract_requested_workspace(&parts.headers, &parts.uri, parsed_json.as_ref()) {
            Ok(ws) => ws,
            Err(resp) => return *resp,
        };
    let scope = match resolve_agent_scope(ScopeResolveRequest {
        auth: &bind.auth,
        resolver: bind.resolver.as_deref(),
        base_agent_root: &bind.base_root,
        data_dir: &bind.data_dir,
        headers: &parts.headers,
        uri: &parts.uri,
        requested_ws: requested_ws.as_deref(),
        write_intent: true,
    }) {
        Ok(scope) => scope,
        Err(refusal) => return *refusal,
    };
    stamp_verified_headers(&mut parts.headers, &scope);

    let rewritten = with_verified_scope_and_policies(
        &bytes,
        if inject { Some(&bind.policies) } else { None },
        &scope,
    )
    .unwrap_or_else(|| bytes.to_vec());
    parts.headers.remove(axum::http::header::CONTENT_LENGTH);
    parts.headers.insert(
        axum::http::header::CONTENT_LENGTH,
        axum::http::HeaderValue::from(rewritten.len()),
    );
    let target_router = match bind.router_for(&scope) {
        Ok(router) => router,
        Err(resp) => return *resp,
    };
    let request = Request::from_parts(parts, Body::from(rewritten));
    if rewrite.is_some() || scope.isolated {
        return match target_router.oneshot(request).await {
            Ok(response) => response,
            Err(error) => match error {},
        };
    }
    next.run(request).await
}

fn force_user_query(uri: &axum::http::Uri, email: &str) -> Option<axum::http::Uri> {
    let mut parts = uri.clone().into_parts();
    let path = uri.path();
    let mut kept: Vec<String> = uri
        .query()
        .unwrap_or("")
        .split('&')
        .filter(|pair| !pair.is_empty() && !pair.starts_with("user="))
        .map(str::to_string)
        .collect();
    let encoded: String = email
        .bytes()
        .map(|b| {
            if b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.' | b'~') {
                (b as char).to_string()
            } else {
                format!("%{b:02X}")
            }
        })
        .collect();
    kept.push(format!("user={encoded}"));
    parts.path_and_query = Some(format!("{path}?{}", kept.join("&")).parse().ok()?);
    axum::http::Uri::from_parts(parts).ok()
}

fn rewrite_uri(uri: &axum::http::Uri, path: &str) -> Option<axum::http::Uri> {
    let mut parts = uri.clone().into_parts();
    let query = uri
        .query()
        .map(|query| format!("?{query}"))
        .unwrap_or_default();
    parts.path_and_query = Some(format!("{path}{query}").parse().ok()?);
    axum::http::Uri::from_parts(parts).ok()
}

fn with_verified_scope_and_policies(
    body: &[u8],
    table: Option<&PolicyTable>,
    scope: &ResolvedAgentScope,
) -> Option<Vec<u8>> {
    let mut parsed: Value = serde_json::from_slice(body).ok()?;
    let object = parsed.as_object_mut()?;
    object.insert(
        ROLE_FIELD.to_string(),
        Value::String(scope.identity.role.clone()),
    );
    if scope.identity.email.is_empty() {
        object.remove("user_email");
    } else {
        object.insert(
            "user_email".into(),
            Value::String(scope.identity.email.clone()),
        );
    }
    match &scope.tenant_id {
        Some(tenant) => {
            object.insert("tenant_id".into(), Value::String(tenant.clone()));
        }
        None => {
            object.remove("tenant_id");
        }
    }
    object.insert(
        "workspace_id".into(),
        Value::String(scope.workspace_id.clone()),
    );
    if let Some(table) = table {
        if policies_missing(object) {
            if let Ok(value) = serde_json::to_value(table) {
                object.insert("policies".into(), value);
            }
        }
        if tool_names_missing(object) {
            let names: Vec<String> = table.tools.keys().cloned().collect();
            object.insert("tool_names".into(), json!(names));
        }
    }
    serde_json::to_vec(&parsed).ok()
}

#[cfg(test)]
fn with_policies_and_role(body: &[u8], table: &PolicyTable, role: &str) -> Option<Vec<u8>> {
    let scope = ResolvedAgentScope {
        identity: lattice_auth::Identity {
            email: String::new(),
            role: role.to_string(),
        },
        tenant_id: None,
        workspace_id: "personal".into(),
        agent_root: PathBuf::from("."),
        skills_dir: None,
        shared_authorized: false,
        isolated: false,
    };
    with_verified_scope_and_policies(body, Some(table), &scope)
}

fn policies_missing(object: &Map<String, Value>) -> bool {
    match object.get("policies") {
        None | Some(Value::Null) => true,
        Some(Value::Object(policy)) => policy
            .get("tools")
            .and_then(Value::as_object)
            .map(serde_json::Map::is_empty)
            .unwrap_or(true),
        _ => true,
    }
}

fn tool_names_missing(object: &Map<String, Value>) -> bool {
    match object.get("tool_names") {
        None | Some(Value::Null) => true,
        Some(Value::Array(names)) => names.is_empty(),
        _ => true,
    }
}

const GOVERNED_TOOLS: &[&str] = &[
    "list_dir",
    "workspace_tree",
    "read_file",
    "search_files",
    "grep",
    "inspect_html",
    "preview_url",
    "todo_read",
    "local_list",
    "local_read",
    "read_document",
    "git_status",
    "git_diff",
    "git_log",
    "git_show",
    "knowledge_search",
    "knowledge_tree",
    "obsidian_search",
    "obsidian_tree",
    "computer_screenshot",
    "computer_status",
    "chrome_status",
    "computer_use_status",
    "network_status",
    "write_file",
    "edit_file",
    "create_web_project",
    "create_docx",
    "create_xlsx",
    "create_pptx",
    "create_pdf",
    "todo_write",
    "knowledge_save",
    "obsidian_save",
    "local_write",
    "run_command",
    "build_project",
    "deploy_project",
    "computer_click",
    "computer_type",
    "computer_key",
    "computer_scroll",
    "computer_drag",
    "computer_move",
    "computer_open_app",
    "computer_open_url",
    "vision_analyze",
    "web_fetch",
    "web_search",
    "computer_ui_inspect",
];

pub fn product_policy_table() -> PolicyTable {
    let mut tools = BTreeMap::new();
    for name in GOVERNED_TOOLS {
        let gov = lattice_platform::toolsurface::tools::governance_for(name);
        tools.insert((*name).to_string(), policy_from_gov(&gov));
    }
    PolicyTable {
        tools,
        default: ToolPolicy::default(),
        blocked_write_prefixes: default_blocked_write_prefixes(),
    }
}

fn policy_from_gov(gov: &lattice_auth::OrderedMap) -> ToolPolicy {
    let get = |key: &str| gov.get(key).and_then(Value::as_str).unwrap_or("");
    ToolPolicy {
        risk: get("risk").to_string(),
        destructive: gov
            .get("destructive")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        shell: gov.get("shell").and_then(Value::as_bool).unwrap_or(false),
        network: gov.get("network").and_then(Value::as_bool).unwrap_or(false),
        auto_approve: gov
            .get("auto_approve")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        sandbox: {
            let sandbox = get("sandbox");
            if sandbox.is_empty() {
                "workspace".into()
            } else {
                sandbox.to_string()
            }
        },
        rollback: get("rollback").to_string(),
        capability: gov
            .get("capability")
            .and_then(Value::as_str)
            .map(str::to_string),
        scope: gov.get("scope").and_then(Value::as_str).map(str::to_string),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_product_table_names_the_writers_the_loop_runs() {
        let table = product_policy_table();
        assert!(table.tools.contains_key("write_file"));
        assert!(table.tools.contains_key("read_file"));
        assert!(table.tools.contains_key("run_command"));
        assert!(table.tools.contains_key("web_fetch"));
        assert!(table.tools.contains_key("web_search"));
        assert!(table.tools.contains_key("computer_ui_inspect"));
        assert!(table.tools.len() > 20);
        assert_eq!(table.tools["write_file"].risk, "write");
        assert_eq!(table.tools["read_file"].risk, "read");
        assert_eq!(table.tools["web_fetch"].risk, "read");
        assert!(table.tools["web_fetch"].network);
        assert!(table.tools["web_fetch"].auto_approve);
        assert_eq!(table.tools["computer_ui_inspect"].risk, "read");
        assert!(table.tools["computer_ui_inspect"].auto_approve);
        assert_eq!(
            table.tools["computer_ui_inspect"].capability.as_deref(),
            Some("desktop:control")
        );
    }

    #[test]
    fn a_bare_run_body_gains_policies_and_the_session_role() {
        let table = product_policy_table();
        let rewritten = with_policies_and_role(br#"{"message":"hi"}"#, &table, "owner").unwrap();
        let value: Value = serde_json::from_slice(&rewritten).unwrap();
        assert_eq!(value["user_role"], "owner");
        assert!(value["policies"]["tools"]["write_file"].is_object());
        assert!(value["tool_names"]
            .as_array()
            .unwrap()
            .iter()
            .any(|name| name == "write_file"));
    }

    #[test]
    fn a_caller_supplied_table_is_left_alone() {
        let table = product_policy_table();
        let body = br#"{"message":"hi","policies":{"tools":{"only":{"risk":"read"}}},"tool_names":["only"]}"#;
        let rewritten = with_policies_and_role(body, &table, "user").unwrap();
        let value: Value = serde_json::from_slice(&rewritten).unwrap();
        assert_eq!(value["user_role"], "user");
        assert!(value["policies"]["tools"].get("only").is_some());
        assert!(value["policies"]["tools"].get("write_file").is_none());
        assert_eq!(value["tool_names"], json!(["only"]));
    }
}
