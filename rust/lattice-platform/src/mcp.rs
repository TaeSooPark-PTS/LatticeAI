//! MCP / skills / plugin-directory family (v11.6.0, WP-R8).
//!
//! Port of `latticeai/api/mcp.py`. Custom MCP entries live in
//! `custom_mcps.json` (I1 `state_files`). Remote catalogs default to the
//! canned offline cache the HTTP fixtures captured; a live fetch is optional
//! and never fails a request.

use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use axum::routing::{delete, get, post};
use axum::Router;
use lattice_auth::AuthState;
use lattice_core::db::tables::state_files;
use serde_json::Value;

include!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/src/generated_catalogs.rs"
));

mod catalog;
mod dispatch;
mod handlers;
mod http;
mod protocol;
mod store;

pub(crate) use dispatch::{dispatch, is_native_tool};
pub(crate) use http::{
    detail, dump_indent2, json_status, json_text, localized, missing_fields, now_iso_seconds,
    parse_json_object, requested_scope, require_admin, require_user, sha256_hex, value_to_ordered,
};
pub(crate) use store::PlatformStore;

use catalog::{
    mcp_call, plugins_directory, plugins_directory_refresh, skills_install, skills_list,
    skills_marketplace, skills_marketplace_refresh,
};
use handlers::{
    mcp_claude_code_servers, mcp_connector, mcp_custom_add, mcp_custom_delete, mcp_custom_list,
    mcp_install, mcp_installed, mcp_recommend, mcp_registry_refresh, mcp_tools,
};
use protocol::mcp_jsonrpc;
// ── MCP family ─────────────────────────────────────────────────────────────

pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/mcp/tools"),
    ("POST", "/mcp/recommend"),
    ("POST", "/mcp/install"),
    ("GET", "/mcp/installed"),
    ("GET", "/mcp/connectors/:mcp_id"),
    ("POST", "/mcp/registry/refresh"),
    ("GET", "/mcp/claude-code-servers"),
    ("GET", "/mcp/custom"),
    ("POST", "/mcp/custom"),
    ("DELETE", "/mcp/custom/*mcp_id"),
    ("GET", "/skills/marketplace"),
    ("POST", "/skills/install"),
    ("GET", "/skills/list"),
    ("POST", "/skills/marketplace/refresh"),
    ("GET", "/plugins/directory"),
    ("POST", "/plugins/directory/refresh"),
    ("POST", "/mcp/call"),
];

/// Streamable-HTTP MCP JSON-RPC. Mounted on the product router, kept out of
/// [`MOUNTED`] so the OpenAPI product contract is unchanged.
pub const STREAMABLE: &[(&str, &str)] = &[("POST", "/mcp")];

pub const CANNED_REMOTE_MCPS: &str = r#"[{"id":"fixture-mcp","name":"Fixture MCP","description":"Canned MCP used by the HTTP fixture generator.","install_mode":"npm","category":"test","package":"fixture-mcp","source":"fixture","capabilities":["search"]},{"id":"fixture-connector","name":"Fixture Connector","description":"Canned connector so /mcp/connectors/{id} has a hit.","install_mode":"connector","category":"connector","source":"fixture","connector_url":"https://example.test/connector"}]"#;

pub const CANNED_SKILLS: &str = r#"[{"skill":"fixture-skill","plugin":"fixture-plugin","category":"development","description":"Canned skill.","author":"Fixture","license":"MIT"}]"#;

pub const CANNED_PLUGINS: &str = r#"[{"name":"fixture-plugin","description":"Canned plugin directory entry.","author":"Fixture","license":"MIT","category":"development"}]"#;

const EXPLICIT_CONSENT: &[&str] = &["local_list", "local_read", "local_write", "read_document"];

