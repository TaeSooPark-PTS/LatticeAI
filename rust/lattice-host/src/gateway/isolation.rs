use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use axum::body::Body;
use axum::extract::{Request, State};
use axum::http::{header, HeaderMap, HeaderName, HeaderValue, Method, StatusCode};
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use axum::Router;
use lattice_agent::tools::sandbox::{
    is_shared_workspace_id, resolve_isolated_agent_root, sanitize_identity_segment,
    IsolatedRootSpec, Workspace,
};
use lattice_auth::{requested_tenant, AuthState, Identity, WorkspaceResolver};
use lattice_platform::toolsurface::{computer_use, mcp, tools};
use serde_json::{json, Value};
use tower::util::ServiceExt;

use super::identity::MAX_REWRITTEN_BODY;
use super::onedoor::OneDoorState;

const CACHE_CAP: usize = 128;

pub const HDR_USER_EMAIL: &str = "x-lattice-user-email";
pub const HDR_USER_ROLE: &str = "x-lattice-user-role";
pub const HDR_TENANT_ID: &str = "x-lattice-tenant-id";
pub const HDR_AGENT_ROOT: &str = "x-lattice-agent-root";

#[derive(Debug, Clone)]
pub struct ResolvedAgentScope {
    pub identity: Identity,
    pub tenant_id: Option<String>,
    pub workspace_id: String,
    pub agent_root: PathBuf,
    pub skills_dir: Option<PathBuf>,
    pub shared_authorized: bool,
    pub isolated: bool,
}

impl ResolvedAgentScope {
    pub fn workspace(&self) -> Result<Workspace, Box<Response>> {
        let ws = Workspace::new(&self.agent_root).map_err(|err| {
            Box::new(
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    axum::Json(json!({
                        "error": "workspace_init_failed",
                        "detail": err.to_string(),
                    })),
                )
                    .into_response(),
            )
        })?;
        Ok(if self.isolated {
            ws.with_confined(true)
        } else {
            ws
        })
    }
}

fn non_empty(value: &str) -> Option<String> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed.to_string())
    }
}

fn query_param(uri: &axum::http::Uri, name: &str) -> Option<String> {
    let query = uri.query()?;
    for pair in query.split('&') {
        let (k, v) = pair.split_once('=').unwrap_or((pair, ""));
        if k == name && !v.trim().is_empty() {
            return Some(percent_decode(v.trim()));
        }
    }
    None
}

fn percent_decode(raw: &str) -> String {
    let bytes = raw.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            if let (Some(hi), Some(lo)) = (hex_val(bytes[i + 1]), hex_val(bytes[i + 2])) {
                out.push((hi << 4) | lo);
                i += 3;
                continue;
            }
        }
        if bytes[i] == b'+' {
            out.push(b' ');
        } else {
            out.push(bytes[i]);
        }
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn hex_val(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        b'A'..=b'F' => Some(byte - b'A' + 10),
        _ => None,
    }
}

pub fn extract_requested_workspace(
    headers: &HeaderMap,
    uri: &axum::http::Uri,
    body_json: Option<&Value>,
) -> Result<Option<String>, Box<Response>> {
    let hdr_ws = headers
        .get("x-workspace-id")
        .and_then(|v| v.to_str().ok())
        .and_then(non_empty);
    let qry_ws = query_param(uri, "workspace_id");
    let body_ws = body_json
        .and_then(|v| v.get("workspace_id"))
        .and_then(Value::as_str)
        .and_then(non_empty);

    let candidates: Vec<&str> = [hdr_ws.as_deref(), qry_ws.as_deref(), body_ws.as_deref()]
        .into_iter()
        .flatten()
        .collect();
    if let Some(&first) = candidates.first() {
        if candidates.iter().any(|&other| other != first) {
            return Err(Box::new(
                (
                    StatusCode::BAD_REQUEST,
                    axum::Json(json!({
                        "error": "conflicting_workspace_scope",
                        "detail": "workspace_id differs between header, query, and body",
                    })),
                )
                    .into_response(),
            ));
        }
        return Ok(Some(first.to_string()));
    }
    Ok(None)
}

pub struct ScopeResolveRequest<'a> {
    pub auth: &'a AuthState,
    pub resolver: Option<&'a dyn WorkspaceResolver>,
    pub base_agent_root: &'a std::path::Path,
    pub data_dir: &'a std::path::Path,
    pub headers: &'a HeaderMap,
    pub uri: &'a axum::http::Uri,
    pub requested_ws: Option<&'a str>,
    pub write_intent: bool,
}

