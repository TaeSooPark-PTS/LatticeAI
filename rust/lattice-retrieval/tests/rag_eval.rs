use std::collections::BTreeSet;
use std::sync::Arc;
use std::thread;

use lattice_core::LocalEmbeddingModel;
use lattice_retrieval::bm25::{
    bm25f_search, bm25f_search_with_options, cached_sidecar_stats, hybrid_bm25_vector_graph_search,
    load_scoped_corpus, Bm25fParams, GraphRagOptions, SidecarCachePool, DEFAULT_MAX_CACHE_BYTES,
};
use lattice_retrieval::rag_eval::run_default_rag_evaluation;
use lattice_retrieval::service::Scope;
use lattice_retrieval::vector::{
    vector_search, vector_search_aligned, VectorQueryInput, VectorSearchOptions,
};
use rusqlite::{params, Connection};
use serde_json::Value;

fn setup_scoped_db() -> Connection {
    let conn = Connection::open_in_memory().expect("in-memory sqlite");
    conn.execute_batch(
        "CREATE TABLE nodes (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00Z',
            updated_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00Z'
        );
        CREATE TABLE nodes_v2 (
            id TEXT PRIMARY KEY,
            workspace_id TEXT,
            visibility TEXT,
            superseded_by TEXT,
            importance_score REAL,
            lifecycle_status TEXT
        );
        CREATE TABLE edges (
            id TEXT PRIMARY KEY,
            from_node TEXT NOT NULL,
            to_node TEXT NOT NULL,
            type TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 1.0
        );
        CREATE TABLE chunks (
            id TEXT PRIMARY KEY,
            source_node TEXT NOT NULL,
            text TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE vector_embeddings (
            item_id TEXT PRIMARY KEY,
            item_type TEXT NOT NULL DEFAULT 'node',
            source_node TEXT NOT NULL,
            embedding BLOB NOT NULL,
            embedding_dim INTEGER NOT NULL,
            embedding_model TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            indexed_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00Z'
        );",
    )
    .expect("schema");
    conn
}

fn encode_f32(vec: &[f64]) -> Vec<u8> {
    let mut out = Vec::with_capacity(vec.len() * 4);
    for &v in vec {
        out.extend_from_slice(&(v as f32).to_le_bytes());
    }
    out
}

