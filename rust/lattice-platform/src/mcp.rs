//! MCP / skills / plugin-directory family (v11.6.0, WP-R8).
//!
//! Port of `latticeai/api/mcp.py`. Custom MCP entries live in
//! `custom_mcps.json` (I1 `state_files`). Remote catalogs default to the
//! canned offline cache the HTTP fixtures captured; a live fetch is optional
//! and never fails a request.


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
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use axum::extract::{Path as AxumPath, Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use axum::routing::{delete, get, post};
use axum::Router;
use lattice_auth::{AuthState, Identity, OrderedMap};
use lattice_core::db::tables::state_files;
use lattice_core::messages;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

include!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/src/generated_catalogs.rs"
));

// ── shared HTTP / time / store (used by sibling R8 modules) ────────────────

pub(crate) fn now_iso_seconds() -> String {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let dt = chrono_naive(secs);
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}",
        dt.0, dt.1, dt.2, dt.3, dt.4, dt.5
    )
}

pub(crate) fn now_iso_full() -> String {
    let dur = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let dt = chrono_naive(dur.as_secs());
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}.{:06}",
        dt.0,
        dt.1,
        dt.2,
        dt.3,
        dt.4,
        dt.5,
        dur.subsec_micros()
    )
}

/// Civil time from a unix timestamp, local-offset-free (UTC numbers).
fn chrono_naive(secs: u64) -> (i32, u32, u32, u32, u32, u32) {
    let z = secs as i64;
    let days = z.div_euclid(86_400);
    let rem = z.rem_euclid(86_400) as u32;
    let hour = rem / 3600;
    let min = (rem % 3600) / 60;
    let sec = rem % 60;
    let (y, m, d) = civil_from_days(days);
    (y, m, d, hour, min, sec)
}

fn civil_from_days(z: i64) -> (i32, u32, u32) {
    // Howard Hinnant civil_from_days
    let z = z + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    (y as i32, m as u32, d as u32)
}

pub(crate) fn lang(headers: &HeaderMap) -> &'static str {
    messages::resolve_language(
        headers
            .get(messages::LANGUAGE_HEADER)
            .and_then(|v| v.to_str().ok()),
        headers
            .get(axum::http::header::ACCEPT_LANGUAGE)
            .and_then(|v| v.to_str().ok()),
    )
}

pub(crate) fn json_text(status: StatusCode, text: &str) -> Response {
    lattice_auth::response::json_response(status, text, None)
}

pub(crate) fn json_status(status: StatusCode, body: &OrderedMap) -> Response {
    let text = serde_json::to_string(body).unwrap_or_else(|_| "{}".into());
    json_text(status, &text)
}

pub(crate) fn json_value(status: StatusCode, body: &Value) -> Response {
    let text = serde_json::to_string(body).unwrap_or_else(|_| "{}".into());
    json_text(status, &text)
}

pub(crate) fn detail(status: StatusCode, message: &str) -> Response {
    let mut body = OrderedMap::new();
    body.insert("detail", json!(message));
    json_status(status, &body)
}

pub(crate) fn localized(status: u16, id: &str, headers: &HeaderMap) -> Response {
    let err = messages::http_error(status, id, lang(headers), &[]);
    let (code, body) = err.into_response_parts();
    json_value(
        StatusCode::from_u16(code).unwrap_or(StatusCode::BAD_REQUEST),
        &body,
    )
}

pub(crate) fn missing_fields(input: &Value, fields: &[&str]) -> Response {
    let mut details = Vec::new();
    for field in fields {
        let mut entry = OrderedMap::new();
        entry.insert("type", json!("missing"));
        entry.insert("loc", json!(["body", field]));
        entry.insert("msg", json!("Field required"));
        entry.insert("input", input.clone());
        details.push(Value::Object(
            entry
                .iter()
                .map(|(k, v)| (k.to_string(), v.clone()))
                .collect(),
        ));
    }
    // Preserve insertion order of each entry: serialize via OrderedMap list.
    let rendered: Vec<String> = fields
        .iter()
        .map(|field| {
            let mut entry = OrderedMap::new();
            entry.insert("type", json!("missing"));
            entry.insert("loc", json!(["body", field]));
            entry.insert("msg", json!("Field required"));
            entry.insert("input", input.clone());
            serde_json::to_string(&entry).unwrap_or_else(|_| "{}".into())
        })
        .collect();
    let body = format!("{{\"detail\":[{}]}}", rendered.join(","));
    let _ = details;
    json_text(StatusCode::UNPROCESSABLE_ENTITY, &body)
}

pub(crate) fn parse_json_object(bytes: &[u8]) -> Result<Value, Response> {
    match serde_json::from_slice::<Value>(bytes) {
        Ok(Value::Object(map)) => Ok(Value::Object(map)),
        Ok(other) => {
            let mut entry = OrderedMap::new();
            entry.insert("type", json!("model_attributes_type"));
            entry.insert("loc", json!(["body"]));
            entry.insert(
                "msg",
                json!("Input should be a valid dictionary or object to extract fields from"),
            );
            entry.insert("input", other);
            let body = format!(
                "{{\"detail\":[{}]}}",
                serde_json::to_string(&entry).unwrap_or_else(|_| "{}".into())
            );
            Err(json_text(StatusCode::UNPROCESSABLE_ENTITY, &body))
        }
        Err(error) => {
            let mut ctx = OrderedMap::new();
            ctx.insert("error", json!(error.to_string()));
            let mut entry = OrderedMap::new();
            entry.insert("type", json!("json_invalid"));
            entry.insert("loc", json!(["body", 0]));
            entry.insert("msg", json!("JSON decode error"));
            entry.insert("input", json!({}));
            entry.insert("ctx", serde_json::to_value(&ctx).unwrap_or(json!({})));
            let body = format!(
                "{{\"detail\":[{}]}}",
                serde_json::to_string(&entry).unwrap_or_else(|_| "{}".into())
            );
            Err(json_text(StatusCode::UNPROCESSABLE_ENTITY, &body))
        }
    }
}

