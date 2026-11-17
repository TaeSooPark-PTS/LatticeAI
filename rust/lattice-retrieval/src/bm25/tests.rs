use super::*;
use lattice_core::LocalEmbeddingModel;
use rusqlite::Connection;
use serde_json::Value;

use crate::service::Scope;

fn setup_test_graph() -> Connection {
    let conn = Connection::open_in_memory().expect("in-memory sqlite");
    conn.execute_batch(
        "CREATE TABLE nodes (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT '2026-04-01T00:00:00Z',
            updated_at TEXT NOT NULL DEFAULT '2026-04-01T00:00:00Z'
        );
        CREATE TABLE edges (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 1.0,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT '2026-04-01T00:00:00Z'
        );
        CREATE TABLE vector_embeddings (
            node_id TEXT PRIMARY KEY,
            embedding_model TEXT NOT NULL,
            embedding_dim INTEGER NOT NULL,
            vector_blob BLOB NOT NULL,
            updated_at TEXT NOT NULL DEFAULT '2026-04-01T00:00:00Z'
        );",
    )
    .expect("schema");
    conn
}

#[test]
fn cjk_and_mixed_tokenizer_handles_korean_particles_and_bigrams() {
    let tokens = tokenize_bm25_cjk("지식그래프에서 하이브리드 검색을 수행하는 GraphRagEngine");
    assert!(tokens.contains(&"지식그래프".to_string()));
    assert!(tokens.contains(&"ko_bi:지식".to_string()));
    assert!(tokens.contains(&"ko_bi:그래".to_string()));
    assert!(tokens.contains(&"ko_bi:래프".to_string()));
    assert!(tokens.contains(&"검색".to_string()));
    assert!(tokens.contains(&"graph".to_string()));
    assert!(tokens.contains(&"rag".to_string()));
    assert!(tokens.contains(&"engine".to_string()));
}

#[test]
fn bm25f_boosts_title_and_tags_over_long_body_and_saturates_tf() {
    let conn = setup_test_graph();
    conn.execute(
        "INSERT INTO nodes (id, type, title, summary, metadata_json) VALUES (?1, ?2, ?3, ?4, ?5)",
        rusqlite::params![
            "doc-title-hit",
            "Document",
            "하이브리드 지식 그래프 아키텍처",
            "핵심 설계 요약 문서입니다.",
            r#"{"workspace_id":"personal","tags":["지식그래프","BM25F"]}"#,
        ],
    )
    .unwrap();
    let noisy_body = format!(
        "{} 하이브리드 지식 그래프 {}",
        "일반적인 회의록 텍스트 문장입니다. ".repeat(40),
        "기타 부가 설명입니다. ".repeat(40)
    );
    conn.execute(
        "INSERT INTO nodes (id, type, title, summary, metadata_json) VALUES (?1, ?2, ?3, ?4, ?5)",
        rusqlite::params![
            "doc-noisy-body",
            "Document",
            "주간 일반 업무 현황 보고",
            noisy_body,
            r#"{"workspace_id":"personal"}"#,
        ],
    )
    .unwrap();

    let hits = bm25f_search(
        &conn,
        "지식그래프에서 하이브리드",
        10,
        &Scope::default(),
        &Bm25fParams::default(),
    )
    .unwrap();
    assert_eq!(hits.len(), 2);
    assert_eq!(hits[0].node.id, "doc-title-hit");
    assert!(
        hits[0].bm25_score > hits[1].bm25_score * 1.8,
        "BM25F title+tag boost and length normalization should rank concise title hit well above noisy body: {} vs {}",
        hits[0].bm25_score,
        hits[1].bm25_score
    );
    assert!(hits[0].matched_fields.contains(&"title".to_string()));
}

