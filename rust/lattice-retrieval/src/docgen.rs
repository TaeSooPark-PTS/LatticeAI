//! Port of the graph reads document generation is built on:
//! `lattice_brain/graph/retrieval_docgen.py` (`search_for_document_generation`,
//! `multi_hop_context`) and the lexical fallback `context_for_query`
//! (`lattice_brain/graph/retrieval/context.py`).
//!
//! Three things here are easy to "improve" and must not be:
//!
//! * **Two candidate lanes, one insertion order.** The full query runs first at
//!   `limit * 5`, then every topic term at `limit * 3`, and a row already seen is
//!   skipped rather than re-ranked. The final sort is by score *descending and
//!   stable*, so equal scores come back in that insertion order — a sort that
//!   broke ties by id would answer differently on the fixture's `tie:` block.
//! * **The related-concept join has no `ORDER BY`.** `LIMIT 8` therefore keeps
//!   whichever eight rows the query plan produced. Adding an order would be a
//!   nicer query and a worse port; the fixture pins what SQLite actually does.
//! * **`multi_hop_context`'s `hop` is the expansion *round*, not the distance.**
//!   Seeds are hop 0 and each round labels what it fetched, so with the default
//!   `max_hops = 2` the nodes discovered by round 1 are never fetched at all —
//!   they appear only as the far endpoints of edges.
//!
//! One deliberate deviation, and it is an ordering guarantee rather than a
//! different answer: Python iterates its frontier as a `set`, so the sequence of
//! node and edge records is unspecified (CPython randomizes string hashing per
//! process). This port walks the frontier in id order and sorts the result —
//! nodes by `(hop, id)`, edges by `(from, to, type, weight)` — which is the same
//! normalization the golden generator applies to the Python side.

use std::collections::{BTreeSet, HashSet};

use lattice_core::pytext::{clean_text, recency_score, round4, safe_loads, truncate_chars};
use lattice_core::read::{filter_scoped_nodes, node_row_from, read_tables, NodeRow};
use lattice_core::CoreError;
use rusqlite::Connection;
use serde_json::{Map, Value};

use crate::concepts::topic_candidates;
use crate::keyword::search;
use crate::service::Scope;

/// The fifteen node types `search_for_document_generation` will look at, as the
/// SQL literal Python spells inline.
const DOCGEN_TYPES_SQL: &str = "'Document', 'File', 'CodeFile', 'SlideDeck', \
     'Spreadsheet', 'Image', 'ImageText', 'Audio', \
     'Chat', 'Decision', 'Task', 'Concept', \
     'Feature', 'Page', 'Slide'";

/// The four types whose hybrid score is multiplied by 1.2.
pub const DOCGEN_BOOST_TYPES: [&str; 4] = ["Document", "File", "SlideDeck", "Decision"];

const NODE_COLUMNS: &str = "id, type, title, summary, metadata_json, updated_at";

/// Half-life of the recency term, in days. Fixed, not a policy knob.
const RECENCY_HALF_LIFE_DAYS: f64 = 14.0;

fn json_opt(value: &Option<String>) -> Value {
    value
        .as_ref()
        .map(|text| Value::String(text.clone()))
        .unwrap_or(Value::Null)
}

/// Python's `f"{title} {summary} {metadata_json}".lower()` — a NULL column
/// contributes the four characters `None`, and the term match searches that.
fn haystack(row: &NodeRow) -> String {
    fn field(value: &Option<String>) -> &str {
        value.as_deref().unwrap_or("None")
    }
    format!(
        "{} {} {}",
        field(&row.title),
        field(&row.summary),
        field(&row.metadata_json)
    )
    .to_lowercase()
}