pub(crate) fn require_user(auth: &AuthState, headers: &HeaderMap) -> Result<Identity, Response> {
    auth.require_user(headers)
}

pub(crate) fn require_admin(auth: &AuthState, headers: &HeaderMap) -> Result<Identity, Response> {
    auth.require_admin(headers)
}

pub(crate) fn requested_scope(headers: &HeaderMap, query: Option<&str>) -> String {
    lattice_auth::workspace_scope_from_request(headers, query)
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "personal".into())
}

pub(crate) fn value_to_ordered(value: &Value) -> OrderedMap {
    let mut out = OrderedMap::new();
    if let Some(obj) = value.as_object() {
        for (k, v) in obj {
            out.insert(k.clone(), v.clone());
        }
    }
    out
}

pub(crate) fn dump_indent2(value: &Value) -> String {
    serde_json::to_string_pretty(value).unwrap_or_else(|_| "[]".into())
}

pub(crate) fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

pub(crate) fn json_hash(value: &Value) -> String {
    let payload = serde_json::to_string(value).unwrap_or_default();
    sha256_hex(payload.as_bytes())
}

// ── workspace_os.json (shared by marketplace / plugins / agents) ───────────

#[derive(Clone)]
pub(crate) struct PlatformStore {
    path: PathBuf,
}

impl PlatformStore {
    pub(crate) fn new(data_dir: impl AsRef<Path>) -> Self {
        Self {
            path: data_dir.as_ref().join("workspace_os.json"),
        }
    }

    pub(crate) fn load(&self) -> Value {
        if let Ok(text) = std::fs::read_to_string(&self.path) {
            if let Ok(value) = serde_json::from_str::<Value>(&text) {
                if value.is_object() {
                    return value;
                }
            }
        }
        default_workspace_state()
    }

    pub(crate) fn save(&self, state: &Value) {
        lattice_auth::atomic::write_text(&self.path, &dump_indent2(state));
    }

