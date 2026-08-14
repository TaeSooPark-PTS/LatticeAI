//! Unit tests for [`super::kg`].
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
use super::kg::*;
use serde_json::{Map, Value};
use std::collections::BTreeSet;

pub(crate) fn seeded() -> (tempfile::TempDir, rusqlite::Connection) {
    let dir = tempfile::tempdir().expect("tempdir");
    let conn = rusqlite::Connection::open(dir.path().join("kg.sqlite")).expect("open");
    conn.execute_batch(
        "CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT, title TEXT, summary TEXT,
                            metadata_json TEXT, created_at TEXT, updated_at TEXT);
         CREATE TABLE edges(id TEXT PRIMARY KEY, from_node TEXT, to_node TEXT, type TEXT,
                            weight REAL, metadata_json TEXT, created_at TEXT);
         CREATE TABLE nodes_v2(id TEXT PRIMARY KEY, type TEXT, workspace_id TEXT);
         CREATE TABLE edges_v2(id TEXT PRIMARY KEY, source TEXT, target TEXT, type TEXT);
         CREATE TABLE chunks(id TEXT PRIMARY KEY, source_node TEXT, text TEXT,
                             metadata_json TEXT, created_at TEXT);
         CREATE TABLE knowledge_sources(id TEXT PRIMARY KEY);
         CREATE TABLE local_file_index(id TEXT PRIMARY KEY, status TEXT);
         CREATE TABLE graph_meta(key TEXT PRIMARY KEY, value TEXT);
         CREATE TABLE vector_jobs(id INTEGER PRIMARY KEY, status TEXT);
         CREATE TABLE vector_embeddings(item_id TEXT PRIMARY KEY, item_type TEXT,
             source_node TEXT, text_hash TEXT, embedding_dim INTEGER, embedding_model TEXT,
             indexed_at TEXT);
         CREATE TABLE vector_index_operations(id INTEGER PRIMARY KEY, operation TEXT,
             status TEXT, requested_at TEXT, started_at TEXT, completed_at TEXT,
             items_total INTEGER, items_indexed INTEGER, items_skipped INTEGER,
             error_message TEXT, metadata_json TEXT);
         INSERT INTO nodes VALUES
           ('a','Decision','Alpha ranking','about ranking','{}','2026-01-01T00:00:00','2026-01-02T00:00:00'),
           ('b','Concept','Beta ranking','also ranking','{\"source\":\"upload\"}','2026-01-01T00:00:00','2026-01-03T00:00:00'),
           ('c','Chunk','chunk','','{}','2026-01-01T00:00:00','2026-01-01T00:00:00');
         INSERT INTO chunks VALUES ('c','a','chunk body','{}','2026-01-01T00:00:00');
         INSERT INTO nodes_v2 VALUES ('a','DECISION','w1'),('b','CONCEPT','w1'),('c','CHUNK','w1');
         INSERT INTO edges_v2 VALUES ('e1','a','b','MENTIONS');
         INSERT INTO edges VALUES ('e1','a','b','MENTIONS',0.9,'{}','2026-02-01T00:00:00');
         INSERT INTO knowledge_sources VALUES ('s1');
         INSERT INTO local_file_index VALUES ('f1','indexed');
         INSERT INTO vector_index_operations VALUES
           (1,'rebuild','completed','2026-02-01T00:00:00','2026-02-01T00:00:00',
            '2026-02-01T00:00:01',2,2,0,NULL,'{\"duration_ms\": 500}');",
    )
    .expect("schema");
    (dir, conn)
}

#[test]
fn stats_counts_both_schemas_and_the_local_bookkeeping() {
    let (_dir, conn) = seeded();
    let payload = stats(&conn, "/tmp/kg.sqlite", None).expect("stats");
    let rendered = serde_json::to_value(&payload).expect("json");
    assert_eq!(rendered["nodes"]["Decision"], 1);
    assert_eq!(rendered["edges"]["MENTIONS"], 1);
    assert_eq!(rendered["local_sources"], 1);
    assert_eq!(rendered["local_file_status"]["indexed"], 1);
    assert_eq!(rendered["v2"]["nodes"], 3);
    assert_eq!(rendered["v2"]["by_node_type"]["CHUNK"], 1);
    assert_eq!(rendered["schema_version"], 1);
}

#[test]
fn a_scope_narrows_the_histograms_and_zeroes_the_machine_local_counts() {
    let (_dir, conn) = seeded();
    let allowed: BTreeSet<String> = ["w1".to_string()].into_iter().collect();
    let scoped =
        serde_json::to_value(stats(&conn, "x", Some(&allowed)).expect("scoped")).expect("json");
    assert_eq!(scoped["local_sources"], 0);
    assert_eq!(scoped["v2"]["edges"], 1);
    let none: BTreeSet<String> = BTreeSet::new();
    let empty = serde_json::to_value(stats(&conn, "x", Some(&none)).expect("empty")).expect("json");
    assert_eq!(
        empty["v2"]["nodes"], 0,
        "an empty scope reads nothing, not everything"
    );
}

