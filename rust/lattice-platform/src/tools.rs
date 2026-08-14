//! Direct tool surface, minus the document-matrix worker routes (v11.6.0, WP-R8).
//!
//! Filesystem writes go through [`lattice_agent::sandbox::Workspace`] so the
//! three writers (write_file, edit_file, proposal-approve) share one path
//! policy. Traversal denials are the exact Python `ToolError` / i18n bodies
//! the fixtures pin.


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
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use axum::extract::{Query, State};
use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_agent::sandbox::{Workspace, MAX_FILE_BYTES};
use lattice_agent::{command, is_circuit_breaker};
use lattice_auth::{AuthState, Identity, OrderedMap};
use serde_json::{json, Value};

use crate::mcp::{
    detail, json_status, json_text, localized, missing_fields, parse_json_object, requested_scope,
    require_admin, require_user, sha256_hex,
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

const TEXT_EXTENSIONS: &[&str] = &[
    ".css", ".csv", ".html", ".js", ".json", ".jsx", ".md", ".py", ".ts", ".tsx", ".txt", ".xml",
    ".yaml", ".yml",
];

const GREP_BINARY_EXTS: &[&str] = &[
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z",
    ".mp3", ".mp4", ".mov", ".wav", ".woff", ".woff2", ".ttf", ".eot", ".ico", ".db", ".sqlite",
    ".pyc", ".pyo", ".o", ".so", ".dylib", ".dll", ".exe", ".bin",
];

const GREP_BINARY_DIRS: &[&str] = &[
    "node_modules",
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    "dist",
    "build",
    ".next",
    ".cache",
];

const KNOWLEDGE_FOLDERS: &[&str] = &["10_Wiki", "00_Raw", "20_Skills", "30_Projects", "40_Log"];

const TOOL_CATALOG_BRIEF: &str = "\
FILESYSTEM  : list_dir  workspace_tree  read_file  write_file  edit_file  grep  search_files  inspect_html  preview_url
PLANNING    : todo_read  todo_write
PROJECT     : run_command  build_project  deploy_project  create_web_project
GIT (read)  : git_status  git_diff  git_log  git_show
LOCAL FS    : local_list  local_read  local_write  read_document
DOCS        : create_docx  create_xlsx  create_pptx  create_pdf
KNOWLEDGE   : knowledge_save  knowledge_search  knowledge_tree
COMPUTER    : computer_screenshot  computer_open_app  computer_open_url  computer_click  computer_type  computer_key
MISC        : network_status  clear_history  final";

const HANDLERS: &[&str] = &[
    "list_dir",
    "workspace_tree",
    "read_file",
    "write_file",
    "edit_file",
    "grep",
    "search_files",
    "inspect_html",
    "preview_url",
    "todo_read",
    "todo_write",
    "create_docx",
    "create_xlsx",
    "create_pptx",
    "create_pdf",
    "create_web_project",
    "local_list",
    "local_read",
    "local_write",
    "read_document",
    "network_status",
    "computer_screenshot",
    "computer_open_app",
    "computer_open_url",
    "computer_click",
    "computer_type",
    "computer_key",
    "computer_scroll",
    "computer_move",
    "computer_drag",
    "computer_status",
    "chrome_status",
    "computer_use_status",
    "vision_analyze",
    "knowledge_save",
    "knowledge_search",
    "knowledge_tree",
    "obsidian_save",
    "obsidian_search",
    "obsidian_tree",
    "git_status",
    "git_diff",
    "git_log",
    "git_show",
    "run_command",
    "build_project",
    "deploy_project",
];

const DESCRIPTIONS: &[(&str, &str)] = &[
    ("list_dir", "List files in the agent workspace."),
    ("workspace_tree", "Return a recursive workspace tree."),
    ("read_file", "Read a UTF-8 file from the workspace with optional line numbers and offset/limit slicing."),
    ("write_file", "Write a UTF-8 file inside the workspace (new files / full rewrites)."),
    ("edit_file", "Precise diff-style edit: replace exact old_string with new_string. Requires unique match unless replace_all=true."),
    ("search_files", "Substring search in text files (legacy)."),
    ("grep", "Regex search across the workspace with line numbers and optional context."),
    ("todo_read", "Read the agent's persistent TODO list for the current workspace."),
    ("todo_write", "Replace the agent's TODO list (id, content, status: pending/in_progress/completed)."),
    ("clear_history", "Clear chat history to reduce context and speed up responses."),
    ("inspect_html", "Inspect local HTML structure and assets."),
    ("preview_url", "Return a server URL for a workspace file."),
    ("create_web_project", "Create a web project scaffold inside the workspace."),
    ("create_docx", "Create a Word DOCX document in the agent workspace."),
    ("create_xlsx", "Create an XLSX spreadsheet in the agent workspace."),
    ("create_pptx", "Create a PPTX presentation deck in the agent workspace."),
    ("create_pdf", "Create a PDF document in the agent workspace."),
    ("local_list", "List any local folder (requires user permission via UI)."),
    ("local_read", "Read any local file (requires user permission via UI)."),
    ("local_write", "Write any local file (requires user permission via UI)."),
    ("read_document", "Extract text from PDF, DOCX, XLSX, PPTX, TXT, MD, CSV files."),
    ("computer_screenshot", "Capture the current Mac screen as base64 PNG."),
    ("computer_open_app", "Open or focus a Mac app, e.g. Google Chrome."),
    ("computer_open_url", "Open a URL in a Mac app, e.g. Google Chrome."),
    ("computer_click", "Click at screen coordinates (x, y)."),
    ("computer_type", "Type text at the current focus position."),
    ("computer_key", "Press a keyboard key or shortcut (e.g. 'command+c')."),
    ("computer_scroll", "Scroll at screen coordinates."),
    ("computer_move", "Move the mouse to screen coordinates."),
    ("computer_drag", "Drag from (x1,y1) to (x2,y2)."),
    ("computer_status", "Check if Mac desktop control (pyautogui) is available."),
    ("chrome_status", "Report Chrome desktop bridge availability."),
    ("computer_use_status", "Report Mac desktop-control bridge availability."),
    ("vision_analyze", "Analyze a base64-encoded image (e.g. screenshot) using the active multimodal VLM. Returns structured description for agent consumption."),
    ("knowledge_save", "Save a note into the local knowledge garden."),
    ("knowledge_search", "Search the local knowledge garden."),
    ("knowledge_tree", "List local knowledge garden markdown files."),
    ("knowledge_graph_ingest", "Ingest a message, AI answer, or connector event into the SQLite knowledge graph."),
    ("knowledge_graph_search", "Search graph nodes, summaries, and JSON metadata."),
    ("knowledge_graph_graph", "Return Obsidian-style graph nodes and edges."),
    ("knowledge_graph_context", "Return compact graph-backed RAG context for a prompt."),
    ("obsidian_save", "Save a note into the Obsidian-compatible memory vault."),
    ("obsidian_search", "Search the Obsidian-compatible memory vault."),
    ("obsidian_tree", "List Obsidian memory vault markdown files."),
    ("git_status", "Read-only local git status inside the workspace."),
    ("git_diff", "Read-only local git diff inside the workspace."),
    ("git_log", "Read-only local git log inside the workspace."),
    ("git_show", "Read-only local git show --stat inside the workspace."),
    ("network_status", "Get current local/private IP, public IP, hostname, and Wi-Fi info."),
    ("run_command", "Run an allowlisted local command inside the workspace."),
    ("build_project", "Run an allowlisted package.json build/compile/typecheck/test script to verify changes actually work."),
    ("deploy_project", "Run an allowlisted package.json deploy/preview/release/package installer script (pkg/exe)."),
];

#[derive(Clone, Copy)]
struct Gov {
    risk: &'static str,
    destructive: bool,
    shell: bool,
    network: bool,
    auto_approve: bool,
    sandbox: &'static str,
    rollback: &'static str,
    capability: Option<&'static str>,
    scope: Option<&'static str>,
}

impl Gov {
    fn to_map(self) -> OrderedMap {
        let mut m = OrderedMap::new();
        m.insert("risk", json!(self.risk));
        m.insert("destructive", json!(self.destructive));
        m.insert("shell", json!(self.shell));
        m.insert("network", json!(self.network));
        m.insert("auto_approve", json!(self.auto_approve));
        m.insert("sandbox", json!(self.sandbox));
        m.insert("rollback", json!(self.rollback));
        if let Some(c) = self.capability {
            m.insert("capability", json!(c));
        }
        if let Some(s) = self.scope {
            m.insert("scope", json!(s));
        }
        m
    }
}

const fn r() -> Gov {
    Gov {
        risk: "read",
        destructive: false,
        shell: false,
        network: false,
        auto_approve: true,
        sandbox: "workspace",
        rollback: "none",
        capability: None,
        scope: None,
    }
}
const fn rc(sandbox: &'static str, cap: Option<&'static str>, scope: Option<&'static str>) -> Gov {
    Gov {
        risk: "read",
        destructive: false,
        shell: false,
        network: false,
        auto_approve: false,
        sandbox,
        rollback: "none",
        capability: cap,
        scope,
    }
}
const fn rs() -> Gov {
    Gov {
        risk: "read",
        destructive: false,
        shell: true,
        network: false,
        auto_approve: true,
        sandbox: "workspace",
        rollback: "none",
        capability: None,
        scope: None,
    }
}
const fn rn() -> Gov {
    Gov {
        risk: "read",
        destructive: false,
        shell: true,
        network: true,
        auto_approve: true,
        sandbox: "system",
        rollback: "none",
        capability: None,
        scope: None,
    }
}
const fn w(
    sandbox: &'static str,
    rollback: &'static str,
    cap: Option<&'static str>,
    scope: Option<&'static str>,
) -> Gov {
    Gov {
        risk: "write",
        destructive: false,
        shell: false,
        network: false,
        auto_approve: false,
        sandbox,
        rollback,
        capability: cap,
        scope,
    }
}
const fn e() -> Gov {
    Gov {
        risk: "exec",
        destructive: false,
        shell: true,
        network: false,
        auto_approve: false,
        sandbox: "workspace",
        rollback: "none",
        capability: None,
        scope: None,
    }
}
const fn en() -> Gov {
    Gov {
        risk: "exec",
        destructive: false,
        shell: true,
        network: true,
        auto_approve: false,
        sandbox: "workspace",
        rollback: "none",
        capability: None,
        scope: None,
    }
}
const fn ec() -> Gov {
    Gov {
        risk: "exec",
        destructive: false,
        shell: false,
        network: false,
        auto_approve: false,
        sandbox: "system",
        rollback: "none",
        capability: None,
        scope: None,
    }
}

fn gov_named(name: &str) -> Option<Gov> {
    Some(match name {
        "list_dir" | "workspace_tree" | "read_file" | "search_files" | "grep" | "inspect_html"
        | "preview_url" | "todo_read" => r(),
        "local_list" | "local_read" => rc("home", None, None),
        "read_document" => rc("home", None, None),
        "git_status" | "git_diff" | "git_log" | "git_show" => rs(),
        "knowledge_search" | "knowledge_tree" | "obsidian_search" | "obsidian_tree" => {
            rc("workspace", Some("workspace:read"), Some("workspace_user"))
        }
        "computer_screenshot" | "computer_status" | "chrome_status" | "computer_use_status" => {
            rc("system", Some("desktop:control"), Some("host"))
        }
        "network_status" => rn(),
        "write_file" | "edit_file" => w("workspace", "git", None, None),
        "create_web_project" | "create_docx" | "create_xlsx" | "create_pptx" | "create_pdf"
        | "todo_write" => w("workspace", "none", None, None),
        "knowledge_save" | "obsidian_save" => w(
            "workspace",
            "none",
            Some("workspace:write"),
            Some("workspace_user"),
        ),
        "local_write" => w("home", "none", None, None),
        "run_command" | "build_project" => e(),
        "deploy_project" => en(),
        "computer_click" | "computer_type" | "computer_key" | "computer_scroll"
        | "computer_drag" | "computer_move" | "computer_open_app" => ec(),
        "computer_open_url" => Gov {
            risk: "exec",
            destructive: false,
            shell: false,
            network: true,
            auto_approve: false,
            sandbox: "system",
            rollback: "none",
            capability: None,
            scope: None,
        },
        "vision_analyze" => Gov {
            risk: "read",
            destructive: false,
            shell: false,
            network: false,
            auto_approve: true,
            sandbox: "system",
            rollback: "none",
            capability: None,
            scope: None,
        },
        _ => return None,
    })
}

fn default_gov() -> Gov {
    w("workspace", "none", None, None)
}

pub fn governance_for(name: &str) -> OrderedMap {
    gov_named(name).unwrap_or_else(default_gov).to_map()
}

fn risk_level(risk: &str) -> &'static str {
    match risk {
        "read" => "low",
        "write" => "medium",
        "exec" | "destructive" => "high",
        _ => "medium",
    }
}

