use super::*;

use std::sync::Arc;

use lattice_core::db::Store;

fn writer(dir: &Path) -> GraphWriter {
    let store = Arc::new(Store::open(&dir.join("knowledge_graph.sqlite")).expect("store"));
    GraphWriter::open(store, dir.join("knowledge_graph_blobs")).expect("writer")
}

#[test]
fn a_watched_file_becomes_a_note_with_its_provenance() {
    let note = NoteSubmission::from_watched_file(
        Path::new("/brain/notes"),
        "회의/2026-08-11.md",
        "결정 사항",
        Some("watch_abc"),
    );
    assert_eq!(note.title, "2026-08-11.md");
    assert_eq!(note.content, "결정 사항");
    assert_eq!(note.enrichment_text(), "2026-08-11.md\n결정 사항");
    let request = note.content_request(Some("owner@lattice.test"), Some("personal"));
    assert_eq!(request.source_type, "note");
    assert_eq!(request.node_type.as_deref(), Some("Document"));
    assert_eq!(request.title, "2026-08-11.md");
    assert_eq!(request.text, "결정 사항");
    assert_eq!(request.owner.as_deref(), Some("owner@lattice.test"));
    assert_eq!(request.workspace_id.as_deref(), Some("personal"));
    assert_eq!(
        request.source_uri.as_deref(),
        Some("/brain/notes/회의/2026-08-11.md")
    );
    assert_eq!(
        request.metadata["relative_path"],
        Value::from("회의/2026-08-11.md")
    );
    assert_eq!(
        request.metadata["path"],
        Value::from("/brain/notes/회의/2026-08-11.md")
    );
    assert_eq!(request.metadata["folder_watch"], Value::Bool(true));
    assert_eq!(request.metadata["watch_id"], Value::from("watch_abc"));
    assert_eq!(
        request.metadata["detected_by"],
        Value::from("lattice-ingest")
    );
}

#[test]
fn without_a_watch_id_the_key_is_absent_rather_than_null() {
    let note = NoteSubmission::from_watched_file(Path::new("/root"), "a.txt", "x", None);
    let request = note.content_request(None, None);
    assert!(request.metadata.get("watch_id").is_none());
    assert_eq!(request.source_uri.as_deref(), Some("/root/a.txt"));
    let empty = note.content_request(Some(""), Some(""));
    assert_eq!(empty.owner, None);
    assert_eq!(empty.workspace_id, None);
    let bare = NoteSubmission {
        title: String::new(),
        content: "c".into(),
        source: None,
        metadata: Map::new(),
    };
    assert_eq!(bare.content_request(None, None).source_uri, None);
    assert_eq!(bare.enrichment_text(), "c", "no title, no leading newline");
}

#[tokio::test]
async fn a_markdown_note_lands_with_its_outline_as_graph_structure() {
    let dir = tempfile::tempdir().expect("tempdir");
    let graph = writer(dir.path());
    let ingestor = NoteIngestor::new(graph.clone());
    let body = format!(
        "# 아키텍처\n\n{}\n\n## 저장소\n\n{}\n",
        "머리말입니다. 이 문단은 절 하나가 자기 청크를 갖도록 충분히 깁니다. ".repeat(6),
        "GraphWriter가 모든 쓰기를 담당하고 SQLite에 기록합니다. ".repeat(8),
    );
    let note = NoteSubmission::from_watched_file(dir.path(), "handbook.md", &body, None);

    let receipt = ingestor
        .ingest_note(&note, None, None)
        .await
        .expect("the note lands");
    assert_eq!(receipt.sections, 2, "the receipt reports what landed");
    assert_eq!(receipt.to_json()["sections"], json!(2));

    let document = receipt.node_id.clone();
    let rows: Vec<(String, String, String)> = graph
        .store()
        .with_read_conn(|conn| {
            let mut statement = conn.prepare(
                "SELECT n.title, e.type, e.to_node FROM nodes n \
                 JOIN edges e ON e.from_node = n.id \
                 WHERE n.type='Section' ORDER BY n.title ASC, e.type ASC, e.to_node ASC",
            )?;
            let found = statement
                .query_map([], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)))?
                .filter_map(Result::ok)
                .collect();
            Ok(found)
        })
        .expect("read");

    let titles: Vec<&str> = rows
        .iter()
        .map(|(title, _, _)| title.as_str())
        .collect::<std::collections::BTreeSet<_>>()
        .into_iter()
        .collect();
    assert_eq!(titles, ["아키텍처", "저장소"], "one node per heading");

    assert!(
        rows.iter()
            .any(|(title, kind, to)| title == "아키텍처" && kind == "PART_OF" && *to == document),
        "{rows:?}"
    );
    let parent = crate::sections::section_id(&document, "아키텍처", None);
    assert!(
        rows.iter()
            .any(|(title, kind, to)| title == "저장소" && kind == "PART_OF" && *to == parent),
        "{rows:?}"
    );
    for section in ["아키텍처", "저장소"] {
        assert!(
            rows.iter().any(|(title, kind, to)| title == section
                && kind == "HAS_CHUNK"
                && to.starts_with("chunk:")),
            "{section} has no chunk edge in {rows:?}"
        );
    }
}

