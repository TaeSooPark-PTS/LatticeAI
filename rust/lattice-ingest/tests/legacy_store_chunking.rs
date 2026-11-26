use std::path::PathBuf;
use std::sync::Arc;

use lattice_core::db::Store;
use lattice_core::embeddings::LocalEmbeddingModel;
use lattice_core::graph_write::types::{ChunkPiece, IngestContentRequest};
use lattice_core::graph_write::GraphWriter;
use lattice_ingest::{sync_document_incremental, IncrementalSyncRequest};
use rusqlite::Connection;
use serde_json::{json, Map};

fn fixture_store() -> PathBuf {
    [
        env!("CARGO_MANIFEST_DIR"),
        "..",
        "fixtures",
        "parity_store.sqlite",
    ]
    .iter()
    .collect()
}

fn count(conn: &Connection, sql: &str) -> i64 {
    conn.query_row(sql, [], |r| r.get(0)).expect(sql)
}

fn section(name: &str, word: &str, words: usize) -> String {
    let body = (0..words)
        .map(|i| format!("{word}{}", i % 89))
        .collect::<Vec<_>>()
        .join(" ");
    format!("# {name}\n{body}")
}

fn request(text: &str, at: &str) -> IncrementalSyncRequest {
    IncrementalSyncRequest {
        title: "handbook.md".into(),
        text: text.into(),
        source_uri: "/vault/handbook.md".into(),
        moved_from_uri: None,
        workspace_id: Some("personal".into()),
        owner: Some("owner@local".into()),
        modified_at: Some(at.into()),
        metadata: Map::new(),
    }
}

#[test]
fn a_python_era_store_opens_additively_and_keeps_its_rows_readable() {
    let dir = tempfile::tempdir().expect("tempdir");
    let path = dir.path().join("knowledge_graph.sqlite");
    std::fs::copy(fixture_store(), &path).expect("copy the legacy store");
    let before = {
        let conn = Connection::open(&path).expect("legacy conn");
        let tables = count(
            &conn,
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='chunk_parents'",
        );
        assert_eq!(tables, 0, "the fixture predates parent-child chunking");
        let texts: Vec<String> = conn
            .prepare("SELECT text FROM chunks ORDER BY id")
            .expect("stmt")
            .query_map([], |r| r.get(0))
            .expect("rows")
            .collect::<Result<_, _>>()
            .expect("texts");
        texts
    };
    let store = Arc::new(Store::open(&path).expect("store"));
    let writer = GraphWriter::open(store, dir.path().join("blobs")).expect("writer");
    let model = LocalEmbeddingModel::new(384);
    let contract = model.contract();
    let receipt = sync_document_incremental(
        &writer,
        &request(
            &format!(
                "{}\n\n{}",
                section("Ops", "restart", 700),
                section("Keys", "rotate", 700)
            ),
            "2026-11-26T01:00:00Z",
        ),
        &contract,
        |texts| Ok(texts.iter().map(|t| model.embed(t)).collect()),
    )
    .expect("sync on a legacy store");
    assert_eq!(receipt.document_version, 1);
    let conn = Connection::open(&path).expect("conn");
    let after: Vec<String> = conn
        .prepare(
            "SELECT text FROM chunks WHERE json_extract(metadata_json, '$.chunker') IS NULL ORDER BY id",
        )
        .expect("stmt")
        .query_map([], |r| r.get(0))
        .expect("rows")
        .collect::<Result<_, _>>()
        .expect("texts");
    assert_eq!(after, before, "legacy chunk rows are untouched");
    assert!(count(&conn, "SELECT COUNT(*) FROM chunk_parents") > 0);
    assert_eq!(
        count(
            &conn,
            "SELECT COUNT(*) FROM chunks c WHERE json_extract(c.metadata_json, '$.parent_id') IS NOT NULL
               AND json_extract(c.metadata_json, '$.parent_id') NOT IN (SELECT id FROM chunk_parents)"
        ),
        0,
        "every child points at a stored parent"
    );
}