const MCP_TOOL_DESCRIPTIONS: &[(&str, &str)] = &[
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

#[derive(Clone)]
pub struct McpState {
    pub auth: Arc<AuthState>,
    pub data_dir: PathBuf,
    pub skills_dir: PathBuf,
    pub remote_mcps: Arc<Mutex<Vec<Value>>>,
    pub skills_market: Arc<Mutex<Vec<Value>>>,
    pub plugin_directory: Arc<Mutex<Vec<Value>>>,
    pub tools: Option<crate::tools::ToolsState>,
}

impl McpState {
    pub fn new(auth: Arc<AuthState>, data_dir: impl AsRef<Path>) -> Self {
        let data_dir = data_dir.as_ref().to_path_buf();
        Self {
            auth,
            skills_dir: resolve_skills_dir(&data_dir),
            data_dir,
            remote_mcps: Arc::new(Mutex::new(parse_arr(CANNED_REMOTE_MCPS))),
            skills_market: Arc::new(Mutex::new(parse_arr(CANNED_SKILLS))),
            plugin_directory: Arc::new(Mutex::new(parse_arr(CANNED_PLUGINS))),
            tools: None,
        }
    }

    pub fn with_tools(mut self, tools: crate::tools::ToolsState) -> Self {
        self.tools = Some(tools);
        self
    }

    pub fn with_skills_dir(mut self, dir: impl AsRef<Path>) -> Self {
        self.skills_dir = dir.as_ref().to_path_buf();
        self
    }

    fn custom_path(&self) -> PathBuf {
        self.data_dir.join(state_files::CUSTOM_MCPS)
    }

    pub(crate) fn load_custom(&self) -> Vec<Value> {
        let path = self.custom_path();
        if !path.exists() {
            return Vec::new();
        }
        std::fs::read_to_string(&path)
            .ok()
            .and_then(|t| serde_json::from_str::<Value>(&t).ok())
            .and_then(|v| v.as_array().cloned())
            .unwrap_or_default()
    }

    pub(crate) fn save_custom(&self, items: &[Value]) {
        let path = self.custom_path();
        if let Some(parent) = path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let _ = std::fs::write(
            &path,
            serde_json::to_string_pretty(&items).unwrap_or_else(|_| "[]".into()),
        );
    }

    pub(crate) fn combined(&self) -> Vec<Value> {
        let mut out = parse_arr(BUILTIN_MCP_JSON);
        if let Ok(remote) = self.remote_mcps.lock() {
            out.extend(remote.iter().cloned());
        }
        out
    }
}

fn resolve_skills_dir(data_dir: &Path) -> PathBuf {
    if let Ok(configured) = std::env::var("LATTICEAI_SKILLS_DIR") {
        let trimmed = configured.trim();
        if !trimmed.is_empty() {
            return PathBuf::from(trimmed);
        }
    }
    let under_data = data_dir.join("skills");
    if under_data.is_dir() {
        return under_data;
    }
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../skills");
    if repo.is_dir() {
        return repo;
    }
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../latticeai/core/skills")
}

fn parse_arr(text: &str) -> Vec<Value> {
    serde_json::from_str::<Value>(text)
        .ok()
        .and_then(|v| v.as_array().cloned())
        .unwrap_or_default()
}

pub fn router(state: McpState) -> Router {
    Router::new()
        .route("/mcp", post(mcp_jsonrpc))
        .route("/mcp/tools", get(mcp_tools))
        .route("/mcp/recommend", post(mcp_recommend))
        .route("/mcp/install", post(mcp_install))
        .route("/mcp/installed", get(mcp_installed))
        .route("/mcp/connectors/:mcp_id", get(mcp_connector))
        .route("/mcp/registry/refresh", post(mcp_registry_refresh))
        .route("/mcp/claude-code-servers", get(mcp_claude_code_servers))
        .route("/mcp/custom", get(mcp_custom_list).post(mcp_custom_add))
        .route("/mcp/custom/*mcp_id", delete(mcp_custom_delete))
        .route("/skills/marketplace", get(skills_marketplace))
        .route("/skills/install", post(skills_install))
        .route("/skills/list", get(skills_list))
        .route(
            "/skills/marketplace/refresh",
            post(skills_marketplace_refresh),
        )
        .route("/plugins/directory", get(plugins_directory))
        .route(
            "/plugins/directory/refresh",
            post(plugins_directory_refresh),
        )
        .route("/mcp/call", post(mcp_call))
        .with_state(state)
}
