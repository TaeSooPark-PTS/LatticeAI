use std::collections::{BTreeSet, HashSet};

use rusqlite::Connection;

use crate::db::CoreError;

pub const VIEWER_SCOPE_PREFIX: &str = "\u{1f}viewer:";
pub const CONVERSATION_NODE_TYPES: [&str; 3] = ["Chat", "Message", "AIResponse"];
pub const CONVERSATION_SOURCE_PREFIXES: [&str; 2] = ["message:", "airesponse:"];
const ID_BATCH: usize = 500;

pub fn normalize_viewer(viewer: &str) -> Option<String> {
    let normalized = viewer.trim().to_lowercase();
    (!normalized.is_empty()).then_some(normalized)
}

pub fn is_viewer_entry(entry: &str) -> bool {
    entry.starts_with(VIEWER_SCOPE_PREFIX)
}

pub fn with_viewer(allowed: BTreeSet<String>, viewer: Option<&str>) -> BTreeSet<String> {
    let mut scoped: BTreeSet<String> = allowed
        .into_iter()
        .filter(|entry| !is_viewer_entry(entry))
        .collect();
    if let Some(viewer) = viewer.and_then(normalize_viewer) {
        scoped.insert(format!("{VIEWER_SCOPE_PREFIX}{viewer}"));
    }
    scoped
}

pub fn workspace_scope(
    workspace_id: Option<&str>,
    viewer: Option<&str>,
) -> Option<BTreeSet<String>> {
    let workspace = workspace_id?;
    Some(with_viewer(BTreeSet::from([workspace.to_string()]), viewer))
}

pub fn scope_viewer(allowed: Option<&BTreeSet<String>>) -> Option<&str> {
    let mut viewers = allowed?
        .iter()
        .filter_map(|entry| entry.strip_prefix(VIEWER_SCOPE_PREFIX));
    let first = viewers.next()?;
    if viewers.next().is_some() {
        return Some("");
    }
    Some(first)
}

pub fn workspace_entries(allowed: &BTreeSet<String>) -> impl Iterator<Item = &String> {
    allowed
        .iter()
        .filter(|entry| !entry.is_empty() && !is_viewer_entry(entry))
}

#[derive(Debug, Clone, Copy)]
pub struct OwnerColumns<'a> {
    pub owner: &'a str,
    pub node_type: &'a str,
    pub metadata: &'a str,
}

pub const NODES_V2_OWNER_COLUMNS: OwnerColumns<'static> = OwnerColumns {
    owner: "owner_id",
    node_type: "COALESCE(NULLIF(legacy_type, ''), type)",
    metadata: "attrs",
};

fn quoted_list(values: &[&str]) -> String {
    values
        .iter()
        .map(|value| format!("'{value}'"))
        .collect::<Vec<_>>()
        .join(", ")
}

fn effective_owner_sql(columns: &OwnerColumns<'_>) -> String {
    let OwnerColumns {
        owner,
        node_type,
        metadata,
    } = *columns;
    format!(
        "NULLIF(LOWER(TRIM(CASE \
         WHEN LOWER(TRIM(COALESCE({owner}, ''))) LIKE 'user:%' THEN \
           CASE WHEN json_valid({metadata}) THEN \
             COALESCE(NULLIF(json_extract({metadata}, '$.user_email'), ''), \
                      CASE WHEN {node_type} = 'Person' THEN NULLIF(json_extract({metadata}, '$.email'), '') END) \
           END \
         ELSE {owner} \
         END)), '')"
    )
}

pub fn conversation_derived_sql(columns: &OwnerColumns<'_>) -> String {
    let OwnerColumns {
        node_type,
        metadata,
        ..
    } = *columns;
    let effective = effective_owner_sql(columns);
    let types = quoted_list(&CONVERSATION_NODE_TYPES);
    let sources = CONVERSATION_SOURCE_PREFIXES
        .iter()
        .map(|prefix| format!("json_extract({metadata}, '$.source_node') LIKE '{prefix}%'"))
        .collect::<Vec<_>>()
        .join(" OR ");
    format!(
        "({node_type} IN ({types}) OR (json_valid({metadata}) AND ({sources} \
         OR ({node_type} = 'Person' AND LOWER(TRIM(json_extract({metadata}, '$.email'))) = {effective}))))"
    )
}

pub fn foreign_conversation_sql(columns: &OwnerColumns<'_>, viewer_param: &str) -> String {
    let effective = effective_owner_sql(columns);
    let derived = conversation_derived_sql(columns);
    format!(
        "COALESCE(({effective} IS NOT NULL \
         AND {effective} <> {viewer_param} AND {derived}), 0)"
    )
}

pub fn owner_visible_sql(columns: &OwnerColumns<'_>, viewer_param: &str) -> String {
    format!("NOT {}", foreign_conversation_sql(columns, viewer_param))
}

pub fn foreign_conversation_ids(
    conn: &Connection,
    ids: &[String],
    viewer: &str,
) -> Result<HashSet<String>, CoreError> {
    let wanted: Vec<&String> = ids.iter().filter(|id| !id.is_empty()).collect();
    let mut hidden = HashSet::new();
    let predicate = foreign_conversation_sql(&NODES_V2_OWNER_COLUMNS, "?");
    for batch in wanted.chunks(ID_BATCH) {
        let placeholders = vec!["?"; batch.len()].join(",");
        let sql = format!("SELECT id FROM nodes_v2 WHERE id IN ({placeholders}) AND {predicate}");
        let mut stmt = conn.prepare(&sql)?;
        let params = rusqlite::params_from_iter(
            batch
                .iter()
                .map(|id| id.as_str())
                .chain(std::iter::once(viewer)),
        );
        let mut rows = stmt.query(params)?;
        while let Some(row) = rows.next()? {
            hidden.insert(row.get::<_, String>(0)?);
        }
    }
    Ok(hidden)
}

pub fn retain_visible_to_viewer<T, F>(
    conn: &Connection,
    items: Vec<T>,
    allowed: Option<&BTreeSet<String>>,
    id_of: F,
) -> Result<Vec<T>, CoreError>
where
    F: Fn(&T) -> String,
{
    let Some(viewer) = scope_viewer(allowed) else {
        return Ok(items);
    };
    if items.is_empty() {
        return Ok(items);
    }
    let ids: Vec<String> = items.iter().map(&id_of).collect();
    let hidden = foreign_conversation_ids(conn, &ids, viewer)?;
    if hidden.is_empty() {
        return Ok(items);
    }
    Ok(items
        .into_iter()
        .zip(ids)
        .filter(|(_, id)| !hidden.contains(id))
        .map(|(item, _)| item)
        .collect())
}

#[cfg(test)]
#[path = "viewer_scope_tests.rs"]
mod tests;
