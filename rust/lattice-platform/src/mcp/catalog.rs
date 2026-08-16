//! Skills marketplace, plugin directory, and `mcp_call`.

use std::collections::BTreeMap;

use axum::extract::{Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use lattice_auth::OrderedMap;
use serde_json::{json, Value};

use super::dispatch::{dispatch, DispatchError};
use super::http::{
    detail, json_text, localized, missing_fields, parse_json_object, require_admin, require_user,
    value_to_ordered,
};
use super::McpState;
use crate::tools::tool_ok;

#[derive(Debug, serde::Deserialize, Default)]
pub(crate) struct SkillsQuery {
    category: Option<String>,
    author: Option<String>,
}

pub(crate) async fn skills_marketplace(
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

pub(crate) async fn skills_install(
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

pub(crate) async fn skills_list(State(state): State<McpState>, headers: HeaderMap) -> Response {
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

pub(crate) async fn skills_marketplace_refresh(
    State(state): State<McpState>,
    headers: HeaderMap,
) -> Response {
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
pub(crate) struct DirQuery {
    category: Option<String>,
    license: Option<String>,
    q: Option<String>,
}

pub(crate) async fn plugins_directory(
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

pub(crate) async fn plugins_directory_refresh(
    State(state): State<McpState>,
    headers: HeaderMap,
) -> Response {
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

pub(crate) async fn mcp_call(
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
    match dispatch(
        state.tools.as_ref(),
        &state.skills_dir,
        &identity,
        &headers,
        action,
        &args,
    ) {
        Ok(result) => {
            if let Some(tools) = state.tools.as_ref() {
                tool_ok(&tools.workspace, result)
            } else {
                let mut body = lattice_auth::OrderedMap::new();
                body.insert("status", json!("ok"));
                body.insert("workspace", json!("."));
                body.insert("result", result);
                crate::mcp::json_status(StatusCode::OK, &body)
            }
        }
        Err(DispatchError::Unknown(name)) => {
            detail(StatusCode::BAD_REQUEST, &format!("Unknown action: {name}"))
        }
        Err(DispatchError::Governance(message)) => detail(StatusCode::FORBIDDEN, &message),
        Err(DispatchError::Missing(field)) => missing_fields(&parsed, &[field]),
        Err(DispatchError::Message(message)) => detail(StatusCode::BAD_REQUEST, &message),
        Err(DispatchError::Unavailable(message)) => {
            detail(StatusCode::SERVICE_UNAVAILABLE, message)
        }
    }
}