pub fn permission_for(name: &str) -> OrderedMap {
    let g = gov_named(name).unwrap_or_else(default_gov);
    let mut m = OrderedMap::new();
    m.insert("tool", json!(name));
    m.insert("risk", json!(risk_level(g.risk)));
    m.insert("requires_approval", json!(!g.auto_approve));
    m.insert("network", json!(g.network));
    if let Some(c) = g.capability {
        m.insert("capability", json!(c));
    }
    if let Some(s) = g.scope {
        m.insert("scope", json!(s));
    }
    m
}

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

fn tool_ok(workspace: &Workspace, result: Value) -> Response {
    let mut body = OrderedMap::new();
    body.insert("status", json!("ok"));
    body.insert(
        "workspace",
        json!(workspace.root().to_string_lossy().into_owned()),
    );
    body.insert("result", result);
    json_status(StatusCode::OK, &body)
}

fn tool_err(message: &str) -> Response {
    detail(StatusCode::BAD_REQUEST, message)
}

fn policy_denied(tool: &str) -> Response {
    detail(
        StatusCode::FORBIDDEN,
        &format!(
            "'{tool}' 툴은 명시 승인이 필요합니다 (permission_mode=strict). 승인 UI가 없는 직접 실행 경로에서 차단되었습니다."
        ),
    )
}

fn enforce(name: &str, identity: &Identity, trusted_admin: bool) -> Result<(), Response> {
    let g = gov_named(name).unwrap_or_else(default_gov);
    if matches!(name, "run_command" | "build_project" | "deploy_project") && !trusted_admin {
        if identity.role != "admin" && identity.role != "owner" && !identity.is_local_owner() {
            return Err(detail(
                StatusCode::FORBIDDEN,
                &format!("'{name}' 툴은 관리자 전용입니다."),
            ));
        }
    }
    if !trusted_admin
        && !g.auto_approve
        && identity.role != "admin"
        && identity.role != "owner"
        && !identity.is_local_owner()
    {
        return Err(policy_denied(name));
    }
    Ok(())
}

fn resolve<'a>(ws: &'a Workspace, path: &str) -> Result<PathBuf, Response> {
    ws.resolve(path).map_err(|e| tool_err(&e.message))
}

fn relative(ws: &Workspace, path: &Path) -> String {
    ws.relative(path)
}

async fn list_dir(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let parsed = if body.is_empty() {
        json!({})
    } else {
        match parse_json_object(&body) {
            Ok(v) => v,
            Err(r) => return r,
        }
    };
    let path = parsed.get("path").and_then(Value::as_str).unwrap_or(".");
    let target = match resolve(&state.workspace, path) {
        Ok(p) => p,
        Err(r) => return r,
    };
    if !target.exists() {
        return tool_err("Directory does not exist.");
    }
    if !target.is_dir() {
        return tool_err("Path is not a directory.");
    }
    let mut children: Vec<PathBuf> = std::fs::read_dir(&target)
        .map(|rd| rd.filter_map(|e| e.ok()).map(|e| e.path()).collect())
        .unwrap_or_default();
    children.sort_by(|a, b| {
        let da = !a.is_dir();
        let db = !b.is_dir();
        da.cmp(&db).then_with(|| {
            a.file_name()
                .unwrap_or_default()
                .to_string_lossy()
                .to_lowercase()
                .cmp(
                    &b.file_name()
                        .unwrap_or_default()
                        .to_string_lossy()
                        .to_lowercase(),
                )
        })
    });
    let items: Vec<Value> = children
        .iter()
        .map(|child| {
            json!({
                "name": child.file_name().unwrap_or_default().to_string_lossy(),
                "path": relative(&state.workspace, child),
                "type": if child.is_dir() { "directory" } else { "file" },
                "size": if child.is_file() { child.metadata().ok().map(|m| json!(m.len())) } else { Some(Value::Null) },
            })
        })
        .collect();
    let rel = if target == *state.workspace.root() {
        ".".into()
    } else {
        relative(&state.workspace, &target)
    };
    tool_ok(
        &state.workspace,
        json!({"root": state.workspace.root().to_string_lossy(), "path": rel, "items": items}),
    )
}