#[test]
fn three_way_graphrag_surfaces_1hop_and_2hop_connected_evidence() {
    let conn = setup_test_graph();
    conn.execute(
        "INSERT INTO nodes (id, type, title, summary, metadata_json) VALUES (?1, ?2, ?3, ?4, ?5)",
        rusqlite::params![
            "seed-bm25",
            "Document",
            "Okapi BM25F 한국어 토크나이저 설계",
            "한국어 조사 제거와 바이그램 색인을 결합합니다. [[역색인 최적화 노트]] 참고.",
            r#"{"workspace_id":"personal","tags":["retrieval","bm25"]}"#,
        ],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO nodes (id, type, title, summary, metadata_json) VALUES (?1, ?2, ?3, ?4, ?5)",
        rusqlite::params![
            "concept-rrf",
            "Concept",
            "Reciprocal Rank Fusion (RRF)",
            "다중 채널 순위 역수 융합 알고리즘",
            r#"{"workspace_id":"personal"}"#,
        ],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO nodes (id, type, title, summary, metadata_json) VALUES (?1, ?2, ?3, ?4, ?5)",
        rusqlite::params![
            "doc-2hop-neighbor",
            "Decision",
            "다중 에이전트 컨텍스트 랭킹 가중치 결정",
            "k=60 상수를 사용하여 희소 점수와 밀집 점수를 결합한다.",
            r#"{"workspace_id":"personal"}"#,
        ],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO nodes (id, type, title, summary, metadata_json) VALUES (?1, ?2, ?3, ?4, ?5)",
        rusqlite::params![
            "doc-wikilink-target",
            "Document",
            "역색인 최적화 노트",
            "블록 단위 포스팅 리스트 압축 기법",
            r#"{"workspace_id":"personal"}"#,
        ],
    )
    .unwrap();

    conn.execute(
        "INSERT INTO edges (id, source_id, target_id, relation_type, weight) VALUES ('e1', 'seed-bm25', 'concept-rrf', 'MENTIONS', 1.5)",
        [],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO edges (id, source_id, target_id, relation_type, weight) VALUES ('e2', 'concept-rrf', 'doc-2hop-neighbor', 'RELATES_TO', 1.2)",
        [],
    )
    .unwrap();

    let model = LocalEmbeddingModel::new(384);
    let report = hybrid_bm25_vector_graph_search(
        &conn,
        &model,
        "BM25F 한국어 토크나이저",
        &GraphRagOptions::default(),
    )
    .unwrap();

    let results = report["results"].as_array().unwrap();
    assert_eq!(report["matches"].as_array().unwrap().len(), results.len());
    let ids: Vec<&str> = results
        .iter()
        .filter_map(|r| r.get("id").and_then(Value::as_str))
        .collect();

    assert_eq!(ids[0], "seed-bm25");
    assert!(
        ids.contains(&"concept-rrf"),
        "1-hop MENTIONS concept node must be surfaced: {ids:?}"
    );
    assert!(
        ids.contains(&"doc-2hop-neighbor"),
        "2-hop RELATES_TO neighbor via shared Concept must be surfaced: {ids:?}"
    );
    assert!(
        ids.contains(&"doc-wikilink-target"),
        "1-hop [[wikilink]] target must be surfaced: {ids:?}"
    );
}

