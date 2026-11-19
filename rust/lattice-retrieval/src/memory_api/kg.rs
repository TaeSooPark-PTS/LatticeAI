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

use std::collections::{BTreeMap, BTreeSet};

use lattice_auth::OrderedMap;
use lattice_core::{clean_text, safe_loads, sha256_text, CoreError};
use rusqlite::Connection;
use serde_json::{Map, Value};

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
/// `"personal"` also matches NULL/blank `workspace_id`.
fn scope_sql(allowed: Option<&BTreeSet<String>>) -> Option<(String, Vec<String>)> {
    lattice_core::workspace_membership_sql(allowed, false)
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

#[path = "kg_vector_status.rs"]
mod kg_vector_status;
pub use kg_vector_status::{index_status, vector_freshness_breakdown, vector_freshness_summary};

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
