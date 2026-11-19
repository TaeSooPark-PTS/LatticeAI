//! Vector index health, embedder fingerprint status, and freshness breakdown.

use std::collections::{BTreeMap, BTreeSet};

use lattice_auth::OrderedMap;
use lattice_core::{safe_loads, CoreError};
use rusqlite::Connection;
use serde_json::Value;

use super::{count_by, source_items, SourceItem, EMBEDDER_FINGERPRINT_KEY};

/// `_embedder_fingerprint_record` + `embedder_fingerprint_status`.
fn embedder_status(conn: &Connection, model_id: &str, dim: i64) -> OrderedMap {
    let recorded = conn
        .query_row(
            "SELECT value FROM graph_meta WHERE key=?",
            [EMBEDDER_FINGERPRINT_KEY],
            |row| row.get::<_, Option<String>>(0),
        )
        .ok()
        .flatten()
        .map(|raw| safe_loads(Some(raw.as_str())))
        .filter(|payload| {
            payload
                .get("model_id")
                .and_then(Value::as_str)
                .is_some_and(|value| !value.is_empty())
        })
        .map(|payload| {
            let mut record = OrderedMap::new();
            record.insert(
                "model_id",
                Value::String(
                    payload
                        .get("model_id")
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        .to_string(),
                ),
            );
            record.insert(
                "dim",
                Value::from(payload.get("dim").and_then(Value::as_i64).unwrap_or(0)),
            );
            record
        });
    let mut current = OrderedMap::new();
    current.insert("model_id", Value::String(model_id.to_string()));
    current.insert("dim", Value::from(dim));
    let stale = recorded.as_ref().is_some_and(|record| {
        record.get("model_id") != Some(&Value::String(model_id.to_string()))
            || record.get("dim") != Some(&Value::from(dim))
    });
    let mut out = OrderedMap::new();
    out.insert(
        "current",
        serde_json::to_value(&current).unwrap_or(Value::Null),
    );
    out.insert(
        "recorded",
        recorded
            .as_ref()
            .map(|record| serde_json::to_value(record).unwrap_or(Value::Null))
            .unwrap_or(Value::Null),
    );
    out.insert("stale_embedder", Value::Bool(stale));
    out
}