    fn scope<'a>(&self, requested: Option<&'a str>) -> &'a str {
        match requested {
            Some(s) if !s.is_empty() => s,
            _ => "personal",
        }
    }

    pub(crate) fn list_agents(&self) -> Value {
        let state = self.load();
        let agents = state
            .get("agents")
            .cloned()
            .unwrap_or_else(|| json!(default_agents()));
        let runs = state
            .get("agent_runs")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        json!({ "agents": agents, "runs": runs })
    }

    pub(crate) fn get_agent_run(&self, run_id: &str) -> Option<Value> {
        let state = self.load();
        state
            .get("agent_runs")
            .and_then(Value::as_array)
            .and_then(|runs| {
                runs.iter()
                    .find(|r| r.get("id").and_then(Value::as_str) == Some(run_id))
            })
            .cloned()
    }

    pub(crate) fn list_handoffs(&self, run_id: Option<&str>) -> Value {
        let state = self.load();
        let mut handoffs: Vec<Value> = state
            .get("handoffs")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        if let Some(run_id) = run_id {
            handoffs.retain(|h| h.get("run_id").and_then(Value::as_str) == Some(run_id));
        }
        handoffs.reverse();
        json!({ "handoffs": handoffs })
    }

    pub(crate) fn list_memory_snapshots(&self, limit: usize) -> Value {
        let state = self.load();
        let snaps = state
            .get("memory_snapshots")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let cap = limit.clamp(1, 200);
        let start = snaps.len().saturating_sub(cap);
        let mut out = snaps[start..].to_vec();
        out.reverse();
        json!({ "snapshots": out })
    }

    pub(crate) fn create_memory_snapshot(
        &self,
        label: &str,
        reason: &str,
        user_email: Option<&str>,
        workspace_id: Option<&str>,
        memory_ids: Option<&[String]>,
    ) -> Value {
        let mut state = self.load();
        let scope = self.scope(workspace_id).to_string();
        let memories: Vec<Value> = state
            .get("memories")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .filter(|m| {
                if let Some(email) = user_email {
                    match m.get("user_email") {
                        None | Some(Value::Null) => true,
                        Some(Value::String(s)) => s == email,
                        _ => false,
                    }
                } else {
                    true
                }
            })
            .filter(|m| {
                if let Some(ids) = memory_ids {
                    m.get("id")
                        .and_then(Value::as_str)
                        .map(|id| ids.iter().any(|x| x == id))
                        .unwrap_or(false)
                } else {
                    true
                }
            })
            .collect();
        let created = now_iso_seconds();
        let hash_src = json!([label, scope, memories, created]);
        let id = format!("memory-snapshot-{}", &json_hash(&hash_src)[..16]);
        let snapshot = json!({
            "id": id,
            "label": label,
            "reason": reason,
            "workspace_id": scope,
            "user_email": user_email,
            "memory_count": memories.len(),
            "memories": memories,
            "created_at": created,
        });
        if let Some(obj) = state.as_object_mut() {
            let list = obj.entry("memory_snapshots").or_insert_with(|| json!([]));
            if let Some(arr) = list.as_array_mut() {
                arr.push(snapshot.clone());
            }
        }
        self.save(&state);
        snapshot
    }

    pub(crate) fn create_workflow(
        &self,
        name: &str,
        steps: Vec<Value>,
        nodes: Vec<Value>,
        metadata: Value,
        user_email: Option<&str>,
        workspace_id: Option<&str>,
    ) -> Value {
        let mut state = self.load();
        let scope = self.scope(workspace_id).to_string();
        let created = now_iso_seconds();
        let hash_src = json!([name, steps, user_email, created]);
        let id = format!("workflow-{}", &json_hash(&hash_src)[..16]);
        let mut workflow = json!({
            "id": id,
            "name": if name.is_empty() { "Untitled workflow" } else { name },
            "steps": steps,
            "user_email": user_email,
            "workspace_id": scope,
            "metadata": metadata,
            "events": [{"type": "created", "timestamp": created}],
            "created_at": created,
            "updated_at": created,
        });
        if !nodes.is_empty() {
            workflow["nodes"] = json!(nodes);
        }
        if let Some(obj) = state.as_object_mut() {
            let list = obj.entry("workflows").or_insert_with(|| json!([]));
            if let Some(arr) = list.as_array_mut() {
                arr.push(workflow.clone());
            }
        }
        self.save(&state);
        workflow
    }

    pub(crate) fn list_plugin_registry(&self) -> BTreeMap<String, Value> {
        let state = self.load();
        state
            .get("plugin_registry")
            .and_then(Value::as_object)
            .map(|m| m.iter().map(|(k, v)| (k.clone(), v.clone())).collect())
            .unwrap_or_default()
    }

    pub(crate) fn set_plugin_enabled(&self, plugin_id: &str, enabled: bool) -> Value {
        let mut state = self.load();
        let updated = now_iso_seconds();
        let entry = {
            let obj = state.as_object_mut().expect("state object");
            let registry = obj.entry("plugin_registry").or_insert_with(|| json!({}));
            let map = registry.as_object_mut().expect("registry object");
            let slot = map
                .entry(plugin_id.to_string())
                .or_insert_with(|| json!({"id": plugin_id}));
            slot["enabled"] = json!(enabled);
            slot["updated_at"] = json!(updated);
            slot.clone()
        };
        self.save(&state);
        entry
    }

    pub(crate) fn mark_plugin_uninstalled(&self, plugin_id: &str) -> Value {
        let mut state = self.load();
        let updated = now_iso_seconds();
        let registry_entry = {
            let obj = state.as_object_mut().expect("state object");
            let registry = obj.entry("plugin_registry").or_insert_with(|| json!({}));
            let map = registry.as_object_mut().expect("registry object");
            let slot = map
                .entry(plugin_id.to_string())
                .or_insert_with(|| json!({"id": plugin_id}));
            slot["installed"] = json!(false);
            slot["enabled"] = json!(false);
            slot["updated_at"] = json!(updated);
            slot.clone()
        };
        self.save(&state);
        json!({
            "status": "ok",
            "plugin_id": plugin_id,
            "registry": registry_entry,
        })
    }

    pub(crate) fn list_template_registry(&self, workspace_id: Option<&str>) -> Value {
        let state = self.load();
        let registry = state
            .get("template_registry")
            .cloned()
            .unwrap_or_else(|| json!({}));
        if workspace_id.is_none() {
            return registry;
        }
        let scope = self.scope(workspace_id);
        let mut filtered = serde_json::Map::new();
        if let Some(obj) = registry.as_object() {
            for (k, v) in obj {
                let ws = v
                    .get("workspace_id")
                    .and_then(Value::as_str)
                    .unwrap_or("personal");
                if ws == scope {
                    filtered.insert(k.clone(), v.clone());
                }
            }
        }
        Value::Object(filtered)
    }

    pub(crate) fn mark_template_installed(
        &self,
        kind: &str,
        template_id: &str,
        version: &str,
        metadata: Value,
        workspace_id: Option<&str>,
    ) -> Value {
        let mut state = self.load();
        let scope = self.scope(workspace_id).to_string();
        let key = if scope == "personal" {
            format!("{kind}:{template_id}")
        } else {
            format!("{scope}:{kind}:{template_id}")
        };
        let updated = now_iso_seconds();
        let entry = json!({
            "id": template_id,
            "kind": kind,
            "version": version,
            "installed": true,
            "workspace_id": scope,
            "metadata": metadata,
            "updated_at": updated,
        });
        if let Some(obj) = state.as_object_mut() {
            let registry = obj.entry("template_registry").or_insert_with(|| json!({}));
            if let Some(map) = registry.as_object_mut() {
                map.insert(key, entry.clone());
            }
        }
        self.save(&state);
        entry
    }
}

fn default_agents() -> Vec<Value> {
    vec![
        json!({"id":"agent:planner","name":"Planner","role":"Breaks workspace goals into executable plans.","status":"available","relationships":["agent:executor","agent:reviewer"]}),
        json!({"id":"agent:executor","name":"Executor","role":"Runs approved tool and code workflows.","status":"available","relationships":["agent:planner","agent:reviewer"]}),
        json!({"id":"agent:reviewer","name":"Reviewer","role":"Checks outputs, tests, and regressions.","status":"available","relationships":["agent:executor","agent:release"]}),
        json!({"id":"agent:researcher","name":"Researcher","role":"Finds and curates relevant workspace knowledge.","status":"available","relationships":["agent:planner"]}),
        json!({"id":"agent:release","name":"Release Agent","role":"Coordinates versioning, packaging, and release checks.","status":"available","relationships":["agent:reviewer"]}),
    ]
}

fn default_workspace_state() -> Value {
    json!({
        "agents": default_agents(),
        "agent_runs": [],
        "handoffs": [],
        "workflows": [],
        "workflow_runs": [],
        "memories": [],
        "memory_snapshots": [],
        "plugin_registry": {},
        "template_registry": {},
    })
}

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
}

