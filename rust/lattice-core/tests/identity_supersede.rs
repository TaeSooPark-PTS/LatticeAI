use std::sync::Arc;

use lattice_core::db::Store;
use lattice_core::graph_write::identity::upload_source_uri;
use lattice_core::graph_write::types::{ChunkPiece, IngestContentRequest, IngestFileRequest};
use lattice_core::graph_write::{GraphWriter, RevisionReplace};
use lattice_core::temporal_state::node_temporal_states;
use serde_json::{json, Map};

fn writer(dir: &std::path::Path) -> GraphWriter {
    let store = Arc::new(Store::open(&dir.join("knowledge_graph.sqlite")).expect("db"));
    GraphWriter::open(store, dir.join("blobs")).expect("writer")
}

fn piece(text: &str) -> ChunkPiece {
    let mut fields = Map::new();
    fields.insert("strategy".into(), json!("prose"));
    ChunkPiece {
        text: text.into(),
        fields,
        embedding: None,
    }
}

fn note(uri: &str, text: &str, owner: &str, workspace: &str) -> IngestContentRequest {
    IngestContentRequest {
        source_type: "note".into(),
        title: "plan".into(),
        text: text.into(),
        source_uri: Some(uri.into()),
        owner: Some(owner.into()),
        workspace_id: Some(workspace.into()),
        chunks: vec![piece(text)],
        ..Default::default()
    }
}

fn superseded(writer: &GraphWriter, id: &str) -> bool {
    let ids = vec![id.to_string()];
    writer
        .store()
        .with_read_conn(|conn| node_temporal_states(conn, &ids, 4_000_000_000.0))
        .expect("states")
        .get(id)
        .is_some_and(|state| state.is_superseded())
}

fn chunk_rows(writer: &GraphWriter, id: &str) -> i64 {
    writer
        .store()
        .with_read_conn(|conn| {
            Ok(conn.query_row(
                "SELECT COUNT(*) FROM chunks WHERE source_node = ?1",
                [id],
                |row| row.get(0),
            )?)
        })
        .expect("count")
}

#[test]
fn a_newer_version_supersedes_only_the_same_identity_and_keeps_its_content() {
    let dir = tempfile::tempdir().expect("tempdir");
    let graph = writer(dir.path());
    let add = |request: IngestContentRequest| {
        graph
            .ingest_content_superseding(&request)
            .expect("ingest")
            .node_id
    };
    let first = add(note("/v/plan.md", "port is 1", "a@x", "team"));
    let other_uri = add(note("/v/other.md", "port is 1", "a@x", "team"));
    let other_owner = add(note("/v/plan.md", "port is 7", "b@x", "team"));
    let other_workspace = add(note("/v/plan.md", "port is 8", "a@x", "lab"));
    let second = add(note("/v/plan.md", "port is 2", "a@x", "team"));

    assert!(superseded(&graph, &first));
    assert_eq!(chunk_rows(&graph, &first), 1);
    for id in [&second, &other_uri, &other_owner, &other_workspace] {
        assert!(!superseded(&graph, id), "{id}");
    }

    let reverted = add(note("/v/plan.md", "port is 1", "a@x", "team"));
    assert_eq!(reverted, first);
    assert!(!superseded(&graph, &first));
    assert!(superseded(&graph, &second));
    assert_eq!(chunk_rows(&graph, &second), 1);

    add(note("/v/plan.md", "port is 1", "a@x", "team"));
    assert!(!superseded(&graph, &first));
}

