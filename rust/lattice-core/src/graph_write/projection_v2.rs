//! Per-row `nodes_v2` / `edges_v2` / `edge_occurrences` projection helpers.
//!
//! Port of `lattice_brain/graph/projection/v2_schema.py`'s per-row write half.
//! Both the live [`super::GraphWriter`] write door (`primitives`) and the
//! schema backfill (`schema`) project through these functions so there is a
//! single definition of canonical v2 row semantics.

use rusqlite::{ToSql, Transaction};
use serde_json::Value;

use crate::db::CoreError;
use crate::pytext::safe_loads;

use super::pyaux::{py_dumps, py_str, sha256_text, truthy};
use super::taxonomy::{edge_type_from_legacy, node_type_from_legacy};

/// The arguments `_v2_project_node` takes.
pub(crate) struct NodeProjection<'a> {
    pub node_id: &'a str,
    pub node_type: &'a str,
    pub title: &'a str,
    pub summary: Option<&'a str>,
    pub metadata_json: Option<&'a str>,
    pub created_at: Option<&'a str>,
    /// Python defaults this to `_now()`; both call sites always pass a stamp
    /// (the write door passes the clock, the backfill passes the legacy row's),
    /// so the fallback is unreachable and is not reproduced.
    pub updated_at: &'a str,
    pub owner: Option<&'a str>,
    pub workspace_id: Option<&'a str>,
    pub visibility: Option<&'a str>,
}

/// The arguments `_v2_project_edge` takes.
pub(crate) struct EdgeProjection<'a> {
    pub from_node: &'a str,
    pub to_node: &'a str,
    pub edge_type: &'a str,
    pub weight: f64,
    pub metadata_json: Option<&'a str>,
    pub edge_id: Option<&'a str>,
    pub created_at: Option<&'a str>,
    /// `None` is Python's "argument not passed"; `Some("")` is an explicit
    /// empty legacy label, and the two behave differently.
    pub legacy_type: Option<&'a str>,
}

/// `_v2_project_node` with `strict=True`.
///
/// Called by [`super::schema`]'s backfill with `updated_at` from the legacy
/// row; in that case there is no clock reading at all, which is why this is a
/// free function rather than a `GraphWriter` method.
pub(crate) fn project_node_v2(
    txn: &Transaction<'_>,
    spec: &NodeProjection<'_>,
) -> Result<(), CoreError> {
    let ts = spec.updated_at;
    let norm_type = node_type_from_legacy(spec.node_type);
    let meta = spec
        .metadata_json
        .map(|raw| safe_loads(Some(raw)))
        .unwrap_or_default();
    let owner = first_truthy(&[
        spec.owner
            .filter(|value| !value.is_empty())
            .map(str::to_string),
        truthy_str(meta.get("user_email")),
        truthy_str(meta.get("owner")),
    ]);
    let workspace_id = first_truthy(&[
        spec.workspace_id
            .filter(|value| !value.is_empty())
            .map(str::to_string),
        truthy_str(meta.get("workspace_id")),
    ]);
    let visibility = match spec.visibility.filter(|value| !value.is_empty()) {
        Some(value) => value.to_string(),
        None => if workspace_id.is_none() {
            "legacy"
        } else {
            "workspace"
        }
        .to_string(),
    };
    txn.execute(
        "INSERT INTO nodes_v2(id, type, legacy_type, label, summary, attrs, 
                              owner_id, workspace_id, visibility, 
                              created_at, updated_at, importance_score) 
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0) 
         ON CONFLICT(id) DO UPDATE SET 
           type=excluded.type, legacy_type=excluded.legacy_type, 
           label=excluded.label, summary=excluded.summary, 
           attrs=excluded.attrs, updated_at=excluded.updated_at, 
           owner_id=COALESCE(excluded.owner_id, nodes_v2.owner_id), 
           workspace_id=COALESCE(excluded.workspace_id, nodes_v2.workspace_id), 
           visibility=CASE WHEN excluded.visibility != 'legacy' 
                           THEN excluded.visibility 
                           ELSE nodes_v2.visibility END",
        rusqlite::params![
            spec.node_id,
            norm_type,
            spec.node_type,
            spec.title,
            spec.summary,
            spec.metadata_json.unwrap_or("{}"),
            owner,
            workspace_id,
            visibility,
            spec.created_at.unwrap_or(ts),
            ts,
        ],
    )?;
    Ok(())
}

