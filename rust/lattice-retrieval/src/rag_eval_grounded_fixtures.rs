//! Seeded SQLite store and ground-truth evaluation cases for [`super`].

use lattice_core::entity_resolution::{apply_entity_consolidation, EntityAliasBook};
use lattice_core::temporal::stamp_temporal_fact;
use lattice_core::{CoreError, LocalEmbeddingModel};
use rusqlite::{params, Connection};
use serde_json::json;

use super::{EvalQueryKind, SeparatedEvalCase};

fn encode_f32_le(vec: &[f64]) -> Vec<u8> {
    let mut out = Vec::with_capacity(vec.len() * 4);
    for &v in vec {
        out.extend_from_slice(&(v as f32).to_le_bytes());
    }
    out
}

/// Seed a realistic Brain SQLite store indexed with the real [`LocalEmbeddingModel`]
/// (`lattice-local-hash-v1`), entity resolution consolidation, and temporal supersession.
pub fn build_production_grounded_eval_store(
    workspace_id: &str,
) -> Result<(Connection, Vec<SeparatedEvalCase>), CoreError> {
    let mut conn = Connection::open_in_memory()?;
    conn.execute_batch(
        "CREATE TABLE nodes (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
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
            indexed_at TEXT NOT NULL DEFAULT '2026-11-01T00:00:00Z'
        );",
    )?;

    let embedder = LocalEmbeddingModel::new(384);
    let model_name = embedder.model_id().to_string();

    let rows = vec![
        (
            "doc:ko_security",
            "Document",
            "로컬 전용 보안 및 외부 네트워크 차단 정책",
            "외부 클라우드 연결이 비활성화되면 모든 추론과 벡터 검색은 로컬 루프백 포트 4824 내부에서만 처리됩니다",
            "active",
            None,
            "2026-06-01T00:00:00Z",
        ),
        (
            "doc:en_storage",
            "Document",
            "SQLite WAL Single Writer Architecture",
            "GraphWriter serializes all knowledge graph mutations through a single IMMEDIATE SQLite WAL transaction",
            "active",
            None,
            "2026-06-02T00:00:00Z",
        ),
        (
            "doc:mixed_gemma",
            "Document",
            "Gemma 4 로컬 모델 교체 및 브레인 호환성",
            "Gemma 4 모델을 Qwen 또는 Cloud LLM으로 교체해도 SQLite 브레인 지식 그래프와 출처 인용은 재구축 없이 그대로 유지됩니다",
            "active",
            None,
            "2026-06-03T00:00:00Z",
        ),
        (
            "doc:multidoc_part1",
            "Decision",
            "Ingestion SHA256 Content Addressing",
            "Incremental folder ingestion skips unchanged files using SHA256 content hashes and mtime snapshots",
            "active",
            None,
            "2026-06-04T00:00:00Z",
        ),
        (
            "doc:multidoc_part2",
            "Decision",
            "Tombstone Cleanup on Deleted Source Files",
            "Deleted source files mark orphaned document chunks archived while preserving historical provenance audit logs",
            "active",
            None,
            "2026-06-05T00:00:00Z",
        ),
        (
            "doc:temporal_jan_old",
            "Decision",
            "Project Orion January Runtime",
            "Project Orion backend runtime was originally implemented in Python FastAPI on port 8000",
            "superseded",
            Some("doc:temporal_mar_cur"),
            "2026-01-10T00:00:00Z",
        ),
        (
            "doc:temporal_mar_cur",
            "Decision",
            "Project Orion Current Rust Runtime",
            "Project Orion migrated its primary HTTP gateway to Rust Axum on port 4824 while retaining stateless Python ML compute",
            "active",
            None,
            "2026-03-20T00:00:00Z",
        ),
        (
            "ent:atlas_en",
            "Project",
            "Atlas Engine",
            "Atlas Engine provides deterministic hybrid BM25 and vector retrieval with RRF fusion",
            "active",
            None,
            "2026-04-01T00:00:00Z",
        ),
        (
            "ent:atlas_ko",
            "Project",
            "아틀라스 엔진",
            "아틀라스 엔진은 한국어 조사 분리와 하이브리드 검색을 지원합니다",
            "active",
            None,
            "2026-04-02T00:00:00Z",
        ),
    ];

    for (id, ntype, title, summary, lifecycle, sup_by, ts) in &rows {
        let meta = json!({
            "workspace_id": workspace_id,
            "lifecycle_status": lifecycle,
            "superseded_by": sup_by,
        });
        conn.execute(
            "INSERT INTO nodes (id, type, title, summary, metadata_json, created_at, updated_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?6)",
            params![id, ntype, title, summary, meta.to_string(), ts],
        )?;
        conn.execute(
            "INSERT INTO nodes_v2 (id, workspace_id, visibility, superseded_by, importance_score, lifecycle_status)
             VALUES (?1, ?2, 'private', ?3, 0.9, ?4)",
            params![id, workspace_id, sup_by, lifecycle],
        )?;
        conn.execute(
            "INSERT INTO chunks (id, source_node, text, metadata_json)
             VALUES (?1, ?2, ?3, ?4)",
            params![format!("chk:{id}"), id, summary, meta.to_string()],
        )?;
        let vec = embedder.embed(&format!("{title}\n{summary}"));
        conn.execute(
            "INSERT INTO vector_embeddings (item_id, item_type, source_node, embedding, embedding_dim, embedding_model, metadata_json, indexed_at)
             VALUES (?1, 'node', ?1, ?2, 384, ?3, ?4, ?5)",
            params![id, encode_f32_le(&vec), model_name, meta.to_string(), ts],
        )?;
    }

    conn.execute(
        "INSERT INTO edges (id, from_node, to_node, type, weight)
         VALUES ('e:multidoc', 'doc:multidoc_part1', 'doc:multidoc_part2', 'RELATED_TO', 1.0)",
        [],
    )?;

    let txn = conn.transaction()?;
    stamp_temporal_fact(
        &txn,
        "doc:temporal_jan_old",
        "Project Orion",
        "2026-01-10T00:00:00Z",
        None,
        "2026-01-10T00:00:00Z",
        None,
    )?;
    stamp_temporal_fact(
        &txn,
        "doc:temporal_mar_cur",
        "Project Orion",
        "2026-03-20T00:00:00Z",
        None,
        "2026-03-20T00:00:00Z",
        Some("doc:temporal_jan_old"),
    )?;
    let mut aliases = EntityAliasBook::new();
    aliases.register_aliases("Atlas Engine", ["아틀라스 엔진"]);
    apply_entity_consolidation(&txn, Some(workspace_id), &aliases, "2026-11-18T00:00:00Z")?;
    txn.commit()?;

    let cases = vec![
        SeparatedEvalCase {
            id: "ko_loopback_policy".to_string(),
            kind: EvalQueryKind::KoreanSingleDoc,
            query: "외부 클라우드 연결 비활성화 시 로컬 루프백 포트 보안 정책은?".to_string(),
            expected_active_ids: vec!["doc:ko_security".to_string()],
            supporting_active_ids: vec![],
            superseded_trap_ids: vec![],
            expect_abstain: false,
        },
        SeparatedEvalCase {
            id: "en_wal_writer".to_string(),
            kind: EvalQueryKind::EnglishSingleDoc,
            query: "How does GraphWriter serialize SQLite WAL knowledge graph mutations?".to_string(),
            expected_active_ids: vec!["doc:en_storage".to_string()],
            supporting_active_ids: vec![],
            superseded_trap_ids: vec![],
            expect_abstain: false,
        },
        SeparatedEvalCase {
            id: "mixed_model_swap".to_string(),
            kind: EvalQueryKind::MixedKoEn,
            query: "Gemma 4 모델을 Qwen이나 Cloud LLM으로 교체할 때 SQLite 브레인 호환성은?".to_string(),
            expected_active_ids: vec!["doc:mixed_gemma".to_string()],
            supporting_active_ids: vec![],
            superseded_trap_ids: vec![],
            expect_abstain: false,
        },
        SeparatedEvalCase {
            id: "multidoc_incremental_ingest".to_string(),
            kind: EvalQueryKind::MultiDocumentSynthesis,
            query: "Incremental folder ingestion SHA256 hashes and deleted source file tombstone cleanup".to_string(),
            expected_active_ids: vec![
                "doc:multidoc_part1".to_string(),
                "doc:multidoc_part2".to_string(),
            ],
            supporting_active_ids: vec![],
            superseded_trap_ids: vec![],
            expect_abstain: false,
        },
        SeparatedEvalCase {
            id: "temporal_orion_current".to_string(),
            kind: EvalQueryKind::TemporalEvolution,
            query: "What is Project Orion current gateway runtime and port?".to_string(),
            expected_active_ids: vec!["doc:temporal_mar_cur".to_string()],
            supporting_active_ids: vec![],
            superseded_trap_ids: vec!["doc:temporal_jan_old".to_string()],
            expect_abstain: false,
        },
        SeparatedEvalCase {
            id: "entity_alias_bilingual".to_string(),
            kind: EvalQueryKind::EntityAliasResolution,
            query: "Atlas Engine 아틀라스 엔진 하이브리드 BM25 RRF 검색".to_string(),
            expected_active_ids: vec!["ent:atlas_en".to_string(), "ent:atlas_ko".to_string()],
            supporting_active_ids: vec![],
            superseded_trap_ids: vec![],
            expect_abstain: false,
        },
        SeparatedEvalCase {
            id: "ungrounded_mars_weather".to_string(),
            kind: EvalQueryKind::UngroundedAbstain,
            query: "화성 올림푸스 산의 2026년 11월 평균 대기압과 풍속은 얼마인가요?".to_string(),
            expected_active_ids: vec![],
            supporting_active_ids: vec![],
            superseded_trap_ids: vec![],
            expect_abstain: true,
        },
        SeparatedEvalCase {
            id: "ungrounded_stock_dividend".to_string(),
            kind: EvalQueryKind::UngroundedAbstain,
            query: "What was the Q3 2026 dividend payout per share of Antarctic Railways Corp?".to_string(),
            expected_active_ids: vec![],
            supporting_active_ids: vec![],
            superseded_trap_ids: vec![],
            expect_abstain: true,
        },
    ];

    Ok((conn, cases))
}
