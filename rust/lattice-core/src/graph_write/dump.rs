//! The whole store, ordered, as JSON — the shape the parity goldens compare.
//!
//! Mirrors `scripts/gen_graph_write_goldens.py`'s `dump_store` exactly: the same
//! table list, the same per-table order, the same BLOB rendering, and the same
//! refusal to skip an object nobody classified. That last part is the load-
//! bearing one — a table the port forgets to write would otherwise pass a
//! comparison that never looked at it.

use std::collections::BTreeMap;

use rusqlite::types::ValueRef;
use rusqlite::Connection;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

use crate::db::CoreError;

/// Every logical table the write engine touches, in the golden's order.
pub const DUMP_TABLES: [&str; 14] = [
    "graph_meta",
    "kg_meta",
    "storage_meta",
    "nodes",
    "edges",
    "chunks",
    "nodes_v2",
    "edges_v2",
    "edge_occurrences",
    "knowledge_sources",
    "local_file_index",
    "vector_embeddings",
    "vector_index_operations",
    "ingestion_provenance",
];

/// The FTS content table, dumped through its logical columns.
pub const FTS_TABLE: &str = "node_fts";

/// A superkey per table — what makes the row order deterministic.
const DUMP_ORDER: [(&str, &str); 14] = [
    ("graph_meta", "key"),
    ("kg_meta", "key"),
    ("storage_meta", "key"),
    ("nodes", "id"),
    ("edges", "id"),
    ("chunks", "id"),
    ("nodes_v2", "id"),
    ("edges_v2", "id"),
    ("edge_occurrences", "id"),
    ("knowledge_sources", "id"),
    ("local_file_index", "id"),
    ("vector_embeddings", "item_id"),
    ("vector_index_operations", "id"),
    ("ingestion_provenance", "id"),
];

/// Objects `sqlite_master` may hold that the dump deliberately skips.
///
/// `conversation_messages` is platform state bootstrap now creates so
/// readers do not 500 on a fresh Brain. It is not a graph-write table and
/// is not in the write-engine goldens.
const SKIPPED: [&str; 4] = [
    "kgv2_nodes",
    "kgv2_edges",
    FTS_TABLE,
    "conversation_messages",
];
const SKIPPED_PREFIXES: [&str; 2] = ["node_fts_", "sqlite_"];

/// How a `BLOB` column is rendered.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Blobs {
    /// Full lowercase hex — the byte-level proof.
    Hex,
    /// `sha256:<hex>` — small enough for a per-step checkpoint.
    Digest,
}

/// Dump every table, ordered.
///
/// `substitutions` rewrites absolute paths out of the result: the file door
/// stamps `blob_path` and `source_uri` into node metadata, and the machine's
/// temp-directory layout is not part of the contract. Each `(from, to)` pair is
/// applied to every string in the dump, in order.
pub fn dump_store(
    conn: &Connection,
    blobs: Blobs,
    substitutions: &[(String, String)],
) -> Result<Value, CoreError> {
    let present = present_objects(conn)?;
    let unknown: Vec<&String> = present
        .iter()
        .filter(|name| {
            !DUMP_TABLES.contains(&name.as_str())
                && !SKIPPED.contains(&name.as_str())
                && !SKIPPED_PREFIXES
                    .iter()
                    .any(|prefix| name.starts_with(prefix))
        })
        .collect();
    if !unknown.is_empty() {
        return Err(CoreError::InvalidRequest(format!(
            "unclassified sqlite objects {unknown:?}; add them to DUMP_TABLES so the \
             parity proof keeps covering the store"
        )));
    }
    let order: BTreeMap<&str, &str> = DUMP_ORDER.into_iter().collect();
    let mut dump = Map::new();
    for table in DUMP_TABLES {
        if !present.iter().any(|name| name == table) {
            dump.insert(table.into(), Value::Null);
            continue;
        }
        let rows = select_rows(
            conn,
            &format!("SELECT * FROM {table} ORDER BY {}", order[table]),
            blobs,
            substitutions,
        )?;
        dump.insert(table.into(), Value::Array(rows));
    }
    if present.iter().any(|name| name == FTS_TABLE) {
        let rows = select_rows(
            conn,
            &format!("SELECT node_id, title, summary, metadata FROM {FTS_TABLE} ORDER BY node_id"),
            blobs,
            substitutions,
        )?;
        dump.insert(FTS_TABLE.into(), Value::Array(rows));
    } else {
        dump.insert(FTS_TABLE.into(), Value::Null);
    }
    let user_version: i64 = conn.query_row("PRAGMA user_version", [], |row| row.get(0))?;
    dump.insert("user_version".into(), json!(user_version));
    Ok(Value::Object(dump))
}