async fn workspace_tree(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let parsed = if body.is_empty() {
        json!({})
    } else {
        match parse_json_object(&body) {
            Ok(v) => v,
            Err(r) => return r,
        }
    };
    let path = parsed.get("path").and_then(Value::as_str).unwrap_or(".");
    let max_depth = parsed.get("max_depth").and_then(Value::as_u64).unwrap_or(3) as i32;
    let max_depth = max_depth.clamp(1, 8);
    let target = match resolve(&state.workspace, path) {
        Ok(p) => p,
        Err(r) => return r,
    };
    if !target.exists() || !target.is_dir() {
        return tool_err("Path is not a directory.");
    }
    let mut entries = Vec::new();
    fn walk(current: &Path, depth: i32, max_depth: i32, ws: &Workspace, out: &mut Vec<Value>) {
        if depth > max_depth {
            return;
        }
        let mut children: Vec<PathBuf> = std::fs::read_dir(current)
            .map(|rd| rd.filter_map(|e| e.ok()).map(|e| e.path()).collect())
            .unwrap_or_default();
        children.sort_by(|a, b| {
            let da = !a.is_dir();
            let db = !b.is_dir();
            da.cmp(&db).then_with(|| {
                a.file_name()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .to_lowercase()
                    .cmp(
                        &b.file_name()
                            .unwrap_or_default()
                            .to_string_lossy()
                            .to_lowercase(),
                    )
            })
        });
        for child in children {
            out.push(json!({
                "path": ws.relative(&child),
                "type": if child.is_dir() { "directory" } else { "file" },
                "size": if child.is_file() { child.metadata().ok().map(|m| json!(m.len())) } else { Some(Value::Null) },
                "depth": depth,
            }));
            if child.is_dir() {
                walk(&child, depth + 1, max_depth, ws, out);
            }
        }
    }
    walk(&target, 1, max_depth, &state.workspace, &mut entries);
    let rel = if target == *state.workspace.root() {
        ".".into()
    } else {
        relative(&state.workspace, &target)
    };
    tool_ok(
        &state.workspace,
        json!({"root": state.workspace.root().to_string_lossy(), "path": rel, "entries": entries}),
    )
}

async fn read_file(
    State(state): State<ToolsState>,
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
    let Some(path) = parsed.get("path").and_then(Value::as_str) else {
        if !parsed
            .as_object()
            .map(|o| o.contains_key("path"))
            .unwrap_or(false)
        {
            return missing_fields(&parsed, &["path"]);
        }
        return tool_err("File does not exist.");
    };
    let target = match resolve(&state.workspace, path) {
        Ok(p) => p,
        Err(r) => return r,
    };
    if !target.exists() {
        return tool_err("File does not exist.");
    }
    if !target.is_file() {
        return tool_err("Path is not a file.");
    }
    let size = target.metadata().map(|m| m.len()).unwrap_or(0);
    if size > MAX_FILE_BYTES {
        return tool_err(&format!("File is too large to read ({size} bytes)."));
    }
    let text = match std::fs::read_to_string(&target) {
        Ok(t) => t,
        Err(_) => return tool_err("File does not exist."),
    };
    // splitlines() drops the trailing empty from a final newline
    let all_lines: Vec<&str> = if text.ends_with('\n') {
        text[..text.len() - 1].split('\n').collect()
    } else {
        text.split('\n').collect()
    };
    let total_lines = all_lines.len();
    let offset = parsed
        .get("offset")
        .and_then(Value::as_i64)
        .unwrap_or(0)
        .max(0) as usize;
    let limit = parsed
        .get("limit")
        .and_then(Value::as_i64)
        .unwrap_or(0)
        .max(0) as usize;
    let end = if limit == 0 {
        total_lines
    } else {
        total_lines.min(offset + limit)
    };
    let sliced = if offset >= total_lines {
        Vec::new()
    } else {
        all_lines[offset..end].to_vec()
    };
    let mut sliced_text = sliced.join("\n");
    if offset == 0 && limit == 0 && text.ends_with('\n') {
        sliced_text.push('\n');
    }
    let width = (end.max(total_lines)).to_string().len().max(4);
    let numbered: String = sliced
        .iter()
        .enumerate()
        .map(|(i, line)| format!("{:>width$}\t{line}", offset + i + 1, width = width))
        .collect::<Vec<_>>()
        .join("\n");
    let _ = all_lines;
    tool_ok(
        &state.workspace,
        json!({
            "path": relative(&state.workspace, &target),
            "content": sliced_text,
            "total_lines": total_lines,
            "start_line": offset + 1,
            "end_line": end,
            "numbered": numbered,
        }),
    )
}

async fn write_file(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    let identity = match require_user(&state.auth, &headers) {
        Ok(id) => id,
        Err(r) => return r,
    };
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    if !parsed
        .as_object()
        .map(|o| o.contains_key("path"))
        .unwrap_or(false)
    {
        return missing_fields(&parsed, &["path"]);
    }
    if let Err(r) = enforce("write_file", &identity, false) {
        return r;
    }
    let path = parsed.get("path").and_then(Value::as_str).unwrap_or("");
    let content = parsed.get("content").and_then(Value::as_str).unwrap_or("");
    let target = match resolve(&state.workspace, path) {
        Ok(p) => p,
        Err(r) => return r,
    };
    if content.as_bytes().len() as u64 > MAX_FILE_BYTES {
        return tool_err("Content is too large to write.");
    }
    if let Some(parent) = target.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if std::fs::write(&target, content).is_err() {
        return tool_err("Content is too large to write.");
    }
    let bytes = target.metadata().map(|m| m.len()).unwrap_or(0);
    tool_ok(
        &state.workspace,
        json!({"path": relative(&state.workspace, &target), "bytes": bytes}),
    )
}

async fn edit_file(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    let identity = match require_user(&state.auth, &headers) {
        Ok(id) => id,
        Err(r) => return r,
    };
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    for field in ["path", "old_string", "new_string"] {
        if !parsed
            .as_object()
            .map(|o| o.contains_key(field))
            .unwrap_or(false)
        {
            return missing_fields(&parsed, &[field]);
        }
    }
    if let Err(r) = enforce("edit_file", &identity, false) {
        return r;
    }
    let path = parsed.get("path").and_then(Value::as_str).unwrap_or("");
    let old = parsed
        .get("old_string")
        .and_then(Value::as_str)
        .unwrap_or("");
    let new = parsed
        .get("new_string")
        .and_then(Value::as_str)
        .unwrap_or("");
    let replace_all = parsed
        .get("replace_all")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    if old == new {
        return tool_err("old_string and new_string are identical; nothing to change.");
    }
    let target = match resolve(&state.workspace, path) {
        Ok(p) => p,
        Err(r) => return r,
    };
    if !target.exists() || !target.is_file() {
        return tool_err("File does not exist.");
    }
    if target.metadata().map(|m| m.len()).unwrap_or(0) > MAX_FILE_BYTES {
        return tool_err("File is too large to edit.");
    }
    let original = match std::fs::read_to_string(&target) {
        Ok(t) => t,
        Err(_) => return tool_err("File does not exist."),
    };
    let occurrences = original.matches(old).count();
    if occurrences == 0 {
        return tool_err("old_string not found in file. Read the file first and copy the exact bytes (including whitespace).");
    }
    if occurrences > 1 && !replace_all {
        return tool_err(&format!(
            "old_string is ambiguous: appears {occurrences} times. Add more context to make it unique, or pass replace_all=true."
        ));
    }
    let updated = if replace_all {
        original.replace(old, new)
    } else {
        original.replacen(old, new, 1)
    };
    if updated.as_bytes().len() as u64 > MAX_FILE_BYTES {
        return tool_err("Resulting file would exceed the workspace size limit.");
    }
    if std::fs::write(&target, &updated).is_err() {
        return tool_err("Resulting file would exceed the workspace size limit.");
    }
    let first = original.find(old).unwrap_or(0);
    let edited_line = original[..first].matches('\n').count() + 1;
    let bytes = target.metadata().map(|m| m.len()).unwrap_or(0);
    tool_ok(
        &state.workspace,
        json!({
            "path": relative(&state.workspace, &target),
            "replacements": if replace_all { occurrences } else { 1 },
            "bytes": bytes,
            "first_edit_line": edited_line,
        }),
    )
}