/// `_v2_project_edge` with `strict=True`, including the `edge_occurrences` row.
pub(crate) fn project_edge_v2(
    txn: &Transaction<'_>,
    spec: &EdgeProjection<'_>,
) -> Result<(), CoreError> {
    let explicit_legacy_type = spec.legacy_type.is_some();
    let mut leg_type = spec
        .legacy_type
        .map(str::to_string)
        .unwrap_or_else(|| spec.edge_type.to_string());
    if !leg_type.is_empty() && edge_type_from_legacy(&leg_type) == leg_type {
        leg_type = String::new();
    }
    let norm_type = edge_type_from_legacy(spec.edge_type);
    let eid = if explicit_legacy_type && !leg_type.is_empty() {
        format!(
            "edge:{}",
            &sha256_text(&format!(
                "{}|{}|{}|{}",
                spec.from_node, norm_type, spec.to_node, leg_type
            ))[..24]
        )
    } else {
        match spec.edge_id {
            Some(id) => id.to_string(),
            None => format!(
                "edge:{}",
                &sha256_text(&format!("{}|{norm_type}|{}", spec.from_node, spec.to_node))[..24]
            ),
        }
    };
    let meta_str = spec.metadata_json.unwrap_or("{}");
    let meta = safe_loads(Some(meta_str));
    let confidence = meta
        .get("confidence")
        .and_then(Value::as_f64)
        .unwrap_or(1.0);
    let created_at = spec.created_at.unwrap_or("");
    txn.execute(
        "INSERT INTO edges_v2(id, source, target, type, legacy_type, weight, 
                              confidence, evidence, metadata, created_by, created_at) 
         VALUES (?, ?, ?, ?, ?, ?, ?, '[]', ?, 'legacy', ?) 
         ON CONFLICT(source, target, type, legacy_type) DO UPDATE SET 
           weight=max(edges_v2.weight, excluded.weight), 
           confidence=excluded.confidence, 
           metadata=excluded.metadata",
        rusqlite::params![
            eid,
            spec.from_node,
            spec.to_node,
            norm_type,
            leg_type,
            spec.weight,
            confidence,
            meta_str,
            created_at,
        ],
    )?;
    let row_id: Option<String> = txn
        .query_row(
            "SELECT id FROM edges_v2 WHERE source=? AND target=? AND type=? AND legacy_type=?",
            rusqlite::params![spec.from_node, spec.to_node, norm_type, leg_type],
            |row| row.get(0),
        )
        .ok();
    if let Some(row_id) = row_id {
        let source = meta.get("source").cloned().unwrap_or(Value::Null);
        txn.execute(
            "INSERT INTO edge_occurrences(edge_id, observed_at, weight, source) VALUES (?, ?, ?, ?)",
            rusqlite::params![row_id, created_at, spec.weight, JsonScalar(&source)],
        )?;
    }
    Ok(())
}

/// `_v2_delete_nodes` — mirror a legacy deletion (edges cascade on the FK).
pub(crate) fn delete_nodes_v2(txn: &Transaction<'_>, ids: &[String]) -> Result<(), CoreError> {
    if ids.is_empty() {
        return Ok(());
    }
    let placeholders = vec!["?"; ids.len()].join(",");
    let params: Vec<&dyn ToSql> = ids.iter().map(|id| id as &dyn ToSql).collect();
    txn.execute(
        &format!("DELETE FROM nodes_v2 WHERE id IN ({placeholders})"),
        params.as_slice(),
    )?;
    Ok(())
}

/// A `serde_json` scalar bound the way Python's sqlite3 binds the same value.
pub(crate) struct JsonScalar<'a>(pub &'a Value);

impl ToSql for JsonScalar<'_> {
    fn to_sql(&self) -> rusqlite::Result<rusqlite::types::ToSqlOutput<'_>> {
        use rusqlite::types::{ToSqlOutput, ValueRef};
        Ok(match self.0 {
            Value::Null => ToSqlOutput::Borrowed(ValueRef::Null),
            Value::Bool(flag) => ToSqlOutput::from(i64::from(*flag)),
            Value::Number(number) => match number.as_i64() {
                Some(value) => ToSqlOutput::from(value),
                None => ToSqlOutput::from(number.as_f64().unwrap_or(0.0)),
            },
            Value::String(text) => ToSqlOutput::Borrowed(ValueRef::Text(text.as_bytes())),
            other => ToSqlOutput::from(py_dumps(other)),
        })
    }
}

fn truthy_str(value: Option<&Value>) -> Option<String> {
    value.filter(|value| truthy(value)).map(py_str)
}

fn first_truthy(candidates: &[Option<String>]) -> Option<String> {
    candidates.iter().flatten().next().cloned()
}