#[test]
fn a_content_edit_bumps_the_document_version_and_purges_every_stale_index() {
    let dir = tempfile::tempdir().expect("tempdir");
    let path = dir.path().join("knowledge_graph.sqlite");
    let store = Arc::new(Store::open(&path).expect("store"));
    let writer = GraphWriter::open(store, dir.path().join("blobs")).expect("writer");
    let model = LocalEmbeddingModel::new(384);
    let contract = model.contract();
    let mut embedded = 0usize;
    let mut embed = |texts: &[String]| -> Result<Vec<Vec<f64>>, String> {
        embedded += texts.len();
        Ok(texts.iter().map(|t| model.embed(t)).collect())
    };

    let legacy_text = format!(
        "{}\n\n{}",
        section("Ops", "restart", 700),
        section("Keys", "rotate", 700)
    );
    let legacy = writer
        .ingest_content(&IngestContentRequest {
            source_type: "note".into(),
            title: "handbook.md".into(),
            text: legacy_text.clone(),
            source_uri: Some("/vault/handbook.md".into()),
            workspace_id: Some("personal".into()),
            node_type: Some("Document".into()),
            chunks: vec![ChunkPiece {
                text: legacy_text.chars().take(1200).collect(),
                fields: {
                    let mut fields = Map::new();
                    fields.insert("strategy".into(), json!("markdown"));
                    fields
                },
                embedding: None,
            }],
            ..Default::default()
        })
        .expect("pre-14.2 style ingest");

    let first = sync_document_incremental(
        &writer,
        &request(&legacy_text, "2026-11-26T01:00:00Z"),
        &contract,
        &mut embed,
    )
    .expect("first sync");
    assert_eq!(
        first.reembedded_chunks, first.total_chunks,
        "rechunked contextual pieces never reuse a legacy vector"
    );
    assert_eq!(first.embedder_calls, 1);
    assert_eq!(
        first.node_id, legacy.node_id,
        "same content keeps its node id"
    );
    assert_eq!(first.document_version, 1);
    assert_eq!(first.purged_chunks, 1, "the legacy chunk row is purged");

    let conn = Connection::open(&path).expect("conn");
    let live = |conn: &Connection| {
        count(
            conn,
            "SELECT COUNT(*) FROM chunks WHERE source_node IN
               (SELECT id FROM nodes WHERE type='Document' AND json_extract(metadata_json,'$.superseded_by') IS NULL)",
        )
    };
    assert_eq!(live(&conn), first.total_chunks as i64);

    let edited = format!(
        "{}\n\n{}",
        section("Ops", "restart", 700),
        section("Keys", "renew", 700)
    );
    let second = sync_document_incremental(
        &writer,
        &request(&edited, "2026-11-26T02:00:00Z"),
        &contract,
        &mut embed,
    )
    .expect("second sync");
    assert_eq!(second.document_version, 2);
    assert_eq!(
        second.previous_node_id.as_deref(),
        Some(first.node_id.as_str())
    );
    assert!(second.reused_chunks > 0, "{second:?}");
    assert!(second.reembedded_chunks < second.total_chunks);
    assert!(second.purged_chunks > 0);

    assert_eq!(
        count(&conn, "SELECT COUNT(*) FROM chunks"),
        second.total_chunks as i64
    );
    assert_eq!(
        count(&conn, "SELECT COUNT(*) FROM nodes WHERE type='Chunk'"),
        second.total_chunks as i64
    );
    assert_eq!(
        count(
            &conn,
            "SELECT COUNT(*) FROM vector_embeddings WHERE item_type='chunk'"
        ),
        second.total_chunks as i64
    );
    assert_eq!(
        count(
            &conn,
            "SELECT COUNT(*) FROM bm25_node_stats WHERE substr(node_id,1,6)='chunk:'
               AND node_id NOT IN (SELECT id FROM chunks)"
        ),
        0,
        "no BM25F stats outlive their chunk"
    );
    assert_eq!(
        count(
            &conn,
            "SELECT COUNT(*) FROM chunk_parents WHERE source_node <> (SELECT ?)"
                .replace("(SELECT ?)", &format!("'{}'", second.node_id))
                .as_str()
        ),
        0,
        "parents of the superseded revision are purged"
    );
    assert_eq!(
        count(
            &conn,
            "SELECT COUNT(*) FROM chunks WHERE text LIKE '%rotate1 %'"
        ),
        0
    );
    let version: i64 = conn
        .query_row(
            "SELECT json_extract(metadata_json, '$.document_version') FROM nodes WHERE id = ?1",
            [second.node_id.as_str()],
            |r| r.get(0),
        )
        .expect("version");
    assert_eq!(version, 2);
    let prefixed = count(
        &conn,
        "SELECT COUNT(*) FROM chunks WHERE json_extract(metadata_json, '$.context_prefix') LIKE 'Document: handbook.md%'",
    );
    assert_eq!(prefixed, second.total_chunks as i64);
    let unchanged = sync_document_incremental(
        &writer,
        &request(&edited, "2026-11-26T03:00:00Z"),
        &contract,
        &mut embed,
    )
    .expect("unchanged sync");
    assert!(unchanged.duplicate);
    assert_eq!(unchanged.embedder_calls, 0);
    assert_eq!(unchanged.document_version, 2);
    assert!(embedded > 0);
}
