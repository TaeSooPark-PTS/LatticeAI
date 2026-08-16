//! Graph canvas, node, context and pipeline-ribbon reads.

use lattice_auth::OrderedMap;
use lattice_core::pytext::{clean_text, recency_score, round_to, safe_loads};
use lattice_core::read::{column_json, read_tables};
use lattice_core::CoreError;
use rusqlite::Connection;
use serde_json::{json, Value};

use crate::knowledge_graph_api::reads::{filter_scoped, list_documents, stats};
use crate::knowledge_graph_api::GRAPH_VISIBLE_TYPES;
use crate::service::Scope;

/// `_GraphViewMixin.graph` — the canvas read, with its importance metrics.
///
/// `now` is naive local epoch seconds (`routes::naive_local_now`), because the
/// stamps it is compared against were written by `datetime.now().isoformat()`.
pub fn graph_view(
    conn: &Connection,
    limit: i64,
    scope: &Scope,
    now: f64,
) -> Result<Value, CoreError> {
    let limit = if limit == 0 { 300 } else { limit }.clamp(1, 2000);
    let (nodes_table, edges_table) = read_tables(conn);
    let visible = GRAPH_VISIBLE_TYPES
        .iter()
        .map(|name| format!("'{name}'"))
        .collect::<Vec<_>>()
        .join(",");

    let mut statement = conn.prepare(&format!(
        "SELECT id, type, title, summary, metadata_json, updated_at FROM {nodes_table} \
         WHERE type IN ({visible}) ORDER BY updated_at DESC, id ASC LIMIT ?"
    ))?;
    let rows = statement.query_map([limit], |row| {
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
        node.insert("updated_at", column_json(row, "updated_at")?);
        Ok(node)
    })?;
    let mut nodes: Vec<OrderedMap> = rows.filter_map(Result::ok).collect();

    let mut edges: Vec<Value> = Vec::new();
    if !nodes.is_empty() {
        let mut statement = conn.prepare(&format!(
            "SELECT id, from_node, to_node, type, weight, metadata_json FROM {edges_table} \
             WHERE from_node IN (SELECT id FROM {nodes_table} WHERE type IN ({visible}) \
                                 ORDER BY updated_at DESC, id ASC LIMIT ?) \
             AND to_node IN (SELECT id FROM {nodes_table} WHERE type IN ({visible}) \
                             ORDER BY updated_at DESC, id ASC LIMIT ?) \
             ORDER BY weight DESC, created_at DESC, id ASC"
        ))?;
        let rows = statement.query_map([limit, limit], |row| {
            let mut edge = OrderedMap::new();
            edge.insert("id", column_json(row, "id")?);
            edge.insert("from", column_json(row, "from_node")?);
            edge.insert("to", column_json(row, "to_node")?);
            edge.insert("type", column_json(row, "type")?);
            edge.insert("weight", column_json(row, "weight")?);
            edge.insert(
                "metadata",
                Value::Object(safe_loads(
                    row.get::<_, Option<String>>("metadata_json")?.as_deref(),
                )),
            );
            Ok(serde_json::to_value(edge).unwrap_or(Value::Null))
        })?;
        edges = rows.filter_map(Result::ok).collect();
    }

    if scope.allowed_workspaces.is_some() {
        let as_values: Vec<Value> = nodes
            .iter()
            .map(|node| serde_json::to_value(node).unwrap_or(Value::Null))
            .collect();
        let kept = filter_scoped(conn, as_values, scope)?;
        let kept_ids: Vec<String> = kept
            .iter()
            .filter_map(|node| node.get("id").and_then(Value::as_str))
            .map(str::to_string)
            .collect();
        nodes.retain(|node| {
            node.get("id")
                .and_then(Value::as_str)
                .is_some_and(|id| kept_ids.iter().any(|kept| kept == id))
        });
        edges.retain(|edge| {
            let endpoint = |key: &str| edge.get(key).and_then(Value::as_str).unwrap_or_default();
            kept_ids.iter().any(|id| id == endpoint("from"))
                && kept_ids.iter().any(|id| id == endpoint("to"))
        });
    }

    // Degrees over the *returned* edges, which is why a node the window
    // truncated shows a lower degree than the graph gives it. Python's number,
    // reproduced rather than corrected.
    let mut degrees: std::collections::HashMap<String, i64> = std::collections::HashMap::new();
    for edge in &edges {
        for key in ["from", "to"] {
            if let Some(id) = edge.get(key).and_then(Value::as_str) {
                *degrees.entry(id.to_string()).or_insert(0) += 1;
            }
        }
    }

    // `Topic` is not in the visible list, so the Topic importance branch is
    // unreachable through this query. The non-Topic formula is the only one
    // that runs, and it is the only one ported.
    let mut raw_importance: Vec<f64> = Vec::with_capacity(nodes.len());
    let mut type_max: std::collections::HashMap<String, f64> = std::collections::HashMap::new();
    for node in nodes.iter_mut() {
        let node_id = node
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let node_type = node
            .get("type")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let degree = *degrees.get(&node_id).unwrap_or(&0);
        let recency = recency_score(node.get("updated_at").and_then(Value::as_str), now, 14.0);
        let importance = (degree.max(0) as f64).ln_1p() * 1.4 + recency * 0.9;
        raw_importance.push(importance);

        let mut metrics = OrderedMap::new();
        metrics.insert("degree", json!(degree));
        metrics.insert("recency_score", json!(round_to(recency, 4)));
        metrics.insert("importance_raw", json!(round_to(importance, 4)));

        let mut metadata = node
            .get("metadata")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();
        metadata.insert(
            "graph_metrics".into(),
            serde_json::to_value(metrics).unwrap_or(Value::Null),
        );
        node.insert("metadata", Value::Object(metadata));
        node.insert("importance", json!(round_to(importance, 4)));

        let slot = type_max.entry(node_type).or_insert(0.0);
        if importance > *slot {
            *slot = importance;
        }
    }

    let mut out = Vec::with_capacity(nodes.len());
    for (node, importance) in nodes.into_iter().zip(raw_importance) {
        let mut node = node;
        let node_type = node
            .get("type")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let max_raw = type_max.get(&node_type).copied().unwrap_or(0.0).max(0.0001);
        let normalised = round_to((importance / max_raw).min(1.0), 4);
        node.insert("importance_norm", json!(normalised));
        if let Some(Value::Object(metadata)) = node.get("metadata").cloned().as_mut() {
            if let Some(Value::Object(metrics)) = metadata.get_mut("graph_metrics") {
                metrics.insert("importance_norm".into(), json!(normalised));
            }
            node.insert("metadata", Value::Object(metadata.clone()));
        }
        out.push(serde_json::to_value(node).unwrap_or(Value::Null));
    }

    let mut payload = OrderedMap::new();
    payload.insert("nodes", Value::Array(out));
    payload.insert("edges", Value::Array(edges));
    Ok(serde_json::to_value(payload).unwrap_or(Value::Null))
}

