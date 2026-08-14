//! The two graph reads a chat turn makes, and the trace it records.
//!
//! * [`build_context_quality`] — `chat_helpers.build_context_quality`, the
//!   honest RAG signal the SPA renders as "근거 있음 / 제한적".
//! * [`build_graph_trace`] — `WorkspaceGraphTrace.build_graph_trace`, the
//!   answer trace with its source files, neighbour edges and confidence.
//! * [`trace_record`] — the row `record_trace` would store.
//!
//! Retrieval itself is **not** reimplemented here: the matches come from
//! `lattice_retrieval::keyword::search` and `lattice_retrieval::hybrid` — the
//! same engines the `/rust/search/*` lanes already prove against goldens. What
//! this module adds is the reshaping the chat trace does on top of them, plus
//! the one-hop edge read (`neighbors`) that had no Rust caller before.
//!
//! Neither call may fail a turn. Python wraps both in `except Exception` and
//! answers with an empty-but-shaped block; so does this.

use std::collections::BTreeSet;

use lattice_core::embeddings::LocalEmbeddingModel;
use lattice_core::read::read_tables;
use lattice_retrieval::hybrid::{hybrid_search, HybridOptions};
use lattice_retrieval::shape::{context_quality_signal, multimodal_signal};
use rusqlite::Connection;
use serde_json::{json, Map, Value};

use crate::pyvalue::field;

/// How many matches the answer trace seeds from.
pub const TRACE_LIMIT: i64 = 8;
/// How many matches contribute neighbour edges.
const EDGE_SEED_MATCHES: usize = 5;
/// The hard cap on collected edges.
const MAX_EDGES: usize = 24;
/// `build_context_quality`'s default match count.
pub const QUALITY_LIMIT: i64 = 6;

/// `build_context_quality` — never raises, always four keys.
///
/// The Python original prefers `hybrid_search` and falls back to `search` for a
/// store without the hybrid mixin. Every store this gateway opens has it, so
/// the lexical fallback here is the *failure* branch rather than a second
/// capability probe — and it reports `lexical_only`, which is what the fallback
/// meant.
pub fn build_context_quality(
    conn: Option<&Connection>,
    model: &LocalEmbeddingModel,
    query: &str,
    allowed_workspaces: Option<&BTreeSet<String>>,
    limit: i64,
    now_secs: f64,
) -> Value {
    let query = query.trim();
    let Some(conn) = conn.filter(|_| !query.is_empty()) else {
        return context_quality_signal(
            "none",
            0,
            Some("지식 그래프 컨텍스트를 사용하지 않았습니다"),
            None,
        );
    };
    let options = HybridOptions {
        top_k: limit,
        allowed_workspaces: allowed_workspaces.cloned(),
        now_secs,
        ..Default::default()
    };
    match hybrid_search(conn, model, query, &options) {
        Ok(result) => {
            let matches: Vec<Value> = result
                .get("matches")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            let mode = result
                .get("mode")
                .and_then(Value::as_str)
                .filter(|mode| !mode.is_empty())
                .unwrap_or("hybrid");
            context_quality_signal(
                mode,
                matches.len() as i64,
                None,
                multimodal_signal(&matches),
            )
        }
        Err(_) => context_quality_signal("none", 0, Some("그래프 검색에 실패했습니다"), None),
    }
}

/// `retrieval_reads.neighbors`' edge half — 1-hop edges, in insertion order.
///
/// Only the edges are read: `build_graph_trace` uses nothing else from the
/// neighbour payload, and loading the neighbour *nodes* would be a second query
/// whose result is discarded.
pub fn neighbor_edges(conn: &Connection, node_id: &str) -> Vec<Value> {
    let (_, edges_table) = read_tables(conn);
    let sql = format!(
        "SELECT from_node, to_node, type, weight FROM {edges_table} \
         WHERE from_node=? OR to_node=? ORDER BY id ASC"
    );
    let Ok(mut statement) = conn.prepare(&sql) else {
        return Vec::new();
    };
    let rows = statement.query_map([node_id, node_id], |row| {
        Ok(json!({
            "from": lattice_core::read::column_json(row, "from_node")?,
            "to": lattice_core::read::column_json(row, "to_node")?,
            "type": lattice_core::read::column_json(row, "type")?,
            "weight": lattice_core::read::column_json(row, "weight")?,
        }))
    });
    match rows {
        Ok(rows) => rows.filter_map(Result::ok).collect(),
        Err(_) => Vec::new(),
    }
}