/// `_VectorStatusMixin.index_status`, on the numbers every caller reads.
///
/// **Stated narrowing.** `storage.engine` in Python is the storage engine's own
/// capability object and `storage.vector_index` is the in-process backend
/// selection; both describe the *Python* process. This port reports the fields
/// it can measure from the store itself and omits the two that would otherwise
/// be a Rust process describing a Python one. Everything the health report, the
/// freshness chip and the memory manager read — `status`, `embedder`,
/// `source_items`, `indexed_items`, `ready/missing/stale/pending_items`,
/// `by_item_type`, `scale` and `operations` — is complete.
pub fn index_status(conn: &Connection, db_path: &str) -> Result<OrderedMap, CoreError> {
    let model = lattice_core::LocalEmbeddingModel::from_env();
    let explicit_contract: Option<lattice_core::embedding_contract::EmbeddingIndexContract> = conn
        .query_row(
            "SELECT value FROM graph_meta WHERE key='embedding_index_contract'",
            [],
            |row| row.get::<_, String>(0),
        )
        .ok()
        .and_then(|raw| serde_json::from_str(&raw).ok());
    let (model_id, dim) = match explicit_contract {
        Some(contract)
            if contract.is_semantic()
                && !contract.model_id.is_empty()
                && contract.dimension > 0 =>
        {
            (contract.model_id, contract.dimension as i64)
        }
        _ => (model.model_id().to_string(), model.dim() as i64),
    };
    let vector_counts = count_by(
        conn,
        "SELECT item_type, COUNT(*) FROM vector_embeddings GROUP BY item_type",
        &[],
    )?;
    let items = source_items(conn)?;
    struct Indexed {
        text_hash: String,
        dim: i64,
        model: String,
    }
    let mut indexed: BTreeMap<String, Indexed> = BTreeMap::new();
    {
        let mut stmt = conn.prepare(
            "SELECT item_id, text_hash, embedding_dim, embedding_model FROM vector_embeddings",
        )?;
        let mut rows = stmt.query([])?;
        while let Some(row) = rows.next()? {
            indexed.insert(
                row.get("item_id")?,
                Indexed {
                    text_hash: row
                        .get::<_, Option<String>>("text_hash")?
                        .unwrap_or_default(),
                    dim: row
                        .get::<_, Option<i64>>("embedding_dim")?
                        .unwrap_or_default(),
                    model: row
                        .get::<_, Option<String>>("embedding_model")?
                        .unwrap_or_default(),
                },
            );
        }
    }
    let (mut missing, mut stale, mut ready) = (0_i64, 0_i64, 0_i64);
    let mut backlog_by_type: BTreeMap<String, i64> = BTreeMap::new();
    let mut backlog_reasons: BTreeMap<String, i64> = BTreeMap::new();
    let mut backlog_samples: Vec<Value> = Vec::new();
    let add_backlog = |item: &SourceItem,
                       reason: &str,
                       by_type: &mut BTreeMap<String, i64>,
                       reasons: &mut BTreeMap<String, i64>,
                       samples: &mut Vec<Value>| {
        *by_type.entry(item.item_type.to_string()).or_default() += 1;
        *reasons.entry(reason.to_string()).or_default() += 1;
        if samples.len() >= 20 {
            return;
        }
        let mut carried = OrderedMap::new();
        for key in [
            "node_type",
            "source",
            "conversation_id",
            "parent_source_node",
        ] {
            if let Some(value) = item.metadata.get(key) {
                carried.insert(key, value.clone());
            }
        }
        let mut sample = OrderedMap::new();
        sample.insert("item_id", Value::String(item.item_id.clone()));
        sample.insert("item_type", Value::String(item.item_type.to_string()));
        sample.insert("source_node", Value::String(item.source_node.clone()));
        sample.insert("reason", Value::String(reason.to_string()));
        sample.insert(
            "metadata",
            serde_json::to_value(&carried).unwrap_or(Value::Null),
        );
        samples.push(serde_json::to_value(&sample).unwrap_or(Value::Null));
    };
    for item in &items {
        match indexed.get(&item.item_id) {
            None => {
                missing += 1;
                add_backlog(
                    item,
                    "missing_vector",
                    &mut backlog_by_type,
                    &mut backlog_reasons,
                    &mut backlog_samples,
                );
            }
            Some(row)
                if row.text_hash != item.text_hash || row.dim != dim || row.model != model_id =>
            {
                stale += 1;
                let reason = if row.model != model_id {
                    "model_changed"
                } else if row.dim != dim {
                    "dimension_changed"
                } else {
                    "text_changed"
                };
                add_backlog(
                    item,
                    reason,
                    &mut backlog_by_type,
                    &mut backlog_reasons,
                    &mut backlog_samples,
                );
            }
            Some(_) => ready += 1,
        }
    }
    let pending = missing + stale;
    let source_ids: BTreeSet<&String> = items.iter().map(|item| &item.item_id).collect();
    let orphaned = indexed.keys().filter(|id| !source_ids.contains(id)).count() as i64;
    let coverage_ratio = if items.is_empty() {
        1.0
    } else {
        lattice_core::pytext::round_to(ready as f64 / items.len() as f64, 6)
    };
    let operations = recent_operations(conn)?;
    let embedder = embedder_status(conn, &model_id, dim);
    let stale_embedder = embedder.get("stale_embedder") == Some(&Value::Bool(true));

    let mut scale = OrderedMap::new();
    scale.insert("version", Value::from(1));
    scale.insert("coverage_ratio", Value::from(coverage_ratio));
    scale.insert(
        "coverage_percent",
        Value::from(lattice_core::pytext::round_to(coverage_ratio * 100.0, 2)),
    );
    scale.insert("source_items", Value::from(items.len() as i64));
    scale.insert("ready_items", Value::from(ready));
    scale.insert("pending_items", Value::from(pending));
    scale.insert("missing_items", Value::from(missing));
    scale.insert("stale_items", Value::from(stale));
    scale.insert("orphaned_items", Value::from(orphaned));
    scale.insert("backlog_by_item_type", ordered_counts(&backlog_by_type));
    scale.insert("backlog_reasons", ordered_counts(&backlog_reasons));
    scale.insert("backlog_samples", Value::Array(backlog_samples));
    scale.insert("incremental_reindex_recommended", Value::Bool(pending > 0));
    scale.insert(
        "full_rebuild_recommended",
        Value::Bool(orphaned > 0 || stale_embedder),
    );
    scale.insert("latency_budget", latency_budget(&operations));

    let mut storage = OrderedMap::new();
    storage.insert("db_path", Value::String(db_path.to_string()));
    storage.insert("backend", Value::String("sqlite".to_string()));
    storage.insert("embedding_model", Value::String(model_id.clone()));
    storage.insert("embedding_dim", Value::from(dim));
    storage.insert(
        "fts_enabled",
        Value::Bool(
            conn.query_row("SELECT 1 FROM node_fts LIMIT 1", [], |_| Ok(()))
                .is_ok(),
        ),
    );

    let mut out = OrderedMap::new();
    out.insert(
        "status",
        Value::String(
            if pending == 0 {
                "ready"
            } else {
                "needs_reindex"
            }
            .to_string(),
        ),
    );
    out.insert(
        "embedder",
        serde_json::to_value(&embedder).unwrap_or(Value::Null),
    );
    out.insert(
        "storage",
        serde_json::to_value(&storage).unwrap_or(Value::Null),
    );
    out.insert("source_items", Value::from(items.len() as i64));
    out.insert(
        "indexed_items",
        Value::from(
            vector_counts
                .iter()
                .filter_map(|(_, v)| v.as_i64())
                .sum::<i64>(),
        ),
    );
    out.insert("ready_items", Value::from(ready));
    out.insert("missing_items", Value::from(missing));
    out.insert("stale_items", Value::from(stale));
    out.insert("pending_items", Value::from(pending));
    out.insert(
        "by_item_type",
        serde_json::to_value(&vector_counts).unwrap_or(Value::Null),
    );
    out.insert("scale", serde_json::to_value(&scale).unwrap_or(Value::Null));
    out.insert("operations", Value::Array(operations));
    Ok(out)
}