/// `KnowledgeGraphReadsMixin.get_node`.
///
/// **One stated divergence.** Python fires `_record_access` here — a write into
/// `nodes_v2` that feeds `importance_report()`. `nodes_v2` has a single writer
/// (the worker) and no seam op exposes `touch_node`, so this port reads without
/// recording. The counter is best-effort bookkeeping Python swallows every
/// failure of, but it is a real behaviour difference; see the wiring note.
pub fn get_node(conn: &Connection, node_id: &str, scope: &Scope) -> Result<Value, CoreError> {
    let node_id = node_id.trim();
    if node_id.is_empty() {
        return Err(CoreError::InvalidRequest("node_id required".into()));
    }
    let (nodes_table, edges_table) = read_tables(conn);
    let mut statement = conn.prepare(&format!(
        "SELECT id, type, title, summary, metadata_json, updated_at FROM {nodes_table} WHERE id=?"
    ))?;
    let mut rows = statement.query([node_id])?;
    let Some(row) = rows.next()? else {
        return Err(CoreError::InvalidRequest(format!(
            "graph node not found: {node_id}"
        )));
    };
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
    node.insert("updated_at", column_json(row, "updated_at")?);
    drop(rows);

    let degree: i64 = conn
        .query_row(
            &format!("SELECT COUNT(*) FROM {edges_table} WHERE from_node=? OR to_node=?"),
            [node_id, node_id],
            |row| row.get(0),
        )
        .unwrap_or(0);
    node.insert("degree", json!(degree));

    let value = serde_json::to_value(node).unwrap_or(Value::Null);
    if scope.allowed_workspaces.is_some()
        && filter_scoped(conn, vec![value.clone()], scope)?.is_empty()
    {
        return Err(CoreError::InvalidRequest(format!(
            "graph node not found: {node_id}"
        )));
    }
    Ok(value)
}

