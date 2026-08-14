//! Tool governance tables, catalog, and permission lookup.

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

pub(crate) const TEXT_EXTENSIONS: &[&str] = &[
    ".css", ".csv", ".html", ".js", ".json", ".jsx", ".md", ".py", ".ts", ".tsx", ".txt", ".xml",
    ".yaml", ".yml",
];

pub(crate) const GREP_BINARY_EXTS: &[&str] = &[
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z",
    ".mp3", ".mp4", ".mov", ".wav", ".woff", ".woff2", ".ttf", ".eot", ".ico", ".db", ".sqlite",
    ".pyc", ".pyo", ".o", ".so", ".dylib", ".dll", ".exe", ".bin",
];

pub(crate) const GREP_BINARY_DIRS: &[&str] = &[
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

pub(crate) const KNOWLEDGE_FOLDERS: &[&str] =
    &["10_Wiki", "00_Raw", "20_Skills", "30_Projects", "40_Log"];

pub(crate) const TOOL_CATALOG_BRIEF: &str = "\
FILESYSTEM  : list_dir  workspace_tree  read_file  write_file  edit_file  grep  search_files  inspect_html  preview_url
PLANNING    : todo_read  todo_write
PROJECT     : run_command  build_project  deploy_project  create_web_project
GIT (read)  : git_status  git_diff  git_log  git_show
LOCAL FS    : local_list  local_read  local_write  read_document
DOCS        : create_docx  create_xlsx  create_pptx  create_pdf
KNOWLEDGE   : knowledge_save  knowledge_search  knowledge_tree
COMPUTER    : computer_screenshot  computer_open_app  computer_open_url  computer_click  computer_type  computer_key
MISC        : network_status  clear_history  final";

pub(crate) const HANDLERS: &[&str] = &[
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

pub(crate) const DESCRIPTIONS: &[(&str, &str)] = &[
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
pub(crate) struct Gov {
    pub(crate) risk: &'static str,
    pub(crate) destructive: bool,
    pub(crate) shell: bool,
    pub(crate) network: bool,
    pub(crate) auto_approve: bool,
    pub(crate) sandbox: &'static str,
    pub(crate) rollback: &'static str,
    pub(crate) capability: Option<&'static str>,
    pub(crate) scope: Option<&'static str>,
}

impl Gov {
    pub(crate) fn to_map(self) -> OrderedMap {
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

pub(crate) fn gov_named(name: &str) -> Option<Gov> {
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

pub(crate) fn default_gov() -> Gov {
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