#[test]
fn workspace_scope_sql_filter_prevents_newer_other_workspace_crowding_out_old_knowledge() {
    let mut conn = setup_scoped_db();
    let hash_model = LocalEmbeddingModel::default();
    let old_text = "Quantum Lattice Consensus Protocol Recovery";
    let old_vec = hash_model.embed(old_text);

    // Insert 1 older node (2024) in `ws-target`.
    conn.execute(
        "INSERT INTO nodes (id, type, title, summary, metadata_json, updated_at)
         VALUES ('target-old-1', 'Decision', ?1, 'Critical recovery steps for consensus partition.', ?2, '2024-03-01T00:00:00Z')",
        params![old_text, r#"{"workspace_id":"ws-target"}"#],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO nodes_v2 (id, workspace_id, visibility, importance_score, lifecycle_status)
         VALUES ('target-old-1', 'ws-target', 'workspace', 0.9, 'active')",
        [],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO vector_embeddings (item_id, item_type, source_node, embedding, embedding_dim, embedding_model, indexed_at)
         VALUES ('target-old-1', 'node', 'target-old-1', ?1, ?2, ?3, '2024-03-01T00:00:00Z')",
        params![hash_model.encode(&old_vec), hash_model.dim() as i64, hash_model.model_id()],
    )
    .unwrap();

    // Insert 5,500 newer (2026) nodes & vectors in `ws-other` (exceeding 5,000 sidecar cap).
    let tx = conn.transaction().unwrap();
    let noise_vec = hash_model.embed("Quarterly sync telemetry heartbeat log");
    let noise_blob = hash_model.encode(&noise_vec);
    for i in 0..5_500 {
        let id = format!("noise-{i}");
        let ts = format!(
            "2026-11-{:02}T{:02}:{:02}:00Z",
            1 + (i % 28),
            (i / 60) % 24,
            i % 60
        );
        tx.execute(
            "INSERT INTO nodes (id, type, title, summary, metadata_json, updated_at)
             VALUES (?1, 'Note', 'Quantum Lattice Consensus Telemetry', 'Routine other workspace log.', ?2, ?3)",
            params![id, r#"{"workspace_id":"ws-other"}"#, ts],
        )
        .unwrap();
        tx.execute(
            "INSERT INTO nodes_v2 (id, workspace_id, visibility, importance_score, lifecycle_status)
             VALUES (?1, 'ws-other', 'workspace', 0.5, 'active')",
            params![id],
        )
        .unwrap();
        if i < 600 {
            tx.execute(
                "INSERT INTO vector_embeddings (item_id, item_type, source_node, embedding, embedding_dim, embedding_model, indexed_at)
                 VALUES (?1, 'node', ?1, ?2, ?3, ?4, ?5)",
                params![id, noise_blob, hash_model.dim() as i64, hash_model.model_id(), ts],
            )
            .unwrap();
        }
    }
    tx.commit().unwrap();

    let scope = Scope::for_workspace("ws-target");

    // 1. Scoped corpus loader returns the old `ws-target` node despite 5,500 newer nodes elsewhere.
    let corpus = load_scoped_corpus(&conn, &scope, 5_000).unwrap();
    assert_eq!(corpus.len(), 1);
    assert_eq!(corpus[0].id, "target-old-1");

    // 2. BM25F search recalls `target-old-1`.
    let bm25_hits = bm25f_search(
        &conn,
        "Quantum Consensus Recovery",
        10,
        &scope,
        &Bm25fParams::default(),
    )
    .unwrap();
    assert_eq!(bm25_hits.len(), 1);
    assert_eq!(bm25_hits[0].node.id, "target-old-1");

    // 3. Keyword search recalls `target-old-1` even though 5,500 newer matches exist in `ws-other`.
    let mut allowed = BTreeSet::new();
    allowed.insert("ws-target".to_string());
    let kw = lattice_retrieval::keyword::search(
        &conn,
        "Quantum Lattice Consensus",
        10,
        Some(&allowed),
        false,
    )
    .unwrap();
    let kw_matches = kw["matches"].as_array().unwrap();
    assert_eq!(kw_matches.len(), 1);
    assert_eq!(kw_matches[0]["id"], "target-old-1");

    // 4. Scoped aligned vector search recalls `target-old-1`.
    let vec_res = vector_search_aligned(
        &conn,
        VectorQueryInput::Model(&hash_model),
        "Quantum Lattice Consensus Recovery",
        &VectorSearchOptions {
            limit: 10,
            min_score: 0.0,
            scope: Some(&scope),
            include_historical: false,
        },
    )
    .unwrap();
    let vec_matches = vec_res["matches"].as_array().unwrap();
    assert_eq!(vec_matches.len(), 1);
    assert_eq!(vec_matches[0]["node_id"], "target-old-1");

    // 5. Full 3-way GraphRAG search recalls `target-old-1`.
    let graphrag = hybrid_bm25_vector_graph_search(
        &conn,
        &hash_model,
        "Quantum Consensus Recovery",
        &GraphRagOptions {
            scope,
            ..GraphRagOptions::default()
        },
    )
    .unwrap();
    let hits = graphrag["matches"].as_array().unwrap();
    assert_eq!(hits.len(), 1);
    assert_eq!(hits[0]["id"], "target-old-1");
}

#[test]
fn lifecycle_status_excludes_superseded_and_deprecated_by_default_and_downranks_in_historical() {
    let conn = setup_scoped_db();
    let scope = Scope::for_workspace("ws-life");

    conn.execute(
        "INSERT INTO nodes (id, type, title, summary, metadata_json, updated_at) VALUES
         ('active-node', 'Decision', 'Cache Eviction Policy', 'Active LRU and byte-bounded policy.', '{\"workspace_id\":\"ws-life\"}', '2026-01-05T00:00:00Z'),
         ('superseded-node', 'Document', 'Cache Eviction Policy', 'Old unbounded cache policy.', '{\"workspace_id\":\"ws-life\"}', '2026-03-01T00:00:00Z'),
         ('deprecated-node', 'Note', 'Cache Eviction Policy', 'Deprecated manual clear note.', '{\"workspace_id\":\"ws-life\"}', '2026-03-10T00:00:00Z')",
        [],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO nodes_v2 (id, workspace_id, visibility, superseded_by, importance_score, lifecycle_status) VALUES
         ('active-node', 'ws-life', 'public', NULL, 0.95, 'active'),
         ('superseded-node', 'ws-life', 'workspace', 'active-node', 0.50, 'superseded'),
         ('deprecated-node', 'ws-life', 'private', NULL, 0.30, 'deprecated')",
        [],
    )
    .unwrap();

    // Default (`include_historical = false`) excludes superseded & deprecated.
    let default_hits = bm25f_search(
        &conn,
        "Cache Eviction Policy",
        10,
        &scope,
        &Bm25fParams::default(),
    )
    .unwrap();
    let default_ids: Vec<&str> = default_hits.iter().map(|h| h.node.id.as_str()).collect();
    assert_eq!(default_ids, vec!["active-node"]);

    // Historical lookup (`include_historical = true`) recalls all 3 and ranks active first.
    let hist_hits = bm25f_search_with_options(
        &conn,
        "Cache Eviction Policy",
        10,
        &scope,
        &Bm25fParams::default(),
        true,
    )
    .unwrap();
    let hist_ids: Vec<&str> = hist_hits.iter().map(|h| h.node.id.as_str()).collect();
    assert_eq!(hist_ids[0], "active-node");
    assert!(hist_ids.contains(&"superseded-node"));
    assert!(hist_ids.contains(&"deprecated-node"));
    assert!(hist_hits[0].bm25_score > hist_hits[1].bm25_score * 2.0);
}

#[test]
fn vector_search_reports_semantic_match_hash_fallback_and_reindex_required() {
    let conn = setup_scoped_db();
    let scope = Scope::for_workspace("ws-vec");

    conn.execute(
        "INSERT INTO nodes (id, type, title, summary, metadata_json)
         VALUES ('doc-sem', 'Document', 'Semantic Embedding Contract', '768-dim embeddinggemma-2 vector row', '{\"workspace_id\":\"ws-vec\"}')",
        [],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO nodes_v2 (id, workspace_id, lifecycle_status)
         VALUES ('doc-sem', 'ws-vec', 'active')",
        [],
    )
    .unwrap();

    // Store a non-hash 768-d embedding (`google/embeddinggemma-2`).
    let mut stored_vec = vec![0.0_f64; 768];
    stored_vec[0] = 0.8;
    stored_vec[1] = 0.6;
    conn.execute(
        "INSERT INTO vector_embeddings (item_id, item_type, source_node, embedding, embedding_dim, embedding_model, indexed_at)
         VALUES ('doc-sem', 'node', 'doc-sem', ?1, 768, 'google/embeddinggemma-2', '2026-04-01T00:00:00Z')",
        params![encode_f32(&stored_vec)],
    )
    .unwrap();

    // 1. Active query is hash model (384-d) while DB only has 768-d semantic vectors -> `reindex_required`.
    let hash_model = LocalEmbeddingModel::default();
    let legacy_res = vector_search(&conn, &hash_model, "Semantic Embedding", 10, 0.0).unwrap();
    assert_eq!(legacy_res["provider_status"], "reindex_required");
    assert_eq!(legacy_res["reindex_required"], true);
    assert!(legacy_res["stored_models"]
        .as_array()
        .unwrap()
        .iter()
        .any(|m| m["model_id"] == "google/embeddinggemma-2" && m["dim"] == 768));

    let aligned_mismatch = vector_search_aligned(
        &conn,
        VectorQueryInput::Model(&hash_model),
        "Semantic Embedding",
        &VectorSearchOptions {
            scope: Some(&scope),
            ..VectorSearchOptions::default()
        },
    )
    .unwrap();
    assert_eq!(aligned_mismatch["provider_status"], "reindex_required");
    assert_eq!(aligned_mismatch["reindex_required"], true);

    // 2. Semantic query matching model & dim (`google/embeddinggemma-2` OR alias `embeddinggemma-2:768`) -> `semantic_provider_match`.
    let mut q_vec = vec![0.0_f64; 768];
    q_vec[0] = 0.8;
    q_vec[1] = 0.6;
    let aligned_ok = vector_search_aligned(
        &conn,
        VectorQueryInput::Precomputed {
            model_id: "embeddinggemma-2:768",
            vector: &q_vec,
            is_hash_fallback: false,
        },
        "Semantic Embedding",
        &VectorSearchOptions {
            scope: Some(&scope),
            ..VectorSearchOptions::default()
        },
    )
    .unwrap();
    assert_eq!(aligned_ok["provider_status"], "semantic_provider_match");
    assert_eq!(aligned_ok["reindex_required"], false);
    let matches = aligned_ok["matches"].as_array().unwrap();
    assert_eq!(matches.len(), 1);
    assert_eq!(matches[0]["node_id"], "doc-sem");
    assert!(matches[0]["score"].as_f64().unwrap() > 0.99);

    // 3. Adding hash vector & querying with hash model -> `hash_fallback`.
    let h_vec = hash_model.embed("Semantic Embedding Contract");
    conn.execute(
        "INSERT INTO vector_embeddings (item_id, item_type, source_node, embedding, embedding_dim, embedding_model, indexed_at)
         VALUES ('doc-sem#hash', 'node', 'doc-sem', ?1, ?2, ?3, '2026-04-02T00:00:00Z')",
        params![hash_model.encode(&h_vec), hash_model.dim() as i64, hash_model.model_id()],
    )
    .unwrap();
    let aligned_hash = vector_search_aligned(
        &conn,
        VectorQueryInput::Model(&hash_model),
        "Semantic Embedding",
        &VectorSearchOptions {
            scope: Some(&scope),
            ..VectorSearchOptions::default()
        },
    )
    .unwrap();
    assert_eq!(aligned_hash["provider_status"], "hash_fallback");
    assert_eq!(aligned_hash["reindex_required"], false);
    assert_eq!(aligned_hash["matches"].as_array().unwrap().len(), 1);
}

#[test]
fn sidecar_precomputes_subword_incrementally_without_per_query_recomputation() {
    let conn = setup_scoped_db();
    for i in 0..25 {
        conn.execute(
            "INSERT INTO nodes (id, type, title, summary, metadata_json, updated_at)
             VALUES (?1, 'Document', ?2, ?3, '{\"workspace_id\":\"ws-sub\"}', '2026-04-01T00:00:00Z')",
            params![
                format!("n-{i}"),
                format!("Incremental Subword Index Node {i}"),
                format!("Details for subword caching benchmark entry {i}")
            ],
        )
        .unwrap();
    }
    let scope = Scope::for_workspace("ws-sub");
    let model = LocalEmbeddingModel::default();
    let opts = GraphRagOptions {
        scope: scope.clone(),
        ..GraphRagOptions::default()
    };

    // Query 1 computes subword embeddings once for all 25 nodes.
    let _ =
        hybrid_bm25_vector_graph_search(&conn, &model, "subword caching benchmark", &opts).unwrap();
    let stats_1 = cached_sidecar_stats(&conn, &scope).expect("stats after q1");
    assert_eq!(stats_1.subword_computed_nodes, 25);

    // Query 2 (different query, unchanged corpus) must recompute 0 subword embeddings!
    let _ =
        hybrid_bm25_vector_graph_search(&conn, &model, "incremental index node", &opts).unwrap();
    let stats_2 = cached_sidecar_stats(&conn, &scope).expect("stats after q2");
    assert_eq!(stats_2.subword_computed_nodes, 25);
    assert!(stats_2.cache_hits > stats_1.cache_hits);

    // Modify 1 node -> only 1 additional subword embedding computed!
    conn.execute(
        "UPDATE nodes SET summary = 'Updated single node content for incremental check', updated_at = '2026-04-02T00:00:00Z' WHERE id = 'n-3'",
        [],
    )
    .unwrap();
    let _ = hybrid_bm25_vector_graph_search(&conn, &model, "incremental check", &opts).unwrap();
    let stats_3 = cached_sidecar_stats(&conn, &scope).expect("stats after update");
    assert_eq!(stats_3.subword_computed_nodes, 26);
}

#[test]
fn sidecar_cache_pool_supports_concurrent_snapshot_reads_and_bounded_eviction() {
    let dir = tempfile::tempdir().unwrap();
    let db_path = dir.path().join("concurrent_cache.sqlite");
    {
        let conn = Connection::open(&db_path).unwrap();
        conn.execute_batch(
            "CREATE TABLE nodes (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL DEFAULT '2026-04-01T00:00:00Z'
            );",
        )
        .unwrap();
        for ws in 0..80 {
            for doc in 0..4 {
                conn.execute(
                    "INSERT INTO nodes (id, type, title, summary, metadata_json) VALUES (?1, 'Document', ?2, ?3, ?4)",
                    params![
                        format!("doc-{ws}-{doc}"),
                        format!("Workspace {ws} title {doc}"),
                        "Repeated shared corpus summary text for memory estimation testing.",
                        format!(r#"{{"workspace_id":"ws-{ws}"}}"#)
                    ],
                )
                .unwrap();
            }
        }
    }

    // 1. Entry count cap (`64`) eviction test.
    let pool_count = SidecarCachePool::new(64, DEFAULT_MAX_CACHE_BYTES);
    let conn = Connection::open(&db_path).unwrap();
    for ws in 0..75 {
        let scope = Scope::for_workspace(format!("ws-{ws}"));
        let snap = pool_count
            .snapshot_for_conn(&conn, &scope, 5_000, false, false, false)
            .unwrap();
        assert_eq!(snap.doc_count(), 4);
    }
    assert_eq!(pool_count.entry_count(), 64);

    // 2. Byte-budget LRU/cost-aware eviction test.
    let single_scope = Scope::for_workspace("ws-0");
    let single_snap = pool_count
        .snapshot_for_conn(&conn, &single_scope, 5_000, false, false, true)
        .unwrap();
    let one_entry_bytes = single_snap.estimated_memory_bytes();
    assert!(one_entry_bytes > 10_000);

    let budget_bytes = one_entry_bytes * 3 + one_entry_bytes / 2;
    let pool_bytes = SidecarCachePool::new(64, budget_bytes);
    for ws in 0..10 {
        let scope = Scope::for_workspace(format!("ws-{ws}"));
        let _ = pool_bytes
            .snapshot_for_conn(&conn, &scope, 5_000, false, false, true)
            .unwrap();
    }
    assert!(pool_bytes.entry_count() <= 3);
    assert!(pool_bytes.total_estimated_bytes() <= budget_bytes);

    // 3. Concurrent snapshot reads across multiple threads without mutex serialization on reads.
    let shared_pool = Arc::new(SidecarCachePool::new(64, DEFAULT_MAX_CACHE_BYTES));
    {
        let warm = shared_pool
            .snapshot_for_conn(
                &conn,
                &Scope::for_workspace("ws-1"),
                5_000,
                false,
                false,
                true,
            )
            .unwrap();
        assert_eq!(warm.doc_count(), 4);
    }
    let mut handles = Vec::new();
    for _ in 0..6 {
        let p = Arc::clone(&shared_pool);
        let path = db_path.clone();
        handles.push(thread::spawn(move || {
            let c = Connection::open(path).unwrap();
            let scope = Scope::for_workspace("ws-1");
            for _ in 0..10 {
                let snap = p
                    .snapshot_for_conn(&c, &scope, 5_000, false, false, true)
                    .unwrap();
                let hits = snap.score("Workspace 1 title", &Bm25fParams::default());
                assert!(!hits.is_empty());
            }
        }));
    }
    for h in handles {
        h.join().expect("reader thread");
    }
}

#[test]
fn quantitative_rag_evaluation_suite_meets_quality_and_latency_gates() {
    let report = run_default_rag_evaluation(5_200).expect("rag evaluation");
    let json_summary: Value = serde_json::to_value(&report).unwrap();
    eprintln!(
        "RAG_EVAL_REPORT={}",
        serde_json::to_string_pretty(&json_summary).unwrap()
    );

    assert_eq!(report.total_queries, 5);
    assert!(
        report.recall_at_5 >= 0.90,
        "Recall@5 {} below 0.90",
        report.recall_at_5
    );
    assert!(
        report.recall_at_10 >= 0.95,
        "Recall@10 {} below 0.95",
        report.recall_at_10
    );
    assert!(
        report.ndcg_at_10 >= 0.85,
        "NDCG@10 {} below 0.85",
        report.ndcg_at_10
    );
    assert!(
        report.citation_precision >= 0.50,
        "Citation Precision {} below 0.50",
        report.citation_precision
    );
    assert!(
        report.citation_recall >= 0.90,
        "Citation Recall {} below 0.90",
        report.citation_recall
    );
    assert!(
        report.faithfulness >= 0.90,
        "Faithfulness {} below 0.90",
        report.faithfulness
    );
    assert!(
        report.answer_relevance >= 0.65,
        "Answer Relevance {} below 0.65",
        report.answer_relevance
    );
    assert!(
        (report.superseded_exclusion_rate - 1.0).abs() < 1e-9,
        "Superseded exclusion rate {} must be 1.0",
        report.superseded_exclusion_rate
    );
    assert!(
        report.korean_recall_at_5 >= 0.90,
        "Korean Recall@5 {} below 0.90",
        report.korean_recall_at_5
    );
    assert!(
        report.korean_ndcg_at_10 >= 0.85,
        "Korean NDCG@10 {} below 0.85",
        report.korean_ndcg_at_10
    );
    assert!(
        report.cross_model_jaccard_at_5 >= 0.60,
        "Cross-model Jaccard@5 {} below 0.60",
        report.cross_model_jaccard_at_5
    );
    assert!(report.ingest_latency.p50_ms >= 0.0 && report.ingest_latency.p95_ms >= 0.0);
    assert!(report.search_latency.p50_ms >= 0.0 && report.search_latency.p95_ms >= 0.0);
}