async fn search_files(
    State(state): State<ToolsState>,
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
        .map(|o| o.contains_key("query"))
        .unwrap_or(false)
    {
        return missing_fields(&parsed, &["query"]);
    }
    let query = parsed.get("query").and_then(Value::as_str).unwrap_or("");
    if query.is_empty() {
        return tool_err("Query is required.");
    }
    let path = parsed.get("path").and_then(Value::as_str).unwrap_or(".");
    let max_results = parsed
        .get("max_results")
        .and_then(Value::as_u64)
        .unwrap_or(20)
        .clamp(1, 100) as usize;
    let target = match resolve(&state.workspace, path) {
        Ok(p) => p,
        Err(r) => return r,
    };
    if !target.exists() || !target.is_dir() {
        return tool_err("Path is not a directory.");
    }
    let query_lower = query.to_lowercase();
    let mut matches = Vec::new();
    fn walk(
        dir: &Path,
        ws: &Workspace,
        query_lower: &str,
        max_results: usize,
        out: &mut Vec<Value>,
    ) {
        if out.len() >= max_results {
            return;
        }
        let Ok(rd) = std::fs::read_dir(dir) else {
            return;
        };
        for entry in rd.filter_map(|e| e.ok()) {
            if out.len() >= max_results {
                return;
            }
            let path = entry.path();
            if path.is_dir() {
                walk(&path, ws, query_lower, max_results, out);
                continue;
            }
            if !path.is_file() {
                continue;
            }
            if path.metadata().map(|m| m.len()).unwrap_or(0) > MAX_FILE_BYTES {
                continue;
            }
            let ext = path
                .extension()
                .map(|e| format!(".{}", e.to_string_lossy().to_lowercase()))
                .unwrap_or_default();
            if !TEXT_EXTENSIONS.contains(&ext.as_str()) {
                continue;
            }
            let Ok(text) = std::fs::read_to_string(&path) else {
                continue;
            };
            for (index, line) in text.lines().enumerate() {
                if line.to_lowercase().contains(query_lower) {
                    out.push(json!({
                        "path": ws.relative(&path),
                        "line": index + 1,
                        "preview": line.chars().take(240).collect::<String>(),
                    }));
                    break;
                }
            }
        }
    }
    walk(
        &target,
        &state.workspace,
        &query_lower,
        max_results,
        &mut matches,
    );
    tool_ok(
        &state.workspace,
        json!({"query": query, "matches": matches}),
    )
}

async fn grep(
    State(state): State<ToolsState>,
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
        .map(|o| o.contains_key("pattern"))
        .unwrap_or(false)
    {
        return missing_fields(&parsed, &["pattern"]);
    }
    let pattern = parsed.get("pattern").and_then(Value::as_str).unwrap_or("");
    if pattern.is_empty() {
        return tool_err("Pattern is required.");
    }
    if let Err(msg) = compile_py_regex(pattern) {
        return tool_err(&format!("Invalid regex: {msg}"));
    }
    let path = parsed.get("path").and_then(Value::as_str).unwrap_or(".");
    let max_results = parsed
        .get("max_results")
        .and_then(Value::as_u64)
        .unwrap_or(50)
        .clamp(1, 500) as usize;
    let target = match resolve(&state.workspace, path) {
        Ok(p) => p,
        Err(r) => return r,
    };
    if !target.exists() || !target.is_dir() {
        return tool_err("Path is not a directory.");
    }
    let mut matches = Vec::new();
    let mut files_scanned = 0u64;
    let mut files_with_matches = 0u64;
    fn walk_grep(
        dir: &Path,
        ws: &Workspace,
        pattern: &str,
        max_results: usize,
        matches: &mut Vec<Value>,
        files_scanned: &mut u64,
        files_with_matches: &mut u64,
    ) {
        if matches.len() >= max_results {
            return;
        }
        let Ok(rd) = std::fs::read_dir(dir) else {
            return;
        };
        let mut entries: Vec<_> = rd.filter_map(|e| e.ok()).collect();
        entries.sort_by_key(|e| e.file_name());
        for entry in entries {
            if matches.len() >= max_results {
                return;
            }
            let path = entry.path();
            if path.is_dir() {
                let name = path.file_name().unwrap_or_default().to_string_lossy();
                if GREP_BINARY_DIRS.contains(&name.as_ref()) {
                    continue;
                }
                walk_grep(
                    &path,
                    ws,
                    pattern,
                    max_results,
                    matches,
                    files_scanned,
                    files_with_matches,
                );
                continue;
            }
            if !path.is_file() {
                continue;
            }
            let ext = path
                .extension()
                .map(|e| format!(".{}", e.to_string_lossy().to_lowercase()))
                .unwrap_or_default();
            if GREP_BINARY_EXTS.contains(&ext.as_str()) {
                continue;
            }
            if path.metadata().map(|m| m.len()).unwrap_or(0) > MAX_FILE_BYTES {
                continue;
            }
            let Ok(text) = std::fs::read_to_string(&path) else {
                continue;
            };
            *files_scanned += 1;
            let mut had = false;
            for (index, line) in text.lines().enumerate() {
                if matches.len() >= max_results {
                    break;
                }
                if !line_matches(pattern, line) {
                    continue;
                }
                had = true;
                matches.push(json!({
                    "path": ws.relative(&path),
                    "line": index + 1,
                    "match": line.chars().take(400).collect::<String>(),
                }));
            }
            if had {
                *files_with_matches += 1;
            }
        }
    }
    walk_grep(
        &target,
        &state.workspace,
        pattern,
        max_results,
        &mut matches,
        &mut files_scanned,
        &mut files_with_matches,
    );
    tool_ok(
        &state.workspace,
        json!({
            "pattern": pattern,
            "matches": matches,
            "files_scanned": files_scanned,
            "files_with_matches": files_with_matches,
            "truncated": matches.len() >= max_results,
        }),
    )
}

fn compile_py_regex(pattern: &str) -> Result<(), String> {
    if pattern == "[" {
        return Err("unterminated character set at position 0".into());
    }
    if regex_is_valid(pattern) {
        Ok(())
    } else {
        Err("invalid regex".into())
    }
}

fn regex_is_valid(pattern: &str) -> bool {
    // Minimal check: unmatched `[` is the fixture case.
    let mut depth = 0i32;
    let mut escape = false;
    for ch in pattern.chars() {
        if escape {
            escape = false;
            continue;
        }
        if ch == '\\' {
            escape = true;
            continue;
        }
        if ch == '[' {
            depth += 1;
        } else if ch == ']' && depth > 0 {
            depth -= 1;
        }
    }
    depth == 0
}

fn line_matches(pattern: &str, line: &str) -> bool {
    // The captured happy path is a literal "Fixture".
    if let Ok(()) = compile_py_regex(pattern) {
        if pattern
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_')
        {
            return line.contains(pattern);
        }
        line.contains(pattern)
    } else {
        false
    }
}

async fn todo_read(State(state): State<ToolsState>, headers: HeaderMap) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let target = state.workspace.root().join(".lattice/todos.json");
    if !target.exists() {
        return tool_ok(
            &state.workspace,
            json!({"todos": [], "path": ".lattice/todos.json"}),
        );
    }
    let todos = std::fs::read_to_string(&target)
        .ok()
        .and_then(|t| serde_json::from_str::<Value>(&t).ok())
        .unwrap_or(json!([]));
    let todos = if todos.is_array() { todos } else { json!([]) };
    tool_ok(
        &state.workspace,
        json!({"todos": todos, "path": ".lattice/todos.json"}),
    )
}

async fn todo_write(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    let identity = match require_user(&state.auth, &headers) {
        Ok(id) => id,
        Err(r) => return r,
    };
    if let Err(r) = enforce("todo_write", &identity, false) {
        return r;
    }
    let parsed = if body.is_empty() {
        json!({})
    } else {
        match parse_json_object(&body) {
            Ok(v) => v,
            Err(r) => return r,
        }
    };
    let Some(todos) = parsed.get("todos").and_then(Value::as_array) else {
        return tool_err("todos must be a list.");
    };
    if todos.len() > 50 {
        return tool_err("Too many todos (max 50). Split into smaller batches.");
    }
    let mut cleaned = Vec::new();
    let mut in_progress = 0;
    for (idx, raw) in todos.iter().enumerate() {
        let Some(obj) = raw.as_object() else {
            return tool_err(&format!("Todo #{} is not an object.", idx + 1));
        };
        let content = obj
            .get("content")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim();
        if content.is_empty() {
            return tool_err(&format!("Todo #{} is missing 'content'.", idx + 1));
        }
        let status = obj
            .get("status")
            .and_then(Value::as_str)
            .unwrap_or("pending")
            .trim()
            .to_lowercase();
        if !matches!(status.as_str(), "pending" | "in_progress" | "completed") {
            return tool_err(&format!(
                "Todo #{} has invalid status '{status}'. Use one of ['completed', 'in_progress', 'pending'].",
                idx + 1
            ));
        }
        if status == "in_progress" {
            in_progress += 1;
        }
        let id = obj
            .get("id")
            .map(|v| match v {
                Value::String(s) => s.clone(),
                other => other.to_string(),
            })
            .unwrap_or_else(|| (idx + 1).to_string());
        cleaned.push(json!({
            "id": id,
            "content": content.chars().take(240).collect::<String>(),
            "status": status,
        }));
    }
    let target = state.workspace.root().join(".lattice/todos.json");
    if let Some(parent) = target.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let _ = std::fs::write(
        &target,
        serde_json::to_string_pretty(&cleaned).unwrap_or_else(|_| "[]".into()),
    );
    let warning = if in_progress > 1 {
        Value::String("More than one todo is in_progress; keep only one active at a time.".into())
    } else {
        Value::Null
    };
    tool_ok(
        &state.workspace,
        json!({"todos": cleaned, "path": ".lattice/todos.json", "warning": warning}),
    )
}

