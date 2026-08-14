//! The knowledge-graph reads the brain families ask for by name.
//!
//! `KnowledgeGraphStore` is a twelve-mixin class; these are the six methods the
//! memory, brain-intelligence, chronicle, command-centre and evidence surfaces
//! actually call — `stats`, `graph`, `index_status`, `vector_freshness`,
//! `vector_freshness_breakdown` and `get_node` — ported over the read-only
//! connection. `search` and `vector_search` are **not** here: this crate
//! already owns proven-equal ports of both ([`crate::keyword::search`],
//! [`crate::vector::vector_search`]) and a second copy would be the divergence
//! the parity harness exists to prevent.
//!
//! Two shapes are contracts rather than details:
//!
//! * `graph()` emits edges keyed `from`/`to`. The Brain Intelligence sampler
//!   re-keys them to `source`/`target` and *keeps both*, and its health report
//!   reads `source or from_node` — so an edge that lost its `from` key silently
//!   scores every node an orphan. The keys are reproduced exactly.
//! * `index_status()`'s `scale.coverage_ratio` is the only reading that lets
//!   `embedding_coverage` be graded at all; its absence is what makes a store
//!   report `unavailable` instead of a flattering 100.

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

use lattice_auth::OrderedMap;
use lattice_core::{clean_text, safe_loads, CoreError};
use rusqlite::Connection;
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

/// `_kg_constants.GRAPH_SCHEMA_VERSION`.
pub const GRAPH_SCHEMA_VERSION: i64 = 1;
/// `schema.KG_SCHEMA_V2_VERSION`.
pub const KG_SCHEMA_V2_VERSION: i64 = 2;
/// `fingerprint._EMBEDDER_FINGERPRINT_KEY`.
pub const EMBEDDER_FINGERPRINT_KEY: &str = "embedder_fingerprint";

/// `_GraphViewMixin._GRAPH_VISIBLE_TYPES`, in declaration order.
pub const GRAPH_VISIBLE_TYPES: [&str; 24] = [
    "Computer",
    "Drive",
    "Folder",
    "File",
    "Chat",
    "Document",
    "CodeFile",
    "Spreadsheet",
    "SlideDeck",
    "Image",
    "ImageText",
    "Audio",
    "Concept",
    "Person",
    "Error",
    "Code",
    "Feature",
    "Task",
    "Decision",
    "Source",
    "Repository",
    "Meeting",
    "Organization",
    "Workflow",
];

/// `_workspace_scope_sql` — `None` is "no scoping", an empty set is "nothing".
fn scope_sql(allowed: Option<&BTreeSet<String>>) -> Option<(String, Vec<String>)> {
    let allowed = allowed?;
    let names: Vec<String> = allowed
        .iter()
        .filter(|value| !value.is_empty())
        .cloned()
        .collect();
    if names.is_empty() {
        return Some(("0".to_string(), Vec::new()));
    }
    let placeholders = vec!["?"; names.len()].join(",");
    Some((format!("workspace_id IN ({placeholders})"), names))
}

fn count_by(conn: &Connection, sql: &str, params: &[&str]) -> Result<OrderedMap, CoreError> {
    let mut stmt = conn.prepare(sql)?;
    let mut rows = stmt.query(rusqlite::params_from_iter(params.iter()))?;
    // BTreeMap first: Python's `GROUP BY type` answers in SQLite's own order,
    // and the committed fixture shows it sorted, which is what a `TEXT` group
    // key yields. Ordering it here makes the answer independent of the plan.
    let mut counts: BTreeMap<String, i64> = BTreeMap::new();
    while let Some(row) = rows.next()? {
        let key: Option<String> = row.get(0)?;
        counts.insert(key.unwrap_or_default(), row.get(1)?);
    }
    let mut out = OrderedMap::new();
    for (key, value) in counts {
        out.insert(key, Value::from(value));
    }
    Ok(out)
}

fn scalar(conn: &Connection, sql: &str) -> i64 {
    conn.query_row(sql, [], |row| row.get::<_, i64>(0))
        .unwrap_or(0)
}

