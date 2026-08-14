//! The six reads `ChronicleService` makes, verbatim, over the read-only lane.
//!
//! Every statement below is copied from `latticeai/services/chronicle.py` —
//! same columns, same aliases, same `ORDER BY`, same bound `:workspace`
//! predicate. Two behaviours of the Python original are contracts rather than
//! details and are reproduced here:
//!
//! * **`_rows` swallows `sqlite3.Error`.** A Brain that was never built has no
//!   `nodes_v2`; a graph-disabled install has no graph at all. Either way the
//!   chronicle answers an honest empty timeline instead of a 500, so each lane
//!   maps a failed statement to no rows.
//! * **`as_of` and `access_stats` do not.** They are read through the store's
//!   own API (`lattice_brain.graph.retrieval_reads`), which lets a sqlite error
//!   out — so those two return `Err` and the route renders a 500.
//!
//! Cells are carried as `serde_json::Value` rather than `String` because the
//! handler emits several of them unchanged (`title`, `label`, `source_type`,
//! `captured_at`): Python hands Starlette whatever sqlite stored, and a port
//! that insisted on `TEXT` would 500 where Python answers 200.

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
use std::collections::{BTreeMap, BTreeSet};

use lattice_core::CoreError;
use rusqlite::types::{Value as SqlValue, ValueRef};
use rusqlite::{Connection, Row, ToSql};
use serde_json::Value;

/// `_SOURCES_SQL`.
pub const SOURCES_SQL: &str = "
    SELECT id,
           node_id,
           COALESCE(title, '') AS title,
           source_type,
           COALESCE(captured_at, created_at) AS at
    FROM ingestion_provenance
    WHERE (:workspace IS NULL OR workspace_id = :workspace)
    ORDER BY at ASC, id ASC
";

/// `_ENTITIES_SQL`.
pub const ENTITIES_SQL: &str = "
    SELECT id,
           label,
           COALESCE(NULLIF(legacy_type, ''), type) AS type,
           created_at AS at
    FROM nodes_v2
    WHERE (:workspace IS NULL OR workspace_id = :workspace)
      AND type NOT IN ('DOCUMENT', 'CHUNK', 'FILE', 'MESSAGE', 'CONVERSATION')
    ORDER BY at ASC, id ASC
";

/// `_CONNECTIONS_SQL`.
pub const CONNECTIONS_SQL: &str = "
    SELECT e.id AS id,
           COALESCE(MIN(o.observed_at), e.created_at) AS at
    FROM edges_v2 e
    LEFT JOIN edge_occurrences o ON o.edge_id = e.id
    WHERE e.source IN (
            SELECT id FROM nodes_v2
            WHERE (:workspace IS NULL OR workspace_id = :workspace))
      AND e.target IN (
            SELECT id FROM nodes_v2
            WHERE (:workspace IS NULL OR workspace_id = :workspace))
    GROUP BY e.id, e.created_at
    ORDER BY at ASC, e.id ASC
";

/// `_MESSAGES_SQL`.
pub const MESSAGES_SQL: &str = "
    SELECT COALESCE(conversation_id, '') AS conversation_id,
           role,
           content,
           timestamp AS at
    FROM conversation_messages
    WHERE (:workspace IS NULL OR workspace_id = :workspace)
      AND (:user IS NULL OR user_email = :user
           OR user_email IS NULL OR user_email = '')
    ORDER BY id ASC
";

/// `_CHANGED_NODES_SQL`.
pub const CHANGED_NODES_SQL: &str = "
    SELECT id,
           label,
           superseded_by,
           COALESCE(valid_to, updated_at) AS at
    FROM nodes_v2
    WHERE (:workspace IS NULL OR workspace_id = :workspace)
      AND (valid_to IS NOT NULL OR superseded_by IS NOT NULL)
    ORDER BY at ASC, id ASC
";