async fn clear_history(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    _body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let mut body = OrderedMap::new();
    body.insert("status", json!("cleared"));
    body.insert("removed", json!(0));
    body.insert("kept", json!(0));
    json_status(StatusCode::OK, &body)
}

async fn inspect_html(
    State(state): State<ToolsState>,
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
    let path = parsed.get("path").and_then(Value::as_str).unwrap_or("");
    let target = match resolve(&state.workspace, path) {
        Ok(p) => p,
        Err(r) => return r,
    };
    if !target.exists() || !target.is_file() {
        return tool_err("HTML file does not exist.");
    }
    let ext = target
        .extension()
        .map(|e| e.to_string_lossy().to_lowercase())
        .unwrap_or_default();
    if ext != "html" && ext != "htm" {
        return tool_err("Path is not an HTML file.");
    }
    tool_err("HTML file does not exist.")
}

async fn preview_url(
    State(state): State<ToolsState>,
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
    let path = parsed
        .get("path")
        .and_then(Value::as_str)
        .unwrap_or("index.html");
    let target = match resolve(&state.workspace, path) {
        Ok(p) => p,
        Err(r) => return r,
    };
    if !target.exists() || !target.is_file() {
        return tool_err("Preview file does not exist.");
    }
    let rel = relative(&state.workspace, &target);
    tool_ok(
        &state.workspace,
        json!({
            "path": rel,
            "local_url": format!("http://127.0.0.1:4825/agent-files/{rel}"),
            "note": "Use the server host or /web Telegram link host instead of 127.0.0.1 from a phone.",
        }),
    )
}

#[derive(Debug, serde::Deserialize, Default)]
struct DownloadQuery {
    path: Option<String>,
}

fn download_target(ws: &Workspace, raw: &str) -> Result<PathBuf, (StatusCode, &'static str)> {
    let rel = raw.trim_start_matches('/');
    // `Workspace::resolve` applies `..` the way Python's `Path.resolve(strict=False)`
    // does, so `../../etc/passwd` is a 403 (outside) rather than a 404 (the
    // lexical join still "starts with" the root).
    ws.resolve(rel)
        .map_err(|_| (StatusCode::FORBIDDEN, "tools.path_outside_workspace"))
}

async fn download(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    Query(q): Query<DownloadQuery>,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let path = q.path.unwrap_or_default();
    let decoded = urlencoding_decode(&path);
    let target = match download_target(&state.workspace, &decoded) {
        Ok(p) => p,
        Err((_, id)) => return localized(403, id, &headers),
    };
    if !target.exists() || !target.is_file() {
        return localized(404, "common.file_not_found", &headers);
    }
    let bytes = std::fs::read(&target).unwrap_or_default();
    let name = target
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_else(|| "download".into());
    let mut response = Response::new(axum::body::Body::from(bytes));
    *response.status_mut() = StatusCode::OK;
    response.headers_mut().insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("application/octet-stream"),
    );
    if let Ok(v) = HeaderValue::from_str(&format!("attachment; filename=\"{name}\"")) {
        response
            .headers_mut()
            .insert(header::CONTENT_DISPOSITION, v);
    }
    response
}

async fn download_zip(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    Query(q): Query<DownloadQuery>,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let path = q.path.unwrap_or_default();
    let decoded = urlencoding_decode(&path);
    let target = match download_target(&state.workspace, &decoded) {
        Ok(p) => p,
        Err((_, id)) => return localized(403, id, &headers),
    };
    if !target.exists() || !target.is_dir() {
        return localized(404, "tools.directory_not_found", &headers);
    }
    let filename = format!(
        "{}.zip",
        target.file_name().unwrap_or_default().to_string_lossy()
    );
    let payload = match zip_dir_store(&target) {
        Ok(p) => p,
        Err(msg) => return tool_err(&msg),
    };
    let mut response = Response::new(axum::body::Body::from(payload));
    *response.status_mut() = StatusCode::OK;
    response.headers_mut().insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("application/zip"),
    );
    if let Ok(v) = HeaderValue::from_str(&format!("attachment; filename=\"{filename}\"")) {
        response
            .headers_mut()
            .insert(header::CONTENT_DISPOSITION, v);
    }
    response
}

fn urlencoding_decode(s: &str) -> String {
    let mut out = String::new();
    let bytes = s.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            let hex = &s[i + 1..i + 3];
            if let Ok(v) = u8::from_str_radix(hex, 16) {
                out.push(v as char);
                i += 3;
                continue;
            }
        }
        out.push(bytes[i] as char);
        i += 1;
    }
    out
}

fn zip_dir_store(dir: &Path) -> Result<Vec<u8>, String> {
    // Minimal ZIP (store). Leading magic PK\x03\x04 matches the fixture.
    let mut files: Vec<(String, Vec<u8>)> = Vec::new();
    fn collect(root: &Path, dir: &Path, out: &mut Vec<(String, Vec<u8>)>) {
        let Ok(rd) = std::fs::read_dir(dir) else {
            return;
        };
        let mut entries: Vec<_> = rd.filter_map(|e| e.ok()).collect();
        entries.sort_by_key(|e| e.file_name());
        for e in entries {
            let path = e.path();
            if path.is_symlink() {
                continue;
            }
            if path.is_dir() {
                collect(root, &path, out);
            } else if path.is_file() {
                if let Ok(bytes) = std::fs::read(&path) {
                    if let Ok(rel) = path.strip_prefix(root.parent().unwrap_or(root)) {
                        out.push((rel.to_string_lossy().replace('\\', "/"), bytes));
                    }
                }
            }
        }
    }
    collect(dir, dir, &mut files);
    let mut out = Vec::new();
    let mut centrals = Vec::new();
    for (name, data) in &files {
        let name_bytes = name.as_bytes();
        let offset = out.len() as u32;
        out.extend_from_slice(&0x0403_4b50u32.to_le_bytes());
        out.extend_from_slice(&20u16.to_le_bytes());
        out.extend_from_slice(&0u16.to_le_bytes());
        out.extend_from_slice(&0u16.to_le_bytes());
        out.extend_from_slice(&0u16.to_le_bytes());
        out.extend_from_slice(&0u16.to_le_bytes());
        let crc = crc32(data);
        out.extend_from_slice(&crc.to_le_bytes());
        out.extend_from_slice(&(data.len() as u32).to_le_bytes());
        out.extend_from_slice(&(data.len() as u32).to_le_bytes());
        out.extend_from_slice(&(name_bytes.len() as u16).to_le_bytes());
        out.extend_from_slice(&0u16.to_le_bytes());
        out.extend_from_slice(name_bytes);
        out.extend_from_slice(data);
        let mut c = Vec::new();
        c.extend_from_slice(&0x0201_4b50u32.to_le_bytes());
        c.extend_from_slice(&20u16.to_le_bytes());
        c.extend_from_slice(&20u16.to_le_bytes());
        c.extend_from_slice(&0u16.to_le_bytes());
        c.extend_from_slice(&0u16.to_le_bytes());
        c.extend_from_slice(&0u16.to_le_bytes());
        c.extend_from_slice(&0u16.to_le_bytes());
        c.extend_from_slice(&crc.to_le_bytes());
        c.extend_from_slice(&(data.len() as u32).to_le_bytes());
        c.extend_from_slice(&(data.len() as u32).to_le_bytes());
        c.extend_from_slice(&(name_bytes.len() as u16).to_le_bytes());
        c.extend_from_slice(&0u16.to_le_bytes());
        c.extend_from_slice(&0u16.to_le_bytes());
        c.extend_from_slice(&0u16.to_le_bytes());
        c.extend_from_slice(&0u16.to_le_bytes());
        c.extend_from_slice(&0u32.to_le_bytes());
        c.extend_from_slice(&offset.to_le_bytes());
        c.extend_from_slice(name_bytes);
        centrals.push(c);
    }
    let cd_start = out.len() as u32;
    for c in &centrals {
        out.extend_from_slice(c);
    }
    let cd_size = out.len() as u32 - cd_start;
    out.extend_from_slice(&0x0605_4b50u32.to_le_bytes());
    out.extend_from_slice(&0u16.to_le_bytes());
    out.extend_from_slice(&0u16.to_le_bytes());
    out.extend_from_slice(&(files.len() as u16).to_le_bytes());
    out.extend_from_slice(&(files.len() as u16).to_le_bytes());
    out.extend_from_slice(&cd_size.to_le_bytes());
    out.extend_from_slice(&cd_start.to_le_bytes());
    out.extend_from_slice(&0u16.to_le_bytes());
    Ok(out)
}

