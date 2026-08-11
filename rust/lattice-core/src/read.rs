//! Schema readers. The lane/table split is Python's and is reproduced as-is.
//!
//! The lexical lane reads the `kgv2_nodes` / `kgv2_edges` reconstruction views
//! (`KnowledgeGraphStore._read_tables`), while the vector lane joins the legacy
//! `nodes` / `chunks` tables directly (`retrieval_vector.search._VECTOR_ROW_SELECT`).
//! That duality is not a tidy design — it is what the running product does, and a
//! port that "fixed" it would stop being a port.

use std::collections::{BTreeSet, HashMap};

use rusqlite::types::ValueRef;
use rusqlite::Connection;
use serde_json::Value;

use crate::db::CoreError;

/// One column of one row, as the JSON `json.dumps` would have produced for it.
///
/// `sqlite3` hands Python an `int` for INTEGER and a `float` for REAL, and those
/// two serialize differently (`1` vs `1.0`). A port that read every numeric
/// column as `f64` would answer `1.0` where Python answers `1` — indistinguishable
/// in a diff you skim, and a hard failure in an exact-equality golden. BLOBs
/// have no JSON form and no reader here needs one, so they become null.
pub fn sql_json(value: ValueRef<'_>) -> Value {
    match value {
        ValueRef::Null => Value::Null,
        ValueRef::Integer(number) => Value::from(number),
        ValueRef::Real(number) => Value::from(number),
        ValueRef::Text(bytes) => Value::String(String::from_utf8_lossy(bytes).into_owned()),
        ValueRef::Blob(_) => Value::Null,
    }
}

/// `sql_json` for a named column of a query row.
pub fn column_json(row: &rusqlite::Row<'_>, name: &str) -> rusqlite::Result<Value> {
    Ok(sql_json(row.get_ref(name)?))
}

/// `LATTICEAI_KG_READ_V2=0` forces the legacy tables, as in `_kg_common`.
pub const READ_V2_ENV: &str = "LATTICEAI_KG_READ_V2";

/// One row of the lexical lane's node projection.
#[derive(Debug, Clone)]
pub struct NodeRow {
    pub id: String,
    pub node_type: Option<String>,
    pub title: Option<String>,
    pub summary: Option<String>,
    pub metadata_json: Option<String>,
    pub updated_at: Option<String>,
}

/// One row of `_VECTOR_ROW_SELECT`, column for column.
#[derive(Debug, Clone)]
pub struct VectorRow {
    pub item_id: String,
    pub item_type: Option<String>,
    pub source_node: Option<String>,
    pub embedding: Vec<u8>,
    pub embedding_dim: Option<i64>,
    pub embedding_model: Option<String>,
    pub vector_metadata: Option<String>,
    pub node_type: Option<String>,
    pub node_title: Option<String>,
    pub node_summary: Option<String>,
    pub node_metadata: Option<String>,
    pub node_updated_at: Option<String>,
    pub chunk_text: Option<String>,
    pub parent_node_id: Option<String>,
    pub chunk_metadata: Option<String>,
    pub parent_type: Option<String>,
    pub parent_title: Option<String>,
    pub parent_summary: Option<String>,
    pub parent_metadata: Option<String>,
    pub parent_updated_at: Option<String>,
}

/// The projection every vector match is built from — byte-identical to Python's.
pub const VECTOR_ROW_SELECT: &str = "
                    SELECT
                      ve.item_id, ve.item_type, ve.source_node, ve.embedding,
                      ve.embedding_dim, ve.embedding_model, ve.metadata_json AS vector_metadata,
                      n.type AS node_type, n.title AS node_title, n.summary AS node_summary,
                      n.metadata_json AS node_metadata, n.updated_at AS node_updated_at,
                      c.text AS chunk_text, c.source_node AS parent_node_id,
                      c.metadata_json AS chunk_metadata,
                      pn.type AS parent_type, pn.title AS parent_title,
                      pn.summary AS parent_summary, pn.metadata_json AS parent_metadata,
                      pn.updated_at AS parent_updated_at
                    FROM vector_embeddings ve
                    LEFT JOIN nodes n ON n.id=ve.source_node
                    LEFT JOIN chunks c ON c.id=ve.item_id
                    LEFT JOIN nodes pn ON pn.id=c.source_node
                    WHERE ve.embedding_model=? AND ve.embedding_dim=?
                    ";

