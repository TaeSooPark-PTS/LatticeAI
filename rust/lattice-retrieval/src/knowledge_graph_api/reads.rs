//! Knowledge-graph SQL reads used by the WP-R6 routes.

use lattice_auth::OrderedMap;
use lattice_core::pytext::{round_to, safe_loads};
use lattice_core::read::{column_json, read_tables};
use lattice_core::CoreError;
use rusqlite::Connection;
use serde_json::{json, Value};

use crate::knowledge_graph_api::view::naive_local_iso_seconds;
use crate::knowledge_graph_api::{DEFAULT_EMBED_DIM, GRAPH_SCHEMA_VERSION, KG_SCHEMA_V2_VERSION};
use crate::service::Scope;
use lattice_core::read::filter_scoped_nodes;

/// `_filter_scoped(kg, items, allowed)` — fail-closed v2 scope.
pub fn filter_scoped(
    conn: &Connection,
    items: Vec<Value>,
    scope: &Scope,
) -> Result<Vec<Value>, CoreError> {
    filter_scoped_nodes(
        conn,
        items,
        scope.allowed_workspaces.as_ref(),
        false,
        |item| {
            item.get("id")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string()
        },
    )
}

/// `_workspace_scope_sql`. `None` = no scoping; empty set matches nothing.
pub fn scope_sql(scope: &Scope) -> Option<(String, Vec<String>)> {
    let allowed = scope.allowed_workspaces.as_ref()?;
    let names: Vec<String> = allowed
        .iter()
        .filter(|value| !value.is_empty())
        .cloned()
        .collect();
    let mut clauses: Vec<String> = Vec::new();
    let mut params: Vec<String> = Vec::new();
    if !names.is_empty() {
        let placeholders = vec!["?"; names.len()].join(",");
        clauses.push(format!("workspace_id IN ({placeholders})"));
        params.extend(names);
    }
    if scope.include_legacy_global {
        clauses.push("workspace_id IS NULL".into());
    }
    if clauses.is_empty() {
        return Some(("0".into(), Vec::new()));
    }
    Some((clauses.join(" OR "), params))
}

fn count_by(
    conn: &Connection,
    sql: &str,
    params: &[String],
) -> Result<Vec<(String, i64)>, CoreError> {
    let mut statement = conn.prepare(sql)?;
    let bound = rusqlite::params_from_iter(params.iter());
    let rows = statement.query_map(bound, |row| {
        Ok((
            row.get::<_, Option<String>>(0)?.unwrap_or_default(),
            row.get::<_, i64>(1)?,
        ))
    })?;
    Ok(rows.filter_map(Result::ok).collect())
}

fn as_object(pairs: Vec<(String, i64)>) -> Value {
    let mut map = OrderedMap::new();
    for (key, count) in pairs {
        map.insert(key, json!(count));
    }
    serde_json::to_value(map).unwrap_or(Value::Null)
}

/// `KnowledgeGraphReadsMixin.stats` plus the router's `_scoped_stats`.
pub fn stats(conn: &Connection, scope: &Scope) -> Result<Value, CoreError> {
    let (nodes_table, edges_table) = read_tables(conn);
    let predicate = scope_sql(scope);

    let (node_counts, edge_counts, local_sources, local_file_status) = match &predicate {
        None => {
            let nodes = count_by(
                conn,
                &format!("SELECT type, COUNT(*) AS count FROM {nodes_table} GROUP BY type"),
                &[],
            )?;
            let edges = count_by(
                conn,
                &format!("SELECT type, COUNT(*) AS count FROM {edges_table} GROUP BY type"),
                &[],
            )?;
            let sources: i64 = conn
                .query_row("SELECT COUNT(*) FROM knowledge_sources", [], |row| {
                    row.get(0)
                })
                .unwrap_or(0);
            let files = count_by(
                conn,
                "SELECT status, COUNT(*) AS count FROM local_file_index GROUP BY status",
                &[],
            )
            .unwrap_or_default();
            (nodes, edges, sources, files)
        }
        Some((predicate, params)) => {
            let visible = format!("SELECT id FROM nodes_v2 WHERE {predicate}");
            let nodes = count_by(
                conn,
                &format!(
                    "SELECT type, COUNT(*) AS count FROM {nodes_table} \
                     WHERE id IN ({visible}) GROUP BY type"
                ),
                params,
            )?;
            let mut doubled = params.clone();
            doubled.extend(params.clone());
            let edges = count_by(
                conn,
                &format!(
                    "SELECT type, COUNT(*) AS count FROM {edges_table} \
                     WHERE from_node IN ({visible}) AND to_node IN ({visible}) GROUP BY type"
                ),
                &doubled,
            )?;
            // Local sources and the file index carry no workspace column. They
            // are machine-local ingestion bookkeeping, not another tenant's
            // content — but they are not this caller's scope either, so a
            // scoped read reports none rather than guessing.
            (nodes, edges, 0, Vec::new())
        }
    };

    let mut payload = OrderedMap::new();
    payload.insert("db_path", json!(database_path(conn)));
    payload.insert("schema_version", json!(GRAPH_SCHEMA_VERSION));
    payload.insert("v2_schema_available", json!(true));
    payload.insert("nodes", as_object(node_counts));
    payload.insert("edges", as_object(edge_counts));
    payload.insert("local_sources", json!(local_sources));
    payload.insert("local_file_status", as_object(local_file_status));
    payload.insert("v2", v2_stats(conn, predicate.as_ref())?);
    Ok(serde_json::to_value(payload).unwrap_or(Value::Null))
}