/// The trace a turn with no graph produces.
fn traceless(question: &str, context: &str) -> Value {
    json!({
        "source_files": [],
        "graph_nodes": [],
        "graph_edges": [],
        "confidence": 0.0,
        "retrieval_metadata": {
            "query": question,
            "matched_nodes": 0,
            "graph_enabled": false,
            "context_chars": context.chars().count(),
        },
    })
}

/// The first metadata key that names a file, in the Python `or` order.
fn source_of(node: &Value) -> Option<String> {
    let meta = node.get("metadata")?.as_object()?;
    for key in [
        "relative_path",
        "file_path",
        "filename",
        "blob_path",
        "source",
    ] {
        if let Some(Value::String(value)) = meta.get(key) {
            if !value.is_empty() {
                return Some(value.clone());
            }
        }
    }
    None
}

/// `WorkspaceGraphTrace.build_graph_trace`.
pub fn build_graph_trace(
    conn: Option<&Connection>,
    question: &str,
    context: &str,
    limit: i64,
    allowed_workspaces: Option<&BTreeSet<String>>,
) -> Value {
    let Some(conn) = conn else {
        return traceless(question, context);
    };
    let (matches, search_error) = match lattice_retrieval::keyword::search(
        conn,
        question,
        limit,
        allowed_workspaces,
        false,
    ) {
        Ok(result) => (
            result
                .get("matches")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default(),
            String::new(),
        ),
        Err(error) => (Vec::new(), error.to_string()),
    };

    let mut source_files: Vec<Value> = Vec::new();
    let mut seen_sources: BTreeSet<String> = BTreeSet::new();
    for node in &matches {
        let Some(source) = source_of(node) else {
            continue;
        };
        if !seen_sources.insert(source.clone()) {
            continue;
        }
        let node_id = node.get("id").cloned().unwrap_or(Value::Null);
        source_files.push(json!({
            "source": source,
            "node_id": node_id,
            "node_title": node.get("title").cloned().unwrap_or(Value::Null),
            "node_type": node.get("type").cloned().unwrap_or(Value::Null),
            "jump": {
                "graph": format!("/graph?node={}", field(node, "id")),
                "source": source,
            },
        }));
    }

    let mut edges: Vec<Value> = Vec::new();
    let mut edge_seen: BTreeSet<String> = BTreeSet::new();
    'seeds: for node in matches.iter().take(EDGE_SEED_MATCHES) {
        let node_id = field(node, "id");
        if node_id.is_empty() {
            continue;
        }
        for edge in neighbor_edges(conn, &node_id) {
            let key = format!(
                "{}\u{1}{}\u{1}{}",
                field(&edge, "from"),
                field(&edge, "to"),
                field(&edge, "type")
            );
            if !edge_seen.insert(key) {
                continue;
            }
            edges.push(edge);
            if edges.len() >= MAX_EDGES {
                // Python `break`s the inner loop only, then `continue`s to the
                // next seed — where the cap immediately stops it again. Leaving
                // the whole scan is the same answer without the extra queries.
                break 'seeds;
            }
        }
    }

    let confidence = if matches.is_empty() {
        if context.is_empty() {
            0.0
        } else {
            0.05
        }
    } else {
        let capped = matches.len().min(limit.max(0) as usize) as f64;
        let bonus = if edges.is_empty() { 0.0 } else { 0.10 };
        (0.35 + capped / limit.max(1) as f64 * 0.45 + bonus).min(0.95)
    };

    json!({
        "source_files": source_files,
        "graph_nodes": matches,
        "graph_edges": edges,
        "confidence": lattice_core::pytext::round4(confidence),
        "retrieval_metadata": {
            "query": question,
            "matched_nodes": matches.len(),
            "matched_edges": edges.len(),
            "graph_enabled": true,
            "context_chars": context.chars().count(),
            "search_error": search_error,
        },
    })
}

