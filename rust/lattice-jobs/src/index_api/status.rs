//! `KnowledgeGraphStore.index_status()` — the expensive half of GET /api/index/status.

use std::collections::BTreeMap;

use lattice_auth::OrderedMap;
use lattice_core::pytext::{clean_text, round_to, safe_loads};
use lattice_core::{CoreError, LocalEmbeddingModel};
use rusqlite::Connection;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use super::{
    DEFAULT_VECTOR_INDEX, HONEST_FALLBACK, SQLITE_VEC_REASON, TARGET_REBUILD_MS, VECTOR_INDEX_ENV,
};

/// One embeddable item, as `_iter_vector_source_items` yields it.
#[derive(Debug, Clone)]
pub(crate) struct SourceItem {
    pub(crate) item_id: String,
    pub(crate) item_type: &'static str,
    pub(crate) source_node: String,
    pub(crate) text: String,
    pub(crate) metadata: serde_json::Map<String, Value>,
}

/// `_vector_text_for_node` — title, summary and the eight metadata fields.
pub(crate) fn vector_text_for_node(
    title: &str,
    summary: &str,
    metadata: &serde_json::Map<String, Value>,
) -> String {
    const KEYS: [&str; 8] = [
        "filename",
        "relative_path",
        "file_path",
        "conversation_id",
        "source",
        "category",
        "ext",
        "role",
    ];
    let mut parts: Vec<String> = Vec::new();
    for key in KEYS {
        match metadata.get(key) {
            Some(value) if truthy(value) => parts.push(py_str(value)),
            _ => {}
        }
    }
    clean_text(&format!("{title}\n{summary}\n{}", parts.join(" ")))
}

/// Python's `str(value)` for the handful of scalars metadata carries.
pub(crate) fn py_str(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Null => "None".into(),
        other => other.to_string(),
    }
}

/// Python truthiness for a JSON value.
pub(crate) fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(flag) => *flag,
        Value::Number(number) => number.as_f64().is_some_and(|float| float != 0.0),
        Value::String(text) => !text.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(map) => !map.is_empty(),
    }
}

/// `_sha256_text` — lowercase hex sha256 of the UTF-8 encoding.
pub(crate) fn sha256_text(text: &str) -> String {
    let digest = Sha256::digest(text.as_bytes());
    let mut out = String::with_capacity(digest.len() * 2);
    for byte in digest {
        out.push_str(&format!("{byte:02x}"));
    }
    out
}

/// `_iter_vector_source_items(conn)` — nodes then chunks, in its two orders.
///
/// Both queries read the **raw** `nodes` / `chunks` tables, not the v2 views:
/// the vector index is built from the legacy projection and status must walk
/// the same rows the builder walked.
pub(crate) fn source_items(conn: &Connection) -> Result<Vec<SourceItem>, CoreError> {
    let mut items = Vec::new();

    let mut statement = conn.prepare(
        "SELECT id, type, title, summary, metadata_json FROM nodes \
         WHERE type <> 'Chunk' ORDER BY updated_at DESC, id ASC",
    )?;
    let rows = statement.query_map([], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, Option<String>>(1)?.unwrap_or_default(),
            row.get::<_, Option<String>>(2)?.unwrap_or_default(),
            row.get::<_, Option<String>>(3)?.unwrap_or_default(),
            safe_loads(row.get::<_, Option<String>>(4)?.as_deref()),
        ))
    })?;
    for (id, node_type, title, summary, metadata) in rows.filter_map(Result::ok) {
        let text = vector_text_for_node(&title, &summary, &metadata);
        if text.is_empty() {
            continue;
        }
        let mut carried = serde_json::Map::new();
        carried.insert("node_type".into(), json!(node_type));
        for (key, value) in metadata {
            carried.insert(key, value);
        }
        items.push(SourceItem {
            item_id: id.clone(),
            item_type: "node",
            source_node: id,
            text,
            metadata: carried,
        });
    }
    drop(statement);

    let mut statement = conn.prepare(
        "SELECT c.id, c.source_node AS parent_source_node, c.text, c.metadata_json \
         FROM chunks c JOIN nodes n ON n.id=c.id ORDER BY c.created_at DESC, c.id ASC",
    )?;
    let rows = statement.query_map([], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, Option<String>>(1)?,
            row.get::<_, Option<String>>(2)?.unwrap_or_default(),
            safe_loads(row.get::<_, Option<String>>(3)?.as_deref()),
        ))
    })?;
    for (id, parent, text, metadata) in rows.filter_map(Result::ok) {
        let text = clean_text(&text);
        if text.is_empty() {
            continue;
        }
        let mut carried = metadata;
        carried.insert(
            "parent_source_node".into(),
            parent.map(Value::String).unwrap_or(Value::Null),
        );
        items.push(SourceItem {
            item_id: id.clone(),
            item_type: "chunk",
            source_node: id,
            text,
            metadata: carried,
        });
    }
    Ok(items)
}