#[test]
fn sidecar_o1_field_lengths_and_postings_match_exact_bm25f_scores() {
    let rows = vec![
        lattice_core::NodeRow {
            id: "n1".to_string(),
            node_type: Some("Document".to_string()),
            title: Some("Hybrid GraphRAG Sidecar Index".to_string()),
            summary: Some("Inverted posting list with O(1) field lengths".to_string()),
            metadata_json: Some(
                r#"{"tags":["bm25f","sidecar"],"workspace_id":"personal"}"#.to_string(),
            ),
            updated_at: Some("2026-04-01T00:00:00Z".to_string()),
        },
        lattice_core::NodeRow {
            id: "n2".to_string(),
            node_type: Some("Note".to_string()),
            title: Some("Weekly Operations Meeting".to_string()),
            summary: Some(format!(
                "{} sidecar {}",
                "routine status update sentence ".repeat(20),
                "unrelated text ".repeat(20)
            )),
            metadata_json: Some(r#"{"workspace_id":"personal"}"#.to_string()),
            updated_at: Some("2026-04-01T00:00:00Z".to_string()),
        },
    ];

    let sidecar = Bm25SidecarIndex::from_corpus(&rows);
    let (dl_title, dl_tags, dl_body, dl_meta) = sidecar.field_lengths("n1").expect("n1 lengths");
    assert_eq!(dl_title, 4); // hybrid, graphrag, sidecar, index
    assert!(dl_tags >= 2);
    assert!(dl_body >= 5);
    assert!(dl_meta >= 3);

    let (tf_title, tf_tags, _tf_body, _tf_meta) = sidecar.term_freqs("n1").expect("n1 tf");
    assert_eq!(tf_title.get("sidecar"), Some(&1));
    assert_eq!(tf_tags.get("bm25f"), Some(&1));
    assert_eq!(sidecar.doc_freq("sidecar"), 2);
    assert_eq!(sidecar.doc_freq("graphrag"), 1);
    assert_eq!(sidecar.postings("sidecar").len(), 2);
    assert_eq!(sidecar.postings("graphrag").len(), 1);

    let params = Bm25fParams::default();
    let direct = bm25f_score_nodes(&rows, "Hybrid Sidecar", &params);
    let via_sidecar = sidecar.score("Hybrid Sidecar", &params);
    assert_eq!(direct.len(), via_sidecar.len());
    for (a, b) in direct.iter().zip(via_sidecar.iter()) {
        assert_eq!(a.node.id, b.node.id);
        assert!((a.bm25_score - b.bm25_score).abs() < 1e-12);
        assert_eq!(a.matched_terms, b.matched_terms);
        assert_eq!(a.matched_fields, b.matched_fields);
    }
}

#[test]
fn sidecar_cache_reuses_unchanged_nodes_and_handles_incremental_updates() {
    let conn = setup_test_graph();
    for i in 0..5 {
        conn.execute(
            "INSERT INTO nodes (id, type, title, summary, metadata_json, updated_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            rusqlite::params![
                format!("doc-{i}"),
                "Document",
                format!("Knowledge Graph Node {i}"),
                format!("Hybrid retrieval indexing benchmark note {i}"),
                r#"{"workspace_id":"personal"}"#,
                "2026-04-01T00:00:00Z",
            ],
        )
        .unwrap();
    }

    let scope = Scope::default();
    let params = Bm25fParams::default();

    // Cold miss tokenizes 5 nodes
    let hits1 = bm25f_search(&conn, "Hybrid retrieval", 5, &scope, &params).unwrap();
    assert_eq!(hits1.len(), 5);
    let stats1 = cached_sidecar_stats(&conn, &scope).expect("cached stats");
    assert_eq!(stats1.tokenized_nodes, 5);
    assert_eq!(stats1.total_docs, 5);

    // Repeat query hits cache with zero extra tokenization
    let hits2 = bm25f_search(&conn, "Knowledge Graph", 5, &scope, &params).unwrap();
    assert_eq!(hits2.len(), 5);
    let stats2 = cached_sidecar_stats(&conn, &scope).expect("cached stats");
    assert!(stats2.cache_hits >= 1);
    assert_eq!(stats2.tokenized_nodes, 5);

    // Update 1 node only -> incremental sync tokenizes ONLY that 1 node
    conn.execute(
        "UPDATE nodes SET title = 'Quantum Sidecar Alpha', updated_at = '2026-04-02T00:00:00Z' WHERE id = 'doc-2'",
        [],
    )
    .unwrap();
    let hits3 = bm25f_search(&conn, "Quantum Sidecar", 5, &scope, &params).unwrap();
    assert_eq!(hits3.len(), 1);
    assert_eq!(hits3[0].node.id, "doc-2");
    let stats3 = cached_sidecar_stats(&conn, &scope).expect("cached stats");
    assert_eq!(
        stats3.tokenized_nodes, 6,
        "only the 1 updated node should be re-tokenized"
    );

    // Delete 1 node -> evicted cleanly
    conn.execute("DELETE FROM nodes WHERE id = 'doc-2'", [])
        .unwrap();
    let hits4 = bm25f_search(&conn, "Quantum Sidecar", 5, &scope, &params).unwrap();
    assert!(hits4.is_empty());
    let stats4 = cached_sidecar_stats(&conn, &scope).expect("cached stats");
    assert_eq!(stats4.total_docs, 4);
}

