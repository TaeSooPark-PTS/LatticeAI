use super::*;

#[test]
fn next_available_fills_the_first_free_slot() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("note.md"), b"x").unwrap();
    assert_eq!(
        next_available_path(dir.path(), "note.md").unwrap(),
        "note_2.md"
    );
    std::fs::write(dir.path().join("note_2.md"), b"x").unwrap();
    assert_eq!(
        next_available_path(dir.path(), "note.md").unwrap(),
        "note_3.md"
    );
    assert_eq!(
        next_available_path(dir.path(), "fresh.md").unwrap(),
        "fresh.md"
    );
}

#[test]
fn previewable_matches_the_agent_table() {
    assert!(previewable("a.md"));
    assert!(previewable("A.HTML"));
    assert!(!previewable("a.docx"));
    assert!(!previewable("noext"));
}

#[test]
fn scoped_file_ids_are_stable() {
    let a = scoped_file_id("Hello Lattice", Some("personal"));
    let b = scoped_file_id("Hello Lattice", Some("personal"));
    assert_eq!(a, b);
    assert!(a.starts_with("file:"));
    assert_eq!(a.len(), 5 + 24);
    assert_ne!(a, scoped_file_id("Hello Lattice", None));
}

#[tokio::test]
async fn clear_writes_the_audit_event_through_graph_writer() {
    use std::collections::HashMap;
    use std::sync::Arc;

    use lattice_auth::AuthConfig;
    use lattice_core::db::Store;
    use lattice_core::graph_write::GraphWriter;

    use crate::state::{ChatConfig, ChatState};

    let dir = tempfile::tempdir().unwrap();
    let mut env = HashMap::new();
    env.insert(
        "LATTICEAI_DATA_DIR".into(),
        dir.path().to_string_lossy().into_owned(),
    );
    let auth = lattice_auth::AuthState::new(AuthConfig::from_map(&env, None));
    let store = Arc::new(Store::open(&dir.path().join("knowledge_graph.sqlite")).unwrap());
    let graph =
        GraphWriter::open(Arc::clone(&store), dir.path().join("knowledge_graph_blobs")).unwrap();
    let state = ChatState::new(
        auth,
        ChatConfig {
            data_dir: dir.path().to_path_buf(),
            graph_db: Some(dir.path().join("knowledge_graph.sqlite")),
            agent_root: dir.path().join("agent"),
            ..ChatConfig::default()
        },
    )
    .with_graph(graph.clone());
    let req = ChatRequest {
        message: "/clear".into(),
        conversation_id: Some("conv-1".into()),
        ..ChatRequest::default()
    };
    let _ = clear(
        &state,
        &req,
        &HeaderMap::new(),
        Some("owner@lattice.test"),
        Some("personal"),
    )
    .await;
    let count: i64 = graph
        .store()
        .with_read_conn(|conn| {
            Ok(conn
                .query_row(
                    "SELECT COUNT(*) FROM nodes WHERE type = ?1",
                    ["ClearEvent"],
                    |row| row.get(0),
                )
                .unwrap_or(0))
        })
        .unwrap();
    assert_eq!(count, 1);
}

/// A state with a Brain, an agent workspace and **no worker** — the shape
/// that used to make `ingest_generated` return `None` and, with a worker,
/// made it post into a route that no longer exists.
fn brain_state(
    dir: &Path,
) -> (
    crate::state::ChatState,
    lattice_core::graph_write::GraphWriter,
) {
    use std::collections::HashMap;
    use std::sync::Arc;

    use lattice_auth::AuthConfig;
    use lattice_core::db::Store;
    use lattice_core::graph_write::GraphWriter;

    use crate::state::{ChatConfig, ChatState};

    let mut env = HashMap::new();
    env.insert(
        "LATTICEAI_DATA_DIR".into(),
        dir.to_string_lossy().into_owned(),
    );
    let auth = lattice_auth::AuthState::new(AuthConfig::from_map(&env, None));
    let store = Arc::new(Store::open(&dir.join("knowledge_graph.sqlite")).unwrap());
    let graph = GraphWriter::open(store, dir.join("knowledge_graph_blobs")).unwrap();
    let agent_root = dir.join("agent");
    std::fs::create_dir_all(&agent_root).unwrap();
    let state = ChatState::new(
        auth,
        ChatConfig {
            data_dir: dir.to_path_buf(),
            graph_db: Some(dir.join("knowledge_graph.sqlite")),
            agent_root,
            ..ChatConfig::default()
        },
    )
    .with_graph(graph.clone());
    (state, graph)
}