#[tokio::test]
async fn a_note_with_no_headings_grows_no_sections() {
    let dir = tempfile::tempdir().expect("tempdir");
    let graph = writer(dir.path());
    let ingestor = NoteIngestor::new(graph.clone());
    let note =
        NoteSubmission::from_watched_file(dir.path(), "plain.txt", "제목 없는 메모입니다.", None);

    ingestor
        .ingest_note(&note, None, None)
        .await
        .expect("lands");

    let sections: i64 = graph
        .store()
        .with_read_conn(|conn| {
            Ok(conn
                .query_row(
                    "SELECT COUNT(*) FROM nodes WHERE type='Section'",
                    [],
                    |row| row.get(0),
                )
                .unwrap_or(0))
        })
        .expect("read");
    assert_eq!(sections, 0, "a heading nobody wrote is not a section");
}

#[test]
fn one_embed_reply_splits_into_the_document_and_its_chunks() {
    let semantic_contract = EmbeddingIndexContract::from_semantic(
        "huggingface",
        "huggingface:google/embeddinggemma-2:768",
        2,
    );
    let outcome = SeamEmbedOutcome::Ready(SeamEmbedBatch {
        contract: semantic_contract,
        rows: vec![vec![1.0, 0.0], vec![0.0, 1.0], vec![0.5, 0.5]],
    });
    let (document, chunks) = split_note_embed(&outcome, 2);
    let doc = document.expect("semantic document vector");
    assert_eq!(doc.model_id, "huggingface:google/embeddinggemma-2:768");
    assert_eq!(doc.dim, 2);
    assert_eq!(doc.values, vec![1.0, 0.0]);
    let chunk_rows = chunks.expect("chunk batch").rows;
    assert_eq!(chunk_rows.len(), 2);
    assert_eq!(chunk_rows[0], vec![0.0, 1.0]);
    let empty = split_note_embed(&SeamEmbedOutcome::Unavailable, 2);
    assert!(empty.0.is_none());
    assert!(empty.1.is_none());
    assert_eq!(INGEST_INFLIGHT, 4);
}

#[test]
fn supplied_vectors_follow_the_node_then_its_chunks() {
    let chunks = vec![
        ChunkPiece {
            text: "a".into(),
            embedding: Some(SuppliedVector {
                values: vec![0.1],
                model_id: "m".into(),
                dim: 1,
            }),
            ..ChunkPiece::default()
        },
        ChunkPiece {
            text: "b".into(),
            ..ChunkPiece::default()
        },
    ];
    let supplied = supplied_for_note(
        "doc-1",
        Some(&SuppliedVector {
            values: vec![1.0],
            model_id: "m".into(),
            dim: 1,
        }),
        &["chunk-a".into(), "chunk-b".into()],
        &chunks,
    );
    assert_eq!(supplied.len(), 2);
    assert_eq!(supplied[0].0, "doc-1");
    assert_eq!(supplied[1].0, "chunk-a");
}

#[test]
fn errors_say_what_went_wrong_in_words() {
    assert!(NoteIngestError::Empty.to_string().contains("non-empty"));
    assert!(NoteIngestError::Write("locked".into())
        .to_string()
        .contains("locked"));
    assert!(NoteIngestError::Task("panicked".into())
        .to_string()
        .contains("did not finish"));
    assert!(NoteIngestError::Client("bad origin".into())
        .to_string()
        .contains("enrichment client"));
    assert!(format!("{:?}", NoteIngestError::Empty).contains("Empty"));
}