#[test]
fn graph_writer_persists_bm25_node_stats_and_read_only_sidecar_hydrates_without_retokenizing() {
    use lattice_core::db::Store;
    use lattice_core::graph_write::types::{EdgeSpec, NodeSpec};
    use lattice_core::graph_write::GraphWriter;
    use lattice_core::open_read_only;
    use std::sync::Arc;

    let dir = tempfile::tempdir().unwrap();
    let db_path = dir.path().join("knowledge_graph.sqlite");
    let store = Arc::new(Store::open(&db_path).unwrap());
    let writer = GraphWriter::open(Arc::clone(&store), dir.path().join("blobs")).unwrap();

    let mut meta = serde_json::Map::new();
    meta.insert("tags".into(), serde_json::json!(["graphrag", "한국어색인"]));
    meta.insert("workspace_id".into(), serde_json::json!("personal"));

    writer
        .upsert_nodes(&[
            NodeSpec {
                id: "gw-1".into(),
                node_type: "Document".into(),
                title: "사이드카 역색인 아키텍처".into(),
                summary: "O(1) 필드 길이 캐시 및 [[연결 개념 노드]] 참조".into(),
                metadata: meta.clone(),
                raw: serde_json::Map::new(),
                owner: None,
                workspace_id: Some("personal".into()),
                visibility: None,
            },
            NodeSpec {
                id: "gw-2".into(),
                node_type: "Concept".into(),
                title: "연결 개념 노드".into(),
                summary: "지식 그래프 1-hop 확장 테스트 노드".into(),
                metadata: serde_json::Map::new(),
                raw: serde_json::Map::new(),
                owner: None,
                workspace_id: Some("personal".into()),
                visibility: None,
            },
        ])
        .unwrap();
    writer
        .upsert_edges(&[EdgeSpec {
            from_node: "gw-1".into(),
            to_node: "gw-2".into(),
            edge_type: "RELATES_TO".into(),
            weight: 1.4,
            metadata: serde_json::Map::new(),
            legacy_label: None,
        }])
        .unwrap();

    // Verify bm25_node_stats rows were persisted by GraphWriter
    store
        .with_read_conn(|conn| {
            let count: i64 =
                conn.query_row("SELECT COUNT(*) FROM bm25_node_stats", [], |r| r.get(0))?;
            assert_eq!(count, 2);
            let (dl_title, tf_json): (i64, String) = conn.query_row(
                "SELECT dl_title, tf_json FROM bm25_node_stats WHERE node_id='gw-1'",
                [],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )?;
            assert!(dl_title >= 3);
            let parsed: Value = serde_json::from_str(&tf_json).unwrap();
            assert!(parsed["tf_title"]["아키텍처"].as_u64().unwrap_or(0) >= 1);
            assert!(parsed["tf_tags"]["graphrag"].as_u64().unwrap_or(0) >= 1);
            Ok(())
        })
        .unwrap();

    // Open a brand-new read-only connection and confirm Bm25SidecarIndex hydrates
    // from bm25_node_stats with zero re-tokenization and supports full GraphRAG
    let ro_conn = open_read_only(&db_path).unwrap();
    let mut fresh_sidecar = Bm25SidecarIndex::new();
    fresh_sidecar
        .sync_from_conn(&ro_conn, &Scope::default(), 5_000)
        .unwrap();
    assert_eq!(fresh_sidecar.cache_stats().sidecar_table_hits, 2);
    assert_eq!(fresh_sidecar.cache_stats().tokenized_nodes, 0);

    let model = LocalEmbeddingModel::new(384);
    let report = hybrid_bm25_vector_graph_search(
        &ro_conn,
        &model,
        "사이드카 역색인",
        &GraphRagOptions::default(),
    )
    .unwrap();
    let ids: Vec<&str> = report["results"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|r| r.get("id").and_then(Value::as_str))
        .collect();
    assert_eq!(ids[0], "gw-1");
    assert!(ids.contains(&"gw-2"));

    // Updating sensitivity updates bm25_node_stats; deleting node removes it
    writer
        .set_node_sensitivity("gw-1", true, Some("private"))
        .unwrap();
    writer.delete_node("gw-2").unwrap();
    store
        .with_read_conn(|conn| {
            let count: i64 =
                conn.query_row("SELECT COUNT(*) FROM bm25_node_stats", [], |r| r.get(0))?;
            assert_eq!(count, 1);
            Ok(())
        })
        .unwrap();
}

