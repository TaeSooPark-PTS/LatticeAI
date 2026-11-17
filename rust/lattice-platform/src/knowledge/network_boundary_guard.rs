//! Workspace scoping, policy filtering, and secret redaction for `network_boundary`.
//!
//! Ensures `POST /api/network-boundary/preview` mirrors actual hybrid cloud
//! egress discipline (workspace visibility, hard-blocked node types & metadata
//! flags, user/workspace `HybridPolicy` overrides, and secret redaction) and
//! `POST /api/network-boundary/node-sensitivity` enforces workspace write
//! authorization before mutating `local_only`.

use std::collections::BTreeSet;

use axum::http::StatusCode;
use lattice_auth::OrderedMap;
use lattice_core::db::RuntimeConfig;
use lattice_core::read::{
    filter_scoped_nodes, read_tables, scoped_workspace_visible, workspaces_of, DEFAULT_WORKSPACE_ID,
};
use rusqlite::Connection;
use serde_json::Value;

use super::network_boundary::{HARD_BLOCK_METADATA_FLAGS, HARD_BLOCK_NODE_TYPES};
use crate::workspaceos::workspace::WorkspaceService;
use lattice_core::redact::redact_secret_text;

#[derive(Clone, Debug)]
pub(crate) struct PreviewNode {
    pub id: String,
    pub title: String,
    pub summary: String,
    pub node_type: String,
}

fn flag_truthy(value: &Value) -> bool {
    match value {
        Value::Bool(flag) => *flag,
        Value::Number(num) => num.as_f64().is_some_and(|n| n != 0.0),
        Value::String(text) => matches!(
            text.trim().to_ascii_lowercase().as_str(),
            "1" | "true" | "yes" | "on"
        ),
        _ => false,
    }
}

fn blocked_sets(policy: &OrderedMap) -> (BTreeSet<String>, BTreeSet<String>) {
    let mut types: BTreeSet<String> = HARD_BLOCK_NODE_TYPES
        .iter()
        .map(|entry| entry.to_ascii_lowercase())
        .collect();
    if let Some(arr) = policy.get("blocked_node_types").and_then(Value::as_array) {
        for item in arr.iter().filter_map(Value::as_str) {
            let trimmed = item.trim();
            if !trimmed.is_empty() {
                types.insert(trimmed.to_ascii_lowercase());
            }
        }
    }

    let mut flags: BTreeSet<String> = HARD_BLOCK_METADATA_FLAGS
        .iter()
        .map(|entry| entry.to_string())
        .collect();
    if let Some(arr) = policy
        .get("blocked_metadata_flags")
        .and_then(Value::as_array)
    {
        for item in arr.iter().filter_map(Value::as_str) {
            let trimmed = item.trim();
            if !trimmed.is_empty() {
                flags.insert(trimmed.to_string());
            }
        }
    }
    (types, flags)
}

fn node_is_blocked(
    node_type: &str,
    metadata_json: Option<&str>,
    blocked_types: &BTreeSet<String>,
    blocked_flags: &BTreeSet<String>,
) -> bool {
    if blocked_types.contains(&node_type.trim().to_ascii_lowercase()) {
        return true;
    }
    let raw = metadata_json.unwrap_or("{}").trim();
    if raw.is_empty() {
        return false;
    }
    let Ok(parsed) = serde_json::from_str::<Value>(raw) else {
        // Fail closed on corrupted metadata JSON at the egress boundary.
        return true;
    };
    let Some(meta) = parsed.as_object() else {
        return true;
    };
    blocked_flags
        .iter()
        .any(|flag| meta.get(flag).is_some_and(flag_truthy))
}

fn has_nodes_v2(conn: &Connection) -> bool {
    conn.query_row(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='nodes_v2' LIMIT 1",
        [],
        |_| Ok(()),
    )
    .is_ok()
}

fn metadata_workspace(metadata_json: Option<&str>) -> Option<String> {
    let raw = metadata_json?.trim();
    let parsed: Value = serde_json::from_str(raw).ok()?;
    parsed
        .get("workspace_id")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
}