/// `workspace_os_utils._json_hash` for a list of scalars.
///
/// `json.dumps` defaults to `", "` between items — the *only* place in the
/// product that is not the compact `","`, and the reason this renders the list
/// by hand instead of calling `serde_json::to_string`.
fn json_hash(values: &[Value]) -> String {
    use sha2::{Digest, Sha256};

    let rendered: Vec<String> = values
        .iter()
        .map(|value| serde_json::to_string(value).unwrap_or_else(|_| "null".into()))
        .collect();
    let payload = format!("[{}]", rendered.join(", "));
    format!("{:x}", Sha256::digest(payload.as_bytes()))
}

/// `record_trace`'s row, without the storage.
///
/// The id is `trace-` plus the first 16 hex characters of
/// `_json_hash([question, response, conversation_id, now])` — sha256 over a
/// `json.dumps(..., ensure_ascii=False, sort_keys=True)` payload, whose
/// separators are Python's spaced defaults, not the compact ones. The caller
/// supplies the timestamp so a test can freeze it.
///
/// The trace's own keys are merged **over** the record's, exactly as
/// `{**record, **trace}` does — a trace carrying `id` would win, and none does.
pub fn trace_record(
    question: &str,
    response: &str,
    conversation_id: Option<&str>,
    user_email: Option<&str>,
    workspace_id: Option<&str>,
    created_at: &str,
    trace: &Value,
) -> Value {
    let digest = json_hash(&[
        json!(question),
        json!(response),
        json!(conversation_id),
        json!(created_at),
    ]);
    let mut record = Map::new();
    record.insert(
        "id".into(),
        json!(format!("trace-{}", &digest[..16.min(digest.len())])),
    );
    record.insert("question".into(), json!(question));
    record.insert(
        "response_preview".into(),
        json!(response.chars().take(700).collect::<String>()),
    );
    record.insert("conversation_id".into(), json!(conversation_id));
    record.insert("user_email".into(), json!(user_email));
    record.insert("workspace_id".into(), json!(workspace_id));
    record.insert("created_at".into(), json!(created_at));
    if let Some(entries) = trace.as_object() {
        for (key, value) in entries {
            record.insert(key.clone(), value.clone());
        }
    }
    Value::Object(record)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn graph() -> (tempfile::TempDir, Connection) {
        let dir = tempfile::tempdir().unwrap();
        let conn = Connection::open(dir.path().join("g.sqlite")).unwrap();
        conn.execute_batch(
            "CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT, title TEXT, summary TEXT,
               metadata_json TEXT, created_at TEXT, updated_at TEXT, user_email TEXT,
               workspace_id TEXT, organization_id TEXT);
             CREATE TABLE edges(id INTEGER PRIMARY KEY AUTOINCREMENT, from_node TEXT,
               to_node TEXT, type TEXT, weight REAL, metadata_json TEXT, created_at TEXT,
               user_email TEXT, workspace_id TEXT, organization_id TEXT);
             INSERT INTO nodes VALUES
               ('n1','Document','리트리벌 가중치','정리',
                '{\"relative_path\":\"weights.md\"}','2026-01-01','2026-01-01',NULL,NULL,NULL),
               ('n2','Document','리트리벌 노트','메모','{}','2026-01-01','2026-01-01',
                NULL,NULL,NULL);
             INSERT INTO edges(from_node,to_node,type,weight,metadata_json,created_at)
               VALUES ('n1','n2','relates_to',1.0,'{}','2026-01-01'),
                      ('n1','n2','relates_to',1.0,'{}','2026-01-01');",
        )
        .unwrap();
        (dir, conn)
    }

    #[test]
    fn a_turn_with_no_graph_gets_a_shaped_empty_trace() {
        let trace = build_graph_trace(None, "질문", "ctx", TRACE_LIMIT, None);
        assert_eq!(trace["confidence"], 0.0);
        assert_eq!(trace["retrieval_metadata"]["graph_enabled"], false);
        assert_eq!(trace["retrieval_metadata"]["context_chars"], 3);
        assert!(trace["graph_nodes"].as_array().unwrap().is_empty());
        assert!(trace["retrieval_metadata"].get("search_error").is_none());
    }

    #[test]
    fn a_trace_collects_sources_edges_and_a_confidence() {
        let (_dir, conn) = graph();
        let trace = build_graph_trace(Some(&conn), "리트리벌", "context", TRACE_LIMIT, None);
        assert_eq!(trace["graph_nodes"].as_array().unwrap().len(), 2);
        // Only n1 carries a file-shaped metadata key.
        assert_eq!(trace["source_files"].as_array().unwrap().len(), 1);
        assert_eq!(trace["source_files"][0]["source"], "weights.md");
        assert_eq!(trace["source_files"][0]["jump"]["graph"], "/graph?node=n1");
        // The duplicate (from, to, type) is collapsed.
        assert_eq!(trace["graph_edges"].as_array().unwrap().len(), 1);
        assert_eq!(trace["retrieval_metadata"]["matched_edges"], 1);
        assert_eq!(trace["retrieval_metadata"]["search_error"], "");
        // 0.35 + 2/8*0.45 + 0.10 (edges present).
        assert_eq!(trace["confidence"], 0.5625);
    }

    #[test]
    fn confidence_is_the_context_floor_when_nothing_matched() {
        let (_dir, conn) = graph();
        let empty = build_graph_trace(Some(&conn), "zzzzz", "some context", TRACE_LIMIT, None);
        assert_eq!(empty["confidence"], 0.05);
        let bare = build_graph_trace(Some(&conn), "zzzzz", "", TRACE_LIMIT, None);
        assert_eq!(bare["confidence"], 0.0);
    }

    #[test]
    fn neighbour_edges_are_empty_for_an_unknown_node_and_a_broken_schema() {
        let (_dir, conn) = graph();
        assert!(neighbor_edges(&conn, "nope").is_empty());
        let dir = tempfile::tempdir().unwrap();
        let bare = Connection::open(dir.path().join("empty.sqlite")).unwrap();
        assert!(neighbor_edges(&bare, "n1").is_empty(), "no edges table");
    }

    #[test]
    fn context_quality_reports_the_no_graph_reason() {
        let model = LocalEmbeddingModel::from_env();
        let signal = build_context_quality(None, &model, "질문", None, QUALITY_LIMIT, 0.0);
        assert_eq!(signal["mode"], "none");
        assert_eq!(signal["limited"], true);
        assert_eq!(
            signal["reason"],
            "지식 그래프 컨텍스트를 사용하지 않았습니다"
        );
        let (_dir, conn) = graph();
        let empty_query =
            build_context_quality(Some(&conn), &model, "   ", None, QUALITY_LIMIT, 0.0);
        assert_eq!(empty_query["nodes"], 0);
    }

    #[test]
    fn context_quality_reports_a_failed_search_rather_than_raising() {
        let model = LocalEmbeddingModel::from_env();
        let dir = tempfile::tempdir().unwrap();
        let bare = Connection::open(dir.path().join("empty.sqlite")).unwrap();
        let signal = build_context_quality(Some(&bare), &model, "질문", None, QUALITY_LIMIT, 0.0);
        assert_eq!(signal["mode"], "none");
        assert_eq!(signal["reason"], "그래프 검색에 실패했습니다");
    }

    #[test]
    fn a_trace_record_merges_the_trace_over_its_own_keys() {
        let record = trace_record(
            "q",
            &"a".repeat(800),
            Some("c1"),
            Some("owner@x"),
            Some("personal"),
            "2026-08-14T00:00:00",
            &json!({"confidence": 0.5, "grounding": {"status": "supported"}}),
        );
        assert!(record["id"].as_str().unwrap().starts_with("trace-"));
        assert_eq!(record["id"].as_str().unwrap().len(), "trace-".len() + 16);
        assert_eq!(record["response_preview"].as_str().unwrap().len(), 700);
        assert_eq!(record["conversation_id"], "c1");
        assert_eq!(record["workspace_id"], "personal");
        assert_eq!(record["confidence"], 0.5);
        assert_eq!(record["grounding"]["status"], "supported");
        // Deterministic in its four inputs.
        assert_eq!(
            record["id"],
            trace_record(
                "q",
                &"a".repeat(800),
                Some("c1"),
                Some("owner@x"),
                Some("personal"),
                "2026-08-14T00:00:00",
                &json!({}),
            )["id"]
        );
        let anonymous = trace_record("q", "a", None, None, None, "t", &Value::Null);
        assert_eq!(anonymous["conversation_id"], Value::Null);
    }
}