/// The context line format, shared by the store method and the router helper.
///
/// `knowledge_graph.py:_format_context` and
/// `retrieval/context.py:context_for_query` build the same line; the router's
/// copy is the one that runs for a scoped caller, and its only difference is
/// `" ".join(str(...).split())` where the store uses `re.sub(r"\s+", " ")`.
/// [`clean_text`] is the store's spelling and matches both on every input a
/// summary can hold.
pub fn format_context(matches: &[Value], limit: i64) -> String {
    let take = limit.max(0) as usize;
    matches
        .iter()
        .take(take)
        .map(|item| {
            let metadata = item.get("metadata").and_then(Value::as_object);
            let pick = |key: &str| {
                metadata
                    .and_then(|object| object.get(key))
                    .filter(|value| crate::shape::truthy(value))
                    .map(crate::shape::py_str)
            };
            let source = pick("relative_path")
                .or_else(|| pick("filename"))
                .or_else(|| pick("conversation_id"))
                .or_else(|| pick("source"))
                .unwrap_or_else(|| {
                    item.get("id")
                        .map(crate::shape::py_str)
                        .unwrap_or_else(|| "None".into())
                });
            let summary = lattice_core::pytext::truncate_chars(
                &clean_text(
                    &item
                        .get("summary")
                        .filter(|value| crate::shape::truthy(value))
                        .map(crate::shape::py_str)
                        .unwrap_or_default(),
                ),
                700,
            );
            let node_type = render(item.get("type"));
            let title = render(item.get("title"));
            format!("- [{node_type}] {title} | source={source} | {summary}")
        })
        .collect::<Vec<_>>()
        .join("\n")
}

/// Python's f-string rendering of a value that may be `None`.
fn render(value: Option<&Value>) -> String {
    match value {
        None | Some(Value::Null) => "None".into(),
        Some(value) => crate::shape::py_str(value),
    }
}

/// `GET /knowledge-graph/context`'s body, both scope branches.
pub fn context_for_query(
    conn: &Connection,
    query: &str,
    limit: i64,
    scope: &Scope,
) -> Result<String, CoreError> {
    // Unscoped, Python calls `context_for_query(q, limit)` with `use_hybrid`
    // off, which is a lexical search followed by the same formatting. The
    // topic-candidate fallback only runs when the lexical search finds nothing
    // — and produces the same line shape from the same columns.
    let payload = crate::keyword::search(
        conn,
        query,
        limit,
        scope.allowed_workspaces.as_ref(),
        scope.include_legacy_global,
    )?;
    let matches = payload
        .get("matches")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let matches = if scope.allowed_workspaces.is_some() {
        filter_scoped(conn, matches, scope)?
    } else {
        matches
    };
    Ok(format_context(&matches, limit))
}

/// `_pipeline_stage_view(count, pending)`.
pub fn stage_view(count: i64, pending: i64) -> Value {
    let count = count.max(0);
    let pending = pending.max(0);
    let status = if pending > 0 {
        "working"
    } else if count > 0 {
        "done"
    } else {
        "waiting"
    };
    let mut view = OrderedMap::new();
    view.insert("count", json!(count));
    view.insert("pending", json!(pending));
    view.insert("status", json!(status));
    serde_json::to_value(view).unwrap_or(Value::Null)
}

