use std::sync::Arc;

use lattice_core::db::Store;
use lattice_core::graph_write::types::{
    ChunkPiece, ConceptSpec, IngestContentRequest, IngestFileRequest,
};
use lattice_core::graph_write::GraphWriter;
use lattice_core::viewer_scope::workspace_scope;
use lattice_core::LocalEmbeddingModel;
use lattice_retrieval::bm25::{bm25f_search, Bm25fParams};
use lattice_retrieval::knowledge_graph_api::{
    filter_scoped, list_documents, neighbors, scoped_documents,
};
use lattice_retrieval::service::Scope;
use lattice_retrieval::vector_alignment::{
    vector_search_aligned, VectorQueryInput, VectorSearchOptions,
};
use serde_json::{json, Value};

const ALICE: &str = "alice@acme.test";
const BOB: &str = "bob@acme.test";
const WORKSPACE: &str = "ws-team";

fn scope_for(viewer: &str) -> Scope {
    Scope {
        allowed_workspaces: workspace_scope(Some(WORKSPACE), Some(viewer)),
        include_legacy_global: false,
    }
}

#[test]
fn private_documents_are_isolated_from_workspace_peers_and_never_dedup_across_owners() {
    let dir = tempfile::tempdir().expect("tempdir");
    let store = Arc::new(Store::open(&dir.path().join("kg.sqlite")).expect("store"));
    let writer = GraphWriter::open(Arc::clone(&store), dir.path().join("blobs")).expect("writer");
    let model = LocalEmbeddingModel::default();

    let shared_path = dir.path().join("budget.txt");
    std::fs::write(&shared_path, b"confidential roadmap 991234").expect("write");

    let alice_file = writer
        .ingest_file(&IngestFileRequest {
            path: shared_path.clone(),
            original_filename: Some("budget.txt".into()),
            owner: Some(ALICE.into()),
            workspace_id: Some(WORKSPACE.into()),
            visibility: Some("private".into()),
            extracted: [("content".into(), json!("confidential roadmap 991234"))]
                .into_iter()
                .collect(),
            chunks: vec![ChunkPiece {
                text: "confidential roadmap 991234".into(),
                ..Default::default()
            }],
            concepts: vec![ConceptSpec {
                text: "PrivateProjectAlpha".into(),
                node_type: "Concept".into(),
            }],
            ..Default::default()
        })
        .expect("alice ingest_file");
    assert!(!alice_file.duplicate);

    let bob_file = writer
        .ingest_file(&IngestFileRequest {
            path: shared_path,
            original_filename: Some("budget.txt".into()),
            owner: Some(BOB.into()),
            workspace_id: Some(WORKSPACE.into()),
            visibility: Some("private".into()),
            extracted: [("content".into(), json!("confidential roadmap 991234"))]
                .into_iter()
                .collect(),
            chunks: vec![ChunkPiece {
                text: "confidential roadmap 991234".into(),
                ..Default::default()
            }],
            concepts: vec![ConceptSpec {
                text: "PrivateProjectAlpha".into(),
                node_type: "Concept".into(),
            }],
            ..Default::default()
        })
        .expect("bob ingest_file");
    assert!(!bob_file.duplicate);
    assert_ne!(alice_file.node_id, bob_file.node_id);

    let alice_note = writer
        .ingest_content(&IngestContentRequest {
            source_type: "note".into(),
            title: "Alice Private Strategy".into(),
            text: "secret launch codename obsidianfalcon".into(),
            owner: Some(ALICE.into()),
            workspace_id: Some(WORKSPACE.into()),
            visibility: Some("private".into()),
            chunks: vec![ChunkPiece {
                text: "secret launch codename obsidianfalcon".into(),
                ..Default::default()
            }],
            ..Default::default()
        })
        .expect("alice note");

    let team_note = writer
        .ingest_content(&IngestContentRequest {
            source_type: "note".into(),
            title: "Team Shared Spec".into(),
            text: "shared architecture handbook obsidianfalcon".into(),
            owner: Some(ALICE.into()),
            workspace_id: Some(WORKSPACE.into()),
            visibility: Some("workspace".into()),
            ..Default::default()
        })
        .expect("team note");

    let _ = writer.write_vectors(&alice_file.node_id);
    let _ = writer.write_vectors(&bob_file.node_id);
    let _ = writer.write_vectors(&alice_note.node_id);
    let _ = writer.write_vectors(&team_note.node_id);

    let conn = lattice_core::db::open_read_only(store.path()).expect("read");
    let alice_scope = scope_for(ALICE);
    let bob_scope = scope_for(BOB);

    let alice_bm25: Vec<String> = bm25f_search(
        &conn,
        "obsidianfalcon",
        10,
        &alice_scope,
        &Bm25fParams::default(),
    )
    .expect("alice bm25")
    .into_iter()
    .map(|h| h.node.id)
    .collect();
    assert!(alice_bm25.contains(&alice_note.node_id));
    assert!(alice_bm25.contains(&team_note.node_id));

    let bob_bm25: Vec<String> = bm25f_search(
        &conn,
        "obsidianfalcon",
        10,
        &bob_scope,
        &Bm25fParams::default(),
    )
    .expect("bob bm25")
    .into_iter()
    .map(|h| h.node.id)
    .collect();
    assert!(!bob_bm25.contains(&alice_note.node_id));
    assert!(bob_bm25.contains(&team_note.node_id));

    let bob_vec = vector_search_aligned(
        &conn,
        VectorQueryInput::Model(&model),
        "secret launch codename obsidianfalcon",
        &VectorSearchOptions {
            limit: 10,
            min_score: -1.0,
            scope: Some(&bob_scope),
            include_historical: false,
        },
    )
    .expect("bob vec");
    let bob_vec_ids: Vec<&str> = bob_vec["matches"]
        .as_array()
        .expect("matches")
        .iter()
        .filter_map(|m| m["node_id"].as_str().or_else(|| m["id"].as_str()))
        .collect();
    assert!(!bob_vec_ids.contains(&alice_note.node_id.as_str()));
    assert!(!bob_vec_ids.contains(&alice_file.node_id.as_str()));

    let all_docs = list_documents(&conn, 50).expect("list docs");
    let bob_docs = scoped_documents(&conn, all_docs, &bob_scope).expect("bob docs");
    let bob_doc_ids: Vec<&str> = bob_docs["documents"]
        .as_array()
        .expect("docs")
        .iter()
        .filter_map(|d| d["id"].as_str())
        .collect();
    assert!(bob_doc_ids.contains(&bob_file.node_id.as_str()));
    assert!(bob_doc_ids.contains(&team_note.node_id.as_str()));
    assert!(!bob_doc_ids.contains(&alice_file.node_id.as_str()));
    assert!(!bob_doc_ids.contains(&alice_note.node_id.as_str()));

    let alice_neighbors = neighbors(&conn, &alice_file.node_id).expect("neighbors");
    let alice_concepts: Vec<Value> = alice_neighbors["neighbors"]
        .as_array()
        .expect("neighbors")
        .iter()
        .filter(|n| n["type"] == "Concept")
        .cloned()
        .collect();
    assert!(!alice_concepts.is_empty());
    let bob_sees_alice_concepts = filter_scoped(&conn, alice_concepts, &bob_scope).expect("filter");
    assert!(bob_sees_alice_concepts.is_empty());
}
