use std::sync::Arc;

use lattice_core::db::Store;
use lattice_core::embeddings::EmbeddingIndexContract;
use lattice_core::graph_write::types::{
    ChunkPiece, EdgeSpec, IngestContentRequest, IngestionRecord, NodeSpec, SuppliedVector,
};
use lattice_core::graph_write::{
    read_lifecycle_from_metadata, GraphWriter, KnowledgeLifecycleState, KnowledgeLifecycleUpdate,
};
use serde_json::{Map, Value};

fn open_writer(dir: &std::path::Path) -> (Arc<Store>, GraphWriter) {
    let store = Arc::new(Store::open(&dir.join("knowledge_graph.sqlite")).expect("store"));
    let writer =
        GraphWriter::open(Arc::clone(&store), dir.join("knowledge_graph_blobs")).expect("writer");
    (store, writer)
}

#[test]
fn semantic_vectors_store_actual_contract_and_detect_reindex_without_hash_fallback() {
    let dir = tempfile::tempdir().expect("tempdir");
    let (store, writer) = open_writer(dir.path());

    // Writer defaults to LocalEmbeddingModel (lattice-local-hash-v1:384), yet
    // real provider vectors from EmbeddingGemma 2 (dim 4 here) must land intact.
    let doc_vec = SuppliedVector {
        model_id: "huggingface:google/embeddinggemma-2:768".into(),
        dim: 4,
        values: vec![0.25, 0.5, 0.75, 0.125],
    };
    let chunk_vec = SuppliedVector {
        model_id: "huggingface:google/embeddinggemma-2:768".into(),
        dim: 4,
        values: vec![0.125, 0.25, 0.5, 0.75],
    };

    let outcome = writer
        .ingest_content(&IngestContentRequest {
            source_type: "note".into(),
            title: "Gemma Spec".into(),
            text: "Asymmetric prompt prefix contract test".into(),
            chunks: vec![ChunkPiece {
                text: "Chunk body about EmbeddingGemma contract".into(),
                embedding: Some(chunk_vec.clone()),
                ..Default::default()
            }],
            embedding: Some(doc_vec.clone()),
            ..Default::default()
        })
        .expect("ingest semantic note");

    let vec_outcome = writer.write_vectors_with(
        &outcome.node_id,
        &[
            (outcome.node_id.clone(), doc_vec),
            (outcome.chunk_ids[0].clone(), chunk_vec),
        ],
    );
    assert!(vec_outcome.semantic);
    assert!(!vec_outcome.reindex_required);
    assert_eq!(
        vec_outcome.embedding_model.as_deref(),
        Some("huggingface:google/embeddinggemma-2:768")
    );

    let contract = writer
        .current_index_contract()
        .expect("contract query")
        .expect("contract recorded");
    assert_eq!(contract.provider, "huggingface");
    assert_eq!(contract.model_id, "huggingface:google/embeddinggemma-2:768");
    assert_eq!(contract.dimension, 4);
    assert_eq!(contract.passage_mode, "embeddinggemma_passage");
    assert_eq!(contract.query_mode, "embeddinggemma_query");
    assert!(contract.semantic);

    store
        .with_read_conn(|conn| {
            let mut stmt = conn
                .prepare(
                    "SELECT embedding_model, embedding_dim, metadata_json \
                     FROM vector_embeddings WHERE item_id IN (?, ?)",
                )
                .unwrap();
            let rows = stmt
                .query_map(
                    rusqlite::params![outcome.node_id, outcome.chunk_ids[0]],
                    |row| {
                        Ok((
                            row.get::<_, String>(0)?,
                            row.get::<_, i64>(1)?,
                            row.get::<_, String>(2)?,
                        ))
                    },
                )
                .unwrap()
                .collect::<Result<Vec<_>, _>>()
                .unwrap();
            assert_eq!(rows.len(), 2);
            for (model, dim, meta_raw) in rows {
                assert_eq!(model, "huggingface:google/embeddinggemma-2:768");
                assert_eq!(dim, 4);
                let meta: Value = serde_json::from_str(&meta_raw).unwrap();
                assert_eq!(meta["embedding_grade"], "production");
                assert_eq!(meta["semantic"], true);
                assert_eq!(meta["embedding_provider"], "huggingface");
            }
            Ok(())
        })
        .unwrap();

    // Switching to another semantic provider/dimension triggers reindex_required.
    let mlx_vec = SuppliedVector {
        model_id: "mlx:mlx-community/all-MiniLM-L6-v2".into(),
        dim: 3,
        values: vec![0.1, 0.2, 0.3],
    };
    let second = writer
        .ingest_content(&IngestContentRequest {
            source_type: "note".into(),
            title: "MLX Note".into(),
            text: "Switched embedding provider".into(),
            embedding: Some(mlx_vec.clone()),
            ..Default::default()
        })
        .expect("second ingest");
    let switch_outcome =
        writer.write_vectors_with(&second.node_id, &[(second.node_id.clone(), mlx_vec)]);
    assert!(switch_outcome.reindex_required);
}