/// One row of `vector_embeddings`, as status compares against it.
struct VectorRow {
    text_hash: String,
    embedding_dim: i64,
    embedding_model: String,
}

/// `_VectorStatusMixin.index_status()`.
pub fn index_status(conn: &Connection) -> Result<Value, CoreError> {
    let model = LocalEmbeddingModel::from_env();
    let model_id = model.model_id().to_string();
    let model_dim = model.dim() as i64;

    let counts_by_type: Vec<(String, i64)> = {
        let mut statement = conn.prepare(
            "SELECT item_type, COUNT(*) AS count FROM vector_embeddings GROUP BY item_type",
        )?;
        let rows = statement.query_map([], |row| {
            Ok((
                row.get::<_, Option<String>>(0)?.unwrap_or_default(),
                row.get::<_, i64>(1)?,
            ))
        })?;
        rows.filter_map(Result::ok).collect()
    };

    let items = source_items(conn)?;

    let mut vector_rows: BTreeMap<String, VectorRow> = BTreeMap::new();
    {
        let mut statement = conn.prepare(
            "SELECT item_id, text_hash, embedding_dim, embedding_model, indexed_at \
             FROM vector_embeddings",
        )?;
        let rows = statement.query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                VectorRow {
                    text_hash: row.get::<_, Option<String>>(1)?.unwrap_or_default(),
                    embedding_dim: row.get::<_, Option<i64>>(2)?.unwrap_or_default(),
                    embedding_model: row.get::<_, Option<String>>(3)?.unwrap_or_default(),
                },
            ))
        })?;
        for (item_id, row) in rows.filter_map(Result::ok) {
            vector_rows.insert(item_id, row);
        }
    }

    let operations = recent_operations(conn)?;

    let mut missing = 0i64;
    let mut stale = 0i64;
    let mut ready = 0i64;
    let mut backlog_by_type = OrderedMap::new();
    let mut backlog_reasons = OrderedMap::new();
    let mut backlog_samples: Vec<Value> = Vec::new();

    for item in &items {
        let reason = match vector_rows.get(&item.item_id) {
            None => {
                missing += 1;
                Some("missing_vector")
            }
            Some(row) => {
                let expected = sha256_text(&clean_text(&item.text));
                if row.text_hash != expected
                    || row.embedding_dim != model_dim
                    || row.embedding_model != model_id
                {
                    stale += 1;
                    Some(if row.embedding_model != model_id {
                        "model_changed"
                    } else if row.embedding_dim != model_dim {
                        "dimension_changed"
                    } else {
                        "text_changed"
                    })
                } else {
                    ready += 1;
                    None
                }
            }
        };
        if let Some(reason) = reason {
            bump(&mut backlog_by_type, item.item_type);
            bump(&mut backlog_reasons, reason);
            if backlog_samples.len() < 20 {
                backlog_samples.push(backlog_sample(item, reason));
            }
        }
    }

    let pending = missing + stale;
    let source_ids: std::collections::BTreeSet<&str> =
        items.iter().map(|item| item.item_id.as_str()).collect();
    let orphaned = vector_rows
        .keys()
        .filter(|item_id| !source_ids.contains(item_id.as_str()))
        .count() as i64;
    let coverage_ratio = if items.is_empty() {
        1.0
    } else {
        round_to(ready as f64 / items.len() as f64, 6)
    };

    let embedder = embedder_status(conn, &model_id, model_dim);
    let stale_embedder = embedder
        .get("stale_embedder")
        .and_then(Value::as_bool)
        .unwrap_or(false);

    let mut payload = OrderedMap::new();
    payload.insert(
        "status",
        json!(if pending == 0 {
            "ready"
        } else {
            "needs_reindex"
        }),
    );
    payload.insert("embedder", embedder);
    payload.insert("storage", storage_block(conn, &model_id, model_dim));
    payload.insert("source_items", json!(items.len()));
    payload.insert(
        "indexed_items",
        json!(counts_by_type.iter().map(|(_, count)| count).sum::<i64>()),
    );
    payload.insert("ready_items", json!(ready));
    payload.insert("missing_items", json!(missing));
    payload.insert("stale_items", json!(stale));
    payload.insert("pending_items", json!(pending));
    payload.insert("by_item_type", ordered(counts_by_type));

    let mut scale = OrderedMap::new();
    scale.insert("version", json!(1));
    scale.insert("coverage_ratio", json!(coverage_ratio));
    scale.insert(
        "coverage_percent",
        json!(round_to(coverage_ratio * 100.0, 2)),
    );
    scale.insert("source_items", json!(items.len()));
    scale.insert("ready_items", json!(ready));
    scale.insert("pending_items", json!(pending));
    scale.insert("missing_items", json!(missing));
    scale.insert("stale_items", json!(stale));
    scale.insert("orphaned_items", json!(orphaned));
    scale.insert(
        "backlog_by_item_type",
        serde_json::to_value(backlog_by_type).unwrap_or(Value::Null),
    );
    scale.insert(
        "backlog_reasons",
        serde_json::to_value(backlog_reasons).unwrap_or(Value::Null),
    );
    scale.insert("backlog_samples", Value::Array(backlog_samples));
    scale.insert("incremental_reindex_recommended", json!(pending > 0));
    scale.insert(
        "full_rebuild_recommended",
        json!(orphaned > 0 || stale_embedder),
    );
    scale.insert("latency_budget", latency_budget(&operations));
    payload.insert("scale", serde_json::to_value(scale).unwrap_or(Value::Null));
    payload.insert(
        "operations",
        Value::Array(operations.iter().map(|(_, value)| value.clone()).collect()),
    );
    Ok(serde_json::to_value(payload).unwrap_or(Value::Null))
}

