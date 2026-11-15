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
