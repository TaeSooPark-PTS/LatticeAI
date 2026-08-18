//! Direct tool surface, minus the document-matrix worker routes (v11.6.0, WP-R8).
//!
//! Filesystem writes go through [`lattice_agent::sandbox::Workspace`] so the
//! three writers (write_file, edit_file, proposal-approve) share one path
//! policy. Traversal denials are the exact Python `ToolError` / i18n bodies
//! the fixtures pin.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use axum::http::StatusCode;
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_agent::sandbox::Workspace;
use lattice_auth::{AuthState, Identity, OrderedMap};
use serde_json::{json, Value};

use crate::toolsurface::mcp::{detail, json_status, missing_fields};

pub(crate) mod downloads;
pub(crate) mod fs;
pub(crate) mod gov;
pub(crate) mod knowledge;
pub(crate) mod meta;
pub(crate) mod shell;

pub use gov::{governance_for, permission_for};

use downloads::{
    clear_history, download, download_zip, inspect_html, preview_url, todo_read, todo_write,
};
use fs::{edit_file, grep, list_dir, read_file, search_files, workspace_tree, write_file};
use gov::{default_gov, gov_named};
use knowledge::{
    knowledge_save, knowledge_search, knowledge_tree, obsidian_save, obsidian_search,
    obsidian_status, obsidian_tree,
};
use meta::{create_docx, create_pdf, create_pptx, create_xlsx, diagnostics, permissions, registry};
use shell::{
    build_project, deploy_project, git_diff, git_log, git_show, git_status, network_status,
    run_command,
};

pub const MOUNTED: &[(&str, &str)] = &[
    ("POST", "/tools/list_dir"),
    ("POST", "/tools/workspace_tree"),
    ("POST", "/tools/read_file"),
    ("POST", "/tools/write_file"),
    ("POST", "/tools/edit_file"),
    ("POST", "/tools/search_files"),
    ("POST", "/tools/grep"),
    ("POST", "/tools/todo_read"),
    ("POST", "/tools/todo_write"),
    ("POST", "/tools/clear_history"),
    ("POST", "/tools/inspect_html"),
    ("POST", "/tools/preview_url"),
    ("GET", "/tools/download"),
    ("GET", "/tools/download_zip"),
    ("POST", "/tools/knowledge_save"),
    ("POST", "/tools/knowledge_search"),
    ("GET", "/tools/knowledge_tree"),
    ("POST", "/tools/obsidian_save"),
    ("POST", "/tools/obsidian_search"),
    ("GET", "/tools/obsidian_tree"),
    ("GET", "/obsidian/status"),
    ("GET", "/tools/git_status"),
    ("POST", "/tools/git_diff"),
    ("POST", "/tools/git_log"),
    ("POST", "/tools/git_show"),
    ("POST", "/tools/run_command"),
    ("GET", "/tools/network_status"),
    ("POST", "/tools/build_project"),
    ("POST", "/tools/deploy_project"),
    ("GET", "/tools/permissions"),
    ("GET", "/tools/registry"),
    ("GET", "/tools/registry/diagnostics"),
    // Native product routes (W3b). Spec still lives in worker_keep.json.
    ("POST", "/tools/create_docx"),
    ("POST", "/tools/create_xlsx"),
    ("POST", "/tools/create_pptx"),
    ("POST", "/tools/create_pdf"),
];

#[derive(Clone)]
pub struct ToolsState {
    pub auth: Arc<AuthState>,
    pub workspace: Workspace,
    pub brain_dir: PathBuf,
    pub require_auth: bool,
    pub worker: Option<lattice_core::worker::WorkerSeamClient>,
}

impl ToolsState {
    pub fn new(auth: Arc<AuthState>, workspace: Workspace, brain_dir: impl AsRef<Path>) -> Self {
        Self {
            auth,
            workspace,
            brain_dir: brain_dir.as_ref().to_path_buf(),
            require_auth: true,
            worker: None,
        }
    }

    /// Worker render seam (`POST /worker/render/{kind}`).
    pub fn with_worker(mut self, worker: lattice_core::worker::WorkerSeamClient) -> Self {
        self.worker = Some(worker);
        self
    }
}