#[test]
fn the_graph_slice_keeps_the_from_and_to_keys_the_quality_layer_needs() {
    let (_dir, conn) = seeded();
    let slice = graph_slice(&conn, 800, None).expect("slice");
    assert_eq!(slice.nodes.len(), 2, "Chunk is not a visible type");
    assert_eq!(slice.nodes[0]["id"], "b", "updated_at DESC");
    assert_eq!(slice.edges.len(), 1);
    assert_eq!(slice.edges[0]["from"], "a");
    assert_eq!(slice.edges[0]["to"], "b");
    let allowed: BTreeSet<String> = ["other".to_string()].into_iter().collect();
    let scoped = graph_slice(&conn, 800, Some(&allowed)).expect("scoped");
    assert!(scoped.nodes.is_empty() && scoped.edges.is_empty());
}

#[test]
fn an_unindexed_store_reports_every_item_missing() {
    let (_dir, conn) = seeded();
    let status = index_status(&conn, "/tmp/kg.sqlite").expect("status");
    let rendered = serde_json::to_value(&status).expect("json");
    assert_eq!(rendered["status"], "needs_reindex");
    assert_eq!(rendered["source_items"], 3, "two nodes plus one chunk");
    assert_eq!(rendered["missing_items"], 3);
    assert_eq!(rendered["scale"]["coverage_ratio"], 0.0);
    assert_eq!(rendered["scale"]["backlog_reasons"]["missing_vector"], 3);
    assert_eq!(
        rendered["scale"]["latency_budget"]["last_rebuild_duration_ms"],
        500.0
    );
    assert_eq!(rendered["scale"]["latency_budget"]["within_target"], true);
    assert_eq!(rendered["operations"].as_array().expect("ops").len(), 1);
    let summary = vector_freshness_summary(&conn, &status);
    assert_eq!(
        serde_json::to_value(&summary).expect("json")["detail"],
        "3 of 3 items are missing or stale in the vector index"
    );
    let breakdown = vector_freshness_breakdown(&conn, &status, &summary);
    let breakdown = serde_json::to_value(&breakdown).expect("json");
    assert_eq!(breakdown["missing"], 3);
    assert_eq!(breakdown["queued"], 0);
}

#[test]
fn a_matching_embedding_row_reads_as_ready() {
    let (_dir, conn) = seeded();
    let model = lattice_core::LocalEmbeddingModel::from_env();
    for item in source_items(&conn).expect("items") {
        conn.execute(
            "INSERT INTO vector_embeddings VALUES (?,?,?,?,?,?,?)",
            rusqlite::params![
                item.item_id,
                item.item_type,
                item.source_node,
                item.text_hash,
                model.dim() as i64,
                model.model_id(),
                "2026-02-01T00:00:00"
            ],
        )
        .expect("insert");
    }
    let status = index_status(&conn, "x").expect("status");
    let rendered = serde_json::to_value(&status).expect("json");
    assert_eq!(rendered["status"], "ready");
    assert_eq!(rendered["ready_items"], 3);
    assert_eq!(rendered["scale"]["coverage_ratio"], 1.0);
    assert_eq!(rendered["by_item_type"]["node"], 2);
    let summary = serde_json::to_value(vector_freshness_summary(&conn, &status)).expect("json");
    assert_eq!(summary["status"], "ready");
    assert_eq!(summary["detail"], "vector index is up to date");
}

#[test]
fn a_recorded_fingerprint_that_disagrees_is_reported_stale() {
    let (_dir, conn) = seeded();
    conn.execute(
        "INSERT INTO graph_meta VALUES (?, ?)",
        rusqlite::params![
            EMBEDDER_FINGERPRINT_KEY,
            "{\"model_id\": \"old\", \"dim\": 8}"
        ],
    )
    .expect("fingerprint");
    conn.execute(
        "INSERT INTO vector_embeddings VALUES ('a','node','a','h',8,'old','2026-01-01T00:00:00')",
        [],
    )
    .expect("row");
    let status = index_status(&conn, "x").expect("status");
    assert_eq!(
        serde_json::to_value(&status).expect("json")["embedder"]["stale_embedder"],
        true
    );
    let summary = serde_json::to_value(vector_freshness_summary(&conn, &status)).expect("json");
    assert_eq!(summary["status"], "stale_embedder");
    assert!(summary["detail"]
        .as_str()
        .expect("detail")
        .contains("old →"));
}

#[test]
fn one_node_is_read_back_whole_or_not_at_all() {
    let (_dir, conn) = seeded();
    let node = get_node(&conn, "a").expect("read").expect("present");
    assert_eq!(node["title"], "Alpha ranking");
    assert_eq!(node["metadata"], serde_json::json!({}));
    assert!(get_node(&conn, "nope").expect("read").is_none());
}

#[test]
fn the_vector_text_is_the_writer_s_own_join() {
    let mut metadata = Map::new();
    metadata.insert("filename".into(), Value::String("notes.md".into()));
    metadata.insert("role".into(), Value::String("user".into()));
    metadata.insert("ignored".into(), Value::String("no".into()));
    assert_eq!(
        vector_text_for_node("Title", "Summary", &metadata),
        "Title Summary notes.md user"
    );
    assert_eq!(vector_text_for_node("", "", &Map::new()), "");
}
