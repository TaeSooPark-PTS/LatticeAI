use std::collections::BTreeMap;

use lattice_core::{parse_iso, CoreError, LocalEmbeddingModel};
use rusqlite::Connection;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::bm25::QueryVectorOverride;
use crate::context::{
    fetch_limit_for, finalize_knowledge, knowledge_search_ranked, ContextRequest,
};
use crate::mmr::{diversify, DiversityConfig};
use crate::query_plan::{plan_query, QueryPlan};
use crate::rag_eval::{ndcg_at_k, recall_at_k};
use crate::rag_eval_dataset::{
    eval_passage_text, seed_versioned_dataset_with, EvalEmbeddings, UpdatedAtMode,
    VersionedRagDataset,
};
use crate::rerank::{
    apply_rerank_scores, rerank_candidate_count, rerank_candidate_documents, RerankConfig,
};
use crate::self_rag::{assess, is_evidence, requires_grounding};
use crate::temporal::TemporalRanking;

pub const CHAT_EVAL_NOW: &str = "2026-11-27T00:00:00";
pub const CHAT_EVAL_LIMIT: usize = 5;
pub const FIRST_PASS: &str = "first";
pub const EXPANDED_PASS: &str = "expanded";
pub const PROCESS_FLAGS: [&str; 9] = [
    crate::expansion::GRAPH_EXPANSION_ENV,
    crate::expansion::FUSION_RRF_ENV,
    crate::bm25::BM25F_ENV,
    "LATTICEAI_FUSION_STRATEGY",
    crate::policy::QUERY_REWRITE_ENV,
    crate::rerank::RERANK_ENV,
    crate::rerank::RERANK_CANDIDATES_ENV,
    crate::rerank::RERANK_PROMOTE_ENV,
    crate::self_rag::SELF_RAG_ENV,
];

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct CategoryMetrics {
    pub queries: usize,
    pub recall_at_5: f64,
    pub mrr: f64,
    pub ndcg_at_10: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ChatPathEvalReport {
    pub updated_at_mode: String,
    pub now: String,
    pub answerable_queries: usize,
    pub recall_at_5: f64,
    pub mrr: f64,
    pub ndcg_at_10: f64,
    pub workspace_boundary_queries: usize,
    pub workspace_boundary_leaks: usize,
    pub superseded_exclusion_rate: f64,
    pub vector_channel_matches: usize,
    pub grounding_required_queries: usize,
    pub expanded_retrievals: usize,
    pub categories: BTreeMap<String, CategoryMetrics>,
}

#[derive(Default)]
struct Sums {
    queries: usize,
    recall: f64,
    rr: f64,
    ndcg: f64,
}

impl Sums {
    fn add(&mut self, recall: f64, rr: f64, ndcg: f64) {
        self.queries += 1;
        self.recall += recall;
        self.rr += rr;
        self.ndcg += ndcg;
    }

    fn metrics(&self) -> CategoryMetrics {
        let denom = self.queries.max(1) as f64;
        CategoryMetrics {
            queries: self.queries,
            recall_at_5: self.recall / denom,
            mrr: self.rr / denom,
            ndcg_at_10: self.ndcg / denom,
        }
    }
}

pub fn process_flags_overridden() -> Vec<&'static str> {
    PROCESS_FLAGS
        .iter()
        .copied()
        .filter(|key| std::env::var(key).is_ok_and(|raw| !raw.trim().is_empty()))
        .collect()
}

fn hit_id(item: &Value) -> Option<String> {
    item.get("node_id")
        .or_else(|| item.get("id"))
        .and_then(Value::as_str)
        .map(str::to_string)
}

fn hit_ids(payload: &Value) -> Vec<String> {
    payload
        .get("matches")
        .and_then(Value::as_array)
        .map(|items| items.iter().filter_map(hit_id).collect())
        .unwrap_or_default()
}

fn reciprocal_rank(hits: &[String], expected: &[String]) -> f64 {
    hits.iter()
        .position(|hit| expected.contains(hit))
        .map_or(0.0, |position| 1.0 / (position as f64 + 1.0))
}

