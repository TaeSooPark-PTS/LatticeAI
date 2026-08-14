//! Permissions, registry, diagnostics, and document-create tools.

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

use super::gov::{
    default_gov, gov_named, permission_for, DESCRIPTIONS, HANDLERS, TOOL_CATALOG_BRIEF,
};
use super::{tool_err, tool_ok, ToolsState};

pub(crate) async fn permissions(State(state): State<ToolsState>, headers: HeaderMap) -> Response {
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

pub(crate) async fn registry(State(state): State<ToolsState>, headers: HeaderMap) -> Response {
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

pub(crate) async fn diagnostics(State(state): State<ToolsState>, headers: HeaderMap) -> Response {
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

pub(crate) async fn create_docx(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    create_document(state, headers, body, "docx").await
}
pub(crate) async fn create_xlsx(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    create_document(state, headers, body, "xlsx").await
}
pub(crate) async fn create_pptx(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    create_document(state, headers, body, "pptx").await
}
pub(crate) async fn create_pdf(
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