#[test]
fn an_upload_supersedes_a_similar_file_of_the_same_name_only() {
    let dir = tempfile::tempdir().expect("tempdir");
    let graph = writer(dir.path());
    let upload = |name: &str, text: &str| {
        let path = dir.path().join("incoming.txt");
        std::fs::write(&path, text).expect("write");
        let mut extracted = Map::new();
        extracted.insert("content".into(), json!(text));
        graph
            .ingest_file_superseding(&IngestFileRequest {
                path,
                original_filename: Some(name.into()),
                source_uri: Some(upload_source_uri(name)),
                extracted,
                chunks: vec![piece(text)],
                ..Default::default()
            })
            .expect("upload")
            .node_id
    };
    let first = upload("plan.txt", "alpha beta gamma delta port one");
    let unrelated = upload("plan.txt", "recipe for tomato soup tonight");
    assert!(!superseded(&graph, &first));
    let renamed = upload("other.txt", "alpha beta gamma delta port three");
    let second = upload("plan.txt", "alpha beta gamma delta port two");

    assert!(superseded(&graph, &first));
    assert_eq!(chunk_rows(&graph, &first), 1);
    for id in [&second, &unrelated, &renamed] {
        assert!(!superseded(&graph, id), "{id}");
    }
}

#[test]
fn a_watched_file_reverted_to_earlier_content_is_current_again() {
    let dir = tempfile::tempdir().expect("tempdir");
    let graph = writer(dir.path());
    let replace = |text: &str, prior: Option<String>| {
        graph
            .replace_document_revision(
                &note("/v/plan.md", text, "a@x", "team"),
                &RevisionReplace {
                    prior_node_id: prior,
                    subject: "plan".into(),
                    recorded_at: "2026-11-26T00:00:00Z".into(),
                },
            )
            .expect("replace")
            .ingest
            .node_id
    };
    let first = replace("port is 1", None);
    let second = replace("port is 2", Some(first.clone()));
    assert_eq!(replace("port is 1", Some(second.clone())), first);
    assert!(!superseded(&graph, &first));
    assert!(superseded(&graph, &second));
}

#[test]
fn identical_bytes_from_another_user_never_take_over_or_supersede() {
    let dir = tempfile::tempdir().expect("tempdir");
    let graph = writer(dir.path());
    let upload = |owner: Option<&str>, name: &str, text: &str| {
        let path = dir.path().join("incoming.txt");
        std::fs::write(&path, text).expect("write");
        let mut extracted = Map::new();
        extracted.insert("content".into(), json!(text));
        graph
            .ingest_file_superseding(&IngestFileRequest {
                path,
                original_filename: Some(name.into()),
                source_uri: Some(upload_source_uri(name)),
                owner: owner.map(str::to_string),
                extracted,
                chunks: vec![piece(text)],
                ..Default::default()
            })
            .expect("upload")
            .node_id
    };
    let held = |id: &str| -> (String, String, String) {
        graph
            .store()
            .with_read_conn(|conn| {
                Ok(conn.query_row(
                    "SELECT json_extract(n.metadata_json, '$.owner'), v.owner_id, n.title
                     FROM nodes n JOIN nodes_v2 v ON v.id = n.id WHERE n.id = ?1",
                    [id],
                    |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
                )?)
            })
            .expect("held")
    };
    let theirs = upload(Some("b@x"), "plan.txt", "alpha beta gamma delta port nine");
    let original = upload(Some("a@x"), "plan.txt", "alpha beta gamma delta port one");
    for intruder in [Some("b@x"), None] {
        assert_eq!(
            upload(intruder, "stolen.txt", "alpha beta gamma delta port one"),
            original
        );
    }
    assert_eq!(
        held(&original),
        ("a@x".into(), "a@x".into(), "plan.txt".into())
    );
    assert!(!superseded(&graph, &original));
    assert!(!superseded(&graph, &theirs));

    let first = graph
        .ingest_content_superseding(&note("/v/n.md", "same text", "a@x", "team"))
        .expect("a");
    let again = graph
        .ingest_content_superseding(&note("/v/n.md", "same text", "b@x", "team"))
        .expect("b");
    assert_eq!(first.node_id, again.node_id);
    assert!(again.duplicate);
    assert_eq!(held(&first.node_id).0, "a@x");
    assert_eq!(held(&first.node_id).1, "a@x");
    let source = first.source_node_id.expect("source");
    graph
        .ingest_content_superseding(&note("/v/n.md", "their text", "b@x", "team"))
        .expect("b new");
    assert_eq!(held(&source).1, "a@x");
    assert!(!superseded(&graph, &first.node_id));
}