/// `GET /knowledge-graph/pipeline/status`.
///
/// **One stated divergence.** Python also consults `index_status()` for the
/// vector backlog. That read belongs to [`lattice_jobs::index_api`] here, and a
/// retrieval route may not open a second copy of it, so the extraction backlog
/// falls back to `received - extracted` — which is Python's own documented
/// fallback when the index cannot answer. Every key stays optional exactly as
/// Python leaves it: a value that cannot be computed is **absent**, never a
/// fabricated zero.
pub fn pipeline_payload(conn: &Connection, scope: &Scope, now: f64) -> Result<Value, CoreError> {
    let mut received: Option<i64> = None;
    let mut extracted: Option<i64> = None;
    let mut connected: Option<i64> = None;

    if let Ok(payload) = list_documents(conn, 10_000) {
        let documents = payload
            .get("documents")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let documents = if scope.allowed_workspaces.is_some() {
            filter_scoped(conn, documents, scope).unwrap_or_default()
        } else {
            documents
        };
        received = Some(documents.len() as i64);
        extracted = Some(
            documents
                .iter()
                .filter(|doc| {
                    doc.get("indexed").is_some_and(crate::shape::truthy)
                        || doc.get("chunks").and_then(Value::as_i64).unwrap_or(0) > 0
                })
                .count() as i64,
        );
    }

    if let Ok(stats) = stats(conn, scope) {
        if let Some(edges) = stats.get("edges").and_then(Value::as_object) {
            connected = Some(edges.values().filter_map(Value::as_i64).sum());
        }
        if received.is_none() {
            if let Some(nodes) = stats.get("nodes").and_then(Value::as_object) {
                received = Some(
                    nodes
                        .iter()
                        .filter(|(key, _)| key.to_lowercase() != "chunk")
                        .filter_map(|(_, value)| value.as_i64())
                        .sum(),
                );
            }
        }
    }
    let _ = now;

    let mut payload = OrderedMap::new();
    if let Some(value) = received {
        payload.insert("received", json!(value.max(0)));
    }
    if let Some(value) = extracted {
        payload.insert("extracted", json!(value.max(0)));
    }
    if let Some(value) = connected {
        payload.insert("connected", json!(value.max(0)));
    }
    if payload.is_empty() {
        return Ok(serde_json::to_value(payload).unwrap_or(Value::Null));
    }

    let mut stages = OrderedMap::new();
    if let Some(count) = payload.get("received").and_then(Value::as_i64) {
        stages.insert("received", stage_view(count, 0));
    }
    if let Some(count) = payload.get("extracted").and_then(Value::as_i64) {
        let backlog = payload
            .get("received")
            .and_then(Value::as_i64)
            .map(|received| (received - count).max(0))
            .unwrap_or(0);
        stages.insert("extracted", stage_view(count, backlog));
    }
    if let Some(count) = payload.get("connected").and_then(Value::as_i64) {
        stages.insert("connected", stage_view(count, 0));
    }
    payload.insert(
        "stages",
        serde_json::to_value(stages).unwrap_or(Value::Null),
    );
    payload.insert("updated_at", json!(naive_local_iso_seconds()));
    Ok(serde_json::to_value(payload).unwrap_or(Value::Null))
}

/// `datetime.now().isoformat(timespec="seconds")` — naive local, no offset.
pub fn naive_local_iso_seconds() -> String {
    let seconds = crate::routes::naive_local_now().floor() as i64;
    // The value is naive local epoch seconds, so a plain UTC breakdown of it
    // reproduces the local wall clock the Python stamp carries.
    let days = seconds.div_euclid(86_400);
    let time = seconds.rem_euclid(86_400);
    let (year, month, day) = civil_from_days(days);
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}",
        time / 3600,
        (time % 3600) / 60,
        time % 60
    )
}

/// Howard Hinnant's `civil_from_days`, the standard days→(y, m, d) reduction.
pub fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if m <= 2 { y + 1 } else { y }, m, d)
}