#[test]
fn supplied_vector_dimension_mismatch_fails_closed() {
    let dir = tempfile::tempdir().expect("tempdir");
    let (_store, writer) = open_writer(dir.path());

    let bad_vec = SuppliedVector {
        model_id: "openai:text-embedding-3-small".into(),
        dim: 4,
        values: vec![0.1, 0.2], // declared dim=4, values len=2
    };
    let err = writer
        .ingest_content(&IngestContentRequest {
            source_type: "note".into(),
            title: "Corrupt vector".into(),
            text: "Should refuse mismatched dim".into(),
            embedding: Some(bad_vec),
            ..Default::default()
        })
        .expect_err("dimension mismatch must error");
    assert!(err.to_string().contains("dimension mismatch"));

    // EmbeddingIndexContract::validate_vector also rejects non-finite values.
    let contract =
        EmbeddingIndexContract::from_semantic("openai", "openai:text-embedding-3-small", 3);
    assert!(contract.validate_vector(&[0.1, f64::NAN, 0.3]).is_err());
    assert!(contract.validate_vector(&[0.1, 0.2, 0.3]).is_ok());
}

#[test]
fn delete_node_unifies_with_delete_document_tree_cleaning_edges_chunks_vectors_and_provenance() {
    let dir = tempfile::tempdir().expect("tempdir");
    let (store, writer) = open_writer(dir.path());

    writer
        .upsert_nodes(&[
            NodeSpec {
                id: "doc:1".into(),
                node_type: "Document".into(),
                title: "Target Doc".into(),
                ..Default::default()
            },
            NodeSpec {
                id: "sec:1".into(),
                node_type: "Section".into(),
                title: "Child Section".into(),
                metadata: {
                    let mut m = Map::new();
                    m.insert("source_node".into(), Value::String("doc:1".into()));
                    m
                },
                ..Default::default()
            },
            NodeSpec {
                id: "concept:1".into(),
                node_type: "Concept".into(),
                title: "Shared Concept".into(),
                ..Default::default()
            },
        ])
        .expect("nodes");
    writer
        .upsert_edges(&[
            EdgeSpec {
                from_node: "sec:1".into(),
                to_node: "doc:1".into(),
                edge_type: "PART_OF".into(),
                weight: 1.0,
                metadata: Map::new(),
                legacy_label: None,
            },
            EdgeSpec {
                from_node: "doc:1".into(),
                to_node: "concept:1".into(),
                edge_type: "MENTIONS".into(),
                weight: 1.0,
                metadata: Map::new(),
                legacy_label: None,
            },
        ])
        .expect("edges");
    writer.write_vectors("doc:1");
    writer.write_vectors("sec:1");
    writer
        .record_ingestion(&IngestionRecord {
            node_id: "doc:1".into(),
            source_type: "note".into(),
            pipeline: "test".into(),
            source_uri: Some("note://doc-1".into()),
            ..Default::default()
        })
        .expect("provenance");

    let deleted = writer.delete_node("doc:1").expect("delete_node");
    assert!(deleted);

    store
        .with_read_conn(|conn| {
            let doc_or_sec: i64 = conn
                .query_row(
                    "SELECT COUNT(*) FROM nodes WHERE id IN ('doc:1', 'sec:1')",
                    [],
                    |row| row.get(0),
                )
                .unwrap();
            assert_eq!(doc_or_sec, 0);

            let dangling_edges: i64 = conn
                .query_row(
                    "SELECT COUNT(*) FROM edges e \
                     WHERE NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id = e.from_node) \
                        OR NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id = e.to_node)",
                    [],
                    |row| row.get(0),
                )
                .unwrap();
            assert_eq!(dangling_edges, 0, "PART_OF and MENTIONS must be swept");

            let dangling_v2_edges: i64 = conn
                .query_row(
                    "SELECT COUNT(*) FROM edges_v2 e \
                     WHERE NOT EXISTS (SELECT 1 FROM nodes_v2 n WHERE n.id = e.source) \
                        OR NOT EXISTS (SELECT 1 FROM nodes_v2 n WHERE n.id = e.target)",
                    [],
                    |row| row.get(0),
                )
                .unwrap();
            assert_eq!(dangling_v2_edges, 0);

            let vecs: i64 = conn
                .query_row(
                    "SELECT COUNT(*) FROM vector_embeddings WHERE item_id IN ('doc:1', 'sec:1')",
                    [],
                    |row| row.get(0),
                )
                .unwrap();
            assert_eq!(vecs, 0);

            let prov: i64 = conn
                .query_row(
                    "SELECT COUNT(*) FROM ingestion_provenance WHERE node_id = 'doc:1'",
                    [],
                    |row| row.get(0),
                )
                .unwrap();
            assert_eq!(prov, 0);

            let concept_alive: i64 = conn
                .query_row(
                    "SELECT COUNT(*) FROM nodes WHERE id = 'concept:1'",
                    [],
                    |row| row.get(0),
                )
                .unwrap();
            assert_eq!(concept_alive, 1, "unrelated concept must survive");
            Ok(())
        })
        .unwrap();
}