impl McpState {
    pub fn new(auth: Arc<AuthState>, data_dir: impl AsRef<Path>) -> Self {
        let data_dir = data_dir.as_ref().to_path_buf();
        let skills_dir = std::env::var("LATTICEAI_SKILLS_DIR")
            .ok()
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../latticeai/core/skills")
            });
        Self {
            auth,
            data_dir,
            skills_dir,
            remote_mcps: Arc::new(Mutex::new(parse_arr(CANNED_REMOTE_MCPS))),
            skills_market: Arc::new(Mutex::new(parse_arr(CANNED_SKILLS))),
            plugin_directory: Arc::new(Mutex::new(parse_arr(CANNED_PLUGINS))),
        }
    }

    fn custom_path(&self) -> PathBuf {
        self.data_dir.join(state_files::CUSTOM_MCPS)
    }

    fn load_custom(&self) -> Vec<Value> {
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

    fn save_custom(&self, items: &[Value]) {
        let path = self.custom_path();
        if let Some(parent) = path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let _ = std::fs::write(
            &path,
            serde_json::to_string_pretty(&items).unwrap_or_else(|_| "[]".into()),
        );
    }

    fn combined(&self) -> Vec<Value> {
        let mut out = parse_arr(BUILTIN_MCP_JSON);
        if let Ok(remote) = self.remote_mcps.lock() {
            out.extend(remote.iter().cloned());
        }
        out
    }
}

fn parse_arr(text: &str) -> Vec<Value> {
    serde_json::from_str::<Value>(text)
        .ok()
        .and_then(|v| v.as_array().cloned())
        .unwrap_or_default()
}