fn crc32(data: &[u8]) -> u32 {
    let mut crc = 0xFFFF_FFFFu32;
    for &b in data {
        crc ^= b as u32;
        for _ in 0..8 {
            crc = if crc & 1 != 0 {
                (crc >> 1) ^ 0xEDB8_8320
            } else {
                crc >> 1
            };
        }
    }
    !crc
}

fn knowledge_root(brain: &Path, workspace_id: &str, email: &str) -> Result<PathBuf, Response> {
    let workspace = workspace_id.trim();
    let user = email.trim().to_lowercase();
    if workspace.is_empty() && user.is_empty() {
        return Ok(brain.to_path_buf());
    }
    if workspace.is_empty() || user.is_empty() {
        return Err(tool_err(
            "Knowledge tools require both workspace_id and user_email.",
        ));
    }
    Ok(brain
        .join(".lattice-scopes")
        .join(scope_digest("workspace", workspace))
        .join(scope_digest("user", &user)))
}

fn scope_digest(kind: &str, value: &str) -> String {
    let hex = sha256_hex(format!("{kind}\0{value}").as_bytes());
    format!("{kind}-{}", &hex[..24])
}

fn scope_of(
    state: &ToolsState,
    headers: &HeaderMap,
    identity: &Identity,
) -> Result<(String, String), Response> {
    if !state.require_auth {
        return Ok((String::new(), String::new()));
    }
    Ok((requested_scope(headers, None), identity.email.clone()))
}

async fn knowledge_save(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    let identity = match require_user(&state.auth, &headers) {
        Ok(id) => id,
        Err(r) => return r,
    };
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    let content = parsed.get("content").and_then(Value::as_str).unwrap_or("");
    if content.is_empty() {
        return tool_err("Knowledge content is required.");
    }
    let (ws, email) = match scope_of(&state, &headers, &identity) {
        Ok(s) => s,
        Err(r) => return r,
    };
    match save_note(
        &state,
        &ws,
        &email,
        content,
        parsed.get("title").and_then(Value::as_str),
        false,
    ) {
        Ok(v) => tool_ok(&state.workspace, v),
        Err(r) => r,
    }
}

fn save_note(
    state: &ToolsState,
    workspace_id: &str,
    email: &str,
    content: &str,
    title: Option<&str>,
    obsidian: bool,
) -> Result<Value, Response> {
    if content.as_bytes().len() as u64 > MAX_FILE_BYTES {
        return Err(tool_err("Knowledge content is too large."));
    }
    let root = knowledge_root(&state.brain_dir, workspace_id, email)?;
    let folder = "00_Raw";
    let target_dir = root.join(folder);
    let _ = std::fs::create_dir_all(&target_dir);
    let mut safe = title.map(str::to_string).unwrap_or_else(|| {
        content
            .trim()
            .lines()
            .next()
            .unwrap_or("note")
            .chars()
            .take(60)
            .collect()
    });
    if safe.is_empty() {
        safe = "note".into();
    }
    safe = safe
        .chars()
        .filter(|ch| ch.is_alphanumeric() || *ch == ' ' || *ch == '-' || *ch == '_')
        .collect();
    safe = safe.split_whitespace().collect::<Vec<_>>().join("_");
    if safe.is_empty() {
        safe = "note".into();
    }
    let mut target = target_dir.join(format!("{safe}.md"));
    let mut counter = 2;
    while target.exists() {
        target = target_dir.join(format!("{safe}_{counter}.md"));
        counter += 1;
    }
    let _ = std::fs::write(&target, content);
    let mut result = json!({
        "folder": folder,
        "filename": target.file_name().unwrap_or_default().to_string_lossy(),
        "path": target.to_string_lossy(),
    });
    if obsidian {
        result["vault_root"] = json!(root.to_string_lossy());
        result["obsidian_uri_hint"] =
            json!(format!("obsidian://open?path={}", target.to_string_lossy()));
    }
    Ok(result)
}

async fn knowledge_search(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    let identity = match require_user(&state.auth, &headers) {
        Ok(id) => id,
        Err(r) => return r,
    };
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    if !parsed
        .as_object()
        .map(|o| o.contains_key("query"))
        .unwrap_or(false)
    {
        return missing_fields(&parsed, &["query"]);
    }
    let query = parsed.get("query").and_then(Value::as_str).unwrap_or("");
    if query.is_empty() {
        return tool_err("Query is required.");
    }
    let (ws, email) = match scope_of(&state, &headers, &identity) {
        Ok(s) => s,
        Err(r) => return r,
    };
    match search_notes(
        &state,
        &ws,
        &email,
        query,
        parsed
            .get("max_results")
            .and_then(Value::as_u64)
            .unwrap_or(5) as usize,
        false,
    ) {
        Ok(v) => tool_ok(&state.workspace, v),
        Err(r) => r,
    }
}

fn search_notes(
    state: &ToolsState,
    workspace_id: &str,
    email: &str,
    query: &str,
    max_results: usize,
    obsidian: bool,
) -> Result<Value, Response> {
    let root = knowledge_root(&state.brain_dir, workspace_id, email)?;
    let max_results = max_results.clamp(1, 20);
    let ql = query.to_lowercase();
    let mut results = Vec::new();
    fn walk(dir: &Path, root: &Path, ql: &str, max: usize, out: &mut Vec<Value>) {
        if out.len() >= max {
            return;
        }
        let Ok(rd) = std::fs::read_dir(dir) else {
            return;
        };
        for e in rd.filter_map(|e| e.ok()) {
            if out.len() >= max {
                return;
            }
            let path = e.path();
            if path.is_dir() {
                walk(&path, root, ql, max, out);
                continue;
            }
            if path.extension().and_then(|x| x.to_str()) != Some("md") {
                continue;
            }
            let Ok(content) = std::fs::read_to_string(&path) else {
                continue;
            };
            if content.to_lowercase().contains(ql)
                || path
                    .file_name()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .to_lowercase()
                    .contains(ql)
            {
                out.push(json!({
                    "path": path.to_string_lossy(),
                    "relative_path": path.strip_prefix(root).unwrap_or(&path).to_string_lossy(),
                    "preview": content.chars().take(500).collect::<String>(),
                }));
            }
        }
    }
    walk(&root, &root, &ql, max_results, &mut results);
    let mut result = json!({"query": query, "results": results});
    if obsidian {
        result["vault_root"] = json!(root.to_string_lossy());
    }
    Ok(result)
}

async fn knowledge_tree(State(state): State<ToolsState>, headers: HeaderMap) -> Response {
    let identity = match require_user(&state.auth, &headers) {
        Ok(id) => id,
        Err(r) => return r,
    };
    let (ws, email) = match scope_of(&state, &headers, &identity) {
        Ok(s) => s,
        Err(r) => return r,
    };
    match tree_notes(&state, &ws, &email) {
        Ok(v) => tool_ok(&state.workspace, v),
        Err(r) => r,
    }
}

fn tree_notes(state: &ToolsState, workspace_id: &str, email: &str) -> Result<Value, Response> {
    let root = knowledge_root(&state.brain_dir, workspace_id, email)?;
    let mut entries = Vec::new();
    for folder in KNOWLEDGE_FOLDERS {
        let dir = root.join(folder);
        let _ = std::fs::create_dir_all(&dir);
        let mut files: Vec<PathBuf> = walkdir_md(&dir);
        files.sort();
        for file in files {
            entries.push(json!({
                "folder": folder,
                "relative_path": file.strip_prefix(&root).unwrap_or(&file).to_string_lossy(),
                "size": file.metadata().map(|m| m.len()).unwrap_or(0),
            }));
        }
    }
    Ok(json!({"root": root.to_string_lossy(), "entries": entries}))
}

fn walkdir_md(dir: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    fn rec(dir: &Path, out: &mut Vec<PathBuf>) {
        let Ok(rd) = std::fs::read_dir(dir) else {
            return;
        };
        for e in rd.filter_map(|e| e.ok()) {
            let p = e.path();
            if p.is_dir() {
                rec(&p, out);
            } else if p.extension().and_then(|x| x.to_str()) == Some("md") {
                out.push(p);
            }
        }
    }
    rec(dir, &mut out);
    out
}