/// `(nodes_table, edges_table)` for read queries — the v2 views when present.
pub fn read_tables(conn: &Connection) -> (&'static str, &'static str) {
    let enabled = std::env::var(READ_V2_ENV)
        .map(|raw| raw != "0")
        .unwrap_or(true);
    if enabled
        && conn
            .query_row("SELECT 1 FROM kgv2_nodes LIMIT 1", [], |_| Ok(()))
            .is_ok()
    {
        return ("kgv2_nodes", "kgv2_edges");
    }
    ("nodes", "edges")
}

/// Read one `NodeRow` off a query row.
pub fn node_row_from(row: &rusqlite::Row<'_>) -> rusqlite::Result<NodeRow> {
    Ok(NodeRow {
        id: row.get("id")?,
        node_type: row.get("type")?,
        title: row.get("title")?,
        summary: row.get("summary")?,
        metadata_json: row.get("metadata_json")?,
        updated_at: row.get("updated_at")?,
    })
}

/// Read one `VectorRow` off a query row.
pub fn vector_row_from(row: &rusqlite::Row<'_>) -> rusqlite::Result<VectorRow> {
    Ok(VectorRow {
        item_id: row.get("item_id")?,
        item_type: row.get("item_type")?,
        source_node: row.get("source_node")?,
        embedding: row
            .get::<_, Option<Vec<u8>>>("embedding")?
            .unwrap_or_default(),
        embedding_dim: row.get("embedding_dim")?,
        embedding_model: row.get("embedding_model")?,
        vector_metadata: row.get("vector_metadata")?,
        node_type: row.get("node_type")?,
        node_title: row.get("node_title")?,
        node_summary: row.get("node_summary")?,
        node_metadata: row.get("node_metadata")?,
        node_updated_at: row.get("node_updated_at")?,
        chunk_text: row.get("chunk_text")?,
        parent_node_id: row.get("parent_node_id")?,
        chunk_metadata: row.get("chunk_metadata")?,
        parent_type: row.get("parent_type")?,
        parent_title: row.get("parent_title")?,
        parent_summary: row.get("parent_summary")?,
        parent_metadata: row.get("parent_metadata")?,
        parent_updated_at: row.get("parent_updated_at")?,
    })
}

/// `KnowledgeGraphReadsMixin.workspaces_of` — id → workspace, missing stays missing.
///
/// A row present in `nodes_v2` with a NULL workspace maps to `Some(None)`; an id
/// with no row at all is simply absent from the map, and the scoping rule below
/// treats "absent" as private rather than as legacy-global.
pub fn workspaces_of(
    conn: &Connection,
    node_ids: &[String],
) -> Result<HashMap<String, Option<String>>, CoreError> {
    let ids: Vec<&String> = node_ids.iter().filter(|id| !id.is_empty()).collect();
    let mut scopes: HashMap<String, Option<String>> = HashMap::new();
    if ids.is_empty() {
        return Ok(scopes);
    }
    let placeholders = vec!["?"; ids.len()].join(",");
    let sql = format!("SELECT id, workspace_id FROM nodes_v2 WHERE id IN ({placeholders})");
    let mut stmt = conn.prepare(&sql)?;
    let params = rusqlite::params_from_iter(ids.iter().map(|id| id.as_str()));
    let mut rows = stmt.query(params)?;
    while let Some(row) = rows.next()? {
        scopes.insert(row.get::<_, String>(0)?, row.get::<_, Option<String>>(1)?);
    }
    Ok(scopes)
}

