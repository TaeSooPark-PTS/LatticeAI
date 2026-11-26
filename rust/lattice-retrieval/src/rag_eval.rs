use std::collections::{HashMap, HashSet};
use std::time::Instant;

use lattice_core::{CoreError, LocalEmbeddingModel};
use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::bm25::{
    hybrid_bm25_vector_graph_search, tokenize_bm25_cjk, GraphRagConfig, GraphRagOptions,
    QueryVectorOverride,
};
use crate::service::Scope;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RagEvalCase {
    pub id: String,
    pub category: String,
    pub query: String,
    pub expected_ids: Vec<String>,
    pub supporting_ids: Vec<String>,
    pub retired_trap_ids: Vec<String>,
    pub required_claim_terms: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RagEvalCaseResult {
    pub id: String,
    pub category: String,
    pub retrieved_ids: Vec<String>,
    pub recall_at_5: f64,
    pub recall_at_10: f64,
    pub reciprocal_rank: f64,
    pub ndcg_at_10: f64,
    pub citation_precision: f64,
    pub citation_recall: f64,
    pub faithfulness: f64,
    pub answer_relevance: f64,
    pub recency_correct: Option<bool>,
    pub retired_leaked: usize,
    pub latency_ms: f64,
}

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize)]
pub struct LatencySummaryMs {
    pub samples: usize,
    pub p50_ms: f64,
    pub p95_ms: f64,
    pub max_ms: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RagEvalReport {
    pub total_queries: usize,
    pub recall_at_5: f64,
    pub recall_at_10: f64,
    pub mrr: f64,
    pub ndcg_at_10: f64,
    pub citation_precision: f64,
    pub citation_recall: f64,
    pub faithfulness: f64,
    pub answer_relevance: f64,
    pub recency_accuracy: f64,
    pub temporal_cases: usize,
    pub superseded_exclusion_rate: f64,
    pub korean_recall_at_5: f64,
    pub korean_ndcg_at_10: f64,
    pub cross_model_jaccard_at_5: f64,
    pub ingest_latency: LatencySummaryMs,
    pub search_latency: LatencySummaryMs,
    pub cases: Vec<RagEvalCaseResult>,
}

pub fn recall_at_k(retrieved: &[String], expected: &[String], k: usize) -> f64 {
    if expected.is_empty() {
        return 1.0;
    }
    let top: HashSet<&str> = retrieved.iter().take(k).map(String::as_str).collect();
    let hits = expected
        .iter()
        .filter(|id| top.contains(id.as_str()))
        .count();
    hits as f64 / expected.len() as f64
}

pub fn reciprocal_rank(retrieved: &[String], expected: &[String]) -> f64 {
    if expected.is_empty() {
        return 1.0;
    }
    let wanted: HashSet<&str> = expected.iter().map(String::as_str).collect();
    retrieved
        .iter()
        .position(|id| wanted.contains(id.as_str()))
        .map(|idx| 1.0 / (idx + 1) as f64)
        .unwrap_or(0.0)
}

pub fn recency_correct(retrieved: &[String], current: &[String], stale: &[String]) -> bool {
    let current_set: HashSet<&str> = current.iter().map(String::as_str).collect();
    let stale_set: HashSet<&str> = stale.iter().map(String::as_str).collect();
    let first_current = retrieved
        .iter()
        .position(|id| current_set.contains(id.as_str()));
    let first_stale = retrieved
        .iter()
        .position(|id| stale_set.contains(id.as_str()));
    match (first_current, first_stale) {
        (Some(cur), Some(old)) => cur < old,
        (Some(_), None) => true,
        _ => false,
    }
}

pub fn ndcg_at_k(
    retrieved: &[String],
    expected: &[String],
    supporting: &[String],
    k: usize,
) -> f64 {
    if expected.is_empty() && supporting.is_empty() {
        return 1.0;
    }
    let mut rel_map: HashMap<&str, f64> = HashMap::new();
    for id in supporting {
        rel_map.insert(id.as_str(), 0.5);
    }
    for id in expected {
        rel_map.insert(id.as_str(), 1.0);
    }

    let dcg: f64 = retrieved
        .iter()
        .take(k)
        .enumerate()
        .map(|(idx, id)| {
            let rel = rel_map.get(id.as_str()).copied().unwrap_or(0.0);
            if rel <= 0.0 {
                0.0
            } else {
                (2.0_f64.powf(rel) - 1.0) / ((idx + 2) as f64).log2()
            }
        })
        .sum();

    let mut ideal_rels: Vec<f64> = rel_map.values().copied().collect();
    ideal_rels.sort_by(|a, b| b.total_cmp(a));
    let idcg: f64 = ideal_rels
        .into_iter()
        .take(k)
        .enumerate()
        .map(|(idx, rel)| (2.0_f64.powf(rel) - 1.0) / ((idx + 2) as f64).log2())
        .sum();
    if idcg <= 1e-12 {
        0.0
    } else {
        (dcg / idcg).clamp(0.0, 1.0)
    }
}

pub fn citation_precision_recall(
    retrieved: &[String],
    expected: &[String],
    supporting: &[String],
    cited_k: usize,
) -> (f64, f64) {
    let relevant: HashSet<&str> = expected
        .iter()
        .chain(supporting.iter())
        .map(String::as_str)
        .collect();
    if relevant.is_empty() {
        return (1.0, 1.0);
    }
    let cited: Vec<&str> = retrieved.iter().take(cited_k).map(String::as_str).collect();
    if cited.is_empty() {
        return (0.0, 0.0);
    }
    let relevant_cited = cited.iter().filter(|id| relevant.contains(**id)).count();
    let precision = relevant_cited as f64 / cited.len() as f64;
    let recall = expected
        .iter()
        .filter(|id| cited.contains(&id.as_str()))
        .count() as f64
        / expected.len().max(1) as f64;
    (precision.clamp(0.0, 1.0), recall.clamp(0.0, 1.0))
}

pub fn faithfulness_and_relevance(
    query: &str,
    required_claim_terms: &[String],
    retrieved_texts: &[String],
) -> (f64, f64) {
    let evidence_tokens: HashSet<String> = retrieved_texts
        .iter()
        .flat_map(|t| tokenize_bm25_cjk(t))
        .collect();

    let faithfulness = if required_claim_terms.is_empty() {
        if retrieved_texts.is_empty() {
            0.0
        } else {
            1.0
        }
    } else {
        let mut supported = 0usize;
        for claim in required_claim_terms {
            let claim_tokens = tokenize_bm25_cjk(claim);
            let claim_ok = !claim_tokens.is_empty()
                && claim_tokens
                    .iter()
                    .filter(|t| !t.starts_with("ko_bi:") && !t.starts_with("ja_bi:"))
                    .all(|t| evidence_tokens.contains(t));
            if claim_ok {
                supported += 1;
            }
        }
        supported as f64 / required_claim_terms.len() as f64
    };

    let q_tokens: Vec<String> = tokenize_bm25_cjk(query)
        .into_iter()
        .filter(|t| !t.starts_with("ko_bi:") && !t.starts_with("ja_bi:"))
        .collect();
    let answer_relevance = if q_tokens.is_empty() {
        1.0
    } else {
        let covered = q_tokens
            .iter()
            .filter(|t| evidence_tokens.contains(t.as_str()))
            .count();
        covered as f64 / q_tokens.len() as f64
    };

    (
        faithfulness.clamp(0.0, 1.0),
        answer_relevance.clamp(0.0, 1.0),
    )
}

pub fn summarize_latencies_ms(mut samples_ms: Vec<f64>) -> LatencySummaryMs {
    if samples_ms.is_empty() {
        return LatencySummaryMs::default();
    }
    samples_ms.sort_by(|a, b| a.total_cmp(b));
    let n = samples_ms.len();
    let p50_idx = ((n as f64 - 1.0) * 0.50).round() as usize;
    let p95_idx = ((n as f64 - 1.0) * 0.95).round() as usize;
    LatencySummaryMs {
        samples: n,
        p50_ms: samples_ms[p50_idx.min(n - 1)],
        p95_ms: samples_ms[p95_idx.min(n - 1)],
        max_ms: *samples_ms.last().unwrap_or(&0.0),
    }
}

pub fn jaccard_at_k(a: &[String], b: &[String], k: usize) -> f64 {
    let sa: HashSet<&str> = a.iter().take(k).map(String::as_str).collect();
    let sb: HashSet<&str> = b.iter().take(k).map(String::as_str).collect();
    if sa.is_empty() && sb.is_empty() {
        return 1.0;
    }
    let inter = sa.intersection(&sb).count();
    let union = sa.union(&sb).count();
    if union == 0 {
        1.0
    } else {
        inter as f64 / union as f64
    }
}

fn synthetic_semantic_vector(text: &str, dim: usize) -> Vec<f64> {
    let mut vec = vec![0.0_f64; dim];
    for tok in tokenize_bm25_cjk(text) {
        let mut h: u64 = 14695981039346656037;
        for b in tok.as_bytes() {
            h ^= u64::from(*b);
            h = h.wrapping_mul(1099511628211);
        }
        let idx = (h as usize) % dim;
        let sign = if (h >> 63) == 0 { 1.0 } else { -1.0 };
        vec[idx] += sign;
    }
    let norm = vec.iter().map(|v| v * v).sum::<f64>().sqrt();
    if norm > 1e-12 {
        for v in &mut vec {
            *v /= norm;
        }
    }
    vec
}

fn encode_f32_le(vec: &[f64]) -> Vec<u8> {
    let mut out = Vec::with_capacity(vec.len() * 4);
    for &v in vec {
        out.extend_from_slice(&(v as f32).to_le_bytes());
    }
    out
}

pub fn build_default_eval_fixture(
    workspace_id: &str,
    noise_other_workspace_count: usize,
) -> Result<(Connection, LatencySummaryMs, Vec<RagEvalCase>), CoreError> {
    let conn = Connection::open_in_memory()?;
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
            indexed_at TEXT NOT NULL DEFAULT '2026-04-01T00:00:00Z'
        );",
    )?;

    struct DocSeed<'a> {
        id: &'a str,
        node_type: &'a str,
        title: &'a str,
        summary: &'a str,
        tags: &'a [&'a str],
        ws: &'a str,
        lifecycle: &'a str,
        superseded_by: Option<&'a str>,
        importance: f64,
        updated_at: &'a str,
    }

    let docs = vec![
        DocSeed {
            id: "ko-kg-hybrid",
            node_type: "Decision",
            title: "지식그래프 하이브리드 검색 엔진 아키텍처",
            summary: "Okapi BM25F, 한국어 조사 제거 토크나이저, 2-hop 지식그래프 확장을 RRF로 결합합니다. [[RRF 융합 규격]] 참조.",
            tags: &["지식그래프", "하이브리드검색", "BM25F"],
            ws: workspace_id,
            lifecycle: "active",
            superseded_by: None,
            importance: 0.95,
            updated_at: "2026-01-10T00:00:00Z",
        },
        DocSeed {
            id: "ko-rrf-spec",
            node_type: "Concept",
            title: "RRF 융합 규격",
            summary: "k=60 상수로 BM25F와 벡터 검색 순위를 안정적으로 융합하는 규격입니다.",
            tags: &["RRF", "ranking"],
            ws: workspace_id,
            lifecycle: "active",
            superseded_by: None,
            importance: 0.85,
            updated_at: "2026-01-12T00:00:00Z",
        },
        DocSeed {
            id: "ko-compound-embed",
            node_type: "Document",
            title: "임베딩모델정렬 및 벡터재색인 가이드",
            summary: "embeddinggemma-2 768차원 모델과 로컬 해시 폴백 불일치 시 reindex_required 상태를 반환합니다.",
            tags: &["임베딩모델", "vector_search", "reindex"],
            ws: workspace_id,
            lifecycle: "active",
            superseded_by: None,
            importance: 0.92,
            updated_at: "2026-02-01T00:00:00Z",
        },
        DocSeed {
            id: "mixed-rust-cache",
            node_type: "Decision",
            title: "Rust sidecar 캐시 RwLock 및 LRU eviction 정책",
            summary: "SidecarCachePool uses RwLock snapshots and memory-byte LRU eviction capped at 64 entries for concurrent retrieval.",
            tags: &["sidecar", "rust", "cache", "eviction"],
            ws: workspace_id,
            lifecycle: "active",
            superseded_by: None,
            importance: 0.90,
            updated_at: "2026-02-15T00:00:00Z",
        },
        DocSeed {
            id: "en-scope-sql-v2",
            node_type: "Decision",
            title: "Workspace scope SQL pre-filtering before LIMIT",
            summary: "load_scoped_corpus filters workspace_id and lifecycle_status in SQL prior to ORDER BY updated_at DESC LIMIT so active workspace recall stays intact.",
            tags: &["workspace", "sqlite", "security"],
            ws: workspace_id,
            lifecycle: "active",
            superseded_by: None,
            importance: 0.96,
            updated_at: "2026-01-05T00:00:00Z",
        },
        DocSeed {
            id: "en-scope-legacy-superseded",
            node_type: "Document",
            title: "Workspace scope SQL pre-filtering legacy draft",
            summary: "Deprecated prototype that filtered workspace_id in Rust after LIMIT 5000, superseded by SQL pre-filtering.",
            tags: &["workspace", "sqlite"],
            ws: workspace_id,
            lifecycle: "superseded",
            superseded_by: Some("en-scope-sql-v2"),
            importance: 0.40,
            updated_at: "2026-03-20T00:00:00Z",
        },
        DocSeed {
            id: "ko-cache-deprecated",
            node_type: "Note",
            title: "구형 단일 Mutex sidecar 캐시 메모",
            summary: "단일 전역 Mutex로 직렬화하던 구형 sidecar 설계로 현재 deprecated 되었습니다.",
            tags: &["sidecar", "deprecated"],
            ws: workspace_id,
            lifecycle: "deprecated",
            superseded_by: None,
            importance: 0.20,
            updated_at: "2026-03-25T00:00:00Z",
        },
        DocSeed {
            id: "en-citation-grounding",
            node_type: "Concept",
            title: "Citation grounding and fail-closed verification",
            summary: "Every generated answer links explicit node citations and fails closed to NEEDS_REVIEW when evidence cannot be verified.",
            tags: &["citation", "verification", "rag"],
            ws: workspace_id,
            lifecycle: "active",
            superseded_by: None,
            importance: 0.88,
            updated_at: "2026-02-20T00:00:00Z",
        },
        DocSeed {
            id: "temporal-release-owner-2026",
            node_type: "Decision",
            title: "Release train owner handover 2026",
            summary: "As of 2026-01-15 the release train owner is Lee Jiwon, who signs every tagged release and approves the publish workflow.",
            tags: &["release", "owner", "temporal"],
            ws: workspace_id,
            lifecycle: "active",
            superseded_by: None,
            importance: 0.87,
            updated_at: "2026-01-15T00:00:00Z",
        },
        DocSeed {
            id: "temporal-release-owner-2024",
            node_type: "Decision",
            title: "Release train owner handover 2024",
            summary: "In 2024 the release train owner was Kim Minsoo, who signed tagged releases before the 2026 handover.",
            tags: &["release", "owner", "temporal"],
            ws: workspace_id,
            lifecycle: "superseded",
            superseded_by: Some("temporal-release-owner-2026"),
            importance: 0.45,
            updated_at: "2026-03-30T00:00:00Z",
        },
    ];

    let hash_model = LocalEmbeddingModel::default();
    let mut ingest_latencies_ms = Vec::with_capacity(docs.len() + noise_other_workspace_count);

    for d in &docs {
        let t0 = Instant::now();
        let meta = serde_json::json!({
            "workspace_id": d.ws,
            "tags": d.tags,
            "lifecycle_status": d.lifecycle,
            "superseded_by": d.superseded_by,
            "importance_score": d.importance,
        })
        .to_string();
        conn.execute(
            "INSERT INTO nodes (id, type, title, summary, metadata_json, updated_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![d.id, d.node_type, d.title, d.summary, meta, d.updated_at],
        )?;
        conn.execute(
            "INSERT INTO nodes_v2 (id, workspace_id, visibility, superseded_by, importance_score, lifecycle_status)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![d.id, d.ws, "workspace", d.superseded_by, d.importance, d.lifecycle],
        )?;
        let text = format!("{} {}", d.title, d.summary);
        let h_vec = hash_model.embed(&text);
        conn.execute(
            "INSERT INTO vector_embeddings (item_id, item_type, source_node, embedding, embedding_dim, embedding_model, indexed_at)
             VALUES (?1, 'node', ?1, ?2, ?3, ?4, ?5)",
            params![
                d.id,
                hash_model.encode(&h_vec),
                hash_model.dim() as i64,
                hash_model.model_id(),
                d.updated_at
            ],
        )?;
        let sem_vec = synthetic_semantic_vector(&text, 768);
        conn.execute(
            "INSERT INTO vector_embeddings (item_id, item_type, source_node, embedding, embedding_dim, embedding_model, indexed_at)
             VALUES (?1, 'node', ?2, ?3, 768, 'google/embeddinggemma-2', ?4)",
            params![format!("{}#sem", d.id), d.id, encode_f32_le(&sem_vec), d.updated_at],
        )?;
        ingest_latencies_ms.push(t0.elapsed().as_secs_f64() * 1000.0);
    }

    conn.execute(
        "INSERT INTO edges (id, from_node, to_node, type, weight) VALUES ('e1', 'ko-kg-hybrid', 'ko-rrf-spec', 'REFERENCES', 1.0)",
        [],
    )?;
    conn.execute(
        "INSERT INTO edges (id, from_node, to_node, type, weight) VALUES ('e2', 'en-scope-sql-v2', 'en-citation-grounding', 'RELATES_TO', 0.9)",
        [],
    )?;

    for i in 0..noise_other_workspace_count {
        let t0 = Instant::now();
        let id = format!("other-ws-noise-{i}");
        let ts = format!("2026-11-15T00:{:02}:{:02}Z", (i / 60) % 60, i % 60);
        conn.execute(
            "INSERT INTO nodes (id, type, title, summary, metadata_json, updated_at)
             VALUES (?1, 'Note', 'Workspace scope SQL noise node', 'Other workspace recent log', ?2, ?3)",
            params![id, r#"{"workspace_id":"ws-noise"}"#, ts],
        )?;
        conn.execute(
            "INSERT INTO nodes_v2 (id, workspace_id, visibility, importance_score, lifecycle_status)
             VALUES (?1, 'ws-noise', 'workspace', 0.5, 'active')",
            params![id],
        )?;
        ingest_latencies_ms.push(t0.elapsed().as_secs_f64() * 1000.0);
    }

    let cases = vec![
        RagEvalCase {
            id: "ko-postposition-graphrag".to_string(),
            category: "korean_postposition".to_string(),
            query: "지식그래프에서 하이브리드 검색을 RRF로 결합하는 방법".to_string(),
            expected_ids: vec!["ko-kg-hybrid".to_string()],
            supporting_ids: vec!["ko-rrf-spec".to_string()],
            retired_trap_ids: vec!["ko-cache-deprecated".to_string()],
            required_claim_terms: vec!["BM25F".to_string(), "RRF".to_string()],
        },
        RagEvalCase {
            id: "ko-compound-embedding".to_string(),
            category: "korean_compound".to_string(),
            query: "임베딩모델정렬 벡터재색인 reindex_required".to_string(),
            expected_ids: vec!["ko-compound-embed".to_string()],
            supporting_ids: vec![],
            retired_trap_ids: vec![],
            required_claim_terms: vec![
                "embeddinggemma".to_string(),
                "reindex_required".to_string(),
            ],
        },
        RagEvalCase {
            id: "mixed-kr-en-cache".to_string(),
            category: "mixed_kr_en".to_string(),
            query: "Rust sidecar 캐시 RwLock LRU eviction 정책".to_string(),
            expected_ids: vec!["mixed-rust-cache".to_string()],
            supporting_ids: vec![],
            retired_trap_ids: vec!["ko-cache-deprecated".to_string()],
            required_claim_terms: vec!["RwLock".to_string(), "eviction".to_string()],
        },
        RagEvalCase {
            id: "scope-and-superseded-exclusion".to_string(),
            category: "lifecycle_scope".to_string(),
            query: "Workspace scope SQL pre-filtering before LIMIT".to_string(),
            expected_ids: vec!["en-scope-sql-v2".to_string()],
            supporting_ids: vec!["en-citation-grounding".to_string()],
            retired_trap_ids: vec!["en-scope-legacy-superseded".to_string()],
            required_claim_terms: vec!["workspace_id".to_string(), "LIMIT".to_string()],
        },
        RagEvalCase {
            id: "citation-grounding-eval".to_string(),
            category: "citation_faithfulness".to_string(),
            query: "Citation grounding fail-closed verification NEEDS_REVIEW".to_string(),
            expected_ids: vec!["en-citation-grounding".to_string()],
            supporting_ids: vec!["en-scope-sql-v2".to_string()],
            retired_trap_ids: vec![],
            required_claim_terms: vec!["NEEDS_REVIEW".to_string(), "citations".to_string()],
        },
        RagEvalCase {
            id: "temporal-current-owner".to_string(),
            category: "temporal_conflict".to_string(),
            query: "Who is the current release train owner after the handover".to_string(),
            expected_ids: vec!["temporal-release-owner-2026".to_string()],
            supporting_ids: vec![],
            retired_trap_ids: vec!["temporal-release-owner-2024".to_string()],
            required_claim_terms: vec!["Lee Jiwon".to_string(), "release".to_string()],
        },
    ];

    Ok((conn, summarize_latencies_ms(ingest_latencies_ms), cases))
}