fn vector_channel_matches(payload: &Value) -> usize {
    payload
        .get("matches")
        .and_then(Value::as_array)
        .map_or(0, |items| {
            items
                .iter()
                .filter(|item| {
                    item.get("sources")
                        .and_then(Value::as_array)
                        .is_some_and(|sources| sources.iter().any(|s| s == "vector"))
                })
                .count()
        })
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RerankCandidate {
    pub id: String,
    pub text: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RerankRequestRecord {
    pub key: String,
    pub query_id: String,
    pub pass: String,
    pub query: String,
    pub candidates: Vec<RerankCandidate>,
}

pub type RerankScores = BTreeMap<String, BTreeMap<String, f64>>;

#[derive(Debug, Clone, Copy, Default)]
pub struct ChatEvalOptions<'a> {
    pub embeddings: Option<&'a EvalEmbeddings>,
    pub rerank_scores: Option<&'a RerankScores>,
}

pub fn rerank_key(query_id: &str, pass: &str) -> String {
    format!("{query_id}/{pass}")
}

fn pinned_ranking() -> TemporalRanking {
    TemporalRanking::from_lookup(|_| None)
}

fn eval_now_secs() -> f64 {
    parse_iso(Some(CHAT_EVAL_NOW)).unwrap_or_default()
}

pub fn chat_query_plan(
    query: &str,
    now_secs: f64,
    ranking: &TemporalRanking,
) -> (QueryPlan, String) {
    let plan = plan_query(query, &[]);
    let search_text = crate::temporal::search_text_for(&plan.standalone, now_secs, ranking);
    let embed_text = crate::query_embed::effective_hybrid_query(&search_text, false);
    (plan, embed_text)
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct EmbeddingInputs {
    pub queries: BTreeMap<String, String>,
    pub documents: BTreeMap<String, String>,
}

pub fn embedding_inputs(dataset: &VersionedRagDataset) -> EmbeddingInputs {
    let ranking = pinned_ranking();
    let now_secs = eval_now_secs();
    EmbeddingInputs {
        queries: dataset
            .queries
            .iter()
            .map(|query| {
                let (_, text) = chat_query_plan(&query.query, now_secs, &ranking);
                (query.id.clone(), text)
            })
            .filter(|(_, text)| !text.is_empty())
            .collect(),
        documents: dataset
            .documents
            .iter()
            .map(|doc| (doc.doc_id.clone(), eval_passage_text(doc)))
            .collect(),
    }
}

struct Found {
    payload: Value,
    historical: bool,
}

impl Found {
    fn matches(&self) -> &[Value] {
        self.payload
            .get("matches")
            .and_then(Value::as_array)
            .map(Vec::as_slice)
            .unwrap_or_default()
    }
}

struct ChatEvalRun<'a> {
    conn: &'a Connection,
    embedder: &'a LocalEmbeddingModel,
    rerank: RerankConfig,
    diversity: DiversityConfig,
    scores: Option<&'a RerankScores>,
    records: Vec<RerankRequestRecord>,
    vector_matches: usize,
    expanded: usize,
}

impl ChatEvalRun<'_> {
    fn rerank(
        &mut self,
        query_id: &str,
        pass: &str,
        query: &str,
        matches: &mut Vec<Value>,
    ) -> Result<(), CoreError> {
        let Ok(count) = rerank_candidate_count(query, matches, &self.rerank) else {
            return Ok(());
        };
        let Ok(documents) = rerank_candidate_documents(matches, count) else {
            return Ok(());
        };
        let key = rerank_key(query_id, pass);
        let ids: Vec<String> = matches[..count]
            .iter()
            .map(|item| hit_id(item).unwrap_or_default())
            .collect();
        if let Some(scores) = self.scores.and_then(|all| all.get(&key)) {
            let ordered = ids
                .iter()
                .map(|id| {
                    scores.get(id).copied().ok_or_else(|| {
                        CoreError::InvalidRequest(format!("rerank scores for {key} omit {id}"))
                    })
                })
                .collect::<Result<Vec<f64>, CoreError>>()?;
            apply_rerank_scores(matches, &ordered, self.rerank.promote);
        }
        self.records.push(RerankRequestRecord {
            key,
            query_id: query_id.to_string(),
            pass: pass.to_string(),
            query: query.trim().to_string(),
            candidates: ids
                .into_iter()
                .zip(documents)
                .map(|(id, text)| RerankCandidate { id, text })
                .collect(),
        });
        Ok(())
    }

    fn retrieve(
        &mut self,
        query_id: &str,
        pass: &str,
        request: &ContextRequest,
        evidence_only: bool,
    ) -> Result<Found, CoreError> {
        let (mut payload, plan) = knowledge_search_ranked(self.conn, self.embedder, request)?;
        self.vector_matches += vector_channel_matches(&payload);
        let rerank_query = plan.as_ref().map_or_else(
            || request.query.clone(),
            |plan| plan.search_query().to_string(),
        );
        if let Some(matches) = payload.get_mut("matches").and_then(Value::as_array_mut) {
            if evidence_only {
                matches.retain(is_evidence);
            }
            self.rerank(query_id, pass, &rerank_query, matches)?;
        }
        let mut historical = false;
        if let Some(mut plan) = plan {
            historical = plan.include_historical();
            let pool = fetch_limit_for(request.knowledge_limit, &self.rerank);
            finalize_knowledge(&mut plan, &mut payload, request, pool);
        }
        if let Some(matches) = payload.get_mut("matches").and_then(Value::as_array_mut) {
            let (selected, _) =
                diversify(std::mem::take(matches), CHAT_EVAL_LIMIT, &self.diversity);
            *matches = selected;
        }
        Ok(Found {
            payload,
            historical,
        })
    }

    fn sufficient(&self, plan: &QueryPlan, request: &ContextRequest, found: &Found) -> bool {
        let parents = crate::context_parents::parents_for_payload(
            self.conn,
            &found.payload,
            found.matches().len() as i64,
        );
        let semantic = request
            .query_embedding
            .as_ref()
            .is_some_and(|embedding| !embedding.is_hash_fallback);
        assess(
            &plan.standalone,
            found.matches(),
            &parents,
            found.historical,
            semantic,
        )
        .sufficient
    }

    fn retrieve_gated(
        &mut self,
        query_id: &str,
        plan: &QueryPlan,
        request: &ContextRequest,
        required: bool,
    ) -> Result<Found, CoreError> {
        let first = self.retrieve(query_id, FIRST_PASS, request, required)?;
        if !required || self.sufficient(plan, request, &first) {
            return Ok(first);
        }
        let expanded = ContextRequest {
            sub_queries: plan.expanded_queries(),
            ..request.clone()
        };
        self.expanded += 1;
        self.retrieve(query_id, EXPANDED_PASS, &expanded, true)
    }
}