/// Resolve and authorize workspace read access for preview / boundary inspection.
pub(crate) fn resolve_read_scope(
    workspaces: Option<&WorkspaceService>,
    user_email: Option<&str>,
    requested_ws: Option<&str>,
) -> Result<(Option<String>, Option<BTreeSet<String>>), String> {
    if let Some(service) = workspaces {
        let resolved = service
            .resolve_read(requested_ws, user_email)
            .map_err(|err| err.to_string())?;
        let allowed = BTreeSet::from([resolved.clone()]);
        return Ok((Some(resolved), Some(allowed)));
    }
    if let Some(ws) = requested_ws.map(str::trim).filter(|s| !s.is_empty()) {
        let owned = ws.to_string();
        let allowed = BTreeSet::from([owned.clone()]);
        return Ok((Some(owned), Some(allowed)));
    }
    Ok((None, None))
}

/// Authorize workspace write access for boundary / policy settings.
pub(crate) fn authorize_scope_write(
    workspaces: Option<&WorkspaceService>,
    user_email: Option<&str>,
    requested_ws: Option<&str>,
) -> Result<Option<String>, String> {
    if let Some(service) = workspaces {
        if requested_ws.is_some() {
            let resolved = service
                .resolve_write(requested_ws, user_email)
                .map_err(|err| err.to_string())?;
            return Ok(Some(resolved));
        }
    }
    Ok(requested_ws
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string))
}

/// Authorize `set_node_sensitivity` against the target node's owning workspace.
pub(crate) fn authorize_node_sensitivity_write(
    config: &RuntimeConfig,
    workspaces: Option<&WorkspaceService>,
    user_email: Option<&str>,
    requested_ws: Option<&str>,
    node_id: &str,
) -> Result<(), (StatusCode, String)> {
    if let Some(service) = workspaces {
        if requested_ws.is_some() {
            service
                .resolve_write(requested_ws, user_email)
                .map_err(|err| (StatusCode::FORBIDDEN, err.to_string()))?;
        }
    }

    let path = config.graph_db_path();
    if !path.exists() {
        return Err((StatusCode::NOT_FOUND, "node not found".into()));
    }
    let conn = lattice_core::db::open_read_only(&path)
        .map_err(|err| (StatusCode::BAD_REQUEST, err.to_string()))?;
    let (nodes_table, _) = read_tables(&conn);
    let sql = format!("SELECT metadata_json FROM {nodes_table} WHERE id=? LIMIT 1");
    let meta_raw: Option<Option<String>> = match conn.query_row(&sql, [node_id], |row| row.get(0)) {
        Ok(m) => Some(m),
        Err(rusqlite::Error::QueryReturnedNoRows) => None,
        Err(_) => None,
    };
    let Some(meta_raw) = meta_raw else {
        return Err((StatusCode::NOT_FOUND, format!("node not found: {node_id}")));
    };

    let node_ws: Option<String> = if has_nodes_v2(&conn) {
        workspaces_of(&conn, &[node_id.to_string()])
            .ok()
            .and_then(|mut map| map.remove(node_id))
            .flatten()
            .or_else(|| metadata_workspace(meta_raw.as_deref()))
    } else {
        metadata_workspace(meta_raw.as_deref())
    };

    if let Some(req_ws) = requested_ws.map(str::trim).filter(|s| !s.is_empty()) {
        let allowed = BTreeSet::from([req_ws]);
        if !scoped_workspace_visible(node_ws.as_deref(), &allowed, false) {
            return Err((
                StatusCode::FORBIDDEN,
                "node belongs to another workspace".into(),
            ));
        }
    }

    if let Some(service) = workspaces {
        let effective_ws = node_ws
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .unwrap_or(DEFAULT_WORKSPACE_ID);
        service
            .resolve_write(Some(effective_ws), user_email)
            .map_err(|err| (StatusCode::FORBIDDEN, err.to_string()))?;
    }

    Ok(())
}

