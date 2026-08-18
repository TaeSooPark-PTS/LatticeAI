//! MCP registry / custom-entry HTTP handlers.

use std::path::PathBuf;

use axum::extract::{Path as AxumPath, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use lattice_auth::OrderedMap;
use serde_json::{json, Value};

use super::http::{
    detail, json_status, json_text, localized, missing_fields, now_iso_full, parse_json_object,
    require_admin, require_user, value_to_ordered,
};
use super::{McpState, EXPLICIT_CONSENT, MCP_TOOL_DESCRIPTIONS};
use crate::toolsurface::tools;
use crate::workspaceos::workspace;

pub(crate) fn public_item(item: &Value, installed_state: &Value) -> OrderedMap {
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

pub(crate) fn public_env_vars(items: &Value) -> Vec<Value> {
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

pub(crate) async fn mcp_tools(State(state): State<McpState>, headers: HeaderMap) -> Response {
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
        let gov = tools::governance_for(name);
        let mut tool = OrderedMap::new();
        tool.insert("name", json!(name));
        tool.insert("description", json!(description));
        tool.insert(
            "permission",
            serde_json::to_value(tools::permission_for(name)).unwrap_or(json!({})),
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

pub(crate) async fn mcp_recommend(
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
    scored.sort_by_key(|entry| std::cmp::Reverse(entry.0));
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

pub(crate) async fn mcp_install(
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
    if let Some(enabled) = enable_local_skill_or_plugin(&state, mcp_id) {
        return enabled;
    }
    let registry = state.combined();
    let Some(item) = registry
        .iter()
        .find(|i| i.get("id").and_then(Value::as_str) == Some(mcp_id))
    else {
        return detail(StatusCode::NOT_FOUND, "MCP를 찾을 수 없습니다.");
    };
    let mode = item
        .get("install_mode")
        .and_then(Value::as_str)
        .unwrap_or("");
    if matches!(mode, "builtin" | "bundled") {
        let mut body = OrderedMap::new();
        body.insert("status", json!("already_available"));
        body.insert("mcp_id", json!(mcp_id));
        body.insert("install_mode", json!(mode));
        body.insert(
            "message",
            json!("This capability is bundled with Lattice and needs no install."),
        );
        return json_status(StatusCode::OK, &body);
    }
    manual_install_required(item, mcp_id, mode)
}

fn enable_local_skill_or_plugin(state: &McpState, mcp_id: &str) -> Option<Response> {
    let on_disk = workspace::skills::scan_installed_skills(&state.skills_dir)
        .into_iter()
        .any(|skill| skill.name == mcp_id);
    let in_market = state
        .skills_market
        .lock()
        .ok()
        .map(|market| {
            market.iter().any(|item| {
                item.get("skill").and_then(Value::as_str) == Some(mcp_id)
                    || item.get("name").and_then(Value::as_str) == Some(mcp_id)
            })
        })
        .unwrap_or(false);
    let in_plugins = state
        .plugin_directory
        .lock()
        .ok()
        .map(|plugins| {
            plugins
                .iter()
                .any(|item| item.get("name").and_then(Value::as_str) == Some(mcp_id))
        })
        .unwrap_or(false);
    if !on_disk && !in_market && !in_plugins {
        return None;
    }
    let store = workspace::WorkspaceOsStore::shared(&state.data_dir);
    let version = if on_disk { "local" } else { "marketplace" };
    let kind = if in_plugins && !on_disk && !in_market {
        "plugin"
    } else {
        "skill"
    };
    let metadata = json!({"source": kind, "mcp_id": mcp_id});
    let installed = workspace::skills::mark_installed(store.as_ref(), mcp_id, version, &metadata);
    let enabled = workspace::skills::set_enabled(store.as_ref(), mcp_id, true);
    match (installed, enabled) {
        (Ok(entry), Ok(_)) | (Ok(entry), Err(_)) => {
            let mut body = OrderedMap::new();
            body.insert("status", json!("ok"));
            body.insert("kind", json!(kind));
            body.insert("mcp_id", json!(mcp_id));
            body.insert("skill", entry);
            Some(json_status(StatusCode::OK, &body))
        }
        (Err(_), Ok(entry)) => {
            let mut body = OrderedMap::new();
            body.insert("status", json!("ok"));
            body.insert("kind", json!(kind));
            body.insert("mcp_id", json!(mcp_id));
            body.insert("skill", entry);
            Some(json_status(StatusCode::OK, &body))
        }
        (Err(error), Err(_)) => Some(detail(
            StatusCode::BAD_REQUEST,
            &format!("could not enable {kind} '{mcp_id}': {error:?}"),
        )),
    }
}

fn manual_install_required(item: &Value, mcp_id: &str, mode: &str) -> Response {
    let package = item
        .get("package")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .or_else(|| {
            item.get("pip_packages")
                .and_then(Value::as_array)
                .and_then(|a| a.iter().filter_map(Value::as_str).next())
        })
        .unwrap_or("");
    let external = item
        .get("external_url")
        .and_then(Value::as_str)
        .or_else(|| item.get("connector_url").and_then(Value::as_str))
        .unwrap_or("");
    let mut instructions = vec![
        "Lattice cannot download or run remote MCP servers automatically.".to_string(),
        format!("Install mode is '{mode}'."),
    ];
    if !package.is_empty() {
        let cmd = if mode == "pip" {
            format!("pip install {package}")
        } else {
            format!("Add this package to your MCP client config: {package}")
        };
        instructions.push(cmd);
    }
    if !external.is_empty() {
        instructions.push(format!("Complete any connector auth at {external}."));
    }
    instructions.push("Restart the MCP client after configuration.".into());
    let mut body = OrderedMap::new();
    body.insert("status", json!("manual_required"));
    body.insert("mcp_id", json!(mcp_id));
    body.insert("install_mode", json!(mode));
    body.insert("package", json!(package));
    body.insert(
        "message",
        json!("Remote MCP servers cannot be installed by Lattice. Configure them in the client."),
    );
    body.insert("instructions", json!(instructions));
    json_status(StatusCode::OK, &body)
}

pub(crate) async fn mcp_installed(State(state): State<McpState>, headers: HeaderMap) -> Response {
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

pub(crate) async fn mcp_connector(
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

pub(crate) async fn mcp_registry_refresh(
    State(state): State<McpState>,
    headers: HeaderMap,
) -> Response {
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

pub(crate) async fn mcp_claude_code_servers(
    State(state): State<McpState>,
    headers: HeaderMap,
) -> Response {
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

pub(crate) async fn mcp_custom_list(State(state): State<McpState>, headers: HeaderMap) -> Response {
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

pub(crate) async fn mcp_custom_add(
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

pub(crate) async fn mcp_custom_delete(
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
