//! Bind the product `POST /agent` surface to the real loop.
//!
//! `lattice-platform::agents` still mounts a stub that always answers
//! "No model loaded" (and never sets `model_loaded`). The working loop is
//! `lattice-agent`'s `loop_router`, mounted at `/rust/agent/{run,resume,
//! approvals}`. This layer:
//!
//! 1. Rewrites `/agent`, `/agent/resume`, `GET /agent/approvals` onto those
//!    native paths so a client written against the product contract reaches
//!    the loop.
//! 2. Injects the real tool-governance table and `tool_names` when the body
//!    did not carry them, so the loop's default prompts name the full set
//!    rather than the five-tool core catalog.
//! 3. Overwrites `user_role` from the session, same rule as
//!    [`super::identity::inject_user_role`].

use std::collections::BTreeMap;
use std::sync::Arc;

use axum::body::Body;
use axum::extract::{Request, State};
use axum::http::{Method, StatusCode};
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use axum::Router;
use lattice_agent::policy::{default_blocked_write_prefixes, PolicyTable, ToolPolicy};
use lattice_agent::sandbox::Workspace;
use lattice_agent::{loop_router, LoopConfig};
use lattice_auth::AuthState;
use serde_json::{json, Map, Value};
use tower::util::ServiceExt;

use super::identity::{MAX_REWRITTEN_BODY, ROLE_FIELD};

/// Paths this layer rewrites onto the loop router.
const REWRITES: &[(&str, &str, &str)] = &[
    ("POST", "/agent", "/rust/agent/run"),
    ("POST", "/agent/resume", "/rust/agent/resume"),
    ("GET", "/agent/approvals", "/rust/agent/approvals"),
];

/// Bodies that receive the policy table (the run, not resume/approvals).
const POLICY_PATHS: &[&str] = &["/agent", "/rust/agent/run"];

/// What the layer needs: identity, the table it injects, and the real loop.
#[derive(Clone)]
pub struct AgentBindState {
    /// Process-wide sessions.
    pub auth: Arc<AuthState>,
    /// The product tool-governance table, built once at boot.
    pub policies: Arc<PolicyTable>,
    /// The loop router at `/rust/agent/{run,resume,approvals}`.
    ///
    /// Called directly for `/agent` so the platform stub never answers.
    pub loop_router: Router,
}

impl AgentBindState {
    /// Bind identity, the product policy table, and a loop over `workspace`.
    pub fn new(auth: Arc<AuthState>, workspace: Workspace, config: LoopConfig) -> Self {
        Self {
            auth,
            policies: Arc::new(product_policy_table()),
            loop_router: loop_router(workspace, config),
        }
    }
}

/// Rewrite `/agent` onto the loop and fill in the policy table.
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
    let inject = method == Method::POST && POLICY_PATHS.contains(&path.as_str());
    if rewrite.is_none() && !inject && path != "/rust/agent/run" {
        return next.run(request).await;
    }

    let identity = match bind.auth.require_user(request.headers()) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };

    let (mut parts, body) = request.into_parts();
    if let Some(target) = rewrite {
        if let Some(rewritten) = rewrite_uri(&parts.uri, target) {
            parts.uri = rewritten;
        }
    }

    if method != Method::POST {
        let request = Request::from_parts(parts, body);
        if rewrite.is_some() {
            return match bind.loop_router.clone().oneshot(request).await {
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
    let rewritten = with_policies_and_role(&bytes, &bind.policies, &identity.role)
        .unwrap_or_else(|| bytes.to_vec());
    parts.headers.remove(axum::http::header::CONTENT_LENGTH);
    parts.headers.insert(
        axum::http::header::CONTENT_LENGTH,
        axum::http::HeaderValue::from(rewritten.len()),
    );
    let request = Request::from_parts(parts, Body::from(rewritten));
    if rewrite.is_some() {
        return match bind.loop_router.clone().oneshot(request).await {
            Ok(response) => response,
            Err(error) => match error {},
        };
    }
    next.run(request).await
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

fn with_policies_and_role(body: &[u8], table: &PolicyTable, role: &str) -> Option<Vec<u8>> {
    let mut parsed: Value = serde_json::from_slice(body).ok()?;
    let object = parsed.as_object_mut()?;
    object.insert(ROLE_FIELD.to_string(), Value::String(role.to_string()));
    if policies_missing(object) {
        if let Ok(value) = serde_json::to_value(table) {
            object.insert("policies".into(), value);
        }
    }
    if tool_names_missing(object) {
        let names: Vec<String> = table.tools.keys().cloned().collect();
        object.insert("tool_names".into(), json!(names));
    }
    serde_json::to_vec(&parsed).ok()
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

/// Every name `lattice_platform::tools::governance_for` knows.
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
];

/// Build the product policy table from platform governance.
pub fn product_policy_table() -> PolicyTable {
    let mut tools = BTreeMap::new();
    for name in GOVERNED_TOOLS {
        let gov = lattice_platform::tools::governance_for(name);
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
        assert!(table.tools.len() > 20);
        assert_eq!(table.tools["write_file"].risk, "write");
        assert_eq!(table.tools["read_file"].risk, "read");
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