pub fn resolve_agent_scope(
    req: ScopeResolveRequest<'_>,
) -> Result<ResolvedAgentScope, Box<Response>> {
    let ScopeResolveRequest {
        auth,
        resolver,
        base_agent_root,
        data_dir,
        headers,
        uri,
        requested_ws,
        write_intent,
    } = req;
    let identity = auth.require_user(headers).map_err(Box::new)?;
    let req_tenant = requested_tenant(headers, query_param(uri, "tenant_id").as_deref());
    let tenant_id = auth
        .verify_tenant_access(&identity, req_tenant.as_deref())
        .map_err(Box::new)?;

    let user_opt = non_empty(&identity.email);
    let require_user_isolation = auth.config().require_auth || user_opt.is_some();

    let shared = is_shared_workspace_id(requested_ws);
    let mut shared_authorized = false;
    let resolved_ws_id = if shared {
        let ws = requested_ws.unwrap_or_default().trim();
        let auth_result = match resolver {
            Some(res) => {
                if write_intent {
                    res.resolve_write_scope(Some(ws), user_opt.as_deref())
                } else {
                    res.resolve_read_scope(Some(ws), user_opt.as_deref())
                }
            }
            None => Err("workspace resolver unavailable".to_string()),
        };
        match auth_result {
            Ok(Some(resolved)) => {
                shared_authorized = true;
                resolved
            }
            Ok(None) => {
                auth.record_audit(
                    "agent.workspace.denied",
                    json!({
                        "email": identity.email,
                        "workspace_id": ws,
                        "write_intent": write_intent,
                        "reason": "empty_scope",
                    }),
                );
                return Err(Box::new(
                    (
                        StatusCode::FORBIDDEN,
                        axum::Json(json!({
                            "error": "workspace_access_denied",
                            "detail": "shared workspace access denied",
                        })),
                    )
                        .into_response(),
                ));
            }
            Err(detail) => {
                auth.record_audit(
                    "agent.workspace.denied",
                    json!({
                        "email": identity.email,
                        "workspace_id": ws,
                        "write_intent": write_intent,
                        "reason": detail,
                    }),
                );
                return Err(Box::new(
                    (
                        StatusCode::FORBIDDEN,
                        axum::Json(json!({
                            "error": "workspace_access_denied",
                            "detail": detail,
                        })),
                    )
                        .into_response(),
                ));
            }
        }
    } else {
        "personal".to_string()
    };

    let isolated = require_user_isolation || shared;
    let agent_root = resolve_isolated_agent_root(&IsolatedRootSpec {
        base_root: base_agent_root.to_path_buf(),
        user_id: user_opt.clone(),
        tenant_id: tenant_id.clone(),
        workspace_id: if shared {
            Some(resolved_ws_id.clone())
        } else {
            None
        },
        shared_authorized,
        require_user_isolation,
    })
    .map_err(|err| {
        Box::new(
            (
                StatusCode::FORBIDDEN,
                axum::Json(json!({
                    "error": "workspace_isolation_error",
                    "detail": err.message,
                })),
            )
                .into_response(),
        )
    })?;

    let skills_dir = if isolated {
        let mut base = data_dir.to_path_buf();
        if let Some(ref tenant) = tenant_id {
            base = base.join("tenants").join(sanitize_identity_segment(tenant));
        }
        let dir = if shared {
            base.join("workspaces")
                .join(sanitize_identity_segment(&resolved_ws_id))
                .join("skills")
        } else if let Some(ref user) = user_opt {
            base.join("users")
                .join(sanitize_identity_segment(user))
                .join("skills")
        } else {
            base.join("skills")
        };
        let _ = std::fs::create_dir_all(&dir);
        Some(dir)
    } else {
        None
    };

    Ok(ResolvedAgentScope {
        identity,
        tenant_id,
        workspace_id: resolved_ws_id,
        agent_root,
        skills_dir,
        shared_authorized,
        isolated,
    })
}

pub fn stamp_verified_headers(headers: &mut HeaderMap, scope: &ResolvedAgentScope) {
    for name in [
        HDR_USER_EMAIL,
        HDR_USER_ROLE,
        HDR_TENANT_ID,
        HDR_AGENT_ROOT,
        "x-workspace-id",
    ] {
        if let Ok(header_name) = HeaderName::from_bytes(name.as_bytes()) {
            headers.remove(&header_name);
        }
    }
    if let Ok(val) = HeaderValue::from_str(&scope.identity.email) {
        headers.insert(HeaderName::from_static(HDR_USER_EMAIL), val);
    }
    if let Ok(val) = HeaderValue::from_str(&scope.identity.role) {
        headers.insert(HeaderName::from_static(HDR_USER_ROLE), val);
    }
    if let Some(ref tenant) = scope.tenant_id {
        if let Ok(val) = HeaderValue::from_str(tenant) {
            headers.insert(HeaderName::from_static(HDR_TENANT_ID), val);
        }
    }
    if let Ok(val) = HeaderValue::from_str(&scope.agent_root.to_string_lossy()) {
        headers.insert(HeaderName::from_static(HDR_AGENT_ROOT), val);
    }
    if let Ok(val) = HeaderValue::from_str(&scope.workspace_id) {
        headers.insert(HeaderName::from_static("x-workspace-id"), val);
    }
}