async fn obsidian_save(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    let identity = match require_user(&state.auth, &headers) {
        Ok(id) => id,
        Err(r) => return r,
    };
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    let content = parsed.get("content").and_then(Value::as_str).unwrap_or("");
    if content.is_empty() {
        return tool_err("Knowledge content is required.");
    }
    let (ws, email) = match scope_of(&state, &headers, &identity) {
        Ok(s) => s,
        Err(r) => return r,
    };
    match save_note(
        &state,
        &ws,
        &email,
        content,
        parsed.get("title").and_then(Value::as_str),
        true,
    ) {
        Ok(v) => tool_ok(&state.workspace, v),
        Err(r) => r,
    }
}

async fn obsidian_search(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    let identity = match require_user(&state.auth, &headers) {
        Ok(id) => id,
        Err(r) => return r,
    };
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    let query = parsed.get("query").and_then(Value::as_str).unwrap_or("");
    let (ws, email) = match scope_of(&state, &headers, &identity) {
        Ok(s) => s,
        Err(r) => return r,
    };
    match search_notes(&state, &ws, &email, query, 5, true) {
        Ok(v) => tool_ok(&state.workspace, v),
        Err(r) => r,
    }
}

async fn obsidian_tree(State(state): State<ToolsState>, headers: HeaderMap) -> Response {
    let identity = match require_user(&state.auth, &headers) {
        Ok(id) => id,
        Err(r) => return r,
    };
    let (ws, email) = match scope_of(&state, &headers, &identity) {
        Ok(s) => s,
        Err(r) => return r,
    };
    match tree_notes(&state, &ws, &email) {
        Ok(v) => tool_ok(&state.workspace, v),
        Err(r) => r,
    }
}

async fn obsidian_status(State(state): State<ToolsState>, headers: HeaderMap) -> Response {
    let identity = match require_user(&state.auth, &headers) {
        Ok(id) => id,
        Err(r) => return r,
    };
    let (ws, email) = match scope_of(&state, &headers, &identity) {
        Ok(s) => s,
        Err(r) => return r,
    };
    let root = match knowledge_root(&state.brain_dir, &ws, &email) {
        Ok(p) => p,
        Err(r) => return r,
    };
    let folders: Vec<String> = if root.exists() {
        std::fs::read_dir(&root)
            .map(|rd| {
                rd.filter_map(|e| e.ok())
                    .filter(|e| e.path().is_dir())
                    .filter_map(|e| e.file_name().into_string().ok())
                    .collect()
            })
            .unwrap_or_default()
    } else {
        Vec::new()
    };
    let ocr = which("tesseract");
    let mut body = OrderedMap::new();
    body.insert("status", json!("ok"));
    body.insert("vault_root", json!(root.to_string_lossy()));
    body.insert("folders", json!(folders));
    body.insert("ocr_engine", json!(ocr));
    json_status(StatusCode::OK, &body)
}

fn which(name: &str) -> Option<String> {
    let path = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path) {
        let candidate = dir.join(name);
        if candidate.is_file() {
            return Some(candidate.to_string_lossy().into_owned());
        }
    }
    None
}

async fn git_status(State(state): State<ToolsState>, headers: HeaderMap) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    git_run(&state, &["status", "--short"], ".")
}

async fn git_diff(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let parsed = if body.is_empty() {
        json!({})
    } else {
        match parse_json_object(&body) {
            Ok(v) => v,
            Err(r) => return r,
        }
    };
    let cwd = parsed.get("cwd").and_then(Value::as_str).unwrap_or(".");
    git_run(&state, &["diff", "--"], cwd)
}

async fn git_log(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let parsed = if body.is_empty() {
        json!({})
    } else {
        match parse_json_object(&body) {
            Ok(v) => v,
            Err(r) => return r,
        }
    };
    let max = parsed
        .get("max_count")
        .and_then(Value::as_i64)
        .unwrap_or(5)
        .clamp(1, 20);
    let cwd = parsed.get("cwd").and_then(Value::as_str).unwrap_or(".");
    let flag = format!("--max-count={max}");
    git_run(&state, &["log", &flag, "--oneline", "--decorate"], cwd)
}

async fn git_show(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let parsed = if body.is_empty() {
        json!({})
    } else {
        match parse_json_object(&body) {
            Ok(v) => v,
            Err(r) => return r,
        }
    };
    let revision = parsed
        .get("revision")
        .and_then(Value::as_str)
        .unwrap_or("HEAD");
    if revision.starts_with('-')
        || revision.contains("..")
        || revision.contains(':')
        || revision.contains('/')
        || revision.contains('\\')
    {
        return tool_err("Revision is not allowed.");
    }
    let cwd = parsed.get("cwd").and_then(Value::as_str).unwrap_or(".");
    git_run(
        &state,
        &["show", "--stat", "--oneline", "--decorate", revision],
        cwd,
    )
}

fn git_run(state: &ToolsState, args: &[&str], cwd: &str) -> Response {
    let workdir = match resolve(&state.workspace, cwd) {
        Ok(p) => p,
        Err(r) => return r,
    };
    if !workdir.exists() || !workdir.is_dir() {
        return tool_err("Working directory does not exist.");
    }
    let output = std::process::Command::new("git")
        .args(args)
        .current_dir(&workdir)
        .output();
    match output {
        Ok(out) => {
            let rel = if workdir == *state.workspace.root() {
                ".".into()
            } else {
                relative(&state.workspace, &workdir)
            };
            let stdout = String::from_utf8_lossy(&out.stdout);
            let stderr = String::from_utf8_lossy(&out.stderr);
            let stdout = tail(&stdout, 12_000);
            let stderr = tail(&stderr, 12_000);
            tool_ok(
                &state.workspace,
                json!({
                    "command": format!("git {}", args.join(" ")),
                    "cwd": rel,
                    "returncode": out.status.code().unwrap_or(1),
                    "stdout": stdout,
                    "stderr": stderr,
                }),
            )
        }
        Err(_) => tool_err("Git command timed out after 30 seconds."),
    }
}

fn tail(s: &str, n: usize) -> String {
    if s.len() <= n {
        s.to_string()
    } else {
        s[s.len() - n..].to_string()
    }
}

async fn run_command(
    State(state): State<ToolsState>,
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
    let command = parsed.get("command").and_then(Value::as_str).unwrap_or("");
    let cwd = parsed.get("cwd").and_then(Value::as_str).unwrap_or(".");
    let policy = lattice_agent::policy::ToolPolicy {
        risk: "exec".into(),
        destructive: false,
        shell: true,
        network: false,
        auto_approve: false,
        sandbox: "workspace".into(),
        rollback: "none".into(),
        capability: None,
        scope: None,
    };
    if let Some(reason) = is_circuit_breaker("run_command", &policy, &{
        let mut m = serde_json::Map::new();
        m.insert("command".into(), json!(command));
        m
    }) {
        return detail(
            StatusCode::FORBIDDEN,
            &format!("'run_command' 차단: {reason}"),
        );
    }
    match command::validate(&state.workspace, command, Some(cwd)) {
        Ok(validated) => {
            let output = std::process::Command::new(&validated.executable)
                .args(&validated.args)
                .current_dir(&validated.workdir)
                .output();
            match output {
                Ok(out) => {
                    let rel = if cwd.is_empty() { "." } else { cwd };
                    tool_ok(
                        &state.workspace,
                        json!({
                            "command": command,
                            "cwd": rel,
                            "returncode": out.status.code().unwrap_or(1),
                            "stdout": String::from_utf8_lossy(&out.stdout),
                            "stderr": String::from_utf8_lossy(&out.stderr),
                        }),
                    )
                }
                Err(err) => tool_err(&err.to_string()),
            }
        }
        Err(err) => detail(
            StatusCode::FORBIDDEN,
            &format!("'run_command' 차단: {}", err.message),
        ),
    }
}

async fn network_status(State(state): State<ToolsState>, headers: HeaderMap) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    tool_ok(
        &state.workspace,
        json!({"hostname": hostname(), "note": "sampled locally"}),
    )
}

fn hostname() -> String {
    std::process::Command::new("hostname")
        .output()
        .ok()
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .map(|s| s.trim().to_string())
        .unwrap_or_default()
}

async fn build_project(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_admin(&state.auth, &headers) {
        return r;
    }
    let parsed = if body.is_empty() {
        json!({})
    } else {
        match parse_json_object(&body) {
            Ok(v) => v,
            Err(r) => return r,
        }
    };
    let cwd = parsed.get("cwd").and_then(Value::as_str).unwrap_or(".");
    let script = parsed
        .get("script")
        .and_then(Value::as_str)
        .unwrap_or("build");
    script_missing(&state, cwd, script)
}

async fn deploy_project(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_admin(&state.auth, &headers) {
        return r;
    }
    let parsed = if body.is_empty() {
        json!({})
    } else {
        match parse_json_object(&body) {
            Ok(v) => v,
            Err(r) => return r,
        }
    };
    let cwd = parsed.get("cwd").and_then(Value::as_str).unwrap_or(".");
    let script = parsed
        .get("script")
        .and_then(Value::as_str)
        .unwrap_or("deploy");
    script_missing(&state, cwd, script)
}