/// `_CHANGED_EDGES_SQL`.
pub const CHANGED_EDGES_SQL: &str = "
    SELECT e.id AS id,
           e.source AS node_id,
           s.label AS source_label,
           t.label AS target_label,
           e.superseded_by AS superseded_by,
           e.valid_to AS at
    FROM edges_v2 e
    JOIN nodes_v2 s ON s.id = e.source
    JOIN nodes_v2 t ON t.id = e.target
    WHERE (:workspace IS NULL OR s.workspace_id = :workspace)
      AND (:workspace IS NULL OR t.workspace_id = :workspace)
      AND e.valid_to IS NOT NULL
    ORDER BY at ASC, e.id ASC
";

/// `lattice_brain.graph.schema.TEMPORAL_PREDICATE_SQL`.
pub const TEMPORAL_PREDICATE_SQL: &str =
    "COALESCE(valid_from, created_at) <= ? AND (valid_to IS NULL OR valid_to > ?)";

/// One `ingestion_provenance` row.
#[derive(Clone, Debug)]
pub struct SourceRow {
    /// `id`.
    pub id: Value,
    /// `node_id`.
    pub node_id: Value,
    /// `COALESCE(title, '')`.
    pub title: Value,
    /// `source_type`.
    pub source_type: Value,
    /// `COALESCE(captured_at, created_at)`.
    pub at: Value,
}

/// One `nodes_v2` row that counts as something the Brain learned.
#[derive(Clone, Debug)]
pub struct EntityRow {
    /// `id`.
    pub id: Value,
    /// `label`.
    pub label: Value,
    /// `COALESCE(NULLIF(legacy_type, ''), type)`.
    pub kind: Value,
    /// `created_at`.
    pub at: Value,
}

/// A row the chronicle only dates — the connections lane.
#[derive(Clone, Debug)]
pub struct AtRow {
    /// The instant the row is filed under.
    pub at: Value,
}

/// One `conversation_messages` row.
#[derive(Clone, Debug)]
pub struct MessageRow {
    /// `COALESCE(conversation_id, '')`.
    pub conversation_id: Value,
    /// `role`.
    pub role: Value,
    /// `content`.
    pub content: Value,
    /// `timestamp`.
    pub at: Value,
}

/// One superseded or retired fact.
#[derive(Clone, Debug)]
pub struct ChangedNodeRow {
    /// `id`.
    pub id: Value,
    /// `label`.
    pub label: Value,
    /// `superseded_by`.
    pub superseded_by: Value,
    /// `COALESCE(valid_to, updated_at)`.
    pub at: Value,
}

/// One relationship that stopped being true.
#[derive(Clone, Debug)]
pub struct ChangedEdgeRow {
    /// `e.source`.
    pub node_id: Value,
    /// `s.label`.
    pub source_label: Value,
    /// `t.label`.
    pub target_label: Value,
    /// `e.superseded_by`.
    pub superseded_by: Value,
    /// `e.valid_to`.
    pub at: Value,
}

/// One node in an `as_of` slice, as far as the chronicle reads it.
#[derive(Clone, Debug)]
pub struct AsOfNode {
    /// `id`.
    pub id: Value,
    /// `label AS title`.
    pub title: Value,
    /// `COALESCE(legacy_type, type)`.
    pub kind: Value,
}

/// `store.as_of()`'s answer, minus the fields the chronicle never reads.
#[derive(Clone, Debug, Default)]
pub struct AsOfWindow {
    /// The node slice, capped by the caller's limit.
    pub nodes: Vec<AsOfNode>,
    /// `node_count` — the length of the slice, not the table.
    pub node_count: i64,
    /// `edge_count` — edges with both endpoints inside the slice.
    pub edge_count: i64,
}

// ── the lanes ───────────────────────────────────────────────────────────────

/// `ChronicleService._sources`.
pub fn sources(conn: &Connection, graph: bool, workspace: Option<&str>) -> Vec<SourceRow> {
    scoped_rows(conn, graph, SOURCES_SQL, workspace, |row| {
        Ok(SourceRow {
            id: cell(row, 0)?,
            node_id: cell(row, 1)?,
            title: cell(row, 2)?,
            source_type: cell(row, 3)?,
            at: cell(row, 4)?,
        })
    })
}