#[tokio::test]
async fn a_note_lands_in_the_brain_with_no_worker_in_reach() {
    let dir = tempfile::tempdir().expect("tempdir");
    let graph = writer(dir.path());
    let ingestor = NoteIngestor::new(graph.clone());
    assert!(ingestor.seam().is_none());
    let note = NoteSubmission::from_watched_file(
        dir.path(),
        "meeting.md",
        "가중치 정리 결정",
        Some("watch_1"),
    );
    let receipt = ingestor
        .ingest_note(&note, Some("owner@lattice.test"), Some("personal"))
        .await
        .expect("the note lands");
    assert!(receipt.node_id.starts_with("webdoc:"), "{receipt:?}");
    assert!(!receipt.duplicate);
    assert!(receipt.provenance_id.is_some());

    let node_id = receipt.node_id.clone();
    let (node_type, title, metadata): (String, String, String) = graph
        .store()
        .with_read_conn(|conn| {
            Ok(conn
                .query_row(
                    "SELECT type, title, metadata_json FROM nodes WHERE id = ?1",
                    [&node_id],
                    |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
                )
                .expect("the node row"))
        })
        .expect("read");
    assert_eq!(node_type, "Document");
    assert_eq!(title, "meeting.md");
    let metadata: Value = serde_json::from_str(&metadata).expect("node metadata");
    assert_eq!(metadata["workspace_id"], "personal");
    assert_eq!(metadata["owner"], "owner@lattice.test");
    assert_eq!(metadata["source_type"], "note");
    assert_eq!(metadata["folder_watch"], Value::Bool(true));
    assert_eq!(metadata["watch_id"], "watch_1");
    let provenance: i64 = graph
        .store()
        .with_read_conn(|conn| {
            Ok(conn
                .query_row(
                    "SELECT COUNT(*) FROM ingestion_provenance WHERE source_type='note'",
                    [],
                    |row| row.get(0),
                )
                .unwrap_or(0))
        })
        .expect("read");
    assert_eq!(provenance, 1, "a watched note carries where it came from");

    let again = ingestor
        .ingest_note(&note, Some("owner@lattice.test"), Some("personal"))
        .await
        .expect("the second pass lands too");
    assert_eq!(again.node_id, receipt.node_id);
    assert!(again.duplicate, "the same note is not a second node");

    let json = receipt.to_json();
    assert_eq!(json["status"], "ok");
    assert_eq!(json["node_id"], receipt.node_id);
    assert_eq!(json["duplicate"], false);
    assert_eq!(json["provenance_id"], json!(receipt.provenance_id));
    assert_eq!(json["indexed"], receipt.indexed);
    assert!(receipt.chunk_count >= 1, "{receipt:?}");
    assert_eq!(json["chunk_count"], receipt.chunk_count);
    let chunks: i64 = graph
        .store()
        .with_read_conn(|conn| {
            Ok(conn
                .query_row("SELECT COUNT(*) FROM chunks", [], |row| row.get(0))
                .unwrap_or(0))
        })
        .expect("read");
    assert_eq!(chunks as usize, receipt.chunk_count);
}

#[tokio::test]
async fn an_empty_note_is_refused_rather_than_written() {
    let dir = tempfile::tempdir().expect("tempdir");
    let graph = writer(dir.path());
    let ingestor = NoteIngestor::new(graph.clone());
    assert!(
        Arc::ptr_eq(ingestor.graph().store(), graph.store()),
        "cloning the writer shares the store, so there is still one writer"
    );
    let blank = NoteSubmission::from_watched_file(dir.path(), "blank.md", "   \n", None);
    let refusal = ingestor
        .ingest_note(&blank, None, None)
        .await
        .expect_err("whitespace is not a note");
    assert!(matches!(refusal, NoteIngestError::Empty));
    let nodes: i64 = graph
        .store()
        .with_read_conn(|conn| {
            Ok(conn
                .query_row("SELECT COUNT(*) FROM nodes", [], |row| row.get(0))
                .unwrap_or(0))
        })
        .expect("read");
    assert_eq!(nodes, 0, "a refused note writes nothing at all");
}

#[tokio::test]
async fn a_bad_worker_origin_is_named_rather_than_swallowed() {
    let dir = tempfile::tempdir().expect("tempdir");
    let ingestor = NoteIngestor::new(writer(dir.path()));
    let wired = ingestor
        .clone()
        .with_worker_origin("http://127.0.0.1:9")
        .expect("a loopback origin builds");
    assert_eq!(
        wired.seam().map(WorkerSeamClient::origin),
        Some("http://127.0.0.1:9")
    );
    assert_eq!(
        wired.seam().map(WorkerSeamClient::timeout),
        Some(DEFAULT_TIMEOUT)
    );
    let note = NoteSubmission::from_watched_file(dir.path(), "n.md", "본문", None);
    let receipt = wired
        .ingest_note(&note, None, None)
        .await
        .expect("an unreachable seam is not a lost note");
    assert!(receipt.node_id.starts_with("webdoc:"));
    assert!(
        receipt.indexed,
        "a seam that answered nothing supplied no vector to disagree with, \
         so the native incremental sync is licensed and runs"
    );
}