#[derive(Clone)]
pub struct IsolationState {
    pub product: Arc<OneDoorState>,
    tools_cache: Arc<Mutex<HashMap<PathBuf, Router>>>,
    mcp_cache: Arc<Mutex<HashMap<(PathBuf, PathBuf), Router>>>,
    cu_cache: Arc<Mutex<HashMap<PathBuf, Router>>>,
    kernel_cache: Arc<Mutex<HashMap<PathBuf, Router>>>,
}

impl IsolationState {
    pub fn new(product: Arc<OneDoorState>) -> Self {
        Self {
            product,
            tools_cache: Arc::new(Mutex::new(HashMap::new())),
            mcp_cache: Arc::new(Mutex::new(HashMap::new())),
            cu_cache: Arc::new(Mutex::new(HashMap::new())),
            kernel_cache: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    fn tools_router_for(&self, scope: &ResolvedAgentScope) -> Result<Router, Box<Response>> {
        let mut guard = self.tools_cache.lock().expect("tools cache lock");
        if let Some(router) = guard.get(&scope.agent_root) {
            return Ok(router.clone());
        }
        if guard.len() >= CACHE_CAP {
            guard.clear();
        }
        let ws = scope.workspace()?;
        let state =
            tools::ToolsState::new(Arc::clone(&self.product.auth), ws, &self.product.brain_dir)
                .with_worker(self.product.seam.clone());
        let router = tools::router(state);
        guard.insert(scope.agent_root.clone(), router.clone());
        Ok(router)
    }

    fn mcp_router_for(&self, scope: &ResolvedAgentScope) -> Result<Router, Box<Response>> {
        let skills_dir = scope
            .skills_dir
            .clone()
            .unwrap_or_else(|| self.product.data_dir.join("skills"));
        let key = (scope.agent_root.clone(), skills_dir.clone());
        let mut guard = self.mcp_cache.lock().expect("mcp cache lock");
        if let Some(router) = guard.get(&key) {
            return Ok(router.clone());
        }
        if guard.len() >= CACHE_CAP {
            guard.clear();
        }
        let ws = scope.workspace()?;
        let tools_state =
            tools::ToolsState::new(Arc::clone(&self.product.auth), ws, &self.product.brain_dir)
                .with_worker(self.product.seam.clone());
        let mcp_state = mcp::McpState::new(Arc::clone(&self.product.auth), &self.product.data_dir)
            .with_skills_dir(skills_dir)
            .with_tools(tools_state);
        let router = mcp::router(mcp_state);
        guard.insert(key, router.clone());
        Ok(router)
    }

    fn cu_router_for(&self, scope: &ResolvedAgentScope) -> Router {
        let mut guard = self.cu_cache.lock().expect("cu cache lock");
        if let Some(router) = guard.get(&scope.agent_root) {
            return router.clone();
        }
        if guard.len() >= CACHE_CAP {
            guard.clear();
        }
        let _ = std::fs::create_dir_all(&scope.agent_root);
        let router = computer_use::router(computer_use::ComputerUseState::new(
            Arc::clone(&self.product.auth),
            Some(self.product.seam.clone()),
            scope.agent_root.clone(),
        ));
        guard.insert(scope.agent_root.clone(), router.clone());
        router
    }

    fn kernel_router_for(&self, scope: &ResolvedAgentScope) -> Result<Router, Box<Response>> {
        let mut guard = self.kernel_cache.lock().expect("kernel cache lock");
        if let Some(router) = guard.get(&scope.agent_root) {
            return Ok(router.clone());
        }
        if guard.len() >= CACHE_CAP {
            guard.clear();
        }
        let ws = scope.workspace()?;
        let router = lattice_agent::surface::router::kernel_router(ws);
        guard.insert(scope.agent_root.clone(), router.clone());
        Ok(router)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SurfaceKind {
    ComputerUse,
    Tools,
    McpOrSkills,
    AgentKernel,
    WorkspaceScoped,
}

fn classify_surface(path: &str) -> Option<SurfaceKind> {
    if path == "/tools/chrome_status"
        || path == "/tools/computer_use_status"
        || path.starts_with("/cu/")
    {
        return Some(SurfaceKind::ComputerUse);
    }
    if path.starts_with("/tools/") || path == "/obsidian/status" {
        return Some(SurfaceKind::Tools);
    }
    if path == "/mcp"
        || path.starts_with("/mcp/")
        || path.starts_with("/skills/")
        || path.starts_with("/plugins/directory")
    {
        return Some(SurfaceKind::McpOrSkills);
    }
    if path == "/rust/agent/preflight" || path == "/rust/agent/exec" {
        return Some(SurfaceKind::AgentKernel);
    }
    if path.starts_with("/api/automation/")
        || path == "/api/activity/runs"
        || path == "/automations/runs/combined"
        || path.starts_with("/api/browser/")
        || path == "/upload/document"
        || path.starts_with("/api/review-queue")
        || path.starts_with("/api/proposals")
        || path.starts_with("/api/hooks")
    {
        return Some(SurfaceKind::WorkspaceScoped);
    }
    None
}

fn write_intent_for(method: &Method, path: &str) -> bool {
    if *method == Method::GET || *method == Method::HEAD || *method == Method::OPTIONS {
        return false;
    }
    if matches!(
        path,
        "/tools/list_dir"
            | "/tools/workspace_tree"
            | "/tools/read_file"
            | "/tools/search_files"
            | "/tools/grep"
            | "/tools/todo_read"
            | "/tools/inspect_html"
            | "/tools/preview_url"
            | "/tools/knowledge_search"
            | "/tools/obsidian_search"
            | "/tools/git_diff"
            | "/tools/git_log"
            | "/tools/git_show"
            | "/rust/agent/preflight"
            | "/api/browser/read-url"
    ) {
        return false;
    }
    true
}

pub async fn enforce_agent_isolation(
    State(iso): State<IsolationState>,
    request: Request,
    next: Next,
) -> Response {
    let path = request.uri().path().to_string();
    let Some(surface) = classify_surface(&path) else {
        return next.run(request).await;
    };

    let method = request.method().clone();
    let (mut parts, body) = request.into_parts();
    let is_json = parts
        .headers
        .get(header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .is_some_and(|ct| ct.to_ascii_lowercase().contains("application/json"));

    let (parts_ref_body, parsed_json) =
        if is_json && matches!(method, Method::POST | Method::PUT | Method::PATCH) {
            match axum::body::to_bytes(body, MAX_REWRITTEN_BODY).await {
                Ok(bytes) => {
                    let parsed = serde_json::from_slice::<Value>(&bytes).ok();
                    (Body::from(bytes), parsed)
                }
                Err(err) => {
                    return (
                        StatusCode::BAD_REQUEST,
                        axum::Json(json!({
                            "error": "invalid_request",
                            "detail": format!("could not read request body: {err}"),
                        })),
                    )
                        .into_response();
                }
            }
        } else {
            (body, None)
        };

    let requested_ws =
        match extract_requested_workspace(&parts.headers, &parts.uri, parsed_json.as_ref()) {
            Ok(ws) => ws,
            Err(resp) => return *resp,
        };

    let write_intent = write_intent_for(&method, &path);
    let scope = match resolve_agent_scope(ScopeResolveRequest {
        auth: &iso.product.auth,
        resolver: Some(iso.product.resolver.as_ref()),
        base_agent_root: &iso.product.agent_root,
        data_dir: &iso.product.data_dir,
        headers: &parts.headers,
        uri: &parts.uri,
        requested_ws: requested_ws.as_deref(),
        write_intent,
    }) {
        Ok(scope) => scope,
        Err(resp) => return *resp,
    };

    stamp_verified_headers(&mut parts.headers, &scope);
    let req = Request::from_parts(parts, parts_ref_body);

    if !scope.isolated || surface == SurfaceKind::WorkspaceScoped {
        return next.run(req).await;
    }

    let router = match surface {
        SurfaceKind::ComputerUse => iso.cu_router_for(&scope),
        SurfaceKind::Tools => match iso.tools_router_for(&scope) {
            Ok(r) => r,
            Err(resp) => return *resp,
        },
        SurfaceKind::McpOrSkills => match iso.mcp_router_for(&scope) {
            Ok(r) => r,
            Err(resp) => return *resp,
        },
        SurfaceKind::AgentKernel => match iso.kernel_router_for(&scope) {
            Ok(r) => r,
            Err(resp) => return *resp,
        },
        SurfaceKind::WorkspaceScoped => return next.run(req).await,
    };

    match router.oneshot(req).await {
        Ok(resp) => resp,
        Err(never) => match never {},
    }
}

#[cfg(test)]
mod upload_scope_tests {
    use super::*;

    #[test]
    fn uploads_pass_the_workspace_membership_check() {
        assert!(matches!(
            classify_surface("/upload/document"),
            Some(SurfaceKind::WorkspaceScoped)
        ));
        assert!(write_intent_for(&Method::POST, "/upload/document"));
    }
}