/// `ChronicleService._entities`.
pub fn entities(conn: &Connection, graph: bool, workspace: Option<&str>) -> Vec<EntityRow> {
    scoped_rows(conn, graph, ENTITIES_SQL, workspace, |row| {
        Ok(EntityRow {
            id: cell(row, 0)?,
            label: cell(row, 1)?,
            kind: cell(row, 2)?,
            at: cell(row, 3)?,
        })
    })
}

/// `ChronicleService._connections`.
pub fn connections(conn: &Connection, graph: bool, workspace: Option<&str>) -> Vec<AtRow> {
    scoped_rows(conn, graph, CONNECTIONS_SQL, workspace, |row| {
        Ok(AtRow { at: cell(row, 1)? })
    })
}

/// `ChronicleService._changed_nodes`.
pub fn changed_nodes(
    conn: &Connection,
    graph: bool,
    workspace: Option<&str>,
) -> Vec<ChangedNodeRow> {
    scoped_rows(conn, graph, CHANGED_NODES_SQL, workspace, |row| {
        Ok(ChangedNodeRow {
            id: cell(row, 0)?,
            label: cell(row, 1)?,
            superseded_by: cell(row, 2)?,
            at: cell(row, 3)?,
        })
    })
}

/// `ChronicleService._changed_edges`.
pub fn changed_edges(
    conn: &Connection,
    graph: bool,
    workspace: Option<&str>,
) -> Vec<ChangedEdgeRow> {
    scoped_rows(conn, graph, CHANGED_EDGES_SQL, workspace, |row| {
        Ok(ChangedEdgeRow {
            node_id: cell(row, 1)?,
            source_label: cell(row, 2)?,
            target_label: cell(row, 3)?,
            superseded_by: cell(row, 4)?,
            at: cell(row, 5)?,
        })
    })
}

/// `ChronicleService._messages`.
///
/// The conversation table lives in the same file as the graph, and it is read
/// whether or not the graph is enabled — `_conversation_db` never consults
/// `_enable_graph`, which is why a graph-disabled Brain still has a chronicle.
pub fn messages(conn: &Connection, user_email: &str, workspace: Option<&str>) -> Vec<MessageRow> {
    // "An empty email is nobody, not a user whose address is ''."
    let user = lattice_core::pytext::strip(user_email);
    let user: Option<String> = (!user.is_empty()).then_some(user);
    let params: [(&str, &dyn ToSql); 2] = [(":workspace", &workspace), (":user", &user)];
    read(conn, MESSAGES_SQL, &params, |row| {
        Ok(MessageRow {
            conversation_id: cell(row, 0)?,
            role: cell(row, 1)?,
            content: cell(row, 2)?,
            at: cell(row, 3)?,
        })
    })
}