fn bump(map: &mut OrderedMap, key: &str) {
    let next = map.get(key).and_then(Value::as_i64).unwrap_or(0) + 1;
    map.insert(key, json!(next));
}

fn ordered(pairs: Vec<(String, i64)>) -> Value {
    let mut map = OrderedMap::new();
    for (key, count) in pairs {
        map.insert(key, json!(count));
    }
    serde_json::to_value(map).unwrap_or(Value::Null)
}

/// One backlog sample, with metadata narrowed to the four keys Python keeps.
fn backlog_sample(item: &SourceItem, reason: &str) -> Value {
    const KEPT: [&str; 4] = [
        "node_type",
        "source",
        "conversation_id",
        "parent_source_node",
    ];
    let mut metadata = OrderedMap::new();
    for (key, value) in &item.metadata {
        if KEPT.contains(&key.as_str()) {
            metadata.insert(key.clone(), value.clone());
        }
    }
    let mut sample = OrderedMap::new();
    sample.insert("item_id", json!(item.item_id));
    sample.insert("item_type", json!(item.item_type));
    sample.insert("source_node", json!(item.source_node));
    sample.insert("reason", json!(reason));
    sample.insert(
        "metadata",
        serde_json::to_value(metadata).unwrap_or(Value::Null),
    );
    serde_json::to_value(sample).unwrap_or(Value::Null)
}

/// `vector_index_operations`, newest five, plus each one's status for the
/// latency budget.
type Operation = (String, Value);