#[tokio::test]
async fn a_generated_file_is_written_through_the_graph_writer() {
    let dir = tempfile::tempdir().unwrap();
    let (state, graph) = brain_state(dir.path());
    std::fs::write(state.config.agent_root.join("메모.md"), b"Hello Lattice").unwrap();

    let receipt = ingest_generated(
        &state,
        "메모.md",
        "Hello Lattice",
        Some("owner@lattice.test"),
        Some("personal"),
        Some("conv-1"),
    )
    .await
    .expect("the Brain is bound, so there is a receipt");
    assert_eq!(receipt["status"], "ok");
    assert_eq!(
        receipt["node_id"],
        json!(scoped_file_id("Hello Lattice", Some("personal"))),
        "the id is the scoped hash of the bytes on disk — the same id the \
         retired route's fixture pinned"
    );
    assert_eq!(receipt["chunk_count"], 0);
    assert_eq!(receipt["duplicate"], false);

    let node_id = receipt["node_id"].as_str().unwrap().to_string();
    let (node_type, title, summary, metadata): (String, String, String, String) = graph
        .store()
        .with_read_conn(|conn| {
            Ok(conn
                .query_row(
                    "SELECT type, title, summary, metadata_json FROM nodes WHERE id = ?1",
                    [&node_id],
                    |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
                )
                .expect("the generated file is a node"))
        })
        .unwrap();
    assert_eq!(node_type, "Document");
    assert_eq!(title, "메모.md");
    assert_eq!(
        summary, "Hello Lattice",
        "the text is searchable, not just the name"
    );
    let metadata: Value = serde_json::from_str(&metadata).unwrap();
    assert_eq!(metadata["source_uri"], "workspace://메모.md");
    assert_eq!(metadata["source_type"], "file");
    assert_eq!(metadata["owner"], "owner@lattice.test");
    assert_eq!(metadata["workspace_id"], "personal");
    assert_eq!(metadata["conversation_id"], "conv-1");

    let provenance: i64 = graph
        .store()
        .with_read_conn(|conn| {
            Ok(conn
                .query_row(
                    "SELECT COUNT(*) FROM ingestion_provenance WHERE source_type = 'file'",
                    [],
                    |row| row.get(0),
                )
                .unwrap_or(0))
        })
        .unwrap();
    assert_eq!(provenance, 1);

    // Writing the same file again is the same node, reported as such.
    let again = ingest_generated(
        &state,
        "메모.md",
        "Hello Lattice",
        Some("owner@lattice.test"),
        Some("personal"),
        Some("conv-1"),
    )
    .await
    .expect("receipt");
    assert_eq!(again["duplicate"], true);
}

#[tokio::test]
async fn without_a_brain_or_with_the_gate_off_nothing_is_claimed() {
    let dir = tempfile::tempdir().unwrap();
    let (state, _graph) = brain_state(dir.path());
    std::fs::write(state.config.agent_root.join("a.md"), b"x").unwrap();

    // Gate off: no ingest, and no `brain_ingest` key for a client to read.
    let mut off = state.clone();
    off.config.ingest_generated = false;
    assert!(ingest_generated(&off, "a.md", "x", None, None, None)
        .await
        .is_none());

    // No Brain bound: the same silence, rather than a "failed" receipt for
    // something that was never attempted.
    let mut brainless = state.clone();
    brainless.graph = None;
    assert!(ingest_generated(&brainless, "a.md", "x", None, None, None)
        .await
        .is_none());

    // A file that is not on disk is a named failure, not a false "ok".
    let missing = ingest_generated(&state, "gone.md", "x", None, None, None)
        .await
        .expect("receipt");
    assert_eq!(missing["status"], "failed");
    assert!(
        missing["detail"].as_str().unwrap_or_default().len() <= 200,
        "the detail is truncated for a log line"
    );
}