/// `KnowledgeGraphReadsMixin.stats`.
pub fn stats(
    conn: &Connection,
    db_path: &str,
    allowed: Option<&BTreeSet<String>>,
) -> Result<OrderedMap, CoreError> {
    let (nt, et) = lattice_core::read::read_tables(conn);
    let scope = scope_sql(allowed);
    let (node_counts, edge_counts, local_sources, local_file_status) = match &scope {
        None => (
            count_by(
                conn,
                &format!("SELECT type, COUNT(*) FROM {nt} GROUP BY type"),
                &[],
            )?,
            count_by(
                conn,
                &format!("SELECT type, COUNT(*) FROM {et} GROUP BY type"),
                &[],
            )?,
            scalar(conn, "SELECT COUNT(*) FROM knowledge_sources"),
            count_by(
                conn,
                "SELECT status, COUNT(*) FROM local_file_index GROUP BY status",
                &[],
            )?,
        ),
        Some((predicate, params)) => {
            let visible = format!("SELECT id FROM nodes_v2 WHERE {predicate}");
            let refs: Vec<&str> = params.iter().map(String::as_str).collect();
            let doubled: Vec<&str> = refs.iter().chain(refs.iter()).copied().collect();
            (
                count_by(
                    conn,
                    &format!(
                        "SELECT type, COUNT(*) FROM {nt} WHERE id IN ({visible}) GROUP BY type"
                    ),
                    &refs,
                )?,
                count_by(
                    conn,
                    &format!(
                        "SELECT type, COUNT(*) FROM {et} \
                         WHERE from_node IN ({visible}) AND to_node IN ({visible}) GROUP BY type"
                    ),
                    &doubled,
                )?,
                // Machine-local ingestion bookkeeping has no workspace column,
                // so a scoped read reports none rather than guessing.
                0,
                OrderedMap::new(),
            )
        }
    };
    let mut out = OrderedMap::new();
    out.insert("db_path", Value::String(db_path.to_string()));
    out.insert("schema_version", Value::from(GRAPH_SCHEMA_VERSION));
    out.insert("v2_schema_available", Value::Bool(true));
    out.insert(
        "nodes",
        serde_json::to_value(&node_counts).unwrap_or(Value::Null),
    );
    out.insert(
        "edges",
        serde_json::to_value(&edge_counts).unwrap_or(Value::Null),
    );
    out.insert("local_sources", Value::from(local_sources));
    out.insert(
        "local_file_status",
        serde_json::to_value(&local_file_status).unwrap_or(Value::Null),
    );
    out.insert(
        "v2",
        serde_json::to_value(&v2_stats(conn, &scope)?).unwrap_or(Value::Null),
    );
    Ok(out)
}

fn v2_stats(
    conn: &Connection,
    scope: &Option<(String, Vec<String>)>,
) -> Result<OrderedMap, CoreError> {
    let dim = lattice_core::LocalEmbeddingModel::from_env().dim() as i64;
    let (nodes, edges, by_node, by_edge) = match scope {
        None => (
            scalar(conn, "SELECT COUNT(*) FROM nodes_v2"),
            scalar(conn, "SELECT COUNT(*) FROM edges_v2"),
            count_by(
                conn,
                "SELECT type, COUNT(*) FROM nodes_v2 GROUP BY type",
                &[],
            )?,
            count_by(
                conn,
                "SELECT type, COUNT(*) FROM edges_v2 GROUP BY type",
                &[],
            )?,
        ),
        Some((predicate, params)) => {
            let visible = format!("SELECT id FROM nodes_v2 WHERE {predicate}");
            let refs: Vec<&str> = params.iter().map(String::as_str).collect();
            let doubled: Vec<&str> = refs.iter().chain(refs.iter()).copied().collect();
            let by_node = count_by(
                conn,
                &format!("SELECT type, COUNT(*) FROM nodes_v2 WHERE {predicate} GROUP BY type"),
                &refs,
            )?;
            let by_edge = count_by(
                conn,
                &format!(
                    "SELECT type, COUNT(*) FROM edges_v2 \
                     WHERE source IN ({visible}) AND target IN ({visible}) GROUP BY type"
                ),
                &doubled,
            )?;
            // `_scoped_v2_stats` overwrites the totals with the scoped sums.
            let node_total = by_node.iter().filter_map(|(_, v)| v.as_i64()).sum();
            let edge_total = by_edge.iter().filter_map(|(_, v)| v.as_i64()).sum();
            (node_total, edge_total, by_node, by_edge)
        }
    };
    let mut out = OrderedMap::new();
    out.insert("schema_version", Value::from(KG_SCHEMA_V2_VERSION));
    out.insert("embed_dim", Value::from(dim));
    out.insert("nodes", Value::from(nodes));
    out.insert("edges", Value::from(edges));
    out.insert(
        "by_node_type",
        serde_json::to_value(&by_node).unwrap_or(Value::Null),
    );
    out.insert(
        "by_edge_type",
        serde_json::to_value(&by_edge).unwrap_or(Value::Null),
    );
    Ok(out)
}

/// One `graph()` slice: the visible node window and the edges inside it.
#[derive(Debug, Clone, Default)]
pub struct GraphSlice {
    /// Nodes, `updated_at DESC, id ASC`, capped at the sample limit.
    pub nodes: Vec<Value>,
    /// Edges keyed `from`/`to`, both endpoints inside `nodes`.
    pub edges: Vec<Value>,
}