/// `value` with every object's keys in code-point order — `sort_keys=True`.
///
/// Explicit rather than inherited from `serde_json::Map`; see [`digest_dump`].
fn sorted_keys(value: &Value) -> Value {
    match value {
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            let mut out = serde_json::Map::with_capacity(keys.len());
            for key in keys {
                out.insert(key.clone(), sorted_keys(&map[key]));
            }
            Value::Object(out)
        }
        Value::Array(items) => Value::Array(items.iter().map(sorted_keys).collect()),
        other => other.clone(),
    }
}

/// The per-table `(rows, sha256)` checkpoint form.
///
/// The digest is taken over `json.dumps(rows, ensure_ascii=False,
/// sort_keys=True, separators=(",", ":"))`, which is what the generator hashes.
pub fn digest_dump(dump: &Value) -> Value {
    let Some(dump) = dump.as_object() else {
        return Value::Null;
    };
    let mut tables = Map::new();
    for (name, rows) in dump {
        if name == "user_version" {
            continue;
        }
        match rows {
            Value::Null => {
                tables.insert(name.clone(), Value::Null);
            }
            Value::Array(items) => {
                // `serde_json::to_string` is compact (`,` / `:`), which matches
                // the generator's `separators=(",", ":")`, and CPython escapes
                // nothing extra under `ensure_ascii=False` — nor does
                // `serde_json`. The generator's `sort_keys=True` is reproduced
                // by `sorted_keys` rather than by `Map` being a `BTreeMap`:
                // `lattice-retrieval` enables `serde_json/preserve_order`, and
                // cargo unifies features across a build, so in any build that
                // contains that crate a `Map` iterates in insertion order and
                // every digest here would differ from the golden.
                let payload = serde_json::to_string(&sorted_keys(rows)).unwrap_or_default();
                tables.insert(
                    name.clone(),
                    json!({
                        "rows": items.len(),
                        "sha256": format!("{:x}", Sha256::digest(payload.as_bytes())),
                    }),
                );
            }
            _ => {}
        }
    }
    json!({"tables": tables, "user_version": dump["user_version"]})
}

fn present_objects(conn: &Connection) -> Result<Vec<String>, CoreError> {
    let mut statement =
        conn.prepare("SELECT name FROM sqlite_master WHERE type IN ('table','view')")?;
    let rows = statement.query_map([], |row| row.get::<_, String>(0))?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

fn select_rows(
    conn: &Connection,
    sql: &str,
    blobs: Blobs,
    substitutions: &[(String, String)],
) -> Result<Vec<Value>, CoreError> {
    let mut statement = conn.prepare(sql)?;
    let columns: Vec<String> = statement
        .column_names()
        .into_iter()
        .map(str::to_string)
        .collect();
    let mut query = statement.query([])?;
    let mut rows = Vec::new();
    while let Some(row) = query.next()? {
        let mut record = Map::new();
        for (index, column) in columns.iter().enumerate() {
            record.insert(
                column.clone(),
                cell(row.get_ref(index)?, blobs, substitutions),
            );
        }
        rows.push(Value::Object(record));
    }
    Ok(rows)
}

fn cell(value: ValueRef<'_>, blobs: Blobs, substitutions: &[(String, String)]) -> Value {
    match value {
        ValueRef::Null => Value::Null,
        ValueRef::Integer(number) => json!(number),
        ValueRef::Real(number) => json!(number),
        ValueRef::Text(bytes) => {
            let mut text = String::from_utf8_lossy(bytes).to_string();
            for (from, to) in substitutions {
                if !from.is_empty() {
                    text = text.replace(from.as_str(), to);
                }
            }
            Value::String(text)
        }
        ValueRef::Blob(bytes) => Value::String(match blobs {
            Blobs::Hex => bytes.iter().map(|byte| format!("{byte:02x}")).collect(),
            Blobs::Digest => format!("sha256:{:x}", Sha256::digest(bytes)),
        }),
    }
}