fn query_vector(
    options: &ChatEvalOptions<'_>,
    query_id: &str,
    embed_text: &str,
) -> Result<Option<QueryVectorOverride>, CoreError> {
    match options.embeddings {
        Some(vectors) if !embed_text.is_empty() => Ok(Some(QueryVectorOverride {
            model_id: vectors.model_id.clone(),
            vector: vectors.query(query_id)?.clone(),
            is_hash_fallback: false,
        })),
        _ => Ok(None),
    }
}

pub fn evaluate_chat_path(
    dataset: &VersionedRagDataset,
    mode: UpdatedAtMode,
) -> Result<ChatPathEvalReport, CoreError> {
    evaluate_chat_path_with(dataset, mode, ChatEvalOptions::default()).map(|(report, _)| report)
}

pub fn evaluate_chat_path_with(
    dataset: &VersionedRagDataset,
    mode: UpdatedAtMode,
    options: ChatEvalOptions<'_>,
) -> Result<(ChatPathEvalReport, Vec<RerankRequestRecord>), CoreError> {
    let embedder = LocalEmbeddingModel::new(384);
    let conn = seed_versioned_dataset_with(dataset, &embedder, mode, options.embeddings)?;
    let now_secs = eval_now_secs();
    let ranking = pinned_ranking();
    let mut run = ChatEvalRun {
        conn: &conn,
        embedder: &embedder,
        rerank: RerankConfig::from_lookup(|_| None),
        diversity: DiversityConfig::from_lookup(|_| None),
        scores: options.rerank_scores,
        records: Vec::new(),
        vector_matches: 0,
        expanded: 0,
    };
    let mut overall = Sums::default();
    let mut per_category: BTreeMap<String, Sums> = BTreeMap::new();
    let mut boundary_queries = 0usize;
    let mut boundary_leaks = 0usize;
    let mut trap_total = 0usize;
    let mut trap_excluded = 0usize;
    let mut grounding_required = 0usize;
    for query in &dataset.queries {
        let (plan, embed_text) = chat_query_plan(&query.query, now_secs, &ranking);
        let required = requires_grounding(&plan.original);
        grounding_required += usize::from(required);
        let request = ContextRequest {
            query: plan.standalone.clone(),
            sub_queries: plan.sub_queries.clone(),
            annotate_temporal: true,
            density_aware: true,
            knowledge: true,
            knowledge_limit: CHAT_EVAL_LIMIT as i64,
            workspace_id: Some(query.workspace_id.clone()),
            now_secs,
            query_embedding: query_vector(&options, &query.id, &embed_text)?,
            temporal: Some(ranking.clone()),
            as_of: query.as_of.clone(),
            ..ContextRequest::default()
        };
        let found = run.retrieve_gated(&query.id, &plan, &request, required)?;
        let hits = hit_ids(&found.payload);
        if query.category == "workspace_boundary" {
            boundary_queries += 1;
            if query
                .forbidden_or_trap_doc_ids
                .iter()
                .any(|forbidden| hits.contains(forbidden))
            {
                boundary_leaks += 1;
            }
        }
        if query.expect_abstain || query.supporting_span_doc_ids.is_empty() {
            continue;
        }
        let expected = &query.supporting_span_doc_ids;
        let recall = recall_at_k(&hits, expected, 5);
        let rr = reciprocal_rank(&hits, expected);
        let ndcg = ndcg_at_k(&hits, expected, &[], 10);
        overall.add(recall, rr, ndcg);
        per_category
            .entry(query.category.clone())
            .or_default()
            .add(recall, rr, ndcg);
        for trap in &query.forbidden_or_trap_doc_ids {
            trap_total += 1;
            if !hits.contains(trap) {
                trap_excluded += 1;
            }
        }
    }
    let totals = overall.metrics();
    let report = ChatPathEvalReport {
        updated_at_mode: format!("{mode:?}"),
        now: CHAT_EVAL_NOW.to_string(),
        answerable_queries: totals.queries,
        recall_at_5: totals.recall_at_5,
        mrr: totals.mrr,
        ndcg_at_10: totals.ndcg_at_10,
        workspace_boundary_queries: boundary_queries,
        workspace_boundary_leaks: boundary_leaks,
        superseded_exclusion_rate: if trap_total == 0 {
            1.0
        } else {
            trap_excluded as f64 / trap_total as f64
        },
        vector_channel_matches: run.vector_matches,
        grounding_required_queries: grounding_required,
        expanded_retrievals: run.expanded,
        categories: per_category
            .into_iter()
            .map(|(name, sums)| (name, sums.metrics()))
            .collect(),
    };
    Ok((report, run.records))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::rag_eval_dataset::load_versioned_dataset;
    use std::path::PathBuf;

    const CATEGORY_FLOORS: [(&str, f64, f64); 7] = [
        ("ambiguous_query", 0.99, 0.76),
        ("contradictory_sources", 0.87, 0.99),
        ("factual_lookup", 0.99, 0.85),
        ("multi_doc_synthesis", 0.99, 0.98),
        ("provenance_verification", 0.99, 0.82),
        ("relational_reasoning", 0.99, 0.98),
        ("temporal_ordering", 0.99, 0.79),
    ];

    fn dataset() -> VersionedRagDataset {
        let path =
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../datasets/rag_eval_v1.json");
        load_versioned_dataset(&path).expect("load rag_eval_v1.json")
    }

    fn gate(report: &ChatPathEvalReport, recall: f64, mrr: f64, ndcg: f64) {
        let label = &report.updated_at_mode;
        assert_eq!(report.answerable_queries, 248, "{label}");
        assert_eq!(report.workspace_boundary_queries, 32, "{label}");
        assert_eq!(report.workspace_boundary_leaks, 0, "{label}");
        assert_eq!(report.superseded_exclusion_rate, 1.0, "{label}");
        assert!(
            report.vector_channel_matches >= 6_000,
            "{label} the vector channel went quiet: {} fused matches",
            report.vector_channel_matches
        );
        assert!(report.grounding_required_queries > 0, "{label}");
        assert!(report.expanded_retrievals > 0, "{label}");
        assert!(
            report.recall_at_5 >= recall,
            "{label} R@5={}",
            report.recall_at_5
        );
        assert!(report.mrr >= mrr, "{label} MRR={}", report.mrr);
        assert!(
            report.ndcg_at_10 >= ndcg,
            "{label} nDCG={}",
            report.ndcg_at_10
        );
        for (category, recall_floor, mrr_floor) in CATEGORY_FLOORS {
            let found = report.categories.get(category).expect(category);
            assert!(
                found.recall_at_5 >= recall_floor,
                "{label} {category} R@5={}",
                found.recall_at_5
            );
            assert!(
                found.mrr >= mrr_floor,
                "{label} {category} MRR={}",
                found.mrr
            );
        }
    }

    #[test]
    fn the_chat_retriever_clears_its_ratchet_floors_on_the_versioned_dataset() {
        assert!(
            process_flags_overridden().is_empty(),
            "unset {:?} to run the pinned chat-path evaluation",
            process_flags_overridden()
        );
        let ds = dataset();
        let constant = evaluate_chat_path(&ds, UpdatedAtMode::Constant).expect("constant eval");
        gate(&constant, 0.98, 0.90, 0.90);
        let dated = evaluate_chat_path(&ds, UpdatedAtMode::ValidFrom).expect("valid_from eval");
        gate(&dated, 0.98, 0.88, 0.88);
        assert_eq!(dated.now, CHAT_EVAL_NOW);
        assert_eq!(dated.updated_at_mode, "ValidFrom");
    }

    #[test]
    fn the_embedding_inputs_are_the_texts_the_chat_path_embeds() {
        let ds = dataset();
        let inputs = embedding_inputs(&ds);
        assert_eq!(inputs.documents.len(), ds.documents.len());
        assert_eq!(inputs.queries.len(), ds.queries.len());
        let doc = &ds.documents[0];
        assert_eq!(inputs.documents[&doc.doc_id], eval_passage_text(doc));
        let query = &ds.queries[0];
        let (plan, text) = chat_query_plan(&query.query, eval_now_secs(), &pinned_ranking());
        assert_eq!(inputs.queries[&query.id], text);
        assert_eq!(plan.original, query.query.trim());
    }

    fn one_query_dataset() -> VersionedRagDataset {
        let mut ds = dataset();
        ds.queries.truncate(1);
        ds.total_queries = 1;
        let workspace = ds.queries[0].workspace_id.clone();
        ds.documents.retain(|doc| doc.workspace_id == workspace);
        ds
    }

    fn unit_embeddings(ds: &VersionedRagDataset) -> EvalEmbeddings {
        EvalEmbeddings {
            model_id: "huggingface:test:2".into(),
            documents: ds
                .documents
                .iter()
                .map(|doc| (doc.doc_id.clone(), vec![1.0, 0.0]))
                .collect(),
            queries: ds
                .queries
                .iter()
                .map(|query| (query.id.clone(), vec![1.0, 0.0]))
                .collect(),
        }
    }

    #[test]
    fn recorded_rerank_scores_reorder_and_missing_ones_fail_loudly() {
        let ds = one_query_dataset();
        let vectors = unit_embeddings(&ds);
        let with = |scores| ChatEvalOptions {
            embeddings: Some(&vectors),
            rerank_scores: scores,
        };
        let (_, records) =
            evaluate_chat_path_with(&ds, UpdatedAtMode::Constant, with(None)).expect("records");
        let record = records.first().expect("a rerank request");
        assert_eq!(record.key, rerank_key(&record.query_id, FIRST_PASS));
        assert!(record.candidates.len() >= 2);
        let last = record.candidates.last().expect("candidate").id.clone();
        let scored: RerankScores = BTreeMap::from([(
            record.key.clone(),
            record
                .candidates
                .iter()
                .map(|item| (item.id.clone(), if item.id == last { 9.0 } else { 0.0 }))
                .collect(),
        )]);
        let (_, rescored) =
            evaluate_chat_path_with(&ds, UpdatedAtMode::Constant, with(Some(&scored)))
                .expect("rescored");
        assert_eq!(rescored.len(), records.len());
        let partial: RerankScores = BTreeMap::from([(record.key.clone(), BTreeMap::new())]);
        let missing = evaluate_chat_path_with(&ds, UpdatedAtMode::Constant, with(Some(&partial)));
        assert!(
            matches!(missing, Err(CoreError::InvalidRequest(reason)) if reason.contains("omit"))
        );
        let empty = EvalEmbeddings {
            queries: BTreeMap::new(),
            ..vectors.clone()
        };
        let absent = evaluate_chat_path_with(
            &ds,
            UpdatedAtMode::Constant,
            ChatEvalOptions {
                embeddings: Some(&empty),
                rerank_scores: None,
            },
        );
        assert!(absent.is_err());
    }

    #[test]
    fn a_single_candidate_or_textless_candidates_are_never_sent_to_rerank() {
        let embedder = LocalEmbeddingModel::new(384);
        let ds = one_query_dataset();
        let conn = seed_versioned_dataset_with(&ds, &embedder, UpdatedAtMode::Constant, None)
            .expect("seed");
        let mut run = ChatEvalRun {
            conn: &conn,
            embedder: &embedder,
            rerank: RerankConfig::default(),
            diversity: DiversityConfig::from_lookup(|_| None),
            scores: None,
            records: Vec::new(),
            vector_matches: 0,
            expanded: 0,
        };
        let mut single = vec![serde_json::json!({"id": "a", "title": "A"})];
        run.rerank("q", FIRST_PASS, "query", &mut single)
            .expect("single");
        let mut blank = vec![
            serde_json::json!({"id": "a"}),
            serde_json::json!({"id": "b"}),
        ];
        run.rerank("q", FIRST_PASS, "query", &mut blank)
            .expect("blank");
        assert!(run.records.is_empty());
        assert!(query_vector(&ChatEvalOptions::default(), "q", "text")
            .expect("hash")
            .is_none());
        let vectors = unit_embeddings(&ds);
        let neural = ChatEvalOptions {
            embeddings: Some(&vectors),
            rerank_scores: None,
        };
        assert!(query_vector(&neural, "q", "").expect("empty").is_none());
    }

    #[test]
    fn the_vector_channel_counter_reads_fused_sources() {
        let payload = serde_json::json!({"matches": [
            {"sources": ["keyword", "vector"]},
            {"sources": ["keyword"]},
            {}
        ]});
        assert_eq!(vector_channel_matches(&payload), 1);
        assert_eq!(vector_channel_matches(&Value::Null), 0);
    }

    #[test]
    fn reciprocal_rank_and_hit_ids_read_the_first_expected_position() {
        let payload = serde_json::json!({"matches": [{"node_id": "a"}, {"id": "b"}, {}]});
        let hits = hit_ids(&payload);
        assert_eq!(hits, ["a", "b"]);
        assert_eq!(reciprocal_rank(&hits, &["b".to_string()]), 0.5);
        assert_eq!(reciprocal_rank(&hits, &["z".to_string()]), 0.0);
        assert!(hit_ids(&Value::Null).is_empty());
        assert_eq!(Sums::default().metrics().queries, 0);
    }
}
