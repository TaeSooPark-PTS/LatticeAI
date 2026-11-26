use std::collections::{BTreeSet, HashMap};

use rusqlite::{params_from_iter, Connection};
use serde::Serialize;
use serde_json::{json, Map, Value};

use crate::db::CoreError;
use crate::graph_write::lifecycle::read_lifecycle_from_metadata;
use crate::temporal::read_temporal_interval;

pub const STATUS_ACTIVE: &str = "active";
pub const STATUS_SUPERSEDED: &str = "superseded";
const ID_BATCH: usize = 400;

#[derive(Debug, Clone, Default, PartialEq, Serialize)]
pub struct NodeTemporalState {
    pub status: &'static str,
    pub lifecycle: String,
    pub valid_from: Option<String>,
    pub valid_until: Option<String>,
    pub superseded_by: Option<String>,
    pub superseded_via: Option<&'static str>,
}

impl NodeTemporalState {
    pub fn is_superseded(&self) -> bool {
        self.status == STATUS_SUPERSEDED
    }

    pub fn as_json(&self) -> Value {
        json!({
            "status": self.status,
            "lifecycle": self.lifecycle,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "superseded_by": self.superseded_by,
            "superseded_via": self.superseded_via,
        })
    }
}

fn table_exists(conn: &Connection, table: &str) -> bool {
    conn.query_row(
        "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name=?1)",
        [table],
        |row| row.get::<_, bool>(0),
    )
    .unwrap_or(false)
}

fn columns(conn: &Connection, table: &str) -> BTreeSet<String> {
    let Ok(mut stmt) = conn.prepare(&format!("PRAGMA table_info({table})")) else {
        return BTreeSet::new();
    };
    stmt.query_map([], |row| row.get::<_, String>(1))
        .map(|rows| rows.filter_map(Result::ok).collect())
        .unwrap_or_default()
}

fn clean(value: Option<String>) -> Option<String> {
    value
        .map(|raw| raw.trim().to_string())
        .filter(|raw| !raw.is_empty())
}

fn placeholders(count: usize) -> String {
    vec!["?"; count].join(",")
}

struct NodeRowState {
    metadata: Map<String, Value>,
    created_at: String,
    v2_superseded_by: Option<String>,
    v2_lifecycle: Option<String>,
    v2_valid_from: Option<String>,
    v2_valid_to: Option<String>,
}

fn v2_select(cols: &BTreeSet<String>, column: &str) -> String {
    if cols.contains(column) {
        format!("v.{column}")
    } else {
        "NULL".to_string()
    }
}

fn read_node_rows(
    conn: &Connection,
    ids: &[String],
) -> Result<HashMap<String, NodeRowState>, CoreError> {
    let has_v2 = table_exists(conn, "nodes_v2");
    let v2_cols = if has_v2 {
        columns(conn, "nodes_v2")
    } else {
        BTreeSet::new()
    };
    let (select, join) = if has_v2 {
        (
            format!(
                "{}, {}, {}, {}",
                v2_select(&v2_cols, "superseded_by"),
                v2_select(&v2_cols, "lifecycle_status"),
                v2_select(&v2_cols, "valid_from"),
                v2_select(&v2_cols, "valid_to"),
            ),
            "LEFT JOIN nodes_v2 v ON v.id = n.id",
        )
    } else {
        ("NULL, NULL, NULL, NULL".to_string(), "")
    };
    let created = if columns(conn, "nodes").contains("created_at") {
        "n.created_at"
    } else {
        "NULL"
    };
    let mut out = HashMap::new();
    for batch in ids.chunks(ID_BATCH) {
        let sql = format!(
            "SELECT n.id, n.metadata_json, {created}, {select} FROM nodes n {join} WHERE n.id IN ({})",
            placeholders(batch.len())
        );
        let mut stmt = conn.prepare(&sql)?;
        let rows = stmt.query_map(params_from_iter(batch.iter()), |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, Option<String>>(1)?,
                row.get::<_, Option<String>>(2)?,
                row.get::<_, Option<String>>(3)?,
                row.get::<_, Option<String>>(4)?,
                row.get::<_, Option<String>>(5)?,
                row.get::<_, Option<String>>(6)?,
            ))
        })?;
        for row in rows {
            let (id, raw_meta, created_at, sup, lifecycle, valid_from, valid_to) = row?;
            out.insert(
                id,
                NodeRowState {
                    metadata: crate::pytext::safe_loads(raw_meta.as_deref()),
                    created_at: created_at.unwrap_or_default(),
                    v2_superseded_by: clean(sup),
                    v2_lifecycle: clean(lifecycle),
                    v2_valid_from: clean(valid_from),
                    v2_valid_to: clean(valid_to),
                },
            );
        }
    }
    Ok(out)
}