pub fn router(state: McpState) -> Router {
    Router::new()
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

fn public_item(item: &Value, installed_state: &Value) -> OrderedMap {
    let id = item.get("id").and_then(Value::as_str).unwrap_or("");
    let mode = item
        .get("install_mode")
        .and_then(Value::as_str)
        .unwrap_or("");
    let state = installed_state
        .get(id)
        .cloned()
        .unwrap_or(Value::Object(Default::default()));
    let installed = matches!(mode, "builtin" | "bundled")
        || state
            .get("installed")
            .and_then(Value::as_bool)
            .unwrap_or(false);
    let connector_pending = mode == "connector"
        && !state
            .get("authenticated")
            .and_then(Value::as_bool)
            .unwrap_or(false);
    let authenticated = mode != "connector"
        || state
            .get("authenticated")
            .and_then(Value::as_bool)
            .unwrap_or(false);
    let status = state
        .get("status")
        .and_then(Value::as_str)
        .map(str::to_string)
        .unwrap_or_else(|| {
            if installed && !connector_pending {
                "active".into()
            } else if connector_pending {
                "needs_auth".into()
            } else {
                "available".into()
            }
        });
    let mut out = OrderedMap::new();
    out.insert("id", json!(id));
    out.insert("name", item.get("name").cloned().unwrap_or(json!("")));
    out.insert(
        "category",
        item.get("category").cloned().unwrap_or(json!("")),
    );
    out.insert("install_mode", json!(mode));
    out.insert(
        "description",
        item.get("description").cloned().unwrap_or(json!("")),
    );
    out.insert(
        "capabilities",
        item.get("capabilities").cloned().unwrap_or(json!([])),
    );
    out.insert(
        "connector_url",
        item.get("connector_url").cloned().unwrap_or(Value::Null),
    );
    out.insert(
        "external_url",
        item.get("external_url").cloned().unwrap_or(Value::Null),
    );
    out.insert(
        "package",
        item.get("package").cloned().unwrap_or(Value::Null),
    );
    out.insert(
        "homepage",
        item.get("homepage").cloned().unwrap_or(Value::Null),
    );
    out.insert(
        "source",
        item.get("source").cloned().unwrap_or(json!("local")),
    );
    out.insert("installed", json!(installed));
    out.insert("status", json!(status));
    out.insert("authenticated", json!(authenticated));
    out.insert(
        "updated_at",
        state.get("updated_at").cloned().unwrap_or(Value::Null),
    );
    out
}

fn public_env_vars(items: &Value) -> Vec<Value> {
    let mut out = Vec::new();
    if let Some(arr) = items.as_array() {
        for item in arr {
            if !item.is_object() {
                continue;
            }
            let name = item.get("name").and_then(Value::as_str).unwrap_or("");
            if name.is_empty() {
                continue;
            }
            let configured = item
                .get("value")
                .map(|v| match v {
                    Value::Null => false,
                    Value::String(s) => !s.is_empty(),
                    Value::Bool(b) => *b,
                    _ => true,
                })
                .unwrap_or(false);
            out.push(json!({"name": name, "configured": configured}));
        }
    }
    out
}

async fn mcp_tools(State(state): State<McpState>, headers: HeaderMap) -> Response {
    if let Err(resp) = require_user(&state.auth, &headers) {
        return resp;
    }
    let registry = state.combined();
    let installed = json!({});
    let mcps: Vec<OrderedMap> = registry
        .iter()
        .map(|i| public_item(i, &installed))
        .collect();
    let mut tools = Vec::new();
    for (name, description) in MCP_TOOL_DESCRIPTIONS {
        if EXPLICIT_CONSENT.contains(name) {
            continue;
        }
        let gov = crate::tools::governance_for(name);
        let mut tool = OrderedMap::new();
        tool.insert("name", json!(name));
        tool.insert("description", json!(description));
        tool.insert(
            "permission",
            serde_json::to_value(crate::tools::permission_for(name)).unwrap_or(json!({})),
        );
        tool.insert(
            "governance",
            serde_json::to_value(&gov).unwrap_or(json!({})),
        );
        tools.push(tool);
    }
    let mut body = OrderedMap::new();
    body.insert("status", json!("ok"));
    body.insert("workspace", json!("."));
    body.insert(
        "installed_mcps",
        json!(mcps
            .iter()
            .map(|m| serde_json::to_value(m).unwrap_or(json!({})))
            .collect::<Vec<_>>()),
    );
    // Re-serialize tools with key order
    let tools_json: Vec<String> = tools
        .iter()
        .map(|t| serde_json::to_string(t).unwrap_or_else(|_| "{}".into()))
        .collect();
    let mcps_json: Vec<String> = mcps
        .iter()
        .map(|t| serde_json::to_string(t).unwrap_or_else(|_| "{}".into()))
        .collect();
    let text = format!(
        "{{\"status\":\"ok\",\"workspace\":\".\",\"installed_mcps\":[{}],\"tools\":[{}]}}",
        mcps_json.join(","),
        tools_json.join(",")
    );
    let _ = body;
    json_text(StatusCode::OK, &text)
}

async fn mcp_recommend(
    State(state): State<McpState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(resp) = require_user(&state.auth, &headers) {
        return resp;
    }
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    if parsed.get("query").and_then(Value::as_str).is_none() && parsed.get("query").is_none() {
        return missing_fields(&parsed, &["query"]);
    }
    if !parsed
        .as_object()
        .map(|o| o.contains_key("query"))
        .unwrap_or(false)
    {
        return missing_fields(&parsed, &["query"]);
    }
    let query = parsed.get("query").and_then(Value::as_str).unwrap_or("");
    let limit = parsed.get("limit").and_then(Value::as_u64).unwrap_or(5) as usize;
    let text = query.to_lowercase();
    let registry = state.combined();
    let installed = json!({});
    let mut scored: Vec<(i32, OrderedMap)> = Vec::new();
    for item in &registry {
        let mut score = 0;
        let mut hits: Vec<String> = Vec::new();
        if let Some(kws) = item.get("keywords").and_then(Value::as_array) {
            for kw in kws {
                let k = kw.as_str().unwrap_or("");
                if !k.is_empty() && text.contains(&k.to_lowercase()) {
                    score += if k.len() > 2 { 3 } else { 1 };
                    hits.push(k.to_string());
                }
            }
        }
        if hits.is_empty() && !text.is_empty() {
            let desc = item
                .get("description")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_lowercase();
            for word in text.split_whitespace() {
                if word.len() > 2 && desc.split_whitespace().any(|w| w == word) {
                    score += 1;
                    hits.push(word.to_string());
                }
            }
        }
        if item.get("id").and_then(Value::as_str) == Some("filesystem")
            && ["만들", "구현", "build", "deploy", "코드", "앱"]
                .iter()
                .any(|w| text.contains(w))
        {
            score += 2;
        }
        if score > 0 {
            let mut public = public_item(item, &installed);
            public.insert("score", json!(score));
            public.insert(
                "matched_keywords",
                json!(hits.into_iter().take(6).collect::<Vec<_>>()),
            );
            scored.push((score, public));
        }
    }
    if scored.is_empty() {
        for item in &registry {
            if matches!(
                item.get("id").and_then(Value::as_str),
                Some("filesystem" | "browser" | "documents")
            ) {
                let mut public = public_item(item, &installed);
                public.insert("score", json!(1));
                public.insert("matched_keywords", json!([]));
                scored.push((1, public));
            }
        }
    }
    scored.sort_by(|a, b| b.0.cmp(&a.0));
    let cap = limit.clamp(1, 24);
    let recs: Vec<String> = scored
        .into_iter()
        .take(cap)
        .map(|(_, m)| serde_json::to_string(&m).unwrap_or_else(|_| "{}".into()))
        .collect();
    json_text(
        StatusCode::OK,
        &format!("{{\"recommendations\":[{}]}}", recs.join(",")),
    )
}

async fn mcp_install(
    State(state): State<McpState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(resp) = require_admin(&state.auth, &headers) {
        return resp;
    }
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    if !parsed
        .as_object()
        .map(|o| o.contains_key("mcp_id"))
        .unwrap_or(false)
    {
        return missing_fields(&parsed, &["mcp_id"]);
    }
    let mcp_id = parsed.get("mcp_id").and_then(Value::as_str).unwrap_or("");
    let registry = state.combined();
    if !registry
        .iter()
        .any(|i| i.get("id").and_then(Value::as_str) == Some(mcp_id))
    {
        return detail(StatusCode::NOT_FOUND, "MCP를 찾을 수 없습니다.");
    }
    detail(StatusCode::NOT_FOUND, "MCP를 찾을 수 없습니다.")
}

async fn mcp_installed(State(state): State<McpState>, headers: HeaderMap) -> Response {
    if let Err(resp) = require_user(&state.auth, &headers) {
        return resp;
    }
    let registry = state.combined();
    let installed = json!({});
    let items: Vec<String> = registry
        .iter()
        .map(|i| serde_json::to_string(&public_item(i, &installed)).unwrap_or_else(|_| "{}".into()))
        .collect();
    json_text(
        StatusCode::OK,
        &format!("{{\"installed\":[{}]}}", items.join(",")),
    )
}

async fn mcp_connector(
    State(state): State<McpState>,
    headers: HeaderMap,
    AxumPath(mcp_id): AxumPath<String>,
) -> Response {
    if let Err(resp) = require_user(&state.auth, &headers) {
        return resp;
    }
    let registry = state.combined();
    let item = registry
        .iter()
        .find(|i| i.get("id").and_then(Value::as_str) == Some(mcp_id.as_str()));
    match item {
        Some(item) if item.get("install_mode").and_then(Value::as_str) == Some("connector") => {
            let mut public = public_item(item, &json!({}));
            let name = item.get("name").and_then(Value::as_str).unwrap_or("");
            public.insert(
                "instructions",
                json!([
                    "Codex 또는 ChatGPT 앱의 Connectors 설정을 엽니다.",
                    format!("{name} 항목을 선택하고 계정을 인증합니다."),
                    "인증 후 Lattice AI에서 이 MCP를 다시 활성화하면 작업에 사용할 수 있습니다.",
                ]),
            );
            json_status(StatusCode::OK, &public)
        }
        _ => localized(404, "mcp.connector_not_found", &headers),
    }
}

async fn mcp_registry_refresh(State(state): State<McpState>, headers: HeaderMap) -> Response {
    if let Err(resp) = require_user(&state.auth, &headers) {
        return resp;
    }
    let registry = state.combined();
    let remote = state.remote_mcps.lock().map(|g| g.len()).unwrap_or(0);
    let mut body = OrderedMap::new();
    body.insert("status", json!("ok"));
    body.insert("total", json!(registry.len()));
    body.insert("remote", json!(remote));
    json_status(StatusCode::OK, &body)
}

async fn mcp_claude_code_servers(State(state): State<McpState>, headers: HeaderMap) -> Response {
    if let Err(resp) = require_admin(&state.auth, &headers) {
        return resp;
    }
    let settings = dirs_home().join(".claude").join("settings.json");
    if !settings.exists() {
        return json_text(StatusCode::OK, "{\"servers\":[]}");
    }
    let Ok(raw) = std::fs::read_to_string(&settings) else {
        return json_text(StatusCode::OK, "{\"servers\":[]}");
    };
    let Ok(parsed) = serde_json::from_str::<Value>(&raw) else {
        return json_text(StatusCode::OK, "{\"servers\":[]}");
    };
    let Some(servers_obj) = parsed.get("mcpServers").and_then(Value::as_object) else {
        return json_text(StatusCode::OK, "{\"servers\":[]}");
    };
    let mut servers = Vec::new();
    for (name, cfg) in servers_obj {
        let cmd = cfg.get("command").and_then(Value::as_str).unwrap_or("");
        let args: Vec<&str> = cfg
            .get("args")
            .and_then(Value::as_array)
            .map(|a| a.iter().filter_map(Value::as_str).collect())
            .unwrap_or_default();
        let package = if args.is_empty() {
            cmd.to_string()
        } else {
            format!("{cmd} {}", args.join(" "))
        };
        let env = cfg.get("env").and_then(Value::as_object);
        let env_vars: Vec<Value> = env
            .map(|m| {
                m.iter()
                    .map(|(k, v)| {
                        json!({"name": k, "configured": !v.as_str().unwrap_or("").is_empty()})
                    })
                    .collect()
            })
            .unwrap_or_default();
        let mut item = OrderedMap::new();
        item.insert("id", json!(format!("claude-code:{name}")));
        item.insert("name", json!(name));
        item.insert("description", json!(format!("Claude Code MCP: {package}")));
        item.insert("package", json!(package));
        item.insert("icon", json!("🤖"));
        item.insert("category", json!("Claude Code"));
        item.insert("source", json!("claude-code"));
        item.insert("installed", json!(true));
        item.insert("env_vars", json!(env_vars));
        servers.push(serde_json::to_string(&item).unwrap_or_else(|_| "{}".into()));
    }
    json_text(
        StatusCode::OK,
        &format!("{{\"servers\":[{}]}}", servers.join(",")),
    )
}

fn dirs_home() -> PathBuf {
    std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("/"))
}