/// `KnowledgeGraphReadsMixin.filter_scoped_nodes`, generic over the item shape.
///
/// `allowed == None` means **no scoping** — the local trusted-owner path — and
/// is deliberately not fail-closed. An empty set is the opposite statement: a
/// caller who may read nothing, so nothing matches.
pub fn filter_scoped_nodes<T, F>(
    conn: &Connection,
    items: Vec<T>,
    allowed: Option<&BTreeSet<String>>,
    include_legacy_global: bool,
    id_of: F,
) -> Result<Vec<T>, CoreError>
where
    F: Fn(&T) -> String,
{
    let Some(allowed) = allowed else {
        return Ok(items);
    };
    let allowed: BTreeSet<&str> = allowed
        .iter()
        .filter(|w| !w.is_empty())
        .map(String::as_str)
        .collect();
    let ids: Vec<String> = items.iter().map(&id_of).collect();
    let scopes = workspaces_of(conn, &ids)?;
    let mut visible = Vec::new();
    for (item, node_id) in items.into_iter().zip(ids) {
        if node_id.is_empty() {
            continue;
        }
        match scopes.get(&node_id) {
            None => continue,
            Some(None) => {
                if include_legacy_global {
                    visible.push(item);
                }
            }
            Some(Some(workspace_id)) => {
                if allowed.contains(workspace_id.as_str()) {
                    visible.push(item);
                }
            }
        }
    }
    Ok(visible)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scoped_db() -> (tempfile::TempDir, Connection) {
        let dir = tempfile::tempdir().unwrap();
        let conn = Connection::open(dir.path().join("g.sqlite")).unwrap();
        conn.execute_batch(
            "CREATE TABLE nodes_v2(id TEXT PRIMARY KEY, workspace_id TEXT);
             INSERT INTO nodes_v2 VALUES ('a','w1'),('b','w2'),('c',NULL);
             CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT, title TEXT, summary TEXT,
                                metadata_json TEXT, updated_at TEXT);",
        )
        .unwrap();
        (dir, conn)
    }

    #[test]
    fn workspaces_of_keeps_missing_ids_missing() {
        let (_dir, conn) = scoped_db();
        let ids = vec![
            "a".to_string(),
            "c".to_string(),
            "zz".to_string(),
            String::new(),
        ];
        let scopes = workspaces_of(&conn, &ids).unwrap();
        assert_eq!(scopes.get("a"), Some(&Some("w1".to_string())));
        assert_eq!(scopes.get("c"), Some(&None));
        assert!(!scopes.contains_key("zz"));
        assert!(workspaces_of(&conn, &[]).unwrap().is_empty());
        assert!(workspaces_of(&conn, &[String::new()]).unwrap().is_empty());
    }

    #[test]
    fn scoping_rules_match_the_python_contract() {
        let (_dir, conn) = scoped_db();
        let items = || {
            vec![
                "a".to_string(),
                "b".to_string(),
                "c".to_string(),
                "zz".to_string(),
            ]
        };
        let id_of = |s: &String| s.clone();

        // None → no scoping at all.
        let all = filter_scoped_nodes(&conn, items(), None, false, id_of).unwrap();
        assert_eq!(all.len(), 4);

        // Empty set → nothing is visible (not "everything").
        let none = BTreeSet::new();
        assert!(
            filter_scoped_nodes(&conn, items(), Some(&none), false, id_of)
                .unwrap()
                .is_empty()
        );

        // A specific workspace, legacy-global excluded then included.
        let w1: BTreeSet<String> = ["w1".to_string()].into_iter().collect();
        assert_eq!(
            filter_scoped_nodes(&conn, items(), Some(&w1), false, id_of).unwrap(),
            vec!["a".to_string()]
        );
        assert_eq!(
            filter_scoped_nodes(&conn, items(), Some(&w1), true, id_of).unwrap(),
            vec!["a".to_string(), "c".to_string()]
        );

        // Blank workspace ids in the allowed set are dropped, and a blank node id
        // is never visible.
        let blank: BTreeSet<String> = [String::new()].into_iter().collect();
        assert!(
            filter_scoped_nodes(&conn, items(), Some(&blank), false, id_of)
                .unwrap()
                .is_empty()
        );
        assert!(
            filter_scoped_nodes(&conn, vec![String::new()], Some(&w1), true, id_of)
                .unwrap()
                .is_empty()
        );
    }

    #[test]
    fn sql_json_keeps_pythons_int_float_distinction() {
        let (_dir, conn) = scoped_db();
        conn.execute_batch(
            "CREATE TABLE mixed(i INT, r REAL, t TEXT, n TEXT, b BLOB);
             INSERT INTO mixed VALUES (1, 1.0, 'x', NULL, x'00ff');",
        )
        .unwrap();
        let row = conn
            .query_row("SELECT i, r, t, n, b FROM mixed", [], |row| {
                Ok((
                    column_json(row, "i")?,
                    column_json(row, "r")?,
                    column_json(row, "t")?,
                    column_json(row, "n")?,
                    column_json(row, "b")?,
                ))
            })
            .unwrap();
        assert_eq!(row.0, serde_json::json!(1));
        assert_eq!(row.1, serde_json::json!(1.0));
        assert_ne!(row.0, row.1, "an int must not compare equal to a float");
        assert_eq!(row.2, serde_json::json!("x"));
        assert_eq!(row.3, Value::Null);
        assert_eq!(row.4, Value::Null);
    }

    #[test]
    fn read_tables_falls_back_when_the_views_are_absent() {
        let (_dir, conn) = scoped_db();
        assert_eq!(read_tables(&conn), ("nodes", "edges"));
        conn.execute_batch("CREATE VIEW kgv2_nodes AS SELECT id FROM nodes_v2")
            .unwrap();
        assert_eq!(read_tables(&conn), ("kgv2_nodes", "kgv2_edges"));
    }

    #[test]
    fn row_readers_project_every_column() {
        let (_dir, conn) = scoped_db();
        conn.execute_batch(
            "INSERT INTO nodes VALUES ('a','Task','T','S','{}','2026-05-01T00:00:00');
             CREATE TABLE chunks(id TEXT PRIMARY KEY, source_node TEXT, text TEXT, metadata_json TEXT);
             CREATE TABLE vector_embeddings(item_id TEXT PRIMARY KEY, item_type TEXT,
               source_node TEXT, embedding BLOB, embedding_dim INT, embedding_model TEXT,
               metadata_json TEXT, indexed_at TEXT);
             INSERT INTO vector_embeddings VALUES ('a','node','a',x'0000',2,'m','{}','2026-05-01');",
        )
        .unwrap();
        let node = conn
            .query_row(
                "SELECT id, type, title, summary, metadata_json, updated_at FROM nodes",
                [],
                node_row_from,
            )
            .unwrap();
        assert_eq!(node.id, "a");
        assert_eq!(node.node_type.as_deref(), Some("Task"));
        assert_eq!(node.title.as_deref(), Some("T"));
        assert_eq!(node.summary.as_deref(), Some("S"));
        assert_eq!(node.metadata_json.as_deref(), Some("{}"));
        assert_eq!(node.updated_at.as_deref(), Some("2026-05-01T00:00:00"));
        assert!(format!("{node:?}").contains("NodeRow"));

        let mut stmt = conn.prepare(VECTOR_ROW_SELECT).unwrap();
        let row = stmt
            .query_row(rusqlite::params!["m", 2i64], vector_row_from)
            .unwrap();
        assert_eq!(row.item_id, "a");
        assert_eq!(row.embedding, vec![0u8, 0u8]);
        assert_eq!(row.node_title.as_deref(), Some("T"));
        assert!(row.chunk_text.is_none());
        assert!(row.parent_summary.is_none());
        assert_eq!(row.embedding_model.as_deref(), Some("m"));
        assert!(format!("{:?}", row.clone()).contains("VectorRow"));
    }
}
