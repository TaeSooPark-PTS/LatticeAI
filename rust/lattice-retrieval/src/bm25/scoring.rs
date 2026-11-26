use lattice_core::{clean_text, CoreError, NodeRow};
use rusqlite::{params_from_iter, types::Value as SqlValue, Connection};

use crate::service::Scope;

use super::sidecar::Bm25SidecarIndex;
use super::sidecar_cache::with_bm25_sidecar_snapshot;

#[derive(Debug, Clone)]
pub struct Bm25fParams {
    pub k1: f64,
    pub b: f64,
    pub title_weight: f64,
    pub b_title: f64,
    pub tags_headings_weight: f64,
    pub b_tags_headings: f64,
    pub body_weight: f64,
    pub b_body: f64,
    pub metadata_weight: f64,
    pub b_metadata: f64,
}

impl Default for Bm25fParams {
    fn default() -> Self {
        Self {
            k1: 1.2,
            b: 0.75,
            title_weight: 3.0,
            b_title: 0.40,
            tags_headings_weight: 2.5,
            b_tags_headings: 0.45,
            body_weight: 1.0,
            b_body: 0.75,
            metadata_weight: 0.6,
            b_metadata: 0.50,
        }
    }
}

pub(crate) fn node_type_multiplier(node_type: &str) -> f64 {
    match node_type {
        "Decision" => 1.12,
        "Document" | "Concept" | "Note" => 1.08,
        "Topic" | "Entity" | "Artifact" => 1.05,
        _ => 1.0,
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum KnowledgeLifecycleStatus {
    #[default]
    Active,
    Superseded,
    Deprecated,
}

impl KnowledgeLifecycleStatus {
    pub fn from_raw(status: Option<&str>, superseded_by: Option<&str>) -> Self {
        if superseded_by.is_some_and(|s| !s.trim().is_empty()) {
            return Self::Superseded;
        }
        match status.map(|s| s.trim().to_ascii_lowercase()).as_deref() {
            Some("superseded" | "replaced") => Self::Superseded,
            Some("deprecated" | "archived" | "retired") => Self::Deprecated,
            _ => Self::Active,
        }
    }

    pub fn is_retired(self) -> bool {
        matches!(self, Self::Superseded | Self::Deprecated)
    }

    pub fn multiplier(self) -> f64 {
        match self {
            Self::Active => 1.0,
            Self::Superseded => 0.35,
            Self::Deprecated => 0.25,
        }
    }
}

pub(crate) fn accessibility_multiplier(visibility: Option<&str>) -> f64 {
    match visibility.map(|s| s.trim().to_ascii_lowercase()).as_deref() {
        Some("public" | "shared" | "team" | "org" | "workspace") => 1.05,
        Some("private" | "restricted") => 0.95,
        _ => 1.0,
    }
}

pub(crate) fn importance_multiplier(importance_score: Option<f64>) -> f64 {
    let clamped = importance_score.unwrap_or(0.5).clamp(0.0, 1.0);
    0.85 + 0.30 * clamped
}

#[derive(Debug, Clone)]
pub struct CorpusLoadOptions {
    pub max_rows: i64,
    pub include_historical: bool,
}

impl Default for CorpusLoadOptions {
    fn default() -> Self {
        Self {
            max_rows: 5_000,
            include_historical: false,
        }
    }
}

#[derive(Debug, Clone)]
pub struct ScopedNodeRecord {
    pub node: NodeRow,
    pub lifecycle_status: KnowledgeLifecycleStatus,
    pub importance_score: Option<f64>,
    pub visibility: Option<String>,
}

#[derive(Debug, Clone)]
pub struct Bm25fScoredNode {
    pub node: NodeRow,
    pub bm25_score: f64,
    pub matched_terms: Vec<String>,
    pub matched_fields: Vec<String>,
    pub wikilinks: Vec<String>,
    pub tags: Vec<String>,
}

pub fn bm25f_score_nodes(
    rows: &[NodeRow],
    query: &str,
    params: &Bm25fParams,
) -> Vec<Bm25fScoredNode> {
    if rows.is_empty() || query.trim().is_empty() {
        return Vec::new();
    }
    Bm25SidecarIndex::from_corpus(rows).score(query, params)
}

pub(crate) fn row_to_node(row: &rusqlite::Row<'_>) -> rusqlite::Result<NodeRow> {
    Ok(NodeRow {
        id: row.get("id")?,
        node_type: row.get("type")?,
        title: row.get("title")?,
        summary: row.get("summary")?,
        metadata_json: row.get("metadata_json")?,
        updated_at: row.get("updated_at")?,
    })
}

#[derive(Debug, Clone, Copy, Default)]
pub(crate) struct NodesV2SchemaInfo {
    pub exists: bool,
    pub has_workspace_id: bool,
    pub has_owner_id: bool,
    pub has_visibility: bool,
    pub has_superseded_by: bool,
    pub has_importance_score: bool,
    pub has_lifecycle_status: bool,
}

pub(crate) fn detect_nodes_v2_schema(conn: &Connection) -> Result<NodesV2SchemaInfo, CoreError> {
    let mut info = NodesV2SchemaInfo::default();
    let mut stmt = conn.prepare("PRAGMA table_info(nodes_v2)")?;
    let mut rows = stmt.query([])?;
    while let Some(row) = rows.next()? {
        info.exists = true;
        let col: String = row.get(1)?;
        match col.as_str() {
            "workspace_id" => info.has_workspace_id = true,
            "owner_id" => info.has_owner_id = true,
            "visibility" => info.has_visibility = true,
            "superseded_by" => info.has_superseded_by = true,
            "importance_score" => info.has_importance_score = true,
            "lifecycle_status" => info.has_lifecycle_status = true,
            _ => {}
        }
    }
    Ok(info)
}

pub(crate) fn extract_meta_from_json(
    metadata_json: Option<&str>,
) -> (KnowledgeLifecycleStatus, Option<f64>, Option<String>) {
    let Some(raw) = metadata_json else {
        return (KnowledgeLifecycleStatus::Active, None, None);
    };
    let Ok(val) = serde_json::from_str::<serde_json::Value>(raw) else {
        return (KnowledgeLifecycleStatus::Active, None, None);
    };
    let status = val
        .get("lifecycle_status")
        .or_else(|| val.get("status"))
        .and_then(|v| v.as_str());
    let superseded_by = val.get("superseded_by").and_then(|v| v.as_str());
    let lifecycle = KnowledgeLifecycleStatus::from_raw(status, superseded_by);
    let importance = val.get("importance_score").and_then(|v| v.as_f64());
    let visibility = val
        .get("visibility")
        .and_then(|v| v.as_str())
        .map(str::to_string);
    (lifecycle, importance, visibility)
}

pub(crate) struct NodeScopeSqlParts {
    pub join_clause: &'static str,
    pub where_clause: String,
    pub params: Vec<SqlValue>,
    pub priority_order_clause: String,
    pub lifecycle_select: &'static str,
    pub superseded_by_select: &'static str,
    pub importance_select: &'static str,
    pub visibility_select: &'static str,
}

pub(crate) fn build_workspace_sql_predicate(
    ws_expr: &str,
    presence_guard: Option<&str>,
    scope: &Scope,
) -> Option<(String, Vec<SqlValue>)> {
    let allowed = scope.allowed_workspaces.as_ref()?;
    let names: Vec<String> = lattice_core::viewer_scope::workspace_entries(allowed)
        .cloned()
        .collect();
    let legacy_or_personal = |inner: String| -> String {
        match presence_guard {
            Some(guard) => format!("(({guard}) AND {inner})"),
            None => inner,
        }
    };
    if names.is_empty() {
        let clause = if scope.include_legacy_global {
            legacy_or_personal(format!("({ws_expr} IS NULL OR {ws_expr} = '')"))
        } else {
            "0 = 1".to_string()
        };
        return Some((clause, Vec::new()));
    }
    let placeholders = std::iter::repeat_n("?", names.len())
        .collect::<Vec<_>>()
        .join(", ");
    let allows_personal = names
        .iter()
        .any(|name| name == lattice_core::DEFAULT_WORKSPACE_ID);
    let params: Vec<SqlValue> = names.into_iter().map(SqlValue::Text).collect();
    let clause = if scope.include_legacy_global || allows_personal {
        legacy_or_personal(format!(
            "({ws_expr} IN ({placeholders}) OR {ws_expr} IS NULL OR {ws_expr} = '')"
        ))
    } else {
        format!("({ws_expr} IN ({placeholders}))")
    };
    Some((clause, params))
}

pub(crate) fn build_node_scope_sql(
    schema: NodesV2SchemaInfo,
    node_alias: &str,
    scope: &Scope,
    include_historical: bool,
) -> NodeScopeSqlParts {
    let join_clause = if schema.exists {
        "LEFT JOIN nodes_v2 v2 ON v2.id = n.id"
    } else {
        ""
    };

    let json_ws = format!(
        "CASE WHEN json_valid({node_alias}.metadata_json) THEN COALESCE(NULLIF(json_extract({node_alias}.metadata_json, '$.workspace_id'), ''), NULLIF(json_extract({node_alias}.metadata_json, '$.workspace'), '')) END"
    );
    let ws_expr = if schema.exists && schema.has_workspace_id {
        format!("COALESCE(NULLIF(v2.workspace_id, ''), {json_ws})")
    } else {
        json_ws.clone()
    };

    let json_superseded = format!(
        "CASE WHEN json_valid({node_alias}.metadata_json) THEN NULLIF(json_extract({node_alias}.metadata_json, '$.superseded_by'), '') END"
    );
    let superseded_expr = if schema.exists && schema.has_superseded_by {
        format!("COALESCE(NULLIF(v2.superseded_by, ''), {json_superseded})")
    } else {
        json_superseded
    };

    let json_status = format!(
        "CASE WHEN json_valid({node_alias}.metadata_json) THEN COALESCE(NULLIF(json_extract({node_alias}.metadata_json, '$.lifecycle_status'), ''), NULLIF(json_extract({node_alias}.metadata_json, '$.status'), '')) END"
    );
    let status_expr = if schema.exists && schema.has_lifecycle_status {
        format!("LOWER(COALESCE(NULLIF(v2.lifecycle_status, ''), {json_status}, 'active'))")
    } else {
        format!("LOWER(COALESCE({json_status}, 'active'))")
    };

    let json_vis = format!(
        "CASE WHEN json_valid({node_alias}.metadata_json) THEN NULLIF(json_extract({node_alias}.metadata_json, '$.visibility'), '') END"
    );
    let vis_expr = if schema.exists && schema.has_visibility {
        format!("LOWER(COALESCE(NULLIF(v2.visibility, ''), {json_vis}, ''))")
    } else {
        format!("LOWER(COALESCE({json_vis}, ''))")
    };

    let json_imp = format!(
        "CASE WHEN json_valid({node_alias}.metadata_json) THEN CAST(json_extract({node_alias}.metadata_json, '$.importance_score') AS REAL) END"
    );
    let imp_expr = if schema.exists && schema.has_importance_score {
        format!("COALESCE(v2.importance_score, {json_imp}, 0.5)")
    } else {
        format!("COALESCE({json_imp}, 0.5)")
    };

    let mut clauses: Vec<String> = Vec::new();
    let mut params: Vec<SqlValue> = Vec::new();

    let presence_guard = if schema.exists && schema.has_workspace_id {
        Some(format!("(v2.id IS NOT NULL OR {json_ws} IS NOT NULL)"))
    } else {
        None
    };

    if let Some((ws_clause, ws_params)) =
        build_workspace_sql_predicate(&ws_expr, presence_guard.as_deref(), scope)
    {
        clauses.push(ws_clause);
        params.extend(ws_params);
    }

    if let Some(viewer) = lattice_core::scope_viewer(scope.allowed_workspaces.as_ref()) {
        if schema.exists && schema.has_owner_id {
            let node_type_col = format!("{node_alias}.type");
            let metadata_col = format!("{node_alias}.metadata_json");
            let cols = lattice_core::viewer_scope::OwnerColumns {
                owner: "v2.owner_id",
                node_type: &node_type_col,
                metadata: &metadata_col,
            };
            clauses.push(lattice_core::viewer_scope::owner_visible_sql(&cols, "?"));
            params.push(SqlValue::Text(viewer.to_string()));
        }
    }

    let retired_pred = format!(
        "({superseded_expr} IS NOT NULL OR {status_expr} IN ('superseded', 'replaced', 'deprecated', 'archived', 'retired'))"
    );
    if !include_historical {
        clauses.push(format!("NOT {retired_pred}"));
    }

    let where_clause = if clauses.is_empty() {
        String::new()
    } else {
        format!("WHERE {}", clauses.join(" AND "))
    };

    let type_priority = format!(
        "CASE {node_alias}.type WHEN 'Decision' THEN 1.12 WHEN 'Document' THEN 1.08 WHEN 'Concept' THEN 1.08 WHEN 'Note' THEN 1.08 WHEN 'Topic' THEN 1.05 WHEN 'Entity' THEN 1.05 WHEN 'Artifact' THEN 1.05 ELSE 1.0 END"
    );
    let priority_order_clause = format!(
        "CASE WHEN {retired_pred} THEN 1 ELSE 0 END ASC, \
         CASE WHEN {vis_expr} IN ('public', 'shared', 'team', 'org', 'workspace') THEN 0 WHEN {vis_expr} IN ('private', 'restricted') THEN 2 ELSE 1 END ASC, \
         ({imp_expr} * {type_priority}) DESC, \
         {node_alias}.updated_at DESC, \
         {node_alias}.id ASC"
    );

    let lifecycle_select = if schema.exists && schema.has_lifecycle_status {
        "COALESCE(NULLIF(v2.lifecycle_status, ''), CASE WHEN json_valid(n.metadata_json) THEN COALESCE(NULLIF(json_extract(n.metadata_json, '$.lifecycle_status'), ''), NULLIF(json_extract(n.metadata_json, '$.status'), '')) END) AS resolved_lifecycle"
    } else {
        "CASE WHEN json_valid(n.metadata_json) THEN COALESCE(NULLIF(json_extract(n.metadata_json, '$.lifecycle_status'), ''), NULLIF(json_extract(n.metadata_json, '$.status'), '')) END AS resolved_lifecycle"
    };
    let superseded_by_select = if schema.exists && schema.has_superseded_by {
        "COALESCE(NULLIF(v2.superseded_by, ''), CASE WHEN json_valid(n.metadata_json) THEN NULLIF(json_extract(n.metadata_json, '$.superseded_by'), '') END) AS resolved_superseded_by"
    } else {
        "CASE WHEN json_valid(n.metadata_json) THEN NULLIF(json_extract(n.metadata_json, '$.superseded_by'), '') END AS resolved_superseded_by"
    };
    let importance_select = if schema.exists && schema.has_importance_score {
        "COALESCE(v2.importance_score, CASE WHEN json_valid(n.metadata_json) THEN CAST(json_extract(n.metadata_json, '$.importance_score') AS REAL) END) AS resolved_importance"
    } else {
        "CASE WHEN json_valid(n.metadata_json) THEN CAST(json_extract(n.metadata_json, '$.importance_score') AS REAL) END AS resolved_importance"
    };
    let visibility_select = if schema.exists && schema.has_visibility {
        "COALESCE(NULLIF(v2.visibility, ''), CASE WHEN json_valid(n.metadata_json) THEN NULLIF(json_extract(n.metadata_json, '$.visibility'), '') END) AS resolved_visibility"
    } else {
        "CASE WHEN json_valid(n.metadata_json) THEN NULLIF(json_extract(n.metadata_json, '$.visibility'), '') END AS resolved_visibility"
    };

    NodeScopeSqlParts {
        join_clause,
        where_clause,
        params,
        priority_order_clause,
        lifecycle_select,
        superseded_by_select,
        importance_select,
        visibility_select,
    }
}

pub fn load_scoped_corpus_records(
    conn: &Connection,
    scope: &Scope,
    options: &CorpusLoadOptions,
) -> Result<Vec<ScopedNodeRecord>, CoreError> {
    let cap = options.max_rows.clamp(100, 20_000);
    let schema = detect_nodes_v2_schema(conn)?;
    let parts = build_node_scope_sql(schema, "n", scope, options.include_historical);
    let sql = format!(
        "SELECT n.id, n.type, n.title, n.summary, n.metadata_json, n.updated_at,
                {}, {}, {}, {}
         FROM nodes n
         {}
         {}
         ORDER BY {}
         LIMIT ?",
        parts.lifecycle_select,
        parts.superseded_by_select,
        parts.importance_select,
        parts.visibility_select,
        parts.join_clause,
        parts.where_clause,
        parts.priority_order_clause
    );
    let mut params = parts.params;
    params.push(SqlValue::Integer(cap));

    let mut stmt = conn.prepare(&sql)?;
    let iter = stmt.query_map(params_from_iter(params), |row| {
        let node = row_to_node(row)?;
        let lifecycle_raw: Option<String> = row.get("resolved_lifecycle")?;
        let superseded_raw: Option<String> = row.get("resolved_superseded_by")?;
        let importance_score: Option<f64> = row.get("resolved_importance")?;
        let visibility: Option<String> = row.get("resolved_visibility")?;
        let lifecycle_status =
            KnowledgeLifecycleStatus::from_raw(lifecycle_raw.as_deref(), superseded_raw.as_deref());
        Ok(ScopedNodeRecord {
            node,
            lifecycle_status,
            importance_score,
            visibility,
        })
    })?;
    let mut records = Vec::new();
    for item in iter {
        records.push(item?);
    }
    Ok(records)
}

pub fn load_scoped_corpus_with_options(
    conn: &Connection,
    scope: &Scope,
    options: &CorpusLoadOptions,
) -> Result<Vec<NodeRow>, CoreError> {
    Ok(load_scoped_corpus_records(conn, scope, options)?
        .into_iter()
        .map(|r| r.node)
        .collect())
}

pub fn load_scoped_corpus(
    conn: &Connection,
    scope: &Scope,
    max_rows: i64,
) -> Result<Vec<NodeRow>, CoreError> {
    load_scoped_corpus_with_options(
        conn,
        scope,
        &CorpusLoadOptions {
            max_rows,
            include_historical: false,
        },
    )
}

pub fn bm25f_search_with_options(
    conn: &Connection,
    query: &str,
    limit: i64,
    scope: &Scope,
    params: &Bm25fParams,
    include_historical: bool,
) -> Result<Vec<Bm25fScoredNode>, CoreError> {
    let clean = clean_text(query);
    if clean.is_empty() {
        return Ok(Vec::new());
    }
    let snapshot = with_bm25_sidecar_snapshot(conn, scope, 5_000, include_historical)?;
    let mut scored = snapshot.score_with_options(&clean, params, include_historical);
    let take = limit.max(1) as usize;
    scored.truncate(take);
    Ok(scored)
}

pub fn bm25f_search(
    conn: &Connection,
    query: &str,
    limit: i64,
    scope: &Scope,
    params: &Bm25fParams,
) -> Result<Vec<Bm25fScoredNode>, CoreError> {
    bm25f_search_with_options(conn, query, limit, scope, params, false)
}