/// `KGStoreV2.stats()`, or `_scoped_v2_stats` when the caller has a scope.
fn v2_stats(
    conn: &Connection,
    predicate: Option<&(String, Vec<String>)>,
) -> Result<Value, CoreError> {
    let embed_dim = std::env::var("LATTICEAI_EMBED_DIM")
        .ok()
        .and_then(|raw| raw.trim().parse::<i64>().ok())
        .unwrap_or(DEFAULT_EMBED_DIM);
    let (node_total, edge_total, by_node, by_edge) = match predicate {
        None => {
            let nodes: i64 = conn
                .query_row("SELECT COUNT(*) FROM nodes_v2", [], |row| row.get(0))
                .unwrap_or(0);
            let edges: i64 = conn
                .query_row("SELECT COUNT(*) FROM edges_v2", [], |row| row.get(0))
                .unwrap_or(0);
            (
                nodes,
                edges,
                count_by(
                    conn,
                    "SELECT type, COUNT(*) AS c FROM nodes_v2 GROUP BY type",
                    &[],
                )?,
                count_by(
                    conn,
                    "SELECT type, COUNT(*) AS c FROM edges_v2 GROUP BY type",
                    &[],
                )?,
            )
        }
        Some((predicate, params)) => {
            let visible = format!("SELECT id FROM nodes_v2 WHERE {predicate}");
            let by_node = count_by(
                conn,
                &format!(
                    "SELECT type, COUNT(*) AS c FROM nodes_v2 WHERE {predicate} GROUP BY type"
                ),
                params,
            )?;
            let mut doubled = params.clone();
            doubled.extend(params.clone());
            let by_edge = count_by(
                conn,
                &format!(
                    "SELECT type, COUNT(*) AS c FROM edges_v2 \
                     WHERE source IN ({visible}) AND target IN ({visible}) GROUP BY type"
                ),
                &doubled,
            )?;
            let nodes = by_node.iter().map(|(_, count)| count).sum();
            let edges = by_edge.iter().map(|(_, count)| count).sum();
            (nodes, edges, by_node, by_edge)
        }
    };
    let mut payload = OrderedMap::new();
    payload.insert("schema_version", json!(KG_SCHEMA_V2_VERSION));
    payload.insert("embed_dim", json!(embed_dim));
    payload.insert("nodes", json!(node_total));
    payload.insert("edges", json!(edge_total));
    payload.insert("by_node_type", as_object(by_node));
    payload.insert("by_edge_type", as_object(by_edge));
    Ok(serde_json::to_value(payload).unwrap_or(Value::Null))
}

/// `str(self.db_path)` — the file this connection is attached to.
fn database_path(conn: &Connection) -> String {
    conn.path().map(str::to_string).unwrap_or_default()
}