fn scoped_rows<T>(
    conn: &Connection,
    graph: bool,
    sql: &str,
    workspace: Option<&str>,
    map: impl Fn(&Row<'_>) -> rusqlite::Result<T>,
) -> Vec<T> {
    // `_graph_db` is `None` when the graph is off, and `_rows(None, ...)` is [].
    if !graph {
        return Vec::new();
    }
    let params: [(&str, &dyn ToSql); 1] = [(":workspace", &workspace)];
    read(conn, sql, &params, map)
}

fn read<T>(
    conn: &Connection,
    sql: &str,
    params: &[(&str, &dyn ToSql)],
    map: impl Fn(&Row<'_>) -> rusqlite::Result<T>,
) -> Vec<T> {
    // `except sqlite3.Error: return []` — the whole statement, not row by row.
    let collect = || -> rusqlite::Result<Vec<T>> {
        let mut stmt = conn.prepare(sql)?;
        let mut rows = stmt.query(params)?;
        let mut out = Vec::new();
        while let Some(row) = rows.next()? {
            out.push(map(row)?);
        }
        Ok(out)
    };
    collect().unwrap_or_default()
}

// ── the two reads that are allowed to fail loudly ───────────────────────────

/// `_workspace_scope_sql(graph_scope_kwargs(workspace_id))`, folded into one.
///
/// `None` is the unscoped single-user read (legacy rows included); a named
/// workspace reads only that workspace. An *empty* name is a caller who may
/// read nothing, and answers a predicate that matches nothing rather than
/// silently widening to everything.
fn scope_sql(workspace: Option<&str>) -> Option<(String, Vec<String>)> {
    let workspace = workspace?;
    if workspace.is_empty() {
        return Some(("0".to_string(), Vec::new()));
    }
    Some((
        "workspace_id IN (?)".to_string(),
        vec![workspace.to_string()],
    ))
}

/// `KnowledgeGraphStore.as_of`, reading only what the chronicle asks for.
pub fn as_of(
    conn: &Connection,
    stamp: &str,
    limit: i64,
    workspace: Option<&str>,
) -> Result<AsOfWindow, CoreError> {
    let limit = limit.clamp(1, 2_000);
    let mut clauses = vec![TEMPORAL_PREDICATE_SQL.to_string()];
    let mut binds: Vec<String> = vec![stamp.to_string(), stamp.to_string()];
    if let Some((sql, values)) = scope_sql(workspace) {
        clauses.push(format!("({sql})"));
        binds.extend(values);
    }
    let sql = format!(
        "SELECT id, COALESCE(legacy_type, type) AS type, label AS title, \
         summary, attrs AS metadata_json, updated_at, \
         valid_from, valid_to, superseded_by \
         FROM nodes_v2 WHERE {} \
         ORDER BY updated_at DESC, id ASC LIMIT ?",
        clauses.join(" AND ")
    );
    let mut params: Vec<SqlValue> = binds.into_iter().map(SqlValue::from).collect();
    params.push(SqlValue::from(limit));
    let mut stmt = conn.prepare(&sql)?;
    let mut rows = stmt.query(rusqlite::params_from_iter(params.iter()))?;
    let mut nodes: Vec<AsOfNode> = Vec::new();
    while let Some(row) = rows.next()? {
        nodes.push(AsOfNode {
            id: cell(row, 0)?,
            kind: cell(row, 1)?,
            title: cell(row, 2)?,
        });
    }
    let node_count = nodes.len() as i64;
    let edge_count = edge_count(conn, stamp, &nodes)?;
    Ok(AsOfWindow {
        nodes,
        node_count,
        edge_count,
    })
}

/// Edges valid at `stamp` whose endpoints are both inside the node slice.
///
/// Python materialises the rows and takes `len()`; only the count is read, and
/// the `ORDER BY` it carries cannot change one.
fn edge_count(conn: &Connection, stamp: &str, nodes: &[AsOfNode]) -> Result<i64, CoreError> {
    let visible: Vec<String> = {
        let mut ids: Vec<String> = nodes.iter().map(|node| py_str(&node.id)).collect();
        ids.sort();
        ids
    };
    if visible.is_empty() {
        return Ok(0);
    }
    let placeholders = vec!["?"; visible.len()].join(",");
    let sql = format!(
        "SELECT COUNT(*) FROM edges_v2 WHERE {TEMPORAL_PREDICATE_SQL} \
         AND source IN ({placeholders}) AND target IN ({placeholders})"
    );
    let mut params: Vec<SqlValue> = vec![
        SqlValue::from(stamp.to_string()),
        SqlValue::from(stamp.to_string()),
    ];
    for _ in 0..2 {
        for id in &visible {
            params.push(SqlValue::from(id.clone()));
        }
    }
    let count = conn.query_row(&sql, rusqlite::params_from_iter(params.iter()), |row| {
        row.get::<_, i64>(0)
    })?;
    Ok(count)
}

/// `ChronicleService._importance` over `KnowledgeGraphStore.access_stats`.
///
/// `float(row["importance_score"] or 0.0)` and `.get("accesses") or 0.0` are
/// both truthiness checks, so a stored `0` and a stored `NULL` are the same
/// answer — which is why this returns `0.0` for either rather than an absent
/// key that a caller might treat as "unknown".
pub fn importance(
    conn: &Connection,
    node_ids: &[String],
) -> Result<BTreeMap<String, f64>, CoreError> {
    if node_ids.is_empty() {
        return Ok(BTreeMap::new());
    }
    // `sorted({str(node_id) for node_id in node_ids if node_id})`.
    let ids: BTreeSet<&String> = node_ids.iter().filter(|id| !id.is_empty()).collect();
    if ids.is_empty() {
        return Ok(BTreeMap::new());
    }
    let placeholders = vec!["?"; ids.len()].join(",");
    let sql = format!(
        "SELECT id, importance_score, last_used FROM nodes_v2 WHERE id IN ({placeholders})"
    );
    let params: Vec<&dyn ToSql> = ids.iter().map(|id| *id as &dyn ToSql).collect();
    let mut stmt = conn.prepare(&sql)?;
    let mut rows = stmt.query(params.as_slice())?;
    let mut out = BTreeMap::new();
    while let Some(row) = rows.next()? {
        let key = py_str(&cell(row, 0)?);
        out.insert(key, py_float(&cell(row, 1)?));
    }
    Ok(out)
}

// ── Python's readings of one sqlite cell ────────────────────────────────────

/// One cell as `sqlite3.Row` would hand it to a route.
fn cell(row: &Row<'_>, index: usize) -> rusqlite::Result<Value> {
    Ok(match row.get_ref(index)? {
        ValueRef::Null => Value::Null,
        ValueRef::Integer(value) => Value::from(value),
        ValueRef::Real(value) => Value::from(value),
        ValueRef::Text(bytes) | ValueRef::Blob(bytes) => {
            Value::String(String::from_utf8_lossy(bytes).into_owned())
        }
    })
}

/// `str(value)`.
pub fn py_str(value: &Value) -> String {
    match value {
        Value::Null => "None".to_string(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::String(text) => text.clone(),
        other => other.to_string(),
    }
}

/// `str(value or "")` — the reading `_moment` and `_preview` both start from.
pub fn py_str_or_empty(value: &Value) -> String {
    if py_truthy(value) {
        py_str(value)
    } else {
        String::new()
    }
}

/// Python truthiness: `0`, `0.0`, `""`, `None` and `False` are all false.
pub fn py_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(flag) => *flag,
        Value::Number(number) => number.as_f64().map(|v| v != 0.0).unwrap_or(true),
        Value::String(text) => !text.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(map) => !map.is_empty(),
    }
}

/// `float(value or 0.0)`, with Python's `ValueError` flattened to `0.0`.
fn py_float(value: &Value) -> f64 {
    if !py_truthy(value) {
        return 0.0;
    }
    match value {
        Value::Number(number) => number.as_f64().unwrap_or(0.0),
        Value::String(text) => text.trim().parse::<f64>().unwrap_or(0.0),
        Value::Bool(_) => 1.0,
        _ => 0.0,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_missing_table_answers_no_rows_rather_than_an_error() {
        let conn = Connection::open_in_memory().expect("memory db");
        assert!(sources(&conn, true, None).is_empty());
        assert!(entities(&conn, true, None).is_empty());
        assert!(connections(&conn, true, None).is_empty());
        assert!(changed_nodes(&conn, true, None).is_empty());
        assert!(changed_edges(&conn, true, None).is_empty());
        assert!(messages(&conn, "", None).is_empty());
    }

    #[test]
    fn a_graph_disabled_brain_reads_no_graph_lane_at_all() {
        let conn = Connection::open_in_memory().expect("memory db");
        conn.execute_batch(
            "CREATE TABLE nodes_v2(id TEXT, label TEXT, type TEXT, legacy_type TEXT, \
             workspace_id TEXT, created_at TEXT, updated_at TEXT, valid_from TEXT, \
             valid_to TEXT, superseded_by TEXT, summary TEXT, attrs TEXT, \
             importance_score REAL, last_used TEXT);
             INSERT INTO nodes_v2(id, label, type, workspace_id, created_at) \
             VALUES('n1', 'Rust', 'Concept', 'personal', '2026-08-11T09:00:00');",
        )
        .expect("schema");
        assert_eq!(entities(&conn, true, Some("personal")).len(), 1);
        assert!(entities(&conn, false, Some("personal")).is_empty());
        // A workspace the row does not belong to excludes it, and so does the
        // empty scope — which is the "may read nothing" predicate.
        assert!(entities(&conn, true, Some("other")).is_empty());
        assert_eq!(entities(&conn, true, None).len(), 1);
    }

    #[test]
    fn the_bitemporal_slice_is_half_open_and_scoped() {
        let conn = Connection::open_in_memory().expect("memory db");
        conn.execute_batch(
            "CREATE TABLE nodes_v2(id TEXT, label TEXT, type TEXT, legacy_type TEXT, \
             workspace_id TEXT, created_at TEXT, updated_at TEXT, valid_from TEXT, \
             valid_to TEXT, superseded_by TEXT, summary TEXT, attrs TEXT, \
             importance_score REAL, last_used TEXT);
             CREATE TABLE edges_v2(id TEXT, source TEXT, target TEXT, type TEXT, \
             legacy_type TEXT, weight REAL, metadata TEXT, created_at TEXT, \
             valid_from TEXT, valid_to TEXT, superseded_by TEXT);
             INSERT INTO nodes_v2(id, label, type, workspace_id, created_at, updated_at, \
             importance_score) VALUES
               ('a', 'A', 'Concept', 'personal', '2026-01-01T00:00:00', '2026-01-01T00:00:00', 2.5),
               ('b', 'B', 'Concept', 'personal', '2026-01-01T00:00:00', '2026-01-02T00:00:00', 0),
               ('c', 'C', 'Concept', 'other',    '2026-01-01T00:00:00', '2026-01-03T00:00:00', NULL);
             UPDATE nodes_v2 SET valid_to='2026-06-01T00:00:00' WHERE id='b';
             INSERT INTO edges_v2(id, source, target, type, created_at) \
             VALUES('e1', 'a', 'b', 'RELATES_TO', '2026-01-01T00:00:00');",
        )
        .expect("schema");

        let window = as_of(&conn, "2026-03-01T00:00:00", 2_000, Some("personal")).expect("slice");
        assert_eq!(window.node_count, 2);
        assert_eq!(window.edge_count, 1);
        // `valid_to` is exclusive: at the instant `b` was retired it is gone,
        // and the edge that needed it goes with it.
        let later = as_of(&conn, "2026-06-01T00:00:00", 2_000, Some("personal")).expect("slice");
        assert_eq!(later.node_count, 1);
        assert_eq!(later.edge_count, 0);
        // Unscoped reads the other workspace too; a scope excludes it.
        assert_eq!(
            as_of(&conn, "2026-03-01T00:00:00", 2_000, None)
                .expect("slice")
                .node_count,
            3
        );
        assert_eq!(
            as_of(&conn, "2026-03-01T00:00:00", 2_000, Some(""))
                .expect("slice")
                .node_count,
            0
        );
        // A limit clamps into `[1, 2000]` rather than re-expanding to a default.
        assert_eq!(
            as_of(&conn, "2026-03-01T00:00:00", 0, Some("personal"))
                .expect("slice")
                .node_count,
            1
        );

        let scores = importance(&conn, &["a".into(), "b".into(), "".into()]).expect("scores");
        assert_eq!(scores.get("a"), Some(&2.5));
        // A stored 0 is falsy in `float(x or 0.0)` and reads as 0.0, exactly
        // like the NULL two rows down.
        assert_eq!(scores.get("b"), Some(&0.0));
        assert!(importance(&conn, &[]).expect("empty").is_empty());
        assert!(importance(&conn, &["".into()]).expect("blank").is_empty());
    }

    #[test]
    fn python_reads_a_cell_the_way_this_module_does() {
        assert_eq!(py_str(&Value::Null), "None");
        assert_eq!(py_str_or_empty(&Value::Null), "");
        assert_eq!(py_str_or_empty(&Value::from(0)), "");
        assert_eq!(py_str_or_empty(&Value::from("x")), "x");
        assert!(!py_truthy(&Value::from(0.0)));
        assert!(py_truthy(&Value::from("0")));
        assert_eq!(py_float(&Value::from("1.5")), 1.5);
        assert_eq!(py_float(&Value::from("nope")), 0.0);
        assert_eq!(py_float(&Value::Bool(true)), 1.0);
        assert_eq!(py_str(&Value::Bool(false)), "False");
    }
}
