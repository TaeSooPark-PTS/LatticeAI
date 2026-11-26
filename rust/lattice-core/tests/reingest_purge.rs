use std::sync::Arc;

use lattice_core::db::Store;
use lattice_core::graph_write::chunk_parents::PARENT_INDEX_FIELD;
use lattice_core::graph_write::types::{
    ChunkParentPiece, ChunkPiece, IngestContentRequest, IngestFileRequest,
};
use lattice_core::graph_write::GraphWriter;
use serde_json::{json, Map};

fn writer(dir: &tempfile::TempDir) -> GraphWriter {
    let store = Arc::new(Store::open(&dir.path().join("knowledge_graph.sqlite")).expect("db"));
    GraphWriter::open(store, dir.path().join("blobs")).expect("writer")
}

fn pieces(texts: &[&str], parented: bool) -> (Vec<ChunkPiece>, Vec<ChunkParentPiece>) {
    let chunks = texts
        .iter()
        .map(|text| {
            let mut fields = Map::new();
            if parented {
                fields.insert(PARENT_INDEX_FIELD.into(), json!(0));
            }
            ChunkPiece {
                text: (*text).into(),
                fields,
                embedding: None,
            }
        })
        .collect();
    let parents = if parented {
        vec![ChunkParentPiece {
            text: texts.join(" "),
            fields: Map::new(),
        }]
    } else {
        Vec::new()
    };
    (chunks, parents)
}

fn note(text: &str, chunk_texts: &[&str], parented: bool) -> IngestContentRequest {
    let (chunks, parents) = pieces(chunk_texts, parented);
    IngestContentRequest {
        source_type: "note".into(),
        title: "guide".into(),
        text: text.into(),
        source_uri: Some("/vault/guide.md".into()),
        workspace_id: Some("personal".into()),
        chunks,
        parents,
        ..Default::default()
    }
}

fn count(writer: &GraphWriter, sql: &str, node_id: &str) -> i64 {
    writer
        .store()
        .with_read_conn(|conn| Ok(conn.query_row(sql, [node_id], |row| row.get(0))?))
        .expect("count")
}

fn assert_only_current_chunks(writer: &GraphWriter, node_id: &str, expected: &[String]) {
    let total = expected.len() as i64;
    assert_eq!(
        count(
            writer,
            "SELECT COUNT(*) FROM chunks WHERE source_node = ?1",
            node_id
        ),
        total
    );
    for sql in [
        "SELECT COUNT(*) FROM nodes WHERE type='Chunk' \
         AND json_extract(metadata_json, '$.source_node') = ?1",
        "SELECT COUNT(*) FROM vector_embeddings WHERE item_type='chunk' \
         AND json_extract(metadata_json, '$.parent_source_node') = ?1",
        "SELECT COUNT(*) FROM bm25_node_stats WHERE node_id IN \
         (SELECT to_node FROM edges WHERE from_node = ?1 AND to_node LIKE 'chunk:%')",
    ] {
        assert_eq!(count(writer, sql, node_id), total, "{sql}");
    }
    for chunk_id in expected {
        assert_eq!(
            count(
                writer,
                "SELECT COUNT(*) FROM chunks WHERE id = ?1",
                chunk_id
            ),
            1
        );
    }
}

fn parent_count(writer: &GraphWriter, node_id: &str) -> i64 {
    count(
        writer,
        "SELECT COUNT(*) FROM chunk_parents WHERE source_node = ?1",
        node_id,
    )
}

#[test]
fn rewriting_a_content_node_purges_only_its_own_stale_chunks_and_parents() {
    let dir = tempfile::tempdir().expect("tempdir");
    let writer = writer(&dir);
    let old = writer
        .ingest_content(&note("one two", &["one", "two"], true))
        .expect("old");
    let sibling = writer
        .ingest_content(&note("one two three", &["one two three"], true))
        .expect("another version");
    assert_ne!(old.node_id, sibling.node_id);

    let new = writer
        .ingest_content(&note("one two", &["one two"], false))
        .expect("rechunked");
    assert_eq!(new.node_id, old.node_id);
    assert!(new.duplicate);
    assert_only_current_chunks(&writer, &new.node_id, &new.chunk_ids);
    assert_eq!(parent_count(&writer, &new.node_id), 0);
    for chunk_id in &old.chunk_ids {
        assert_eq!(
            count(
                &writer,
                "SELECT COUNT(*) FROM nodes WHERE id = ?1",
                chunk_id
            ),
            0
        );
    }
    assert_only_current_chunks(&writer, &sibling.node_id, &sibling.chunk_ids);
    assert_eq!(parent_count(&writer, &sibling.node_id), 1);

    writer
        .ingest_content(&note("one two", &[], false))
        .expect("chunkless rewrite");
    assert_only_current_chunks(&writer, &new.node_id, &new.chunk_ids);
}

#[test]
fn re_uploading_the_same_bytes_purges_stale_chunks_of_that_file_node() {
    let dir = tempfile::tempdir().expect("tempdir");
    let writer = writer(&dir);
    let path = dir.path().join("report.bin");
    std::fs::write(&path, b"same bytes").expect("write");
    let upload = |texts: &[&str], parented: bool| {
        let (chunks, parents) = pieces(texts, parented);
        writer
            .ingest_file(&IngestFileRequest {
                path: path.clone(),
                chunks,
                parents,
                workspace_id: Some("personal".into()),
                ..Default::default()
            })
            .expect("upload")
    };
    let old = upload(&["ocr line one", "ocr line two"], true);
    let new = upload(&["better parse"], true);
    assert_eq!(new.node_id, old.node_id);
    assert_only_current_chunks(&writer, &new.node_id, &new.chunk_ids);
    assert_eq!(parent_count(&writer, &new.node_id), 1);

    upload(&[], false);
    assert_only_current_chunks(&writer, &new.node_id, &new.chunk_ids);
    assert_eq!(parent_count(&writer, &new.node_id), 1);

    std::fs::write(&path, b"different bytes").expect("rewrite");
    let other = upload(&["new version"], false);
    assert_ne!(other.node_id, new.node_id);
    assert_only_current_chunks(&writer, &new.node_id, &new.chunk_ids);
}