fn candidate_rows(
    conn: &Connection,
    nodes_table: &str,
    needle: &str,
    cap: i64,
) -> Result<Vec<NodeRow>, CoreError> {
    let sql = format!(
        "SELECT {NODE_COLUMNS} FROM {nodes_table} \
         WHERE (title LIKE ? OR summary LIKE ? OR metadata_json LIKE ?) \
           AND type IN ({DOCGEN_TYPES_SQL}) \
         ORDER BY updated_at DESC, id ASC LIMIT ?"
    );
    let like = format!("%{needle}%");
    let mut statement = conn.prepare(&sql)?;
    let rows = statement.query_map(rusqlite::params![like, like, like, cap], node_row_from)?;
    Ok(rows.filter_map(Result::ok).collect())
}

/// `KnowledgeGraphDocGenMixin.search_for_document_generation`.
///
/// `now_secs` is the wall clock the recency term reads, as naive local seconds:
/// a golden that depends on the real clock is not a golden.
pub fn search_for_document_generation(
    conn: &Connection,
    query: &str,
    limit: i64,
    scope: &Scope,
    now_secs: f64,
) -> Result<Vec<Value>, CoreError> {
    let query = query.trim();
    if query.is_empty() {
        return Ok(Vec::new());
    }
    // `max(1, min(int(limit or 10), 50))`: a zero is Python-falsy and means the
    // default of ten, and only a negative limit reaches the lower clamp.
    let limit = if limit == 0 { 10 } else { limit }.clamp(1, 50);
    let terms = topic_candidates(query, 12);
    let (nodes_table, edges_table) = read_tables(conn);

    let mut seen_ids: HashSet<String> = HashSet::new();
    let mut candidates: Vec<NodeRow> = Vec::new();
    let mut collect = |rows: Vec<NodeRow>| {
        for row in rows {
            if seen_ids.insert(row.id.clone()) {
                candidates.push(row);
            }
        }
    };
    collect(candidate_rows(conn, nodes_table, query, limit * 5)?);
    for term in &terms {
        collect(candidate_rows(conn, nodes_table, term, limit * 3)?);
    }

    let lowered: Vec<String> = terms.iter().map(|term| term.to_lowercase()).collect();
    let mut scored: Vec<Value> = Vec::new();
    for row in &candidates {
        let hay = haystack(row);
        let hits = lowered.iter().filter(|term| hay.contains(*term)).count();
        let text_score = (hits as f64 / terms.len().max(1) as f64).min(1.0);
        let edge_count = edge_count(conn, edges_table, &row.id)?;
        let graph_score = ((1.0 + edge_count as f64).ln() / 4.0).min(1.0);
        let recency = recency_score(row.updated_at.as_deref(), now_secs, RECENCY_HALF_LIFE_DAYS);
        let boost = match row.node_type.as_deref() {
            Some(node_type) if DOCGEN_BOOST_TYPES.contains(&node_type) => 1.2,
            _ => 1.0,
        };
        let hybrid = (0.5 * text_score + 0.3 * graph_score + 0.2 * recency) * boost;

        let mut scores = Map::new();
        scores.insert("text".into(), Value::from(round4(text_score)));
        scores.insert("graph".into(), Value::from(round4(graph_score)));
        scores.insert("recency".into(), Value::from(round4(recency)));
        let mut item = Map::new();
        item.insert("id".into(), Value::String(row.id.clone()));
        item.insert("type".into(), json_opt(&row.node_type));
        item.insert("title".into(), json_opt(&row.title));
        item.insert("summary".into(), json_opt(&row.summary));
        item.insert(
            "metadata".into(),
            Value::Object(safe_loads(row.metadata_json.as_deref())),
        );
        item.insert("updated_at".into(), json_opt(&row.updated_at));
        item.insert("hybrid_score".into(), Value::from(round4(hybrid)));
        item.insert("scores".into(), Value::Object(scores));
        item.insert(
            "related_concepts".into(),
            Value::Array(related_concepts(conn, nodes_table, edges_table, &row.id)?),
        );
        scored.push(Value::Object(item));
    }

    if scope.allowed_workspaces.is_some() {
        scored = filter_scoped_nodes(
            conn,
            scored,
            scope.allowed_workspaces.as_ref(),
            scope.include_legacy_global,
            id_of,
        )?;
        for item in scored.iter_mut() {
            let related = item
                .get("related_concepts")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            let visible = filter_scoped_nodes(
                conn,
                related,
                scope.allowed_workspaces.as_ref(),
                scope.include_legacy_global,
                id_of,
            )?;
            item["related_concepts"] = Value::Array(visible);
        }
    }
    // Stable and descending, so equal scores keep the candidate insertion order.
    scored.sort_by(|left, right| {
        score_of(right)
            .partial_cmp(&score_of(left))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    scored.truncate(limit as usize);
    Ok(scored)
}

fn id_of(item: &Value) -> String {
    item.get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string()
}

fn score_of(item: &Value) -> f64 {
    item.get("hybrid_score")
        .and_then(Value::as_f64)
        .unwrap_or(0.0)
}

fn edge_count(conn: &Connection, edges_table: &str, node_id: &str) -> Result<i64, CoreError> {
    let sql = format!("SELECT COUNT(*) AS c FROM {edges_table} WHERE from_node=? OR to_node=?");
    Ok(conn.query_row(&sql, rusqlite::params![node_id, node_id], |row| row.get(0))?)
}

/// The neighbour join, verbatim — including the missing `ORDER BY`.
fn related_concepts(
    conn: &Connection,
    nodes_table: &str,
    edges_table: &str,
    node_id: &str,
) -> Result<Vec<Value>, CoreError> {
    let sql = format!(
        "SELECT n.id, n.title, n.type FROM {edges_table} e \
         JOIN {nodes_table} n ON n.id = CASE WHEN e.from_node = ? THEN e.to_node ELSE e.from_node END \
         WHERE (e.from_node = ? OR e.to_node = ?) \
           AND n.type IN ('Concept', 'Feature', 'Decision', 'Task') \
         LIMIT 8"
    );
    let mut statement = conn.prepare(&sql)?;
    let rows = statement.query_map(rusqlite::params![node_id, node_id, node_id], |row| {
        let mut neighbour = Map::new();
        neighbour.insert("id".into(), Value::String(row.get("id")?));
        neighbour.insert("title".into(), json_opt(&row.get("title")?));
        neighbour.insert("type".into(), json_opt(&row.get("type")?));
        Ok(Value::Object(neighbour))
    })?;
    Ok(rows.filter_map(Result::ok).collect())
}

/// `KnowledgeGraphDocGenMixin.multi_hop_context`.
pub fn multi_hop_context(
    conn: &Connection,
    node_ids: &[String],
    max_hops: i64,
    scope: &Scope,
) -> Result<Value, CoreError> {
    let (nodes_table, edges_table) = read_tables(conn);
    let mut visited: BTreeSet<String> = BTreeSet::new();
    let mut visited_edges: BTreeSet<String> = BTreeSet::new();
    let mut nodes: Vec<Value> = Vec::new();
    let mut edges: Vec<Value> = Vec::new();
    let mut frontier: BTreeSet<String> = node_ids.iter().cloned().collect();

    for hop in 0..max_hops.max(0) {
        if frontier.is_empty() {
            break;
        }
        let mut next: BTreeSet<String> = BTreeSet::new();
        for node_id in std::mem::take(&mut frontier) {
            if !visited.insert(node_id.clone()) {
                continue;
            }
            if let Some(node) = hop_node(conn, nodes_table, &node_id, hop)? {
                nodes.push(node);
            }
            for (edge_id, from, to, edge) in incident_edges(conn, edges_table, &node_id)? {
                if !visited_edges.insert(edge_id) {
                    continue;
                }
                edges.push(edge);
                let other = if from == node_id { to } else { from };
                if !visited.contains(&other) {
                    next.insert(other);
                }
            }
        }
        frontier = next;
    }

    if scope.allowed_workspaces.is_some() {
        nodes = filter_scoped_nodes(
            conn,
            nodes,
            scope.allowed_workspaces.as_ref(),
            scope.include_legacy_global,
            id_of,
        )?;
        let kept: BTreeSet<&str> = nodes
            .iter()
            .filter_map(|node| node.get("id").and_then(Value::as_str))
            .collect();
        edges.retain(|edge| {
            let end = |key: &str| edge.get(key).and_then(Value::as_str).unwrap_or_default();
            kept.contains(end("from")) && kept.contains(end("to"))
        });
    }

    // The golden normalization (see the module docs): Python's own order is
    // unspecified, so both sides agree on this one instead.
    nodes.sort_by(|left, right| {
        let key = |node: &Value| {
            (
                node.get("hop").and_then(Value::as_i64).unwrap_or(0),
                id_of(node),
            )
        };
        key(left).cmp(&key(right))
    });
    edges.sort_by(|left, right| {
        let text = |edge: &Value, key: &str| {
            edge.get(key)
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string()
        };
        let key = |edge: &Value| (text(edge, "from"), text(edge, "to"), text(edge, "type"));
        key(left).cmp(&key(right)).then_with(|| {
            let weight = |edge: &Value| edge.get("weight").and_then(Value::as_f64).unwrap_or(0.0);
            weight(left).total_cmp(&weight(right))
        })
    });

    let mut out = Map::new();
    out.insert("nodes".into(), Value::Array(nodes));
    out.insert("edges".into(), Value::Array(edges));
    Ok(Value::Object(out))
}

fn hop_node(
    conn: &Connection,
    nodes_table: &str,
    node_id: &str,
    hop: i64,
) -> Result<Option<Value>, CoreError> {
    let sql = format!("SELECT {NODE_COLUMNS} FROM {nodes_table} WHERE id=?");
    let mut statement = conn.prepare(&sql)?;
    let mut rows = statement.query_map(rusqlite::params![node_id], node_row_from)?;
    let Some(Ok(row)) = rows.next() else {
        return Ok(None);
    };
    let mut node = Map::new();
    node.insert("id".into(), Value::String(row.id.clone()));
    node.insert("type".into(), json_opt(&row.node_type));
    node.insert("title".into(), json_opt(&row.title));
    node.insert("summary".into(), json_opt(&row.summary));
    node.insert(
        "metadata".into(),
        Value::Object(safe_loads(row.metadata_json.as_deref())),
    );
    node.insert("hop".into(), Value::from(hop));
    Ok(Some(Value::Object(node)))
}

type IncidentEdge = (String, String, String, Value);

fn incident_edges(
    conn: &Connection,
    edges_table: &str,
    node_id: &str,
) -> Result<Vec<IncidentEdge>, CoreError> {
    let sql = format!(
        "SELECT id, from_node, to_node, type, weight FROM {edges_table} \
         WHERE from_node=? OR to_node=? ORDER BY id ASC"
    );
    let mut statement = conn.prepare(&sql)?;
    let rows = statement.query_map(rusqlite::params![node_id, node_id], |row| {
        let id: String = row.get("id")?;
        let from: String = row.get("from_node")?;
        let to: String = row.get("to_node")?;
        let mut edge = Map::new();
        edge.insert("from".into(), Value::String(from.clone()));
        edge.insert("to".into(), Value::String(to.clone()));
        edge.insert("type".into(), json_opt(&row.get("type")?));
        edge.insert(
            "weight".into(),
            lattice_core::read::column_json(row, "weight")?,
        );
        Ok((id, from, to, Value::Object(edge)))
    })?;
    Ok(rows.filter_map(Result::ok).collect())
}

/// `_ContextMixin.context_for_query` in its default configuration
/// (`use_hybrid=False`, `with_meta=False`) — the lexical string the document
/// context falls back to when the hybrid document search finds nothing.
pub fn context_for_query(
    conn: &Connection,
    query: &str,
    limit: i64,
    scope: &Scope,
) -> Result<String, CoreError> {
    let query = query.trim();
    if query.is_empty() {
        return Ok(String::new());
    }
    let found = search(
        conn,
        query,
        limit,
        scope.allowed_workspaces.as_ref(),
        scope.include_legacy_global,
    )?;
    let mut matches: Vec<Value> = found
        .get("matches")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    if matches.is_empty() {
        matches = topic_matches(conn, query, limit, scope)?;
    }

    let mut lines: Vec<String> = Vec::new();
    for item in matches.iter().take(limit.max(0) as usize) {
        let empty = Map::new();
        let meta = item
            .get("metadata")
            .and_then(Value::as_object)
            .unwrap_or(&empty);
        let source = first_truthy(
            meta,
            &["relative_path", "filename", "conversation_id", "source"],
        )
        .unwrap_or_else(|| py_text(item.get("id")));
        let summary = truncate_chars(&clean_text(&py_text(item.get("summary"))), 700);
        lines.push(format!(
            "- [{}] {} | source={source} | {summary}",
            py_text(item.get("type")),
            py_text(item.get("title")),
        ));
    }
    Ok(lines.join("\n"))
}

/// The topic-term top-up `context_for_query` reaches when `search()` is empty.
///
/// Note the narrower `WHERE`: `title` or `metadata_json`, **not** `summary`.
fn topic_matches(
    conn: &Connection,
    query: &str,
    limit: i64,
    scope: &Scope,
) -> Result<Vec<Value>, CoreError> {
    let topics = topic_candidates(query, 4);
    if topics.is_empty() {
        return Ok(Vec::new());
    }
    let (nodes_table, _) = read_tables(conn);
    let sql = format!(
        "SELECT id, type, title, summary, metadata_json FROM {nodes_table} \
         WHERE title LIKE ? OR metadata_json LIKE ? \
         ORDER BY updated_at DESC, id ASC LIMIT 3"
    );
    let mut rows: Vec<NodeRow> = Vec::new();
    for topic in &topics {
        let like = format!("%{topic}%");
        let mut statement = conn.prepare(&sql)?;
        let found = statement.query_map(rusqlite::params![like, like], |row| {
            Ok(NodeRow {
                id: row.get("id")?,
                node_type: row.get("type")?,
                title: row.get("title")?,
                summary: row.get("summary")?,
                metadata_json: row.get("metadata_json")?,
                updated_at: None,
            })
        })?;
        rows.extend(found.filter_map(Result::ok));
    }
    let mut seen: HashSet<String> = HashSet::new();
    let mut matches: Vec<Value> = Vec::new();
    for row in rows {
        if !seen.insert(row.id.clone()) {
            continue;
        }
        let mut item = Map::new();
        item.insert("id".into(), Value::String(row.id.clone()));
        item.insert("type".into(), json_opt(&row.node_type));
        item.insert("title".into(), json_opt(&row.title));
        item.insert("summary".into(), json_opt(&row.summary));
        item.insert(
            "metadata".into(),
            Value::Object(safe_loads(row.metadata_json.as_deref())),
        );
        matches.push(Value::Object(item));
        // Python breaks *after* appending, so the cut lands one row late by
        // design when the last topic contributed several rows at once.
        if matches.len() as i64 >= limit {
            break;
        }
    }
    if scope.allowed_workspaces.is_some() {
        matches = filter_scoped_nodes(
            conn,
            matches,
            scope.allowed_workspaces.as_ref(),
            scope.include_legacy_global,
            id_of,
        )?;
    }
    Ok(matches)
}

/// Python's `meta.get(a) or meta.get(b) or …` over a metadata blob.
pub fn first_truthy(meta: &Map<String, Value>, keys: &[&str]) -> Option<String> {
    keys.iter()
        .find_map(|key| meta.get(*key).filter(|value| crate::shape::truthy(value)))
        .map(|value| py_text(Some(value)))
}

/// `str(value)` for the JSON a row or a metadata blob can hold — including the
/// `None` a missing value formats as inside a Python f-string.
pub fn py_text(value: Option<&Value>) -> String {
    match value {
        None | Some(Value::Null) => "None".to_string(),
        Some(Value::String(text)) => text.clone(),
        Some(Value::Bool(flag)) => (if *flag { "True" } else { "False" }).to_string(),
        Some(other) => other.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn graph() -> (tempfile::TempDir, Connection) {
        let dir = tempfile::tempdir().unwrap();
        let conn = Connection::open(dir.path().join("g.sqlite")).unwrap();
        conn.execute_batch(
            "CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT, title TEXT, summary TEXT,
                                metadata_json TEXT, updated_at TEXT);
             CREATE TABLE edges(id TEXT PRIMARY KEY, from_node TEXT, to_node TEXT, type TEXT,
                                weight REAL, metadata_json TEXT, created_at TEXT);
             CREATE TABLE nodes_v2(id TEXT PRIMARY KEY, workspace_id TEXT);
             INSERT INTO nodes VALUES
               ('d1','Document','Proposal draft','about ranking','{\"filename\":\"p.md\"}',
                '2026-08-01T00:00:00'),
               ('c1','Concept','Ranking',NULL,'{}','2026-07-01T00:00:00'),
               ('m1','Meeting','Proposal meeting','not allow-listed','{}','2026-08-01T00:00:00');
             INSERT INTO nodes_v2 VALUES ('d1','w1'),('c1',NULL),('m1','w1');
             INSERT INTO edges VALUES
               ('e1','d1','c1','MENTIONS',0.9,'{}','2026-07-01T00:00:00');",
        )
        .unwrap();
        (dir, conn)
    }

    fn now() -> f64 {
        lattice_core::parse_iso(Some("2026-08-01T12:00:00")).unwrap()
    }

    #[test]
    fn a_blank_query_is_no_answer_and_the_limit_clamps() {
        let (_dir, conn) = graph();
        let scope = Scope::default();
        assert!(
            search_for_document_generation(&conn, "   ", 10, &scope, now())
                .unwrap()
                .is_empty()
        );
        let one = search_for_document_generation(&conn, "Proposal", 1, &scope, now()).unwrap();
        assert_eq!(one.len(), 1);
        // A zero limit is Python-falsy and means the default of ten.
        let default = search_for_document_generation(&conn, "Proposal", 0, &scope, now()).unwrap();
        assert_eq!(default.len(), 1, "only one allow-listed row matches");
        assert_eq!(default[0]["id"], "d1", "the Meeting is not an allowed type");
    }

    #[test]
    fn the_score_is_weighted_boosted_and_rounded() {
        let (_dir, conn) = graph();
        let found =
            search_for_document_generation(&conn, "ranking", 10, &Scope::default(), now()).unwrap();
        let doc = found.iter().find(|item| item["id"] == "d1").unwrap();
        assert_eq!(doc["scores"]["text"], 1.0);
        // log1p(1)/4 = 0.1733; twelve hours old on a 14-day half-life = 0.9755.
        assert_eq!(doc["scores"]["graph"], 0.1733);
        assert_eq!(doc["scores"]["recency"], 0.9755);
        assert_eq!(doc["hybrid_score"], 0.8965, "a Document is boosted by 1.2");
        assert_eq!(doc["related_concepts"][0]["id"], "c1");
        assert_eq!(doc["metadata"], serde_json::json!({"filename": "p.md"}));
        // The Concept is not boosted, and its NULL summary survives as null.
        let concept = found.iter().find(|item| item["id"] == "c1").unwrap();
        assert_eq!(concept["summary"], Value::Null);
        assert_eq!(concept["related_concepts"], serde_json::json!([]));
    }

    #[test]
    fn scoping_prunes_the_results_and_their_related_concepts() {
        let (_dir, conn) = graph();
        let w1: BTreeSet<String> = ["w1".to_string()].into_iter().collect();
        let scoped = search_for_document_generation(
            &conn,
            "ranking",
            10,
            &Scope {
                allowed_workspaces: Some(w1.clone()),
                include_legacy_global: false,
            },
            now(),
        )
        .unwrap();
        assert_eq!(scoped.len(), 1);
        assert_eq!(scoped[0]["id"], "d1");
        assert_eq!(
            scoped[0]["related_concepts"],
            serde_json::json!([]),
            "the neighbour is a legacy row this caller may not read"
        );
        let legacy = search_for_document_generation(
            &conn,
            "ranking",
            10,
            &Scope {
                allowed_workspaces: Some(w1),
                include_legacy_global: true,
            },
            now(),
        )
        .unwrap();
        assert_eq!(legacy[0]["related_concepts"][0]["id"], "c1");
    }

    #[test]
    fn hop_labels_the_expansion_round_and_the_last_round_only_leaves_edges() {
        let (_dir, conn) = graph();
        let seeds = vec!["d1".to_string()];
        let one = multi_hop_context(&conn, &seeds, 1, &Scope::default()).unwrap();
        assert_eq!(one["nodes"].as_array().unwrap().len(), 1);
        assert_eq!(one["nodes"][0]["hop"], 0);
        assert_eq!(
            one["edges"].as_array().unwrap().len(),
            1,
            "the far endpoint is an edge, not a node, at the last round"
        );
        let two = multi_hop_context(&conn, &seeds, 2, &Scope::default()).unwrap();
        assert_eq!(two["nodes"][1]["id"], "c1");
        assert_eq!(two["nodes"][1]["hop"], 1);
        assert_eq!(
            two["edges"][0],
            serde_json::json!({
                "from": "d1", "to": "c1", "type": "MENTIONS", "weight": 0.9
            })
        );
        // No rounds at all, and a seed that is not in the store.
        for hops in [0, -1] {
            let none = multi_hop_context(&conn, &seeds, hops, &Scope::default()).unwrap();
            assert_eq!(none["nodes"], serde_json::json!([]));
        }
        let missing =
            multi_hop_context(&conn, &["nope".to_string()], 2, &Scope::default()).unwrap();
        assert_eq!(missing["nodes"], serde_json::json!([]));
        assert_eq!(missing["edges"], serde_json::json!([]));
    }

    #[test]
    fn multi_hop_scoping_drops_nodes_then_their_edges() {
        let (_dir, conn) = graph();
        let w1: BTreeSet<String> = ["w1".to_string()].into_iter().collect();
        let scoped = multi_hop_context(
            &conn,
            &["d1".to_string()],
            2,
            &Scope {
                allowed_workspaces: Some(w1),
                include_legacy_global: false,
            },
        )
        .unwrap();
        assert_eq!(scoped["nodes"].as_array().unwrap().len(), 1);
        assert_eq!(scoped["edges"], serde_json::json!([]));
    }

    #[test]
    fn the_lexical_fallback_renders_one_line_per_match() {
        let (_dir, conn) = graph();
        let scope = Scope::default();
        assert_eq!(context_for_query(&conn, "  ", 5, &scope).unwrap(), "");
        let text = context_for_query(&conn, "Proposal draft", 5, &scope).unwrap();
        assert!(
            text.contains("- [Document] Proposal draft | source=p.md | about ranking"),
            "{text}"
        );
        // A query nothing matches at all is an empty string, not a fabrication.
        assert_eq!(
            context_for_query(&conn, "zzqq wumpus", 5, &scope).unwrap(),
            ""
        );
    }

    #[test]
    fn python_text_and_the_or_chain_keep_pythons_spellings() {
        let mut meta = Map::new();
        assert_eq!(first_truthy(&meta, &["filename"]), None);
        meta.insert("filename".into(), Value::String(String::new()));
        assert_eq!(first_truthy(&meta, &["filename"]), None, "empty is falsy");
        meta.insert("source".into(), Value::Bool(true));
        assert_eq!(
            first_truthy(&meta, &["filename", "source"]),
            Some("True".into())
        );
        assert_eq!(py_text(None), "None");
        assert_eq!(py_text(Some(&Value::Null)), "None");
        assert_eq!(py_text(Some(&Value::Bool(false))), "False");
        assert_eq!(py_text(Some(&serde_json::json!(7))), "7");
        assert_eq!(DOCGEN_BOOST_TYPES.len(), 4);
    }
}