fn recent_operations(conn: &Connection) -> Result<Vec<Operation>, CoreError> {
    let mut statement = match conn.prepare(
        "SELECT id, operation, status, requested_at, started_at, completed_at, \
                items_total, items_indexed, items_skipped, error_message, metadata_json \
         FROM vector_index_operations ORDER BY requested_at DESC, id DESC LIMIT 5",
    ) {
        Ok(statement) => statement,
        // A Brain that has never rebuilt has no table; Python's read would
        // raise and `index_status` would 500, but the table is created with the
        // schema, so an absent one means a store this port should not pretend
        // to know about. Report no history rather than fail the whole read.
        Err(_) => return Ok(Vec::new()),
    };
    let rows = statement.query_map([], |row| {
        let mut operation = OrderedMap::new();
        operation.insert("id", sql_value(row, 0)?);
        operation.insert("operation", sql_value(row, 1)?);
        let status = row.get::<_, Option<String>>(2)?.unwrap_or_default();
        operation.insert("status", json!(status));
        operation.insert("requested_at", sql_value(row, 3)?);
        operation.insert("started_at", sql_value(row, 4)?);
        operation.insert("completed_at", sql_value(row, 5)?);
        operation.insert("items_total", sql_value(row, 6)?);
        operation.insert("items_indexed", sql_value(row, 7)?);
        operation.insert("items_skipped", sql_value(row, 8)?);
        operation.insert("error_message", sql_value(row, 9)?);
        operation.insert(
            "metadata",
            Value::Object(safe_loads(row.get::<_, Option<String>>(10)?.as_deref())),
        );
        Ok((
            status,
            serde_json::to_value(operation).unwrap_or(Value::Null),
        ))
    })?;
    Ok(rows.filter_map(Result::ok).collect())
}

fn sql_value(row: &rusqlite::Row<'_>, index: usize) -> rusqlite::Result<Value> {
    Ok(lattice_core::read::sql_json(row.get_ref(index)?))
}

/// `latency_budget` — the newest **completed** operation, or four nulls.
fn latency_budget(operations: &[Operation]) -> Value {
    let mut budget = OrderedMap::new();
    budget.insert("target_rebuild_ms", json!(TARGET_REBUILD_MS));
    budget.insert("last_rebuild_duration_ms", Value::Null);
    budget.insert("last_items_per_second", Value::Null);
    budget.insert("within_target", Value::Null);
    let completed = operations
        .iter()
        .find(|(status, _)| status == "completed")
        .map(|(_, value)| value);
    let Some(operation) = completed else {
        return serde_json::to_value(budget).unwrap_or(Value::Null);
    };
    let duration = operation
        .get("metadata")
        .and_then(|metadata| metadata.get("duration_ms"))
        .and_then(Value::as_f64);
    let Some(duration) = duration.filter(|value| *value > 0.0) else {
        return serde_json::to_value(budget).unwrap_or(Value::Null);
    };
    let items_total = operation
        .get("items_total")
        .and_then(Value::as_i64)
        .unwrap_or(0);
    budget.insert("last_rebuild_duration_ms", json!(round_to(duration, 2)));
    budget.insert(
        "last_items_per_second",
        json!(round_to(items_total as f64 / (duration / 1000.0), 2)),
    );
    budget.insert("within_target", json!(duration <= TARGET_REBUILD_MS as f64));
    serde_json::to_value(budget).unwrap_or(Value::Null)
}

/// `embedder_fingerprint_status()` — current, recorded, and whether they differ.
fn embedder_status(conn: &Connection, model_id: &str, dim: i64) -> Value {
    let mut current = OrderedMap::new();
    current.insert("model_id", json!(model_id));
    current.insert("dim", json!(dim));

    let recorded: Option<Value> = conn
        .query_row(
            "SELECT value FROM graph_meta WHERE key='embedder_fingerprint'",
            [],
            |row| row.get::<_, Option<String>>(0),
        )
        .ok()
        .flatten()
        .and_then(|raw| {
            let parsed = safe_loads(Some(raw.as_str()));
            let recorded_id = parsed.get("model_id").and_then(Value::as_str)?;
            if recorded_id.is_empty() {
                return None;
            }
            let recorded_dim = parsed
                .get("dim")
                .and_then(|value| value.as_i64().or_else(|| value.as_str()?.parse().ok()))
                .unwrap_or(0);
            let mut record = OrderedMap::new();
            record.insert("model_id", json!(recorded_id));
            record.insert("dim", json!(recorded_dim));
            Some(serde_json::to_value(record).unwrap_or(Value::Null))
        });

    let stale = recorded.as_ref().is_some_and(|record| {
        record.get("model_id").and_then(Value::as_str) != Some(model_id)
            || record.get("dim").and_then(Value::as_i64) != Some(dim)
    });

    let mut status = OrderedMap::new();
    status.insert(
        "current",
        serde_json::to_value(current).unwrap_or(Value::Null),
    );
    status.insert("recorded", recorded.unwrap_or(Value::Null));
    status.insert("stale_embedder", json!(stale));
    serde_json::to_value(status).unwrap_or(Value::Null)
}