/// `KnowledgeGraphReadsMixin.list_documents`.
pub fn list_documents(conn: &Connection, limit: i64) -> Result<Value, CoreError> {
    // `max(1, min(int(limit or 200), 1000))`: a zero is Python-falsy and means
    // the default of 200, not one document.
    let limit = if limit == 0 { 200 } else { limit }.clamp(1, 1000);
    let (nodes_table, _) = read_tables(conn);
    let mut statement = conn.prepare(&format!(
        "SELECT id, title, summary, metadata_json, created_at, updated_at \
         FROM {nodes_table} WHERE type='Document' ORDER BY updated_at DESC, id ASC LIMIT ?"
    ))?;
    let rows = statement.query_map([limit], |row| {
        Ok((
            row.get::<_, String>("id")?,
            column_json(row, "title")?,
            safe_loads(row.get::<_, Option<String>>("metadata_json")?.as_deref()),
            column_json(row, "created_at")?,
            column_json(row, "updated_at")?,
        ))
    })?;
    let rows: Vec<_> = rows.filter_map(Result::ok).collect();

    let mut documents = Vec::new();
    for (node_id, title, metadata, created_at, updated_at) in rows {
        let mut chunk_count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM chunks WHERE source_node=?",
                [&node_id],
                |row| row.get(0),
            )
            .unwrap_or(0);
        if chunk_count == 0 {
            // Legacy projections linked chunks only through metadata_json.
            // Kept as the fallback, never as the primary query.
            chunk_count = conn
                .query_row(
                    &format!(
                        "SELECT COUNT(*) FROM {nodes_table} \
                         WHERE type='Chunk' AND metadata_json LIKE ?"
                    ),
                    [format!("%{node_id}%")],
                    |row| row.get(0),
                )
                .unwrap_or(0);
        }
        let extracted = metadata
            .get("extracted")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();
        let field = |key: &str| metadata.get(key).cloned().unwrap_or(Value::Null);
        let mut document = OrderedMap::new();
        document.insert("id", json!(node_id));
        document.insert(
            "filename",
            match metadata.get("filename") {
                Some(value) if crate::shape::truthy(value) => value.clone(),
                _ => title,
            },
        );
        document.insert("ext", field("ext"));
        document.insert("mime_type", field("mime_type"));
        document.insert("bytes", field("bytes"));
        document.insert("sha256", field("sha256"));
        document.insert("uploader", field("uploader"));
        document.insert(
            "chars",
            extracted.get("chars").cloned().unwrap_or(Value::Null),
        );
        document.insert("chunks", json!(chunk_count));
        document.insert("indexed", json!(chunk_count > 0));
        document.insert(
            "ingest_state",
            json!(if chunk_count > 0 {
                "indexed"
            } else {
                "ingested"
            }),
        );
        document.insert("created_at", created_at);
        document.insert("updated_at", updated_at);
        documents.push(serde_json::to_value(document).unwrap_or(Value::Null));
    }
    let total = documents.len();
    let mut payload = OrderedMap::new();
    payload.insert("documents", Value::Array(documents));
    payload.insert("total", json!(total));
    payload.insert("generated_at", json!(naive_local_iso_seconds()));
    Ok(serde_json::to_value(payload).unwrap_or(Value::Null))
}

/// The router's scope pass over `list_documents`.
pub fn scoped_documents(
    conn: &Connection,
    payload: Value,
    scope: &Scope,
) -> Result<Value, CoreError> {
    if scope.allowed_workspaces.is_none() {
        return Ok(payload);
    }
    let documents = payload
        .get("documents")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let kept = filter_scoped(conn, documents, scope)?;
    let mut object = payload.as_object().cloned().unwrap_or_default();
    let total = kept.len();
    object.insert("documents".into(), Value::Array(kept));
    object.insert("total".into(), json!(total));
    Ok(Value::Object(object))
}