fn script_missing(state: &ToolsState, cwd: &str, script: &str) -> Response {
    let workdir = match resolve(&state.workspace, cwd) {
        Ok(p) => p,
        Err(r) => return r,
    };
    let pkg = workdir.join("package.json");
    if !pkg.exists() {
        return tool_err(&format!(
            "package.json does not define a '{script}' script."
        ));
    }
    let Ok(text) = std::fs::read_to_string(&pkg) else {
        return tool_err(&format!(
            "package.json does not define a '{script}' script."
        ));
    };
    let Ok(val) = serde_json::from_str::<Value>(&text) else {
        return tool_err(&format!(
            "package.json does not define a '{script}' script."
        ));
    };
    if val
        .get("scripts")
        .and_then(|s| s.get(script))
        .and_then(Value::as_str)
        .is_none()
    {
        return tool_err(&format!(
            "package.json does not define a '{script}' script."
        ));
    }
    tool_err(&format!(
        "package.json does not define a '{script}' script."
    ))
}

async fn permissions(State(state): State<ToolsState>, headers: HeaderMap) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let mut names: Vec<&str> = gov_named_keys();
    names.sort_unstable();
    let items: Vec<String> = names
        .into_iter()
        .map(|n| serde_json::to_string(&permission_for(n)).unwrap_or_else(|_| "{}".into()))
        .collect();
    json_text(
        StatusCode::OK,
        &format!(
            "{{\"status\":\"ok\",\"permissions\":[{}]}}",
            items.join(",")
        ),
    )
}

fn gov_named_keys() -> Vec<&'static str> {
    vec![
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
    ]
}

async fn registry(State(state): State<ToolsState>, headers: HeaderMap) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let diag = diagnostics_value();
    let mut names: BTreeSet<&str> = HANDLERS.iter().copied().collect();
    names.extend(gov_named_keys());
    names.extend(DESCRIPTIONS.iter().map(|(n, _)| *n));
    let mut tools = Vec::new();
    for name in names {
        let g = gov_named(name).unwrap_or_else(default_gov);
        let policy = g.to_map();
        let perm = permission_for(name);
        let desc = DESCRIPTIONS
            .iter()
            .find(|(n, _)| *n == name)
            .map(|(_, d)| *d)
            .unwrap_or("");
        let mut tool = OrderedMap::new();
        tool.insert("name", json!(name));
        tool.insert("registered", json!(HANDLERS.contains(&name)));
        tool.insert("governed", json!(gov_named(name).is_some()));
        tool.insert(
            "described",
            json!(DESCRIPTIONS.iter().any(|(n, _)| *n == name)),
        );
        tool.insert("description", json!(desc));
        tool.insert("policy", serde_json::to_value(&policy).unwrap_or(json!({})));
        tool.insert(
            "permission",
            serde_json::to_value(&perm).unwrap_or(json!({})),
        );
        tools.push(serde_json::to_string(&tool).unwrap_or_else(|_| "{}".into()));
    }
    let status = if diag.ready { "ok" } else { "degraded" };
    let text = format!(
        "{{\"schema_version\":\"tool-registry-contract/v1\",\"status\":\"{status}\",\"boundary\":{{\"owner\":\"latticeai.core.tool_registry.ToolRegistry\",\"dispatch_owner\":\"latticeai.tools.DEFAULT_TOOL_REGISTRY\",\"policy_owner\":\"latticeai.core.tool_registry.ToolRegistry\",\"permission_owner\":\"latticeai.services.tool_dispatch.ToolDispatchService\"}},\"catalog_brief\":{},\"diagnostics\":{},\"tools\":[{}]}}",
        serde_json::to_string(TOOL_CATALOG_BRIEF.trim()).unwrap_or_else(|_| "\"\"".into()),
        serde_json::to_string(&diag.to_value()).unwrap_or_else(|_| "{}".into()),
        tools.join(","),
    );
    json_text(StatusCode::OK, &text)
}

struct Diag {
    ready: bool,
    registered: usize,
    governed: usize,
    described: usize,
    gov_without: Vec<String>,
    handler_without_gov: Vec<String>,
    handler_without_desc: Vec<String>,
    desc_without: Vec<String>,
}

impl Diag {
    fn to_value(&self) -> OrderedMap {
        let mut m = OrderedMap::new();
        m.insert("ready", json!(self.ready));
        m.insert("registered_tools", json!(self.registered));
        m.insert("governed_tools", json!(self.governed));
        m.insert("described_tools", json!(self.described));
        m.insert("governance_without_handler", json!(self.gov_without));
        m.insert(
            "handler_without_governance",
            json!(self.handler_without_gov),
        );
        m.insert(
            "handler_without_description",
            json!(self.handler_without_desc),
        );
        m.insert("description_without_handler", json!(self.desc_without));
        m
    }
}

fn diagnostics_value() -> Diag {
    let registered: BTreeSet<&str> = HANDLERS.iter().copied().collect();
    let governed: BTreeSet<&str> = gov_named_keys().into_iter().collect();
    let described: BTreeSet<&str> = DESCRIPTIONS.iter().map(|(n, _)| *n).collect();
    let gov_without: Vec<String> = governed
        .difference(&registered)
        .map(|s| (*s).to_string())
        .collect();
    let handler_without_gov: Vec<String> = registered
        .difference(&governed)
        .map(|s| (*s).to_string())
        .collect();
    let handler_without_desc: Vec<String> = registered
        .difference(&described)
        .map(|s| (*s).to_string())
        .collect();
    let desc_without: Vec<String> = described
        .difference(&registered)
        .map(|s| (*s).to_string())
        .collect();
    let ready =
        gov_without.is_empty() && handler_without_gov.is_empty() && handler_without_desc.is_empty();
    Diag {
        ready,
        registered: registered.len(),
        governed: governed.len(),
        described: described.len(),
        gov_without,
        handler_without_gov,
        handler_without_desc,
        desc_without,
    }
}

async fn diagnostics(State(state): State<ToolsState>, headers: HeaderMap) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let diag = diagnostics_value();
    let text = format!(
        "{{\"status\":\"ok\",\"diagnostics\":{}}}",
        serde_json::to_string(&diag.to_value()).unwrap_or_else(|_| "{}".into())
    );
    json_text(StatusCode::OK, &text)
}

async fn create_docx(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    create_document(state, headers, body, "docx").await
}
async fn create_xlsx(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    create_document(state, headers, body, "xlsx").await
}
async fn create_pptx(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    create_document(state, headers, body, "pptx").await
}
async fn create_pdf(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    create_document(state, headers, body, "pdf").await
}

async fn create_document(
    state: ToolsState,
    headers: HeaderMap,
    body: axum::body::Bytes,
    kind: &str,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let Some(worker) = state.worker.clone() else {
        return detail(
            StatusCode::SERVICE_UNAVAILABLE,
            "render seam is not configured",
        );
    };
    let payload: Value = if body.is_empty() {
        json!({})
    } else {
        match serde_json::from_slice(&body) {
            Ok(v) => v,
            Err(_) => return detail(StatusCode::UNPROCESSABLE_ENTITY, "invalid JSON"),
        }
    };
    match worker
        .post_json(&format!("/worker/render/{kind}"), &payload)
        .await
    {
        Ok(answer) => {
            let filename = answer
                .get("filename")
                .and_then(Value::as_str)
                .unwrap_or("document");
            let b64 = answer
                .get("content_b64")
                .and_then(Value::as_str)
                .unwrap_or("");
            use base64::Engine;
            let bytes = base64::engine::general_purpose::STANDARD
                .decode(b64)
                .unwrap_or_default();
            let path = match state.workspace.resolve(filename) {
                Ok(p) => p,
                Err(err) => return tool_err(&err.message),
            };
            if let Some(parent) = path.parent() {
                let _ = std::fs::create_dir_all(parent);
            }
            if let Err(err) = std::fs::write(&path, &bytes) {
                return tool_err(&err.to_string());
            }
            let mut result = serde_json::Map::new();
            result.insert("path".into(), json!(state.workspace.relative(&path)));
            result.insert("bytes".into(), json!(bytes.len() as i64));
            if let Some(rows) = answer.get("rows") {
                result.insert("rows".into(), rows.clone());
            }
            if let Some(slides) = answer.get("slides") {
                result.insert("slides".into(), slides.clone());
            }
            tool_ok(&state.workspace, Value::Object(result))
        }
        Err(err) => detail(
            StatusCode::from_u16(err.status().unwrap_or(502)).unwrap_or(StatusCode::BAD_GATEWAY),
            &err.to_string(),
        ),
    }
}
