use std::collections::BTreeMap;

use rusqlite::{Connection, ToSql, Transaction};
use serde_json::{json, Map, Value};

use crate::db::CoreError;
use crate::pytext::safe_loads;

use super::projection_v2::delete_nodes_v2;
use super::pyaux::{json_of, sha256_text};
use super::types::ChunkParentPiece;

pub const CONTEXT_PREFIX_FIELD: &str = "context_prefix";
pub const PARENT_INDEX_FIELD: &str = "parent_index";
pub const PARENT_ID_FIELD: &str = "parent_id";

pub const CHUNK_PARENTS_SQL: &str = "
    CREATE TABLE IF NOT EXISTS chunk_parents (
      id TEXT PRIMARY KEY,
      source_node TEXT NOT NULL,
      parent_index INTEGER NOT NULL,
      text TEXT NOT NULL,
      metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
      created_at TEXT NOT NULL,
      FOREIGN KEY(source_node) REFERENCES nodes(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_chunk_parents_source ON chunk_parents(source_node);
";

#[derive(Debug, Clone, PartialEq)]
pub struct ChunkParentRow {
    pub id: String,
    pub source_node: String,
    pub parent_index: i64,
    pub text: String,
    pub metadata: Map<String, Value>,
}

pub fn context_prefix_of(fields: &Map<String, Value>) -> Option<&str> {
    fields
        .get(CONTEXT_PREFIX_FIELD)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|prefix| !prefix.is_empty())
}

pub fn chunk_index_text(text: &str, fields: &Map<String, Value>) -> String {
    match context_prefix_of(fields) {
        Some(prefix) => format!("{prefix}\n{text}"),
        None => text.to_string(),
    }
}

pub fn chunk_index_text_json(text: &str, metadata_json: Option<&str>) -> String {
    chunk_index_text(text, &safe_loads(metadata_json))
}

pub fn parent_id_for(source_node: &str, parent_index: usize, text: &str) -> String {
    format!(
        "parent:{}",
        &sha256_text(&format!("{source_node}:parent:{parent_index}:{text}"))[..24]
    )
}

pub fn chunk_parents_exist(conn: &Connection) -> bool {
    conn.query_row(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chunk_parents'",
        [],
        |_| Ok(()),
    )
    .is_ok()
}

pub fn ensure_chunk_parents_schema(conn: &Connection) -> Result<(), CoreError> {
    conn.execute_batch(CHUNK_PARENTS_SQL)?;
    Ok(())
}

pub fn load_chunk_parents(
    conn: &Connection,
    ids: &[String],
) -> Result<BTreeMap<String, ChunkParentRow>, CoreError> {
    let mut out = BTreeMap::new();
    if ids.is_empty() || !chunk_parents_exist(conn) {
        return Ok(out);
    }
    for batch in ids.chunks(450) {
        let placeholders = vec!["?"; batch.len()].join(",");
        let params: Vec<&dyn ToSql> = batch.iter().map(|id| id as &dyn ToSql).collect();
        let mut statement = conn.prepare(&format!(
            "SELECT id, source_node, parent_index, text, metadata_json \
             FROM chunk_parents WHERE id IN ({placeholders})"
        ))?;
        let rows = statement.query_map(params.as_slice(), |row| {
            Ok(ChunkParentRow {
                id: row.get(0)?,
                source_node: row.get(1)?,
                parent_index: row.get(2)?,
                text: row.get(3)?,
                metadata: safe_loads(row.get::<_, Option<String>>(4)?.as_deref()),
            })
        })?;
        for row in rows {
            let row = row?;
            out.insert(row.id.clone(), row);
        }
    }
    Ok(out)
}

pub fn export_chunk_parents(conn: &Connection) -> Result<Vec<Value>, CoreError> {
    if !chunk_parents_exist(conn) {
        return Ok(Vec::new());
    }
    let mut statement = conn.prepare(
        "SELECT id, source_node, parent_index, text, metadata_json, created_at \
         FROM chunk_parents ORDER BY source_node, parent_index, id",
    )?;
    let rows = statement.query_map([], |row| {
        Ok(json!({
            "id": row.get::<_, String>(0)?,
            "source_node": row.get::<_, String>(1)?,
            "parent_index": row.get::<_, i64>(2)?,
            "text": row.get::<_, String>(3)?,
            "metadata_json": row.get::<_, String>(4)?,
            "created_at": row.get::<_, String>(5)?,
        }))
    })?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

pub fn import_chunk_parents(conn: &Connection, rows: &[Value]) -> Result<usize, CoreError> {
    if rows.is_empty() {
        return Ok(0);
    }
    conn.execute_batch(CHUNK_PARENTS_SQL)?;
    let mut written = 0usize;
    for row in rows {
        let field = |key: &str| row.get(key).and_then(Value::as_str).unwrap_or("");
        let metadata = Some(field("metadata_json"))
            .filter(|raw| serde_json::from_str::<Value>(raw).is_ok())
            .unwrap_or("{}");
        written += conn.execute(
            "INSERT OR REPLACE INTO chunk_parents(\
               id, source_node, parent_index, text, metadata_json, created_at\
             ) SELECT ?1, ?2, ?3, ?4, ?5, ?6 \
             WHERE ?1 <> '' AND EXISTS(SELECT 1 FROM nodes WHERE id = ?2)",
            rusqlite::params![
                field("id"),
                field("source_node"),
                row.get("parent_index").and_then(Value::as_i64).unwrap_or(0),
                field("text"),
                metadata,
                field("created_at"),
            ],
        )?;
    }
    Ok(written)
}

pub fn clear_chunk_parents(conn: &Connection) -> Result<(), CoreError> {
    if chunk_parents_exist(conn) {
        conn.execute("DELETE FROM chunk_parents", [])?;
    }
    Ok(())
}

pub(crate) fn write_chunk_parents(
    txn: &Transaction<'_>,
    source_node: &str,
    parents: &[ChunkParentPiece],
    now: &str,
) -> Result<Vec<String>, CoreError> {
    if parents.is_empty() {
        return Ok(Vec::new());
    }
    txn.execute_batch(CHUNK_PARENTS_SQL)?;
    let mut ids = Vec::with_capacity(parents.len());
    for (index, parent) in parents.iter().enumerate() {
        let id = parent_id_for(source_node, index, &parent.text);
        let mut metadata = parent.fields.clone();
        metadata.insert("source_node".into(), json!(source_node));
        txn.execute(
            "INSERT OR REPLACE INTO chunk_parents(\
               id, source_node, parent_index, text, metadata_json, created_at\
             ) VALUES (?, ?, ?, ?, ?, ?)",
            rusqlite::params![
                id,
                source_node,
                index as i64,
                parent.text,
                json_of(&metadata),
                now
            ],
        )?;
        ids.push(id);
    }
    Ok(ids)
}

pub(crate) fn resolve_parent_id(
    fields: &Map<String, Value>,
    parent_ids: &[String],
) -> Option<String> {
    let index = fields.get(PARENT_INDEX_FIELD)?.as_u64()? as usize;
    parent_ids.get(index).cloned()
}

fn ids_in(txn: &Transaction<'_>, sql: &str, source_node: &str) -> Result<Vec<String>, CoreError> {
    let mut statement = txn.prepare(sql)?;
    let rows = statement.query_map(rusqlite::params![source_node], |row| {
        row.get::<_, String>(0)
    })?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

pub(crate) fn purge_chunk_ids(txn: &Transaction<'_>, ids: &[String]) -> Result<usize, CoreError> {
    if ids.is_empty() {
        return Ok(0);
    }
    let mut removed = 0usize;
    for batch in ids.chunks(400) {
        let placeholders = vec!["?"; batch.len()].join(",");
        let params: Vec<&dyn ToSql> = batch.iter().map(|id| id as &dyn ToSql).collect();
        let doubled: Vec<&dyn ToSql> = params.iter().chain(params.iter()).copied().collect();
        txn.execute(
            &format!("DELETE FROM vector_embeddings WHERE item_id IN ({placeholders})"),
            params.as_slice(),
        )?;
        txn.execute(
            &format!(
                "DELETE FROM edges WHERE from_node IN ({placeholders}) OR to_node IN ({placeholders})"
            ),
            doubled.as_slice(),
        )?;
        let _ = txn.execute(
            &format!(
                "DELETE FROM edges_v2 WHERE source IN ({placeholders}) OR target IN ({placeholders})"
            ),
            doubled.as_slice(),
        );
        removed += txn.execute(
            &format!("DELETE FROM chunks WHERE id IN ({placeholders})"),
            params.as_slice(),
        )?;
        txn.execute(
            &format!("DELETE FROM nodes WHERE id IN ({placeholders})"),
            params.as_slice(),
        )?;
    }
    let owned: Vec<String> = ids.to_vec();
    let _ = delete_nodes_v2(txn, &owned);
    crate::bm25_stats::delete_bm25_node_stats_by_ids(txn, &owned)?;
    Ok(removed)
}

pub(crate) fn purge_stale_chunks(
    txn: &Transaction<'_>,
    source_node: &str,
    keep_chunk_ids: &[String],
    keep_parent_ids: &[String],
) -> Result<usize, CoreError> {
    let stale: Vec<String> = ids_in(
        txn,
        "SELECT id FROM chunks WHERE source_node = ?1",
        source_node,
    )?
    .into_iter()
    .filter(|id| !keep_chunk_ids.contains(id))
    .collect();
    let removed = purge_chunk_ids(txn, &stale)?;
    if chunk_parents_exist(txn) {
        let stale_parents: Vec<String> = ids_in(
            txn,
            "SELECT id FROM chunk_parents WHERE source_node = ?1",
            source_node,
        )?
        .into_iter()
        .filter(|id| !keep_parent_ids.contains(id))
        .collect();
        for id in &stale_parents {
            txn.execute(
                "DELETE FROM chunk_parents WHERE id = ?1",
                rusqlite::params![id],
            )?;
        }
    }
    Ok(removed)
}

pub(crate) fn purge_document_chunks(
    txn: &Transaction<'_>,
    source_node: &str,
) -> Result<usize, CoreError> {
    txn.execute(
        "DELETE FROM vector_embeddings WHERE item_id = ?1",
        rusqlite::params![source_node],
    )?;
    purge_stale_chunks(txn, source_node, &[], &[])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_index_text_prepends_a_non_blank_prefix_only() {
        let mut fields = Map::new();
        assert_eq!(chunk_index_text("body", &fields), "body");
        fields.insert(CONTEXT_PREFIX_FIELD.into(), json!("   "));
        assert_eq!(chunk_index_text("body", &fields), "body");
        fields.insert(CONTEXT_PREFIX_FIELD.into(), json!(" Doc: a "));
        assert_eq!(chunk_index_text("body", &fields), "Doc: a\nbody");
        assert_eq!(
            chunk_index_text_json("body", Some(r#"{"context_prefix":"P"}"#)),
            "P\nbody"
        );
        assert_eq!(chunk_index_text_json("body", Some("not json")), "body");
        assert_eq!(chunk_index_text_json("body", None), "body");
    }

    #[test]
    fn parent_ids_are_stable_and_resolve_from_the_child_index() {
        let a = parent_id_for("webdoc:1", 0, "text");
        assert_eq!(a, parent_id_for("webdoc:1", 0, "text"));
        assert_ne!(a, parent_id_for("webdoc:1", 1, "text"));
        assert!(a.starts_with("parent:"));
        let ids = vec!["p0".to_string(), "p1".to_string()];
        let mut fields = Map::new();
        assert_eq!(resolve_parent_id(&fields, &ids), None);
        fields.insert(PARENT_INDEX_FIELD.into(), json!(1));
        assert_eq!(resolve_parent_id(&fields, &ids).as_deref(), Some("p1"));
        fields.insert(PARENT_INDEX_FIELD.into(), json!(7));
        assert_eq!(resolve_parent_id(&fields, &ids), None);
    }

    #[test]
    fn a_legacy_database_without_the_table_reads_as_no_parents() {
        let conn = Connection::open_in_memory().expect("memory db");
        assert!(!chunk_parents_exist(&conn));
        assert!(load_chunk_parents(&conn, &["parent:x".to_string()])
            .expect("tolerant read")
            .is_empty());
        ensure_chunk_parents_schema(&conn).expect("ddl");
        assert!(chunk_parents_exist(&conn));
        assert!(load_chunk_parents(&conn, &[]).expect("empty").is_empty());
    }
}