fn read_supersedes_edges(
    conn: &Connection,
    ids: &[String],
) -> Result<HashMap<String, String>, CoreError> {
    let mut out: HashMap<String, String> = HashMap::new();
    let mut sources: Vec<(&str, &str, &str, &str)> = Vec::new();
    if table_exists(conn, "edges") {
        sources.push(("edges", "from_node", "to_node", ""));
    }
    if table_exists(conn, "edges_v2") {
        let filter = if columns(conn, "edges_v2").contains("valid_to") {
            " AND COALESCE(valid_to, '') = ''"
        } else {
            ""
        };
        sources.push(("edges_v2", "source", "target", filter));
    }
    for (table, from_col, to_col, filter) in sources {
        for batch in ids.chunks(ID_BATCH) {
            let sql = format!(
                "SELECT {to_col}, {from_col} FROM {table} WHERE type = 'SUPERSEDES' AND {to_col} IN ({}){filter} ORDER BY {from_col}",
                placeholders(batch.len())
            );
            let mut stmt = conn.prepare(&sql)?;
            let rows = stmt.query_map(params_from_iter(batch.iter()), |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
            })?;
            for row in rows {
                let (old_id, new_id) = row?;
                if old_id != new_id {
                    out.entry(old_id).or_insert(new_id);
                }
            }
        }
    }
    Ok(out)
}

fn resolve_state(
    row: &NodeRowState,
    edge_successor: Option<&String>,
    as_of_epoch: f64,
) -> NodeTemporalState {
    let meta_value = Value::Object(row.metadata.clone());
    let interval = read_temporal_interval(
        &meta_value,
        &row.created_at,
        row.v2_superseded_by.as_deref(),
    );
    let lifecycle = read_lifecycle_from_metadata(
        &row.metadata,
        interval.superseded_by.as_deref(),
        interval
            .valid_from
            .as_deref()
            .or(row.v2_valid_from.as_deref()),
        interval
            .valid_until
            .as_deref()
            .or(row.v2_valid_to.as_deref()),
    );
    let raw_status = row
        .v2_lifecycle
        .clone()
        .or_else(|| {
            ["lifecycle_status", "status"]
                .iter()
                .find_map(|key| row.metadata.get(*key).and_then(Value::as_str))
                .map(str::to_string)
        })
        .unwrap_or_default();
    let named = crate::graph_write::lifecycle::KnowledgeLifecycleState::parse(&raw_status)
        .filter(|state| state.is_superseded_or_deprecated())
        .or_else(|| {
            matches!(
                raw_status.trim().to_ascii_lowercase().as_str(),
                "replaced" | "archived" | "retired"
            )
            .then_some(crate::graph_write::lifecycle::KnowledgeLifecycleState::Deprecated)
        });
    let state = named.unwrap_or(lifecycle.state);
    let superseded_by = lifecycle.superseded_by.clone().or(edge_successor.cloned());
    let ended = interval.has_ended_at_epoch(as_of_epoch);
    let via = if lifecycle.superseded_by.is_some() {
        Some("superseded_by")
    } else if edge_successor.is_some() {
        Some("supersedes_edge")
    } else if state.is_superseded_or_deprecated() {
        Some("lifecycle")
    } else if ended {
        Some("valid_until")
    } else {
        None
    };
    NodeTemporalState {
        status: if via.is_some() {
            STATUS_SUPERSEDED
        } else {
            STATUS_ACTIVE
        },
        lifecycle: if via == Some("supersedes_edge") && state.is_active() {
            "superseded".to_string()
        } else {
            state.as_str().to_string()
        },
        valid_from: lifecycle.effective_from.clone().or(interval.valid_from),
        valid_until: lifecycle.valid_until.clone().or(interval.valid_until),
        superseded_by,
        superseded_via: via,
    }
}

pub fn node_temporal_states(
    conn: &Connection,
    ids: &[String],
    as_of_epoch: f64,
) -> Result<HashMap<String, NodeTemporalState>, CoreError> {
    let unique: Vec<String> = ids
        .iter()
        .map(|id| id.trim().to_string())
        .filter(|id| !id.is_empty())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    if unique.is_empty() || !table_exists(conn, "nodes") {
        return Ok(HashMap::new());
    }
    let rows = read_node_rows(conn, &unique)?;
    let edges = read_supersedes_edges(conn, &unique)?;
    Ok(rows
        .iter()
        .map(|(id, row)| (id.clone(), resolve_state(row, edges.get(id), as_of_epoch)))
        .collect())
}

#[cfg(test)]
#[path = "temporal_state_tests.rs"]
mod tests;