fn ordered_counts(counts: &BTreeMap<String, i64>) -> Value {
    let mut out = OrderedMap::new();
    for (key, value) in counts {
        out.insert(key.clone(), Value::from(*value));
    }
    serde_json::to_value(&out).unwrap_or(Value::Null)
}

fn recent_operations(conn: &Connection) -> Result<Vec<Value>, CoreError> {
    let mut stmt = conn.prepare(
        "SELECT id, operation, status, requested_at, started_at, completed_at, \
         items_total, items_indexed, items_skipped, error_message, metadata_json \
         FROM vector_index_operations ORDER BY requested_at DESC, id DESC LIMIT 5",
    )?;
    let mut rows = stmt.query([])?;
    let mut out = Vec::new();
    while let Some(row) = rows.next()? {
        let mut record = OrderedMap::new();
        for key in [
            "id",
            "operation",
            "status",
            "requested_at",
            "started_at",
            "completed_at",
            "items_total",
            "items_indexed",
            "items_skipped",
            "error_message",
        ] {
            record.insert(key, lattice_core::sql_json(row.get_ref(key)?));
        }
        record.insert(
            "metadata",
            Value::Object(safe_loads(
                row.get::<_, Option<String>>("metadata_json")?.as_deref(),
            )),
        );
        out.push(serde_json::to_value(&record).unwrap_or(Value::Null));
    }
    Ok(out)
}

fn latency_budget(operations: &[Value]) -> Value {
    let mut budget = OrderedMap::new();
    budget.insert("target_rebuild_ms", Value::from(10_000));
    budget.insert("last_rebuild_duration_ms", Value::Null);
    budget.insert("last_items_per_second", Value::Null);
    budget.insert("within_target", Value::Null);
    let completed = operations
        .iter()
        .find(|row| row.get("status").and_then(Value::as_str) == Some("completed"));
    if let Some(row) = completed {
        let duration = row
            .get("metadata")
            .and_then(|meta| meta.get("duration_ms"))
            .and_then(Value::as_f64);
        if let Some(duration) = duration.filter(|value| *value > 0.0) {
            let total = row.get("items_total").and_then(Value::as_i64).unwrap_or(0);
            budget.insert(
                "last_rebuild_duration_ms",
                Value::from(lattice_core::pytext::round_to(duration, 2)),
            );
            budget.insert(
                "last_items_per_second",
                Value::from(lattice_core::pytext::round_to(
                    total as f64 / (duration / 1000.0),
                    2,
                )),
            );
            budget.insert("within_target", Value::Bool(duration <= 10_000.0));
        }
    }
    serde_json::to_value(&budget).unwrap_or(Value::Null)
}

