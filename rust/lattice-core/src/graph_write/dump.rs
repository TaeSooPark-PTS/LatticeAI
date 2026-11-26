use std::collections::BTreeMap;

use rusqlite::types::ValueRef;
use rusqlite::Connection;
use serde_json::{json, Map, Value};

use super::pyaux::{sha256_bytes, sha256_text};
use crate::db::CoreError;

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

pub const FTS_TABLE: &str = "node_fts";

pub const OPTIONAL_TABLES: [(&str, &str); 1] = [("chunk_parents", "id")];

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

const SKIPPED: [&str; 4] = [
    "kgv2_nodes",
    "kgv2_edges",
    FTS_TABLE,
    "conversation_messages",
];
const SKIPPED_PREFIXES: [&str; 2] = ["node_fts_", "sqlite_"];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Blobs {
    Hex,
    Digest,
}

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
                && !OPTIONAL_TABLES.iter().any(|(table, _)| table == name)
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
    for (table, key) in OPTIONAL_TABLES {
        if present.iter().any(|name| name == table) {
            let rows = select_rows(
                conn,
                &format!("SELECT * FROM {table} ORDER BY {key}"),
                blobs,
                substitutions,
            )?;
            dump.insert(table.into(), Value::Array(rows));
        }
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
                let payload = serde_json::to_string(&sorted_keys(rows)).unwrap_or_default();
                tables.insert(
                    name.clone(),
                    json!({
                        "rows": items.len(),
                        "sha256": sha256_text(&payload),
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
    let columns: Vec<String> = statement.column_names().map(str::to_string).collect();
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
            Blobs::Digest => format!("sha256:{}", sha256_bytes(bytes)),
        }),
    }
}
