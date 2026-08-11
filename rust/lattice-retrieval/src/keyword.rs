//! Port of `KnowledgeGraphStore.search` (`graph/retrieval/graph_view.py`).
//!
//! Three candidate sources feed one re-score: trigram FTS5, a LIKE scan when FTS
//! matched nothing, and a topic-term top-up when the pool is still short. The
//! re-score then runs over **all** of them, which is why the FTS bm25 order is
//! discarded — `sorted(rows, key=id)` followed by a stable `sorted(..., reverse=True)`
//! is the deterministic contract, and the two-pass shape is load-bearing.

use std::collections::BTreeSet;

use lattice_core::pytext::safe_loads;
use lattice_core::read::{filter_scoped_nodes, read_tables, NodeRow};
use lattice_core::CoreError;
use rusqlite::Connection;
use serde_json::{Map, Value};

use crate::concepts::topic_candidates;

/// Node types `search()` gives a +1 relevance boost. Fixed list, not a config.
pub const TYPE_BOOST: [&str; 12] = [
    "Decision",
    "Task",
    "File",
    "Document",
    "CodeFile",
    "Spreadsheet",
    "SlideDeck",
    "Image",
    "ImageText",
    "Audio",
    "Page",
    "Slide",
];

const NODE_COLUMNS: &str = "id, type, title, summary, metadata_json, updated_at";

/// `projection.v2_schema._fts_match_ids` — ranked ids, `[]` on any failure.
pub fn fts_match_ids(conn: &Connection, query: &str, limit: i64) -> Vec<String> {
    if query.chars().count() < 3 {
        return Vec::new();
    }
    let escaped = format!("\"{}\"", query.replace('"', "\"\""));
    let Ok(mut stmt) =
        conn.prepare("SELECT node_id FROM node_fts WHERE node_fts MATCH ? ORDER BY rank LIMIT ?")
    else {
        return Vec::new();
    };
    let Ok(rows) = stmt.query_map(rusqlite::params![escaped, limit], |row| {
        row.get::<_, String>(0)
    }) else {
        return Vec::new();
    };
    rows.filter_map(Result::ok).collect()
}

fn query_nodes(
    conn: &Connection,
    sql: &str,
    params: &[&dyn rusqlite::ToSql],
) -> Result<Vec<NodeRow>, CoreError> {
    let mut stmt = conn.prepare(sql)?;
    let rows = stmt.query_map(params, |row| {
        Ok(NodeRow {
            id: row.get("id")?,
            node_type: row.get("type")?,
            title: row.get("title")?,
            summary: row.get("summary")?,
            metadata_json: row.get("metadata_json")?,
            updated_at: row.get("updated_at")?,
        })
    })?;
    Ok(rows.filter_map(Result::ok).collect())
}

/// Python's `f"{row['title']} {row['summary']} {row['metadata_json']}"`.
///
/// A NULL column formats as the four characters `None` in Python, and the
/// re-score searches that haystack, so the port has to say `None` too.
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

fn score_key(row: &NodeRow, terms: &BTreeSet<String>) -> (usize, u8, String) {
    let hay = haystack(row);
    let hits = terms
        .iter()
        .filter(|term| hay.contains(&term.to_lowercase()))
        .count();
    let boost = u8::from(
        row.node_type
            .as_deref()
            .map(|t| TYPE_BOOST.contains(&t))
            .unwrap_or(false),
    );
    (hits, boost, row.updated_at.clone().unwrap_or_default())
}

fn as_match(row: &NodeRow) -> Map<String, Value> {
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
    item
}

fn json_opt(value: &Option<String>) -> Value {
    value
        .as_ref()
        .map(|v| Value::String(v.clone()))
        .unwrap_or(Value::Null)
}