fn extract_hit_ids_and_texts(report: &Value) -> (Vec<String>, Vec<String>) {
    let mut ids = Vec::new();
    let mut texts = Vec::new();
    if let Some(arr) = report.get("matches").and_then(Value::as_array) {
        for item in arr {
            if let Some(id) = item
                .get("id")
                .or_else(|| item.get("node_id"))
                .and_then(Value::as_str)
            {
                ids.push(id.to_string());
                let title = item.get("title").and_then(Value::as_str).unwrap_or("");
                let summary = item.get("summary").and_then(Value::as_str).unwrap_or("");
                texts.push(format!("{title} {summary}"));
            }
        }
    }
    (ids, texts)
}

pub fn run_default_rag_evaluation(
    noise_other_workspace_count: usize,
) -> Result<RagEvalReport, CoreError> {
    let workspace_id = "ws-eval";
    let (conn, ingest_latency, cases) =
        build_default_eval_fixture(workspace_id, noise_other_workspace_count)?;
    let scope = Scope::for_workspace(workspace_id);
    let hash_model = LocalEmbeddingModel::default();

    let mut base_opts = GraphRagOptions {
        limit: 10,
        bm25_limit: 20,
        vector_limit: 20,
        graph_limit: 20,
        scope: scope.clone(),
        include_historical: false,
        graph_config: GraphRagConfig {
            include_historical: false,
            ..GraphRagConfig::default()
        },
        ..GraphRagOptions::default()
    };

    let mut results = Vec::with_capacity(cases.len());
    let mut search_latencies_ms = Vec::with_capacity(cases.len());
    let mut retired_checks = 0usize;
    let mut retired_excluded = 0usize;
    let mut cross_model_jaccards = Vec::with_capacity(cases.len());

    for case in &cases {
        let t0 = Instant::now();
        base_opts.query_embedding = None;
        let report_hash =
            hybrid_bm25_vector_graph_search(&conn, &hash_model, &case.query, &base_opts)?;
        let elapsed_ms = t0.elapsed().as_secs_f64() * 1000.0;
        search_latencies_ms.push(elapsed_ms);

        let (ids, texts) = extract_hit_ids_and_texts(&report_hash);

        let mut sem_opts = base_opts.clone();
        sem_opts.query_embedding = Some(QueryVectorOverride {
            model_id: "google/embeddinggemma-2".to_string(),
            vector: synthetic_semantic_vector(&case.query, 768),
            is_hash_fallback: false,
        });
        let report_sem =
            hybrid_bm25_vector_graph_search(&conn, &hash_model, &case.query, &sem_opts)?;
        let (sem_ids, _) = extract_hit_ids_and_texts(&report_sem);
        cross_model_jaccards.push(jaccard_at_k(&ids, &sem_ids, 5));

        let r5 = recall_at_k(&ids, &case.expected_ids, 5);
        let r10 = recall_at_k(&ids, &case.expected_ids, 10);
        let rr = reciprocal_rank(&ids, &case.expected_ids);
        let recency = if case.retired_trap_ids.is_empty() {
            None
        } else {
            Some(recency_correct(
                &ids,
                &case.expected_ids,
                &case.retired_trap_ids,
            ))
        };
        let ndcg = ndcg_at_k(&ids, &case.expected_ids, &case.supporting_ids, 10);
        let (cit_p, cit_r) =
            citation_precision_recall(&ids, &case.expected_ids, &case.supporting_ids, 3);
        let (faith, rel) =
            faithfulness_and_relevance(&case.query, &case.required_claim_terms, &texts);

        let leaked = case
            .retired_trap_ids
            .iter()
            .filter(|tid| ids.iter().any(|rid| rid == *tid))
            .count();
        if !case.retired_trap_ids.is_empty() {
            retired_checks += case.retired_trap_ids.len();
            retired_excluded += case.retired_trap_ids.len().saturating_sub(leaked);
        }

        results.push(RagEvalCaseResult {
            id: case.id.clone(),
            category: case.category.clone(),
            retrieved_ids: ids,
            recall_at_5: r5,
            recall_at_10: r10,
            reciprocal_rank: rr,
            ndcg_at_10: ndcg,
            citation_precision: cit_p,
            citation_recall: cit_r,
            faithfulness: faith,
            answer_relevance: rel,
            recency_correct: recency,
            retired_leaked: leaked,
            latency_ms: elapsed_ms,
        });
    }

    let n = results.len().max(1) as f64;
    let ko_cases: Vec<&RagEvalCaseResult> = results
        .iter()
        .filter(|r| r.category.starts_with("korean") || r.category == "mixed_kr_en")
        .collect();
    let ko_n = ko_cases.len().max(1) as f64;
    let temporal: Vec<bool> = results.iter().filter_map(|r| r.recency_correct).collect();
    let recency_accuracy = if temporal.is_empty() {
        1.0
    } else {
        temporal.iter().filter(|ok| **ok).count() as f64 / temporal.len() as f64
    };

    Ok(RagEvalReport {
        total_queries: results.len(),
        recall_at_5: results.iter().map(|r| r.recall_at_5).sum::<f64>() / n,
        recall_at_10: results.iter().map(|r| r.recall_at_10).sum::<f64>() / n,
        mrr: results.iter().map(|r| r.reciprocal_rank).sum::<f64>() / n,
        recency_accuracy,
        temporal_cases: temporal.len(),
        ndcg_at_10: results.iter().map(|r| r.ndcg_at_10).sum::<f64>() / n,
        citation_precision: results.iter().map(|r| r.citation_precision).sum::<f64>() / n,
        citation_recall: results.iter().map(|r| r.citation_recall).sum::<f64>() / n,
        faithfulness: results.iter().map(|r| r.faithfulness).sum::<f64>() / n,
        answer_relevance: results.iter().map(|r| r.answer_relevance).sum::<f64>() / n,
        superseded_exclusion_rate: if retired_checks == 0 {
            1.0
        } else {
            retired_excluded as f64 / retired_checks as f64
        },
        korean_recall_at_5: ko_cases.iter().map(|r| r.recall_at_5).sum::<f64>() / ko_n,
        korean_ndcg_at_10: ko_cases.iter().map(|r| r.ndcg_at_10).sum::<f64>() / ko_n,
        cross_model_jaccard_at_5: cross_model_jaccards.iter().sum::<f64>()
            / cross_model_jaccards.len().max(1) as f64,
        ingest_latency,
        search_latency: summarize_latencies_ms(search_latencies_ms),
        cases: results,
    })
}