/// `_vector_freshness_summary` over an already-read `index_status`.
pub fn vector_freshness_summary(conn: &Connection, status: &OrderedMap) -> OrderedMap {
    let pending = status
        .get("pending_items")
        .and_then(Value::as_i64)
        .unwrap_or(0);
    let total = status
        .get("source_items")
        .and_then(Value::as_i64)
        .unwrap_or(0);
    let stale_embedder = status
        .get("embedder")
        .and_then(|value| value.get("stale_embedder"))
        .and_then(Value::as_bool)
        .unwrap_or(false);
    if stale_embedder {
        let model = lattice_core::LocalEmbeddingModel::from_env();
        let old_rows: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM vector_embeddings WHERE embedding_model<>? OR embedding_dim<>?",
                rusqlite::params![model.model_id(), model.dim() as i64],
                |row| row.get(0),
            )
            .unwrap_or(0);
        if old_rows > 0 {
            let recorded = status
                .get("embedder")
                .and_then(|value| value.get("recorded"))
                .and_then(|value| value.get("model_id"))
                .and_then(Value::as_str)
                .map(str::to_string)
                .unwrap_or_else(|| "None".to_string());
            return freshness(
                "stale_embedder",
                pending,
                total,
                &format!(
                    "embedding model changed ({recorded} → {}); {old_rows} indexed rows still \
                     use the previous model — run a full vector index rebuild",
                    model.model_id()
                ),
            );
        }
    }
    if pending > 0 {
        return freshness(
            "pending",
            pending,
            total,
            &format!("{pending} of {total} items are missing or stale in the vector index"),
        );
    }
    let detail = if total > 0 {
        "vector index is up to date"
    } else {
        "vector index is empty (no indexable items yet)"
    };
    freshness("ready", 0, total, detail)
}

fn freshness(status: &str, pending: i64, total: i64, detail: &str) -> OrderedMap {
    let mut out = OrderedMap::new();
    out.insert("status", Value::String(status.to_string()));
    out.insert("pending_items", Value::from(pending));
    out.insert("total_items", Value::from(total));
    out.insert("detail", Value::String(detail.to_string()));
    out
}

/// `vector_freshness_breakdown` — the four numbers behind the chip.
pub fn vector_freshness_breakdown(
    conn: &Connection,
    status: &OrderedMap,
    summary: &OrderedMap,
) -> OrderedMap {
    let get = |map: &OrderedMap, key: &str| map.get(key).and_then(Value::as_i64).unwrap_or(0);
    let mut out = OrderedMap::new();
    out.insert(
        "status",
        summary.get("status").cloned().unwrap_or(Value::Null),
    );
    out.insert(
        "detail",
        summary.get("detail").cloned().unwrap_or(Value::Null),
    );
    out.insert("embedded", Value::from(get(status, "ready_items")));
    out.insert("pending", Value::from(get(summary, "pending_items")));
    out.insert("missing", Value::from(get(status, "missing_items")));
    out.insert("stale", Value::from(get(status, "stale_items")));
    out.insert("total", Value::from(get(summary, "total_items")));
    // `VectorEmbedQueue.available` is true whenever the store has a database,
    // which it does here by construction; `None` stays reserved for a queue
    // with nowhere to persist rather than standing in for an empty backlog.
    let queued: Option<i64> = conn
        .query_row(
            "SELECT COUNT(*) FROM vector_jobs WHERE status='pending'",
            [],
            |row| row.get(0),
        )
        .ok();
    out.insert("queued", queued.map(Value::from).unwrap_or(Value::Null));
    out
}