async fn mcp_custom_list(State(state): State<McpState>, headers: HeaderMap) -> Response {
    if let Err(resp) = require_user(&state.auth, &headers) {
        return resp;
    }
    let items: Vec<String> = state
        .load_custom()
        .into_iter()
        .map(|raw| {
            let mut item = value_to_ordered(&raw);
            item.insert(
                "env_vars",
                json!(public_env_vars(raw.get("env_vars").unwrap_or(&json!([])))),
            );
            serde_json::to_string(&item).unwrap_or_else(|_| "{}".into())
        })
        .collect();
    json_text(
        StatusCode::OK,
        &format!("{{\"custom\":[{}]}}", items.join(",")),
    )
}

async fn mcp_custom_add(
    State(state): State<McpState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(resp) = require_admin(&state.auth, &headers) {
        return resp;
    }
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    for field in ["name", "package"] {
        if !parsed
            .as_object()
            .map(|o| o.contains_key(field))
            .unwrap_or(false)
        {
            return missing_fields(&parsed, &[field]);
        }
    }
    let name = parsed
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim();
    let package = parsed
        .get("package")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim();
    if name.is_empty() {
        return localized(400, "mcp.name_required", &headers);
    }
    if package.is_empty() {
        return localized(400, "mcp.package_required", &headers);
    }
    let description = parsed
        .get("description")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim();
    let category = parsed
        .get("category")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .unwrap_or("custom");
    let icon = parsed
        .get("icon")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .unwrap_or("🔌");
    let env_vars = parsed.get("env_vars").cloned().unwrap_or(json!([]));
    let id = format!("custom:{}", name.to_lowercase().replace(' ', "-"));
    let mut entry = OrderedMap::new();
    entry.insert("id", json!(id));
    entry.insert("name", json!(name));
    entry.insert("package", json!(package));
    entry.insert("description", json!(description));
    entry.insert("category", json!(category));
    entry.insert("icon", json!(icon));
    entry.insert("env_vars", env_vars);
    entry.insert("install_mode", json!("npm"));
    entry.insert("source", json!("custom"));
    entry.insert("installed", json!(false));
    entry.insert("added_at", json!(now_iso_full()));
    let mut items = state.load_custom();
    items.retain(|e| e.get("id").and_then(Value::as_str) != Some(&id));
    items.push(serde_json::to_value(&entry).unwrap_or(json!({})));
    state.save_custom(&items);
    let text = format!(
        "{{\"status\":\"ok\",\"entry\":{}}}",
        serde_json::to_string(&entry).unwrap_or_else(|_| "{}".into())
    );
    json_text(StatusCode::OK, &text)
}