#[test]
fn knowledge_lifecycle_states_metadata_and_edges_round_trip() {
    let dir = tempfile::tempdir().expect("tempdir");
    let (store, writer) = open_writer(dir.path());

    writer
        .upsert_nodes(&[
            NodeSpec {
                id: "policy:v1".into(),
                node_type: "Decision".into(),
                title: "Retention Policy v1".into(),
                ..Default::default()
            },
            NodeSpec {
                id: "policy:v2".into(),
                node_type: "Decision".into(),
                title: "Retention Policy v2".into(),
                ..Default::default()
            },
            NodeSpec {
                id: "policy:alt".into(),
                node_type: "Decision".into(),
                title: "Regional Addendum".into(),
                ..Default::default()
            },
        ])
        .expect("nodes");

    // 1. Pending verification -> verified Active with authority & approver.
    let pending = writer
        .set_node_lifecycle(
            "policy:v2",
            &KnowledgeLifecycleUpdate {
                state: Some(KnowledgeLifecycleState::PendingVerification),
                effective_from: Some("2026-01-01T00:00:00Z".into()),
                authority: Some("Compliance Board".into()),
                supersedes: vec!["policy:v1".into()],
                actor: Some("author@lattice.test".into()),
                reason: Some("draft policy".into()),
                ..Default::default()
            },
        )
        .expect("pending lifecycle");
    assert_eq!(pending.state, KnowledgeLifecycleState::PendingVerification);
    assert_eq!(pending.authority.as_deref(), Some("Compliance Board"));

    let verified = writer
        .verify_node("policy:v2", "lead@lattice.test", Some("Security Council"))
        .expect("verify");
    assert_eq!(verified.state, KnowledgeLifecycleState::Active);
    assert_eq!(verified.approver.as_deref(), Some("lead@lattice.test"));
    assert_eq!(verified.authority.as_deref(), Some("Security Council"));
    assert!(verified.version >= 2);

    // 2. Mark v1 superseded by v2 via curation method -> updates lifecycle + SUPERSEDES edge.
    let sup = writer
        .mark_superseded("policy:v1", "policy:v2")
        .expect("supersede");
    assert_eq!(sup["status"], "ok");
    let v1_lc = writer
        .get_node_lifecycle("policy:v1")
        .expect("get lifecycle")
        .expect("present");
    assert_eq!(v1_lc.state, KnowledgeLifecycleState::Superseded);
    assert_eq!(v1_lc.superseded_by.as_deref(), Some("policy:v2"));
    assert!(v1_lc.deprecated_at.is_some());

    // 3. Mark disputed -> sets both sides disputed + CONFLICTS_WITH edge.
    let disputed = writer
        .mark_disputed(
            "policy:v2",
            Some("policy:alt"),
            Some("auditor@lattice.test"),
            Some("EU vs US conflict"),
        )
        .expect("dispute");
    assert_eq!(disputed.state, KnowledgeLifecycleState::Disputed);
    assert!(disputed.conflicts_with.contains(&"policy:alt".to_string()));
    let alt_lc = writer
        .get_node_lifecycle("policy:alt")
        .expect("alt")
        .expect("present");
    assert_eq!(alt_lc.state, KnowledgeLifecycleState::Disputed);

    // 4. Explicit Deprecated transition with deprecated_at + read_lifecycle_from_metadata helper.
    writer
        .set_node_lifecycle(
            "policy:alt",
            &KnowledgeLifecycleUpdate {
                state: Some(KnowledgeLifecycleState::Deprecated),
                deprecated_at: Some("2026-06-01T00:00:00Z".into()),
                ..Default::default()
            },
        )
        .expect("deprecate");
    let alt_after = writer.get_node_lifecycle("policy:alt").unwrap().unwrap();
    assert_eq!(alt_after.state, KnowledgeLifecycleState::Deprecated);
    assert_eq!(
        alt_after.deprecated_at.as_deref(),
        Some("2026-06-01T00:00:00Z")
    );

    let legacy = read_lifecycle_from_metadata(
        &Map::new(),
        Some("policy:v2"),
        Some("2025-01-01T00:00:00Z"),
        None,
    );
    assert_eq!(legacy.state, KnowledgeLifecycleState::Superseded);
    assert_eq!(legacy.superseded_by.as_deref(), Some("policy:v2"));

    store
        .with_read_conn(|conn| {
            let conflicts: i64 = conn
                .query_row(
                    "SELECT COUNT(*) FROM edges WHERE type='CONFLICTS_WITH'",
                    [],
                    |row| row.get(0),
                )
                .unwrap();
            assert!(conflicts >= 1);
            let supersedes: i64 = conn
                .query_row(
                    "SELECT COUNT(*) FROM edges WHERE type='SUPERSEDES'",
                    [],
                    |row| row.get(0),
                )
                .unwrap();
            assert!(supersedes >= 1);
            Ok(())
        })
        .unwrap();
}