/// `_GraphViewMixin.graph`, without the topic-metric decoration.
///
/// The decoration (`degree`, topic mention counts) is rendered by
/// `/knowledge-graph/graph`, which belongs to another package; every consumer
/// in these six families reads only `nodes` and `edges`, so producing the rest
/// here would be dead weight that still had to be kept true.
pub fn graph_slice(
    conn: &Connection,
    limit: i64,
    allowed: Option<&BTreeSet<String>>,
) -> Result<GraphSlice, CoreError> {
    let limit = limit.clamp(1, 2000);
    let (nt, et) = lattice_core::read::read_tables(conn);
    let visible = GRAPH_VISIBLE_TYPES
        .iter()
        .map(|kind| format!("'{kind}'"))
        .collect::<Vec<_>>()
        .join(",");
    let mut nodes: Vec<Value> = Vec::new();
    {
        let sql = format!(
            "SELECT id, type, title, summary, metadata_json, updated_at FROM {nt} \
             WHERE type IN ({visible}) ORDER BY updated_at DESC, id ASC LIMIT ?"
        );
        let mut stmt = conn.prepare(&sql)?;
        let mut rows = stmt.query([limit])?;
        while let Some(row) = rows.next()? {
            let mut node = OrderedMap::new();
            node.insert("id", lattice_core::sql_json(row.get_ref("id")?));
            node.insert("type", lattice_core::sql_json(row.get_ref("type")?));
            node.insert("title", lattice_core::sql_json(row.get_ref("title")?));
            node.insert("summary", lattice_core::sql_json(row.get_ref("summary")?));
            node.insert(
                "metadata",
                Value::Object(safe_loads(
                    row.get::<_, Option<String>>("metadata_json")?.as_deref(),
                )),
            );
            node.insert(
                "updated_at",
                lattice_core::sql_json(row.get_ref("updated_at")?),
            );
            nodes.push(serde_json::to_value(&node).unwrap_or(Value::Null));
        }
    }
    let mut edges: Vec<Value> = Vec::new();
    if !nodes.is_empty() {
        let window = format!(
            "SELECT id FROM {nt} WHERE type IN ({visible}) ORDER BY updated_at DESC, id ASC LIMIT ?"
        );
        let sql = format!(
            "SELECT id, from_node, to_node, type, weight, metadata_json FROM {et} \
             WHERE from_node IN ({window}) AND to_node IN ({window}) \
             ORDER BY weight DESC, created_at DESC, id ASC"
        );
        let mut stmt = conn.prepare(&sql)?;
        let mut rows = stmt.query([limit, limit])?;
        while let Some(row) = rows.next()? {
            let mut edge = OrderedMap::new();
            edge.insert("id", lattice_core::sql_json(row.get_ref("id")?));
            edge.insert("from", lattice_core::sql_json(row.get_ref("from_node")?));
            edge.insert("to", lattice_core::sql_json(row.get_ref("to_node")?));
            edge.insert("type", lattice_core::sql_json(row.get_ref("type")?));
            edge.insert("weight", lattice_core::sql_json(row.get_ref("weight")?));
            edge.insert(
                "metadata",
                Value::Object(safe_loads(
                    row.get::<_, Option<String>>("metadata_json")?.as_deref(),
                )),
            );
            edges.push(serde_json::to_value(&edge).unwrap_or(Value::Null));
        }
    }
    if allowed.is_some() {
        nodes = lattice_core::filter_scoped_nodes(conn, nodes, allowed, false, |node| {
            node.get("id")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string()
        })?;
        let kept: BTreeSet<String> = nodes
            .iter()
            .filter_map(|node| node.get("id").and_then(Value::as_str))
            .map(str::to_string)
            .collect();
        edges.retain(|edge| {
            let from = edge.get("from").and_then(Value::as_str).unwrap_or_default();
            let to = edge.get("to").and_then(Value::as_str).unwrap_or_default();
            kept.contains(from) && kept.contains(to)
        });
    }
    Ok(GraphSlice { nodes, edges })
}

// ── the vector index's honesty surface ──────────────────────────────────────

/// One embeddable item, exactly as `_iter_vector_source_items` yields it.
pub(crate) struct SourceItem {
    pub(crate) item_id: String,
    pub(crate) item_type: &'static str,
    pub(crate) source_node: String,
    pub(crate) text_hash: String,
    pub(crate) metadata: Map<String, Value>,
}