/// Read, scope, policy-filter, and secret-redact candidate preview nodes.
pub(crate) fn preview_nodes_filtered(
    config: &RuntimeConfig,
    keywords: &[String],
    top_k: usize,
    allowed_workspaces: Option<&BTreeSet<String>>,
    resolved_policy: &OrderedMap,
) -> Vec<PreviewNode> {
    let path = config.graph_db_path();
    if !path.exists() {
        return Vec::new();
    }
    let Ok(conn) = lattice_core::db::open_read_only(&path) else {
        return Vec::new();
    };
    let (nodes_table, _) = read_tables(&conn);
    let sql = format!(
        "SELECT id, type, title, summary, metadata_json 
         FROM {nodes_table} ORDER BY updated_at DESC LIMIT 500"
    );
    let Ok(mut stmt) = conn.prepare(&sql) else {
        return Vec::new();
    };
    let (blocked_types, blocked_flags) = blocked_sets(resolved_policy);
    let v2_present = has_nodes_v2(&conn);
    let allowed_refs: Option<BTreeSet<&str>> =
        allowed_workspaces.map(|set| set.iter().map(String::as_str).collect());
    let lowered_keywords: Vec<String> = keywords.iter().map(|k| k.to_lowercase()).collect();
    let mut scored = Vec::new();
    if let Ok(rows) = stmt.query_map([], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, Option<String>>(1)?.unwrap_or_default(),
            row.get::<_, Option<String>>(2)?.unwrap_or_default(),
            row.get::<_, Option<String>>(3)?.unwrap_or_default(),
            row.get::<_, Option<String>>(4)?,
        ))
    }) {
        for row in rows.flatten() {
            let (id, node_type, raw_title, raw_summary, metadata_json) = row;
            if !v2_present {
                if let Some(allowed) = allowed_refs.as_ref() {
                    let meta_ws = metadata_workspace(metadata_json.as_deref());
                    if !scoped_workspace_visible(meta_ws.as_deref(), allowed, false) {
                        continue;
                    }
                }
            }
            if node_is_blocked(
                &node_type,
                metadata_json.as_deref(),
                &blocked_types,
                &blocked_flags,
            ) {
                continue;
            }
            let hay = format!("{raw_title} {raw_summary}").to_lowercase();
            let score = if lowered_keywords.is_empty() {
                1
            } else {
                lowered_keywords
                    .iter()
                    .filter(|kw| hay.contains(kw.as_str()))
                    .count()
            };
            if score > 0 {
                let title = redact_secret_text(&raw_title);
                let redacted_summary = redact_secret_text(&raw_summary);
                let summary: String = redacted_summary.chars().take(400).collect();
                let clean_type = if node_type.trim().is_empty() {
                    "Concept".to_string()
                } else {
                    node_type
                };
                scored.push((
                    score,
                    PreviewNode {
                        id,
                        title,
                        summary,
                        node_type: clean_type,
                    },
                ));
            }
        }
    }
    if v2_present && allowed_workspaces.is_some() {
        scored = filter_scoped_nodes(&conn, scored, allowed_workspaces, false, |(_, n)| {
            n.id.clone()
        })
        .unwrap_or_default();
    }
    scored.sort_by_key(|entry| std::cmp::Reverse(entry.0));
    scored
        .into_iter()
        .take(top_k)
        .map(|(_, node)| node)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn setup_db() -> (tempfile::TempDir, RuntimeConfig) {
        let dir = tempfile::tempdir().unwrap();
        let data_str = dir.path().to_string_lossy().into_owned();
        let config = RuntimeConfig::resolve(Some(&data_str), None, None, None);
        let conn = rusqlite::Connection::open(config.graph_db_path()).unwrap();
        conn.execute_batch(
            r#"CREATE TABLE nodes(
                id TEXT PRIMARY KEY,
                type TEXT,
                title TEXT,
                summary TEXT,
                metadata_json TEXT,
                created_at TEXT,
                updated_at TEXT
             );
             CREATE TABLE nodes_v2(
                id TEXT PRIMARY KEY,
                workspace_id TEXT
             );
             INSERT INTO nodes VALUES
               ('pub-personal', 'Decision', 'Architecture note', 'token=sk-1234567890abcdef123456 safe summary', '{}', '2026-08-01T00:00:00', '2026-08-01T00:00:00'),
               ('legacy-null', 'Concept', 'Legacy shared note', 'visible in personal scope', '{}', '2026-08-01T00:00:00', '2026-08-01T00:00:00'),
               ('secret-type', 'ApiKey', 'Prod OpenAI key', 'should never preview', '{}', '2026-08-01T00:00:00', '2026-08-01T00:00:00'),
               ('local-flag', 'Note', 'Private diary', 'should never preview', '{"local_only": true}', '2026-08-01T00:00:00', '2026-08-01T00:00:00'),
               ('policy-blocked', 'Task', 'Blocked task note', 'policy blocks Task', '{}', '2026-08-01T00:00:00', '2026-08-01T00:00:00'),
               ('other-ws', 'Decision', 'Team B architecture note', 'belongs to team-b', '{}', '2026-08-01T00:00:00', '2026-08-01T00:00:00');
             INSERT INTO nodes_v2 VALUES
               ('pub-personal', 'personal'),
               ('legacy-null', NULL),
               ('secret-type', 'personal'),
               ('local-flag', 'personal'),
               ('policy-blocked', 'personal'),
               ('other-ws', 'team-b');"#,
        )
        .unwrap();
        (dir, config)
    }

    #[test]
    fn preview_filters_hard_blocks_policy_workspaces_and_redacts_secrets() {
        let (_dir, config) = setup_db();
        let mut policy = OrderedMap::new();
        policy.insert("blocked_node_types", json!(["Task"]));
        policy.insert("blocked_metadata_flags", json!(["local_only"]));

        let personal_allowed = BTreeSet::from(["personal".to_string()]);
        let personal_nodes = preview_nodes_filtered(
            &config,
            &["note".to_string()],
            10,
            Some(&personal_allowed),
            &policy,
        );
        let ids: Vec<&str> = personal_nodes.iter().map(|n| n.id.as_str()).collect();
        assert!(ids.contains(&"pub-personal"));
        assert!(
            ids.contains(&"legacy-null"),
            "L-4: personal scope sees legacy NULL workspace rows"
        );
        assert!(
            !ids.contains(&"secret-type"),
            "HARD_BLOCK_NODE_TYPES must be excluded"
        );
        assert!(
            !ids.contains(&"local-flag"),
            "local_only metadata must be excluded"
        );
        assert!(
            !ids.contains(&"policy-blocked"),
            "HybridPolicy blocked_node_types must be excluded"
        );
        assert!(
            !ids.contains(&"other-ws"),
            "cross-workspace nodes must be excluded"
        );

        let pub_node = personal_nodes
            .iter()
            .find(|n| n.id == "pub-personal")
            .unwrap();
        assert!(
            !pub_node.summary.contains("sk-1234567890abcdef123456"),
            "preview summary must redact secrets: {}",
            pub_node.summary
        );

        let team_b_allowed = BTreeSet::from(["team-b".to_string()]);
        let team_b_nodes = preview_nodes_filtered(
            &config,
            &["note".to_string()],
            10,
            Some(&team_b_allowed),
            &policy,
        );
        let b_ids: Vec<&str> = team_b_nodes.iter().map(|n| n.id.as_str()).collect();
        assert_eq!(
            b_ids,
            vec!["other-ws"],
            "named workspace must not see personal or NULL rows"
        );
    }

    #[test]
    fn node_sensitivity_write_rejects_cross_workspace_mutation() {
        let (_dir, config) = setup_db();
        let err = authorize_node_sensitivity_write(
            &config,
            None,
            Some("user@example.com"),
            Some("personal"),
            "other-ws",
        )
        .unwrap_err();
        assert_eq!(err.0, StatusCode::FORBIDDEN);

        authorize_node_sensitivity_write(
            &config,
            None,
            Some("user@example.com"),
            Some("team-b"),
            "other-ws",
        )
        .expect("matching workspace succeeds");
    }
}