/// `KnowledgeGraphReadsMixin.neighbors`, unscoped — the router scopes after.
pub fn neighbors(conn: &Connection, node_id: &str) -> Result<Value, CoreError> {
    let (nodes_table, edges_table) = read_tables(conn);
    let mut statement = conn.prepare(&format!(
        "SELECT from_node, to_node, type, weight FROM {edges_table} \
         WHERE from_node=? OR to_node=? ORDER BY id ASC"
    ))?;
    let rows = statement.query_map([node_id, node_id], |row| {
        Ok((
            row.get::<_, String>("from_node")?,
            row.get::<_, String>("to_node")?,
            column_json(row, "type")?,
            column_json(row, "weight")?,
        ))
    })?;
    let mut neighbor_ids: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();
    let mut edges = Vec::new();
    for (from, to, edge_type, weight) in rows.filter_map(Result::ok) {
        neighbor_ids.insert(from.clone());
        neighbor_ids.insert(to.clone());
        let mut edge = OrderedMap::new();
        edge.insert("from", json!(from));
        edge.insert("to", json!(to));
        edge.insert("type", edge_type);
        edge.insert("weight", weight);
        edges.push(serde_json::to_value(edge).unwrap_or(Value::Null));
    }
    neighbor_ids.remove(node_id);

    let mut nodes = Vec::new();
    if !neighbor_ids.is_empty() {
        let placeholders = vec!["?"; neighbor_ids.len()].join(",");
        let mut statement = conn.prepare(&format!(
            "SELECT id, type, title, summary, metadata_json FROM {nodes_table} \
             WHERE id IN ({placeholders}) ORDER BY id ASC"
        ))?;
        let bound = rusqlite::params_from_iter(neighbor_ids.iter());
        let rows = statement.query_map(bound, |row| {
            let mut node = OrderedMap::new();
            node.insert("id", column_json(row, "id")?);
            node.insert("type", column_json(row, "type")?);
            node.insert("title", column_json(row, "title")?);
            node.insert("summary", column_json(row, "summary")?);
            node.insert(
                "metadata",
                Value::Object(safe_loads(
                    row.get::<_, Option<String>>("metadata_json")?.as_deref(),
                )),
            );
            Ok(serde_json::to_value(node).unwrap_or(Value::Null))
        })?;
        nodes = rows.filter_map(Result::ok).collect();
    }

    let mut payload = OrderedMap::new();
    payload.insert("node_id", json!(node_id));
    payload.insert("neighbors", Value::Array(nodes));
    payload.insert("edges", Value::Array(edges));
    Ok(serde_json::to_value(payload).unwrap_or(Value::Null))
}

/// `KnowledgeGraphProvenanceMixin.provenance_coverage`.
pub fn provenance_coverage(conn: &Connection) -> Result<Value, CoreError> {
    let (nodes_table, _) = read_tables(conn);
    let total: i64 = conn
        .query_row(&format!("SELECT COUNT(*) FROM {nodes_table}"), [], |row| {
            row.get(0)
        })
        .unwrap_or(0);
    let covered: i64 = conn
        .query_row(
            &format!(
                "SELECT COUNT(*) FROM {nodes_table} \
                 WHERE id IN (SELECT DISTINCT node_id FROM ingestion_provenance)"
            ),
            [],
            |row| row.get(0),
        )
        .unwrap_or(0);
    let uncovered = count_by(
        conn,
        &format!(
            "SELECT type, COUNT(*) AS c FROM {nodes_table} \
             WHERE id NOT IN (SELECT DISTINCT node_id FROM ingestion_provenance) \
             GROUP BY type ORDER BY c DESC LIMIT 20"
        ),
        &[],
    )
    .unwrap_or_default();
    let by_source = count_by(
        conn,
        "SELECT source_type, COUNT(*) AS c FROM ingestion_provenance GROUP BY source_type",
        &[],
    )
    .unwrap_or_default();

    let mut payload = OrderedMap::new();
    payload.insert("total_nodes", json!(total));
    payload.insert("nodes_with_provenance", json!(covered));
    payload.insert(
        "coverage_ratio",
        if total > 0 {
            json!(round_to(covered as f64 / total as f64, 4))
        } else {
            Value::Null
        },
    );
    payload.insert("uncovered_by_type", as_object(uncovered));
    payload.insert("provenance_by_source_type", as_object(by_source));
    Ok(serde_json::to_value(payload).unwrap_or(Value::Null))
}

/// `KnowledgeGraphCurationMixin.pending_promotions`.
///
/// The queue is one JSON array in `graph_meta` under `pending_promotions`, not
/// a table, and the read is deliberately tolerant: a corrupt or non-list value
/// is an empty queue, and an entry without an `id` is dropped. Entries are
/// returned **verbatim** rather than through a struct, because the writer's
/// shape is not the reader's contract.
pub fn pending_promotions(conn: &Connection) -> Result<Vec<Value>, CoreError> {
    let raw: Option<String> = conn
        .query_row(
            "SELECT value FROM graph_meta WHERE key='pending_promotions'",
            [],
            |row| row.get(0),
        )
        .ok();
    let Some(raw) = raw.filter(|text| !text.is_empty()) else {
        return Ok(Vec::new());
    };
    let Ok(Value::Array(entries)) = serde_json::from_str::<Value>(&raw) else {
        return Ok(Vec::new());
    };
    Ok(entries
        .into_iter()
        .filter(|entry| {
            entry
                .as_object()
                .and_then(|object| object.get("id"))
                .is_some_and(crate::shape::truthy)
        })
        .collect())
}