pub fn router(state: ToolsState) -> Router {
    Router::new()
        .route("/tools/list_dir", post(list_dir))
        .route("/tools/workspace_tree", post(workspace_tree))
        .route("/tools/read_file", post(read_file))
        .route("/tools/write_file", post(write_file))
        .route("/tools/edit_file", post(edit_file))
        .route("/tools/search_files", post(search_files))
        .route("/tools/grep", post(grep))
        .route("/tools/todo_read", post(todo_read))
        .route("/tools/todo_write", post(todo_write))
        .route("/tools/clear_history", post(clear_history))
        .route("/tools/inspect_html", post(inspect_html))
        .route("/tools/preview_url", post(preview_url))
        .route("/tools/download", get(download))
        .route("/tools/download_zip", get(download_zip))
        .route("/tools/knowledge_save", post(knowledge_save))
        .route("/tools/knowledge_search", post(knowledge_search))
        .route("/tools/knowledge_tree", get(knowledge_tree))
        .route("/tools/obsidian_save", post(obsidian_save))
        .route("/tools/obsidian_search", post(obsidian_search))
        .route("/tools/obsidian_tree", get(obsidian_tree))
        .route("/obsidian/status", get(obsidian_status))
        .route("/tools/git_status", get(git_status))
        .route("/tools/git_diff", post(git_diff))
        .route("/tools/git_log", post(git_log))
        .route("/tools/git_show", post(git_show))
        .route("/tools/run_command", post(run_command))
        .route("/tools/network_status", get(network_status))
        .route("/tools/build_project", post(build_project))
        .route("/tools/deploy_project", post(deploy_project))
        .route("/tools/permissions", get(permissions))
        .route("/tools/registry", get(registry))
        .route("/tools/registry/diagnostics", get(diagnostics))
        .route("/tools/create_docx", post(create_docx))
        .route("/tools/create_xlsx", post(create_xlsx))
        .route("/tools/create_pptx", post(create_pptx))
        .route("/tools/create_pdf", post(create_pdf))
        .with_state(state)
}

pub(crate) fn tool_ok(workspace: &Workspace, result: Value) -> Response {
    let mut body = OrderedMap::new();
    body.insert("status", json!("ok"));
    body.insert(
        "workspace",
        json!(workspace.root().to_string_lossy().into_owned()),
    );
    body.insert("result", result);
    json_status(StatusCode::OK, &body)
}

pub(crate) fn tool_err(message: &str) -> Response {
    detail(StatusCode::BAD_REQUEST, message)
}

/// Failure from a tool's core (the HTTP handler turns this into a response).
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum ToolExecError {
    Missing(&'static str),
    Message(String),
}

impl ToolExecError {
    pub(crate) fn into_response(self, input: &Value) -> Response {
        match self {
            Self::Missing(field) => missing_fields(input, &[field]),
            Self::Message(message) => tool_err(&message),
        }
    }
}

pub(crate) fn policy_denied_message(tool: &str) -> String {
    format!(
        "'{tool}' 툴은 명시 승인이 필요합니다 (permission_mode=strict). 승인 UI가 없는 직접 실행 경로에서 차단되었습니다."
    )
}

/// Same rules [`enforce`] applies, as a string so MCP can surface them as JSON-RPC.
pub(crate) fn check_governance(
    name: &str,
    identity: &Identity,
    trusted_admin: bool,
) -> Result<(), String> {
    let g = gov_named(name).unwrap_or_else(default_gov);
    if matches!(name, "run_command" | "build_project" | "deploy_project")
        && !trusted_admin
        && identity.role != "admin"
        && identity.role != "owner"
        && !identity.is_local_owner()
    {
        return Err(format!("'{name}' 툴은 관리자 전용입니다."));
    }
    if !trusted_admin
        && !g.auto_approve
        && identity.role != "admin"
        && identity.role != "owner"
        && !identity.is_local_owner()
    {
        return Err(policy_denied_message(name));
    }
    Ok(())
}

pub(crate) fn enforce(
    name: &str,
    identity: &Identity,
    trusted_admin: bool,
) -> Result<(), Response> {
    check_governance(name, identity, trusted_admin)
        .map_err(|message| detail(StatusCode::FORBIDDEN, &message))
}

pub(crate) fn resolve(ws: &Workspace, path: &str) -> Result<PathBuf, Response> {
    ws.resolve(path).map_err(|e| tool_err(&e.message))
}

pub(crate) fn relative(ws: &Workspace, path: &Path) -> String {
    ws.relative(path)
}
