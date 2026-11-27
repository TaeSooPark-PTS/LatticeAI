use std::collections::BTreeSet;

use lattice_core::pytext::safe_loads;
use lattice_core::read::{filter_scoped_nodes, read_tables, NodeRow};
use lattice_core::CoreError;
use rusqlite::{params_from_iter, types::Value as SqlValue, Connection};
use serde_json::{Map, Value};

use crate::bm25::scoring::{build_node_scope_sql, detect_nodes_v2_schema};
use crate::concepts::{ranking_terms, topic_candidates, weighted_ranking_terms};
use crate::service::Scope;
use crate::shape::{haystack, json_opt};

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

const NODE_COLUMNS_N: &str = "n.id, n.type, n.title, n.summary, n.metadata_json, n.updated_at";

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
    params: Vec<SqlValue>,
) -> Result<Vec<NodeRow>, CoreError> {
    let mut stmt = conn.prepare(sql)?;
    let rows = stmt.query_map(params_from_iter(params), |row| {
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

pub const TITLE_WEIGHT: f64 = 2.0;
pub const SUMMARY_WEIGHT: f64 = 1.0;
pub const METADATA_WEIGHT: f64 = 0.3;
const BM25_K1: f64 = 1.2;
const BM25_B: f64 = 0.75;

fn fields_of(row: &NodeRow) -> [String; 3] {
    [&row.title, &row.summary, &row.metadata_json]
        .map(|value| value.as_deref().unwrap_or("None").to_lowercase())
}

fn occurrences(field: &str, term: &str) -> f64 {
    if term.is_empty() {
        0.0
    } else {
        field.matches(term).count() as f64
    }
}

pub struct LexicalStats {
    terms: Vec<(String, f64)>,
    idf: Vec<f64>,
    average_len: [f64; 3],
}

impl LexicalStats {
    pub fn over(rows: &[NodeRow], terms: Vec<(String, f64)>) -> Self {
        let fields: Vec<[String; 3]> = rows.iter().map(fields_of).collect();
        let docs = fields.len().max(1) as f64;
        let idf = terms
            .iter()
            .map(|(term, weight)| {
                let df = fields
                    .iter()
                    .filter(|row| row.iter().any(|field| field.contains(term.as_str())))
                    .count() as f64;
                weight * (1.0 + (docs - df + 0.5) / (df + 0.5)).ln()
            })
            .collect();
        let mut average_len = [0.0; 3];
        for row in &fields {
            for (slot, field) in row.iter().enumerate() {
                average_len[slot] += field.chars().count() as f64 / docs;
            }
        }
        Self {
            terms,
            idf,
            average_len,
        }
    }

    pub fn score(&self, row: &NodeRow) -> f64 {
        let fields = fields_of(row);
        let weights = [TITLE_WEIGHT, SUMMARY_WEIGHT, METADATA_WEIGHT];
        let lengths: Vec<f64> = fields
            .iter()
            .zip(self.average_len)
            .map(|(field, average)| {
                let ratio = if average > 0.0 {
                    field.chars().count() as f64 / average
                } else {
                    1.0
                };
                1.0 - BM25_B + BM25_B * ratio
            })
            .collect();
        self.terms
            .iter()
            .zip(&self.idf)
            .map(|((term, _), idf)| {
                let tf: f64 = fields
                    .iter()
                    .zip(weights)
                    .zip(&lengths)
                    .map(|((field, weight), norm)| weight * occurrences(field, term) / norm)
                    .sum();
                idf * tf / (BM25_K1 + tf)
            })
            .sum()
    }
}

fn legacy_key(row: &NodeRow, terms: &BTreeSet<String>) -> (usize, u8, String) {
    let hay = haystack(row);
    let hits = terms
        .iter()
        .filter(|term| hay.contains(&term.to_lowercase()))
        .count();
    (
        hits,
        u8::from(type_boost(row)),
        row.updated_at.clone().unwrap_or_default(),
    )
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum KeywordRanking {
    #[default]
    Legacy,
    Relevance,
}

impl KeywordRanking {
    fn pool_terms(self, query: &str, limit: usize) -> Vec<String> {
        match self {
            Self::Legacy => topic_candidates(query, limit),
            Self::Relevance => ranking_terms(query, limit),
        }
    }

    fn order(self, rows: &mut Vec<NodeRow>, query: &str) {
        match self {
            Self::Legacy => {
                let terms: BTreeSet<String> = topic_candidates(query, 12).into_iter().collect();
                rows.sort_by_key(|row| row.id.clone());
                rows.sort_by_key(|row| std::cmp::Reverse(legacy_key(row, &terms)));
            }
            Self::Relevance => {
                let stats = LexicalStats::over(rows, weighted_ranking_terms(query, 12));
                rank_rows(rows, &stats);
            }
        }
    }
}

fn type_boost(row: &NodeRow) -> bool {
    row.node_type
        .as_deref()
        .is_some_and(|t| TYPE_BOOST.contains(&t))
}

fn rank_rows(rows: &mut Vec<NodeRow>, stats: &LexicalStats) {
    let mut scored: Vec<(f64, NodeRow)> =
        rows.drain(..).map(|row| (stats.score(&row), row)).collect();
    scored.sort_by(|(left_score, left), (right_score, right)| {
        right_score
            .partial_cmp(left_score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| type_boost(right).cmp(&type_boost(left)))
            .then_with(|| right.updated_at.cmp(&left.updated_at))
            .then_with(|| left.id.cmp(&right.id))
    });
    rows.extend(scored.into_iter().map(|(_, row)| row));
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

pub fn search(
    conn: &Connection,
    query: &str,
    limit: i64,
    allowed: Option<&BTreeSet<String>>,
    include_legacy_global: bool,
) -> Result<Value, CoreError> {
    search_ranked(
        conn,
        query,
        limit,
        allowed,
        include_legacy_global,
        KeywordRanking::Legacy,
    )
}

pub fn search_ranked(
    conn: &Connection,
    query: &str,
    limit: i64,
    allowed: Option<&BTreeSet<String>>,
    include_legacy_global: bool,
    ranking: KeywordRanking,
) -> Result<Value, CoreError> {
    let query = query.trim().to_string();
    let like = format!("%{query}%");
    let limit = if limit == 0 { 30 } else { limit }.clamp(1, 100);
    let (nodes_table, _edges_table) = read_tables(conn);
    let scope = Scope {
        allowed_workspaces: allowed.cloned(),
        include_legacy_global,
    };
    let schema = detect_nodes_v2_schema(conn)?;
    let scope_parts = build_node_scope_sql(schema, "n", &scope, true);
    let scope_cond = scope_parts
        .where_clause
        .strip_prefix("WHERE ")
        .unwrap_or("")
        .to_string();
    let join_clause = scope_parts.join_clause;

    let mut rows: Vec<NodeRow> = Vec::new();
    if !query.is_empty() {
        let fts_limit = if allowed.is_some() {
            (limit * 4).min(400)
        } else {
            limit
        };
        let fts_ids = fts_match_ids(conn, &query, fts_limit);
        if !fts_ids.is_empty() {
            let placeholders = vec!["?"; fts_ids.len()].join(",");
            let extra_scope = if scope_cond.is_empty() {
                String::new()
            } else {
                format!(" AND ({scope_cond})")
            };
            let sql = format!(
                "SELECT {NODE_COLUMNS_N} FROM {nodes_table} n {join_clause} \
                 WHERE n.id IN ({placeholders}){extra_scope}"
            );
            let mut params: Vec<SqlValue> = fts_ids.iter().cloned().map(SqlValue::Text).collect();
            params.extend(scope_parts.params.iter().cloned());
            let hydrated = query_nodes(conn, &sql, params)?;
            rows = fts_ids
                .iter()
                .filter_map(|id| hydrated.iter().find(|row| &row.id == id).cloned())
                .take(limit as usize)
                .collect();
        } else {
            let extra_scope = if scope_cond.is_empty() {
                String::new()
            } else {
                format!(" AND ({scope_cond})")
            };
            let sql = format!(
                "SELECT {NODE_COLUMNS_N} FROM {nodes_table} n {join_clause} \
                 WHERE (n.title LIKE ? OR n.summary LIKE ? OR n.metadata_json LIKE ?){extra_scope} \
                 ORDER BY n.updated_at DESC, n.id ASC LIMIT ?"
            );
            let mut params = vec![
                SqlValue::Text(like.clone()),
                SqlValue::Text(like.clone()),
                SqlValue::Text(like.clone()),
            ];
            params.extend(scope_parts.params.iter().cloned());
            params.push(SqlValue::Integer(limit));
            rows = query_nodes(conn, &sql, params)?;
        }
    }

    if (rows.len() as i64) < limit {
        let terms = ranking.pool_terms(&query, 8);
        if !terms.is_empty() {
            let clauses =
                vec!["(n.title LIKE ? OR n.summary LIKE ? OR n.metadata_json LIKE ?)"; terms.len()]
                    .join(" OR ");
            let extra_scope = if scope_cond.is_empty() {
                String::new()
            } else {
                format!(" AND ({scope_cond})")
            };
            let sql = format!(
                "SELECT {NODE_COLUMNS_N} FROM {nodes_table} n {join_clause} \
                 WHERE ({clauses}){extra_scope} \
                 ORDER BY n.updated_at DESC, n.id ASC LIMIT ?"
            );
            let mut params: Vec<SqlValue> = Vec::with_capacity(terms.len() * 3 + 8);
            for term in &terms {
                let pat = SqlValue::Text(format!("%{term}%"));
                for _ in 0..3 {
                    params.push(pat.clone());
                }
            }
            params.extend(scope_parts.params.iter().cloned());
            params.push(SqlValue::Integer(limit * 3));
            let extra = query_nodes(conn, &sql, params)?;
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

    ranking.order(&mut rows, &query);

    let matches: Vec<Map<String, Value>> = rows.iter().map(as_match).collect();
    let mut matches = filter_scoped_nodes(conn, matches, allowed, include_legacy_global, |item| {
        item.get("id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string()
    })?;
    matches.truncate(limit as usize);

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

    fn row(id: &str, node_type: &str, title: &str, updated_at: &str) -> NodeRow {
        NodeRow {
            id: id.into(),
            node_type: Some(node_type.into()),
            title: Some(title.into()),
            summary: Some("about ranking".into()),
            metadata_json: Some("{}".into()),
            updated_at: Some(updated_at.into()),
        }
    }

    #[test]
    fn type_boost_then_recency_then_id_break_relevance_ties() {
        let (_dir, conn) = fixture();
        let out = search(&conn, "ranking", 30, None, false).unwrap();
        let mut ids: Vec<&str> = out["matches"]
            .as_array()
            .unwrap()
            .iter()
            .map(|m| m["id"].as_str().unwrap())
            .collect();
        ids.sort_unstable();
        assert_eq!(&ids[..2], &["a", "b"]);
        assert_eq!(out["query"], "ranking");
        let mut rows = vec![
            row("d", "Concept", "Beta ranking", "2026-01-02T00:00:00"),
            row("c", "Concept", "Beta ranking", "2026-01-02T00:00:00"),
            row("e", "Concept", "Beta ranking", "2026-01-03T00:00:00"),
            row("f", "Decision", "Beta ranking", "2026-01-01T00:00:00"),
        ];
        let stats = LexicalStats::over(&rows, vec![("ranking".into(), 1.0)]);
        rank_rows(&mut rows, &stats);
        let order: Vec<&str> = rows.iter().map(|row| row.id.as_str()).collect();
        assert_eq!(order, ["f", "e", "c", "d"]);
    }

    #[test]
    fn rarer_and_concept_terms_outrank_a_single_common_term() {
        let mut rows = vec![
            row(
                "common",
                "Document",
                "Beta ranking ranking",
                "2026-03-01T00:00:00",
            ),
            row(
                "specific",
                "Document",
                "Atlas ranking",
                "2026-01-01T00:00:00",
            ),
            row("other", "Document", "Gamma ranking", "2026-02-01T00:00:00"),
        ];
        let stats = LexicalStats::over(&rows, weighted_ranking_terms("Atlas ranking", 12));
        rank_rows(&mut rows, &stats);
        assert_eq!(rows[0].id, "specific");
    }

    #[test]
    fn null_columns_format_as_python_none() {
        let empty = NodeRow {
            id: "c".into(),
            node_type: Some("Concept".into()),
            title: None,
            summary: None,
            metadata_json: None,
            updated_at: None,
        };
        assert_eq!(
            fields_of(&empty),
            ["none", "none", "none"].map(String::from)
        );
        let stats = LexicalStats::over(std::slice::from_ref(&empty), vec![("none".into(), 1.0)]);
        assert!(stats.score(&empty) > 0.0);
        let terms: BTreeSet<String> = ["None".to_string()].into_iter().collect();
        assert_eq!(legacy_key(&empty, &terms), (1, 0, String::new()));
        assert!(!type_boost(&empty));

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
        let personal: BTreeSet<String> = [lattice_core::DEFAULT_WORKSPACE_ID.into()]
            .into_iter()
            .collect();
        let out = search(&conn, "ranking", 30, Some(&personal), false).unwrap();
        let ids: Vec<&str> = out["matches"]
            .as_array()
            .unwrap()
            .iter()
            .map(|m| m["id"].as_str().unwrap())
            .collect();
        assert_eq!(ids, vec!["b"]);
        let team: BTreeSet<String> = ["acme".into()].into_iter().collect();
        let out = search(&conn, "ranking", 30, Some(&team), false).unwrap();
        assert!(out["matches"].as_array().unwrap().is_empty());
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
        assert!(fts_match_ids(&conn, "ranking", 10).is_empty());
        conn.execute_batch(
            "CREATE VIRTUAL TABLE node_fts USING fts5(node_id UNINDEXED, title, tokenize='trigram');
             INSERT INTO node_fts(node_id, title) VALUES ('a','Alpha ranking');",
        )
        .unwrap();
        assert_eq!(fts_match_ids(&conn, "ranking", 10), vec!["a".to_string()]);
        assert!(fts_match_ids(&conn, "say \"hi\" now", 10).is_empty());
    }

    #[test]
    fn boost_table_is_the_python_one() {
        assert_eq!(TYPE_BOOST.len(), 12);
        assert!(TYPE_BOOST.contains(&"ImageText"));
        assert!(!TYPE_BOOST.contains(&"Concept"));
    }
}