async fn mcp_custom_delete(
    State(state): State<McpState>,
    headers: HeaderMap,
    AxumPath(mcp_id): AxumPath<String>,
) -> Response {
    if let Err(resp) = require_admin(&state.auth, &headers) {
        return resp;
    }
    let mut items = state.load_custom();
    let before = items.len();
    items.retain(|e| e.get("id").and_then(Value::as_str) != Some(mcp_id.as_str()));
    if items.len() == before {
        return localized(404, "mcp.item_not_found", &headers);
    }
    state.save_custom(&items);
    json_text(StatusCode::OK, "{\"status\":\"ok\"}")
}

#[derive(Debug, serde::Deserialize, Default)]
struct SkillsQuery {
    category: Option<String>,
    author: Option<String>,
}

async fn skills_marketplace(
    State(state): State<McpState>,
    headers: HeaderMap,
    Query(q): Query<SkillsQuery>,
) -> Response {
    if let Err(resp) = require_user(&state.auth, &headers) {
        return resp;
    }
    let skills = state
        .skills_market
        .lock()
        .map(|g| g.clone())
        .unwrap_or_default();
    let installed: std::collections::HashSet<String> = if state.skills_dir.exists() {
        std::fs::read_dir(&state.skills_dir)
            .map(|rd| {
                rd.filter_map(|e| e.ok())
                    .filter(|e| e.path().is_dir())
                    .filter_map(|e| e.file_name().into_string().ok())
                    .collect()
            })
            .unwrap_or_default()
    } else {
        Default::default()
    };
    let mut filtered = skills.clone();
    if let Some(cat) = q.category.as_deref() {
        filtered.retain(|s| {
            s.get("category")
                .and_then(Value::as_str)
                .unwrap_or("")
                .eq_ignore_ascii_case(cat)
        });
    }
    if let Some(author) = q.author.as_deref() {
        filtered.retain(|s| {
            s.get("author")
                .and_then(Value::as_str)
                .unwrap_or("")
                .eq_ignore_ascii_case(author)
        });
    }
    let authors: std::collections::BTreeSet<String> = skills
        .iter()
        .filter_map(|s| s.get("author").and_then(Value::as_str).map(str::to_string))
        .collect();
    let categories: std::collections::BTreeSet<String> = skills
        .iter()
        .filter_map(|s| {
            s.get("category")
                .and_then(Value::as_str)
                .map(str::to_string)
        })
        .collect();
    let listed: Vec<String> = filtered
        .into_iter()
        .map(|s| {
            let mut item = value_to_ordered(&s);
            let name = s.get("skill").and_then(Value::as_str).unwrap_or("");
            item.insert("installed", json!(installed.contains(name)));
            serde_json::to_string(&item).unwrap_or_else(|_| "{}".into())
        })
        .collect();
    let authors_json = serde_json::to_string(&authors.into_iter().collect::<Vec<_>>())
        .unwrap_or_else(|_| "[]".into());
    let cats_json = serde_json::to_string(&categories.into_iter().collect::<Vec<_>>())
        .unwrap_or_else(|_| "[]".into());
    json_text(
        StatusCode::OK,
        &format!(
            "{{\"skills\":[{}],\"total\":{},\"authors\":{},\"categories\":{}}}",
            listed.join(","),
            listed.len(),
            authors_json,
            cats_json
        ),
    )
}

async fn skills_install(
    State(state): State<McpState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(resp) = require_admin(&state.auth, &headers) {
        return resp;
    }
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    for field in ["plugin", "skill"] {
        if !parsed
            .as_object()
            .map(|o| o.contains_key(field))
            .unwrap_or(false)
        {
            return missing_fields(&parsed, &[field]);
        }
    }
    let plugin = parsed.get("plugin").and_then(Value::as_str).unwrap_or("");
    let skill = parsed.get("skill").and_then(Value::as_str).unwrap_or("");
    detail(
        StatusCode::NOT_FOUND,
        &format!("Skill '{plugin}/{skill}' not found in marketplace"),
    )
}

async fn skills_list(State(state): State<McpState>, headers: HeaderMap) -> Response {
    if let Err(resp) = require_user(&state.auth, &headers) {
        return resp;
    }
    if !state.skills_dir.exists() {
        return json_text(StatusCode::OK, "{\"skills\":[]}");
    }
    let mut skills = Vec::new();
    let mut entries: Vec<_> = std::fs::read_dir(&state.skills_dir)
        .map(|rd| rd.filter_map(|e| e.ok()).collect())
        .unwrap_or_default();
    entries.sort_by_key(|e| e.file_name());
    for entry in entries {
        if !entry.path().is_dir() {
            continue;
        }
        let skill_md = entry.path().join("SKILL.md");
        if !skill_md.exists() {
            continue;
        }
        let Ok(text) = std::fs::read_to_string(&skill_md) else {
            continue;
        };
        let lines: Vec<&str> = text.lines().collect();
        let desc = lines
            .iter()
            .find(|ln| ln.starts_with("description:"))
            .and_then(|ln| ln.split_once(':'))
            .map(|(_, v)| v.trim().to_string())
            .unwrap_or_default();
        let comment = lines.first().copied().unwrap_or("");
        let source = if comment.contains("anthropics/claude-plugins-official") {
            "anthropic"
        } else if comment.contains("Source:") {
            "third-party"
        } else {
            "local"
        };
        let mut item = OrderedMap::new();
        item.insert(
            "name",
            json!(entry.file_name().to_string_lossy().into_owned()),
        );
        item.insert("description", json!(desc));
        item.insert("source", json!(source));
        skills.push(serde_json::to_string(&item).unwrap_or_else(|_| "{}".into()));
    }
    if skills.is_empty() {
        return json_text(StatusCode::OK, "{\"skills\":[]}");
    }
    json_text(
        StatusCode::OK,
        &format!(
            "{{\"skills\":[{}],\"total\":{}}}",
            skills.join(","),
            skills.len()
        ),
    )
}