#[test]
fn sidecar_works_on_readonly_db_without_bm25_node_stats_table() {
    let dir = tempfile::tempdir().unwrap();
    let db_path = dir.path().join("legacy_frozen.sqlite");
    {
        let rw = Connection::open(&db_path).unwrap();
        rw.execute_batch(
            "CREATE TABLE nodes (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '2026-04-01T00:00:00Z',
                updated_at TEXT NOT NULL DEFAULT '2026-04-01T00:00:00Z'
            );
            CREATE TABLE edges (
                id TEXT PRIMARY KEY,
                from_node TEXT NOT NULL,
                to_node TEXT NOT NULL,
                type TEXT NOT NULL,
                weight REAL NOT NULL DEFAULT 1.0,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '2026-04-01T00:00:00Z'
            );
            CREATE TABLE vector_embeddings (
                item_id TEXT PRIMARY KEY,
                item_type TEXT NOT NULL,
                source_node TEXT NOT NULL,
                embedding BLOB NOT NULL,
                embedding_dim INTEGER NOT NULL,
                embedding_model TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                indexed_at TEXT NOT NULL DEFAULT '2026-04-01T00:00:00Z'
            );
            INSERT INTO nodes (id, type, title, summary) VALUES
                ('f1', 'Document', 'Frozen Golden Retrieval', 'Works on read-only DB without sidecar table');",
        )
        .unwrap();
    }

    let ro = lattice_core::open_read_only(&db_path).unwrap();
    let hits = bm25f_search(
        &ro,
        "Frozen Golden",
        10,
        &Scope::default(),
        &Bm25fParams::default(),
    )
    .unwrap();
    assert_eq!(hits.len(), 1);
    assert_eq!(hits[0].node.id, "f1");
}

#[test]
fn multi_hop_reasoning_paths_and_contradiction_synthesis_surface_2hop_bridges() {
    let dir = tempfile::tempdir().unwrap();
    let db_path = dir.path().join("multihop.sqlite");
    let conn = Connection::open(&db_path).unwrap();
    conn.execute_batch(
        "CREATE TABLE nodes (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT '2026-04-01T00:00:00Z',
            updated_at TEXT NOT NULL DEFAULT '2026-04-01T00:00:00Z'
        );
        CREATE TABLE edges (
            id TEXT PRIMARY KEY,
            from_node TEXT NOT NULL,
            to_node TEXT NOT NULL,
            type TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 1.0,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT '2026-04-01T00:00:00Z'
        );
        CREATE TABLE vector_embeddings (
            item_id TEXT PRIMARY KEY,
            item_type TEXT NOT NULL,
            source_node TEXT NOT NULL,
            embedding BLOB NOT NULL,
            embedding_dim INTEGER NOT NULL,
            embedding_model TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            indexed_at TEXT NOT NULL DEFAULT '2026-04-01T00:00:00Z'
        );
        INSERT INTO nodes (id, type, title, summary) VALUES
            ('seed-rfc', 'Document', 'Zero-Copy Wal Checkpointer RFC', 'Design of async wal checkpoint cadence'),
            ('bridge-concept', 'Concept', 'Shared Reader Lock Contention', 'Shared lock behavior under bursty readers'),
            ('hop2-incident', 'Document', 'October Tail Latency Postmortem', 'Root cause traced to long reader transactions'),
            ('conflict-note', 'Decision', 'Disable Auto-Checkpoint Policy', 'Legacy recommendation superseded by async cadence');
        INSERT INTO edges (id, from_node, to_node, type, weight) VALUES
            ('e1', 'seed-rfc', 'bridge-concept', 'MENTIONS', 1.0),
            ('e2', 'bridge-concept', 'hop2-incident', 'RELATES_TO', 1.0),
            ('e3', 'seed-rfc', 'conflict-note', 'CONTRADICTS', 1.0);",
    )
    .unwrap();

    let model = LocalEmbeddingModel::new(384);
    let report = hybrid_bm25_vector_graph_search(
        &conn,
        &model,
        "Zero-Copy Wal Checkpointer",
        &GraphRagOptions::default(),
    )
    .unwrap();

    let paths = report["reasoning_paths"]
        .as_array()
        .expect("reasoning_paths array");
    let two_hop = paths
        .iter()
        .find(|p| p["target_id"] == "hop2-incident")
        .expect("2-hop reasoning path to hop2-incident");
    assert_eq!(two_hop["seed_id"], "seed-rfc");
    assert_eq!(two_hop["bridge_id"], "bridge-concept");
    assert_eq!(two_hop["bridge_title"], "Shared Reader Lock Contention");
    assert_eq!(two_hop["hops"], 2);

    let bridges = report["synthesis"]["bridge_concepts"]
        .as_array()
        .expect("bridge_concepts");
    assert!(bridges.iter().any(|b| b["id"] == "bridge-concept"));

    let conflicts = report["synthesis"]["contradictions"]
        .as_array()
        .expect("contradictions");
    assert!(conflicts
        .iter()
        .any(|c| c["relation"] == "CONTRADICTS" && c["target_id"] == "conflict-note"));
}