/// `KnowledgeGraphStore.search(query, limit, allowed_workspaces=…)`.
pub fn search(
    conn: &Connection,
    query: &str,
    limit: i64,
    allowed: Option<&BTreeSet<String>>,
    include_legacy_global: bool,
) -> Result<Value, CoreError> {
    let query = query.trim().to_string();
    let like = format!("%{query}%");
    let limit = if limit == 0 { 30 } else { limit }.clamp(1, 100);
    let (nodes_table, _edges_table) = read_tables(conn);

    let mut rows: Vec<NodeRow> = Vec::new();
    if !query.is_empty() {
        let fts_ids = fts_match_ids(conn, &query, limit);
        if !fts_ids.is_empty() {
            let placeholders = vec!["?"; fts_ids.len()].join(",");
            let sql =
                format!("SELECT {NODE_COLUMNS} FROM {nodes_table} WHERE id IN ({placeholders})");
            let params: Vec<&dyn rusqlite::ToSql> = fts_ids
                .iter()
                .map(|id| id as &dyn rusqlite::ToSql)
                .collect();
            let hydrated = query_nodes(conn, &sql, &params)?;
            // Preserve the FTS rank order the ids arrived in.
            rows = fts_ids
                .iter()
                .filter_map(|id| hydrated.iter().find(|row| &row.id == id).cloned())
                .collect();
        } else {
            let sql = format!(
                "SELECT {NODE_COLUMNS} FROM {nodes_table} \
                 WHERE title LIKE ? OR summary LIKE ? OR metadata_json LIKE ? \
                 ORDER BY updated_at DESC, id ASC LIMIT ?"
            );
            rows = query_nodes(conn, &sql, rusqlite::params![like, like, like, limit])?;
        }
    }

    if (rows.len() as i64) < limit {
        let terms = topic_candidates(&query, 8);
        if !terms.is_empty() {
            let clauses =
                vec!["(title LIKE ? OR summary LIKE ? OR metadata_json LIKE ?)"; terms.len()]
                    .join(" OR ");
            let sql = format!(
                "SELECT {NODE_COLUMNS} FROM {nodes_table} WHERE {clauses} \
                 ORDER BY updated_at DESC, id ASC LIMIT ?"
            );
            let mut owned: Vec<Box<dyn rusqlite::ToSql>> = Vec::new();
            for term in &terms {
                for _ in 0..3 {
                    owned.push(Box::new(format!("%{term}%")));
                }
            }
            owned.push(Box::new(limit * 3));
            let params: Vec<&dyn rusqlite::ToSql> = owned.iter().map(|p| p.as_ref()).collect();
            let extra = query_nodes(conn, &sql, &params)?;
            // `by_id.setdefault` semantics: dedupe by id, existing rows win,
            // new ones append in their own order.
            let mut merged: Vec<NodeRow> = Vec::new();
            let mut known: BTreeSet<String> = BTreeSet::new();
            for row in rows.into_iter().chain(extra) {
                if known.insert(row.id.clone()) {
                    merged.push(row);
                }
            }
            rows = merged;
        }
    }

    let terms_for_score: BTreeSet<String> = topic_candidates(&query, 12).into_iter().collect();
    // Pass one: id ASC. Pass two: score descending, stable — so equal relevance
    // keeps the id order rather than whatever bm25 or SQLite happened to yield.
    rows.sort_by_key(|row| row.id.clone());
    rows.sort_by_key(|row| std::cmp::Reverse(score_key(row, &terms_for_score)));
    rows.truncate(limit as usize);

    let matches: Vec<Map<String, Value>> = rows.iter().map(as_match).collect();
    let matches = filter_scoped_nodes(conn, matches, allowed, include_legacy_global, |item| {
        item.get("id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string()
    })?;

    let mut out = Map::new();
    out.insert("query".into(), Value::String(query));
    out.insert(
        "matches".into(),
        Value::Array(matches.into_iter().map(Value::Object).collect()),
    );
    Ok(Value::Object(out))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> (tempfile::TempDir, Connection) {
        let dir = tempfile::tempdir().unwrap();
        let conn = Connection::open(dir.path().join("g.sqlite")).unwrap();
        conn.execute_batch(
            "CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT, title TEXT, summary TEXT,
                                metadata_json TEXT, updated_at TEXT);
             CREATE TABLE nodes_v2(id TEXT PRIMARY KEY, workspace_id TEXT);
             INSERT INTO nodes VALUES
               ('a','Decision','Alpha ranking','about ranking','{\"k\":1}','2026-01-02T00:00:00'),
               ('b','Concept','Beta ranking','about ranking','{}','2026-01-02T00:00:00'),
               ('c','Concept',NULL,NULL,NULL,NULL);
             INSERT INTO nodes_v2 VALUES ('a','w1'),('b',NULL),('c','w2');",
        )
        .unwrap();
        (dir, conn)
    }

    #[test]
    fn type_boost_and_id_order_break_ties() {
        let (_dir, conn) = fixture();
        let out = search(&conn, "ranking", 30, None, false).unwrap();
        let ids: Vec<&str> = out["matches"]
            .as_array()
            .unwrap()
            .iter()
            .map(|m| m["id"].as_str().unwrap())
            .collect();
        // Same hits and timestamp; the Decision's type boost puts it first.
        assert_eq!(&ids[..2], &["a", "b"]);
        assert_eq!(out["query"], "ranking");
    }

    #[test]
    fn null_columns_format_as_python_none() {
        // Python builds the re-score haystack with an f-string, so a NULL column
        // contributes the four characters "None" — a term that happens to be
        // "none" therefore scores a hit on an empty row. Reproduced, not fixed.
        let empty = NodeRow {
            id: "c".into(),
            node_type: Some("Concept".into()),
            title: None,
            summary: None,
            metadata_json: None,
            updated_at: None,
        };
        assert_eq!(haystack(&empty), "none none none");
        let terms: BTreeSet<String> = ["None".to_string()].into_iter().collect();
        assert_eq!(score_key(&empty, &terms), (1, 0, String::new()));

        // NULLs survive into the match as JSON nulls, and a corrupt metadata
        // blob degrades to `{}` rather than dropping the row.
        let item = as_match(&empty);
        assert_eq!(item["title"], Value::Null);
        assert_eq!(item["summary"], Value::Null);
        assert_eq!(item["updated_at"], Value::Null);
        assert_eq!(item["metadata"], serde_json::json!({}));
        assert_eq!(item["type"], "Concept");
    }

    #[test]
    fn scoping_and_limits_are_applied() {
        let (_dir, conn) = fixture();
        let w1: BTreeSet<String> = ["w1".to_string()].into_iter().collect();
        let out = search(&conn, "ranking", 30, Some(&w1), false).unwrap();
        assert_eq!(out["matches"].as_array().unwrap().len(), 1);
        let out = search(&conn, "ranking", 30, Some(&w1), true).unwrap();
        assert_eq!(out["matches"].as_array().unwrap().len(), 2);
        // limit 0 means "the default 30", and the clamp caps at 100.
        assert!(
            !search(&conn, "ranking", 0, None, false).unwrap()["matches"]
                .as_array()
                .unwrap()
                .is_empty()
        );
        assert_eq!(
            search(&conn, "ranking", 1, None, false).unwrap()["matches"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        assert!(search(&conn, "", 30, None, false).unwrap()["matches"]
            .as_array()
            .unwrap()
            .is_empty());
    }

    #[test]
    fn fts_is_skipped_below_three_characters_and_when_absent() {
        let (_dir, conn) = fixture();
        assert!(fts_match_ids(&conn, "ab", 10).is_empty());
        // No node_fts table at all → the prepare fails → empty, never a panic.
        assert!(fts_match_ids(&conn, "ranking", 10).is_empty());
        conn.execute_batch(
            "CREATE VIRTUAL TABLE node_fts USING fts5(node_id UNINDEXED, title, tokenize='trigram');
             INSERT INTO node_fts(node_id, title) VALUES ('a','Alpha ranking');",
        )
        .unwrap();
        assert_eq!(fts_match_ids(&conn, "ranking", 10), vec!["a".to_string()]);
        // An internal quote is doubled, not injected.
        assert!(fts_match_ids(&conn, "say \"hi\" now", 10).is_empty());
    }

    #[test]
    fn boost_table_is_the_python_one() {
        assert_eq!(TYPE_BOOST.len(), 12);
        assert!(TYPE_BOOST.contains(&"ImageText"));
        assert!(!TYPE_BOOST.contains(&"Concept"));
    }
}