/// The `storage` block, including this runtime's honest capability report.
fn storage_block(conn: &Connection, model_id: &str, dim: i64) -> Value {
    let db_path = conn.path().map(str::to_string).unwrap_or_default();

    let mut metadata = OrderedMap::new();
    metadata.insert("db_path", json!(db_path));
    metadata.insert("sqlite_vec_loaded", json!(false));
    metadata.insert("sqlite_vec_ann_available", json!(false));
    metadata.insert("vector_mode", json!("fallback"));
    metadata.insert("degraded", json!(true));
    metadata.insert("honest_fallback", json!(HONEST_FALLBACK));

    let mut engine = OrderedMap::new();
    engine.insert("engine", json!("sqlite"));
    engine.insert("available", json!(true));
    engine.insert("reason", json!(SQLITE_VEC_REASON));
    engine.insert("vector_backend", json!("bruteforce-cosine"));
    engine.insert("vector_available", json!(true));
    engine.insert("backup_restore", json!(true));
    engine.insert("migrations", json!(true));
    engine.insert("encrypted_archives", json!(true));
    engine.insert(
        "metadata",
        serde_json::to_value(metadata).unwrap_or(Value::Null),
    );

    let mut storage = OrderedMap::new();
    storage.insert("db_path", json!(db_path));
    storage.insert("backend", json!("sqlite"));
    storage.insert("embedding_model", json!(model_id));
    storage.insert("embedding_dim", json!(dim));
    storage.insert("fts_enabled", json!(fts_enabled(conn)));
    storage.insert(
        "engine",
        serde_json::to_value(engine).unwrap_or(Value::Null),
    );
    storage.insert("vector_search_backend", json!("bruteforce-cosine"));
    storage.insert("vector_search_mode", json!("fallback"));
    storage.insert("sqlite_vec_ann_available", json!(false));
    storage.insert("vector_index", vector_index_selection());
    serde_json::to_value(storage).unwrap_or(Value::Null)
}

/// Whether the trigram FTS5 keyword index exists in this store.
fn fts_enabled(conn: &Connection) -> bool {
    conn.query_row(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='node_fts'",
        [],
        |row| row.get::<_, i64>(0),
    )
    .map(|count| count > 0)
    .unwrap_or(false)
}

/// `resolve_vector_index()` — the backend choice and any honest substitution.
///
/// `hnsw` is a Python optional extra (`hnswlib`), so the gateway can never
/// honour it; the substitution is reported the same way Python reports it on a
/// machine without the package installed.
pub(crate) fn vector_index_selection() -> Value {
    let requested_raw = std::env::var(VECTOR_INDEX_ENV).unwrap_or_default();
    let requested = requested_raw.trim().to_ascii_lowercase();
    let requested = if requested.is_empty() {
        DEFAULT_VECTOR_INDEX.to_string()
    } else {
        requested
    };
    let (name, detail): (&str, Option<String>) = match requested.as_str() {
        "brute" => ("brute", None),
        "quantized" => ("quantized", None),
        "hnsw" => (
            "brute",
            Some("hnswlib is a Python optional extra and is not available to the Rust gateway; falling back to the exact brute-force scan".into()),
        ),
        other => (
            "brute",
            Some(format!(
                "unknown vector index '{other}'; falling back to the exact brute-force scan"
            )),
        ),
    };
    let backend = match name {
        "quantized" => "int8-quantized-cosine",
        _ => "bruteforce-cosine",
    };
    let mut selection = OrderedMap::new();
    selection.insert("requested", json!(requested));
    selection.insert("backend", json!(backend));
    selection.insert("name", json!(name));
    selection.insert("approx", json!(name == "quantized"));
    selection.insert("exhaustive", json!(true));
    selection.insert("honored", json!(requested == name));
    selection.insert("detail", detail.map(Value::String).unwrap_or(Value::Null));
    serde_json::to_value(selection).unwrap_or(Value::Null)
}