/// `_vector_text_for_node` — title, summary, and eight metadata fields.
pub(crate) fn vector_text_for_node(
    title: &str,
    summary: &str,
    metadata: &Map<String, Value>,
) -> String {
    let mut parts: Vec<String> = Vec::new();
    for key in [
        "filename",
        "relative_path",
        "file_path",
        "conversation_id",
        "source",
        "category",
        "ext",
        "role",
    ] {
        match metadata.get(key) {
            Some(Value::String(text)) if !text.is_empty() => parts.push(text.clone()),
            Some(Value::Number(number)) => parts.push(number.to_string()),
            Some(Value::Bool(true)) => parts.push("True".to_string()),
            _ => {}
        }
    }
    clean_text(&format!("{title}\n{summary}\n{}", parts.join(" ")))
}

fn sha256_text(text: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(text.as_bytes());
    format!("{:x}", hasher.finalize())
}

pub(crate) fn source_items(conn: &Connection) -> Result<Vec<SourceItem>, CoreError> {
    let mut items = Vec::new();
    {
        let mut stmt = conn.prepare(
            "SELECT id, type, title, summary, metadata_json FROM nodes \
             WHERE type <> 'Chunk' ORDER BY updated_at DESC, id ASC",
        )?;
        let mut rows = stmt.query([])?;
        while let Some(row) = rows.next()? {
            let metadata = safe_loads(row.get::<_, Option<String>>("metadata_json")?.as_deref());
            let title: Option<String> = row.get("title")?;
            let summary: Option<String> = row.get("summary")?;
            let text = vector_text_for_node(
                title.as_deref().unwrap_or_default(),
                summary.as_deref().unwrap_or_default(),
                &metadata,
            );
            if text.is_empty() {
                continue;
            }
            let mut carried = Map::new();
            carried.insert(
                "node_type".to_string(),
                lattice_core::sql_json(row.get_ref("type")?),
            );
            for (key, value) in metadata {
                carried.insert(key, value);
            }
            let id: String = row.get("id")?;
            items.push(SourceItem {
                item_id: id.clone(),
                item_type: "node",
                source_node: id,
                text_hash: sha256_text(&clean_text(&text)),
                metadata: carried,
            });
        }
    }
    {
        let mut stmt = conn.prepare(
            "SELECT c.id, c.source_node AS parent_source_node, c.text, c.metadata_json \
             FROM chunks c JOIN nodes n ON n.id=c.id ORDER BY c.created_at DESC, c.id ASC",
        )?;
        let mut rows = stmt.query([])?;
        while let Some(row) = rows.next()? {
            let mut metadata =
                safe_loads(row.get::<_, Option<String>>("metadata_json")?.as_deref());
            let raw: Option<String> = row.get("text")?;
            let text = clean_text(raw.as_deref().unwrap_or_default());
            if text.is_empty() {
                continue;
            }
            metadata.insert(
                "parent_source_node".to_string(),
                lattice_core::sql_json(row.get_ref("parent_source_node")?),
            );
            let id: String = row.get("id")?;
            items.push(SourceItem {
                item_id: id.clone(),
                item_type: "chunk",
                source_node: id,
                text_hash: sha256_text(&clean_text(&text)),
                metadata,
            });
        }
    }
    Ok(items)
}

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
    let dim = model.dim() as i64;
    let model_id = model.model_id().to_string();
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

/// `KnowledgeGraphReadsMixin.get_node` — the shape evidence actions read.
pub fn get_node(conn: &Connection, node_id: &str) -> Result<Option<Value>, CoreError> {
    let (nt, _) = lattice_core::read::read_tables(conn);
    let sql = format!(
        "SELECT id, type, title, summary, metadata_json, created_at, updated_at FROM {nt} WHERE id=?"
    );
    let mut stmt = conn.prepare(&sql)?;
    let mut rows = stmt.query([node_id])?;
    let Some(row) = rows.next()? else {
        return Ok(None);
    };
    let mut node = OrderedMap::new();
    node.insert("id", lattice_core::sql_json(row.get_ref("id")?));
    node.insert("type", lattice_core::sql_json(row.get_ref("type")?));
    node.insert("title", lattice_core::sql_json(row.get_ref("title")?));
    node.insert("summary", lattice_core::sql_json(row.get_ref("summary")?));
    node.insert(
        "metadata",
        Value::Object(safe_loads(
            row.get::<_, Option<String>>("metadata_json")?.as_deref(),
        )),
    );
    node.insert(
        "created_at",
        lattice_core::sql_json(row.get_ref("created_at")?),
    );
    node.insert(
        "updated_at",
        lattice_core::sql_json(row.get_ref("updated_at")?),
    );
    Ok(Some(serde_json::to_value(&node).unwrap_or(Value::Null)))
}