async fn skills_marketplace_refresh(State(state): State<McpState>, headers: HeaderMap) -> Response {
    if let Err(resp) = require_user(&state.auth, &headers) {
        return resp;
    }
    let skills = state
        .skills_market
        .lock()
        .map(|g| g.clone())
        .unwrap_or_default();
    let mut by_author: BTreeMap<String, i64> = BTreeMap::new();
    for s in &skills {
        let author = s.get("author").and_then(Value::as_str).unwrap_or("");
        *by_author.entry(author.to_string()).or_insert(0) += 1;
    }
    // Python dict insertion order, not BTree — rebuild in first-seen order
    let mut seen = Vec::new();
    let mut counts: BTreeMap<String, i64> = BTreeMap::new();
    for s in &skills {
        let author = s.get("author").and_then(Value::as_str).unwrap_or("");
        if !counts.contains_key(author) {
            seen.push(author.to_string());
        }
        *counts.entry(author.to_string()).or_insert(0) += 1;
    }
    let mut by = OrderedMap::new();
    for a in seen {
        by.insert(a.clone(), json!(counts.get(&a).copied().unwrap_or(0)));
    }
    let _ = by_author;
    let text = format!(
        "{{\"status\":\"ok\",\"total\":{},\"by_author\":{}}}",
        skills.len(),
        serde_json::to_string(&by).unwrap_or_else(|_| "{}".into())
    );
    json_text(StatusCode::OK, &text)
}

#[derive(Debug, serde::Deserialize, Default)]
struct DirQuery {
    category: Option<String>,
    license: Option<String>,
    q: Option<String>,
}

async fn plugins_directory(
    State(state): State<McpState>,
    headers: HeaderMap,
    Query(q): Query<DirQuery>,
) -> Response {
    if let Err(resp) = require_user(&state.auth, &headers) {
        return resp;
    }
    let plugins = state
        .plugin_directory
        .lock()
        .map(|g| g.clone())
        .unwrap_or_default();
    let mut filtered = plugins.clone();
    if let Some(cat) = q.category.as_deref() {
        filtered.retain(|p| {
            p.get("category")
                .and_then(Value::as_str)
                .unwrap_or("")
                .eq_ignore_ascii_case(cat)
        });
    }
    if let Some(lic) = q.license.as_deref() {
        filtered.retain(|p| {
            p.get("license")
                .and_then(Value::as_str)
                .unwrap_or("")
                .eq_ignore_ascii_case(lic)
        });
    }
    if let Some(query) = q.q.as_deref() {
        let ql = query.to_lowercase();
        filtered.retain(|p| {
            p.get("name")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_lowercase()
                .contains(&ql)
                || p.get("description")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_lowercase()
                    .contains(&ql)
                || p.get("author")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_lowercase()
                    .contains(&ql)
        });
    }
    let mut cats: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();
    let mut lics: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();
    for p in &plugins {
        if let Some(c) = p.get("category").and_then(Value::as_str) {
            if !c.is_empty() {
                cats.insert(c.to_string());
            }
        }
        if let Some(l) = p.get("license").and_then(Value::as_str) {
            if !l.is_empty() {
                lics.insert(l.to_string());
            }
        }
    }
    let listed: Vec<String> = filtered
        .iter()
        .map(|p| serde_json::to_string(&value_to_ordered(p)).unwrap_or_else(|_| "{}".into()))
        .collect();
    json_text(
        StatusCode::OK,
        &format!(
            "{{\"plugins\":[{}],\"total\":{},\"categories\":{},\"licenses\":{}}}",
            listed.join(","),
            listed.len(),
            serde_json::to_string(&cats.into_iter().collect::<Vec<_>>())
                .unwrap_or_else(|_| "[]".into()),
            serde_json::to_string(&lics.into_iter().collect::<Vec<_>>())
                .unwrap_or_else(|_| "[]".into()),
        ),
    )
}

async fn plugins_directory_refresh(State(state): State<McpState>, headers: HeaderMap) -> Response {
    if let Err(resp) = require_user(&state.auth, &headers) {
        return resp;
    }
    let plugins = state
        .plugin_directory
        .lock()
        .map(|g| g.clone())
        .unwrap_or_default();
    let mut seen = Vec::new();
    let mut counts: BTreeMap<String, i64> = BTreeMap::new();
    for p in &plugins {
        let lic = p
            .get("license")
            .and_then(Value::as_str)
            .unwrap_or("unknown");
        if !counts.contains_key(lic) {
            seen.push(lic.to_string());
        }
        *counts.entry(lic.to_string()).or_insert(0) += 1;
    }
    let mut by = OrderedMap::new();
    for l in seen {
        by.insert(l.clone(), json!(counts.get(&l).copied().unwrap_or(0)));
    }
    json_text(
        StatusCode::OK,
        &format!(
            "{{\"status\":\"ok\",\"total\":{},\"by_license\":{}}}",
            plugins.len(),
            serde_json::to_string(&by).unwrap_or_else(|_| "{}".into())
        ),
    )
}

async fn mcp_call(
    State(state): State<McpState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    let identity = match require_user(&state.auth, &headers) {
        Ok(id) => id,
        Err(resp) => return resp,
    };
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    if !parsed
        .as_object()
        .map(|o| o.contains_key("action"))
        .unwrap_or(false)
    {
        return missing_fields(&parsed, &["action"]);
    }
    let action = parsed.get("action").and_then(Value::as_str).unwrap_or("");
    let args = parsed.get("args").cloned().unwrap_or(json!({}));
    if action == "knowledge_graph_ingest" {
        let claimed = args
            .get("user_email")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim();
        if !identity.email.is_empty()
            && !claimed.is_empty()
            && claimed.to_lowercase() != identity.email.trim().to_lowercase()
        {
            return localized(403, "common.user_mismatch", &headers);
        }
    }
    detail(
        StatusCode::BAD_REQUEST,
        &format!("Unknown action: {action}"),
    )
}
