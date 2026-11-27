use std::collections::BTreeMap;

use lattice_core::{parse_iso, CoreError, LocalEmbeddingModel};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::bm25::QueryVectorOverride;
use crate::context::{knowledge_search, ContextRequest};
use crate::mmr::{diversify, DiversityConfig};
use crate::rag_eval::{ndcg_at_k, recall_at_k};
use crate::rag_eval_dataset::{seed_versioned_dataset, UpdatedAtMode, VersionedRagDataset};
use crate::temporal::TemporalRanking;

pub const CHAT_EVAL_NOW: &str = "2026-11-27T00:00:00";
pub const CHAT_EVAL_LIMIT: usize = 5;
pub const PROCESS_FLAGS: [&str; 5] = [
    crate::expansion::GRAPH_EXPANSION_ENV,
    crate::expansion::FUSION_RRF_ENV,
    crate::bm25::BM25F_ENV,
    "LATTICEAI_FUSION_STRATEGY",
    crate::policy::QUERY_REWRITE_ENV,
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

fn hit_ids(payload: &Value) -> Vec<String> {
    payload
        .get("matches")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(|item| {
                    item.get("node_id")
                        .or_else(|| item.get("id"))
                        .and_then(Value::as_str)
                        .map(str::to_string)
                })
                .collect()
        })
        .unwrap_or_default()
}

fn reciprocal_rank(hits: &[String], expected: &[String]) -> f64 {
    hits.iter()
        .position(|id| expected.iter().any(|want| want == id))
        .map_or(0.0, |position| 1.0 / (position as f64 + 1.0))
}

pub fn evaluate_chat_path(
    dataset: &VersionedRagDataset,
    mode: UpdatedAtMode,
) -> Result<ChatPathEvalReport, CoreError> {
    let embedder = LocalEmbeddingModel::new(384);
    let conn = seed_versioned_dataset(dataset, &embedder, mode)?;
    let now_secs = parse_iso(Some(CHAT_EVAL_NOW)).unwrap_or_default();
    let ranking = TemporalRanking::from_lookup(|_| None);
    let diversity = DiversityConfig::from_lookup(|_| None);
    let mut overall = Sums::default();
    let mut per_category: BTreeMap<String, Sums> = BTreeMap::new();
    let mut boundary_queries = 0usize;
    let mut boundary_leaks = 0usize;
    let mut trap_total = 0usize;
    let mut trap_excluded = 0usize;
    for query in &dataset.queries {
        let request = ContextRequest {
            query: query.query.clone(),
            knowledge: true,
            knowledge_limit: CHAT_EVAL_LIMIT as i64,
            workspace_id: Some(query.workspace_id.clone()),
            now_secs,
            query_embedding: Some(QueryVectorOverride {
                model_id: embedder.model_id().to_string(),
                vector: embedder.embed(&query.query),
                is_hash_fallback: true,
            }),
            temporal: Some(ranking.clone()),
            as_of: query.as_of.clone(),
            ..ContextRequest::default()
        };
        let mut payload = knowledge_search(&conn, &embedder, &request)?;
        if let Some(matches) = payload.get_mut("matches").and_then(Value::as_array_mut) {
            let (selected, _) = diversify(std::mem::take(matches), CHAT_EVAL_LIMIT, &diversity);
            *matches = selected;
        }
        let hits = hit_ids(&payload);
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
    Ok(ChatPathEvalReport {
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
        categories: per_category
            .into_iter()
            .map(|(name, sums)| (name, sums.metrics()))
            .collect(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::rag_eval_dataset::load_versioned_dataset;
    use std::path::PathBuf;

    const CATEGORY_FLOORS: [(&str, f64, f64); 7] = [
        ("ambiguous_query", 0.99, 0.70),
        ("contradictory_sources", 0.87, 0.99),
        ("factual_lookup", 0.99, 0.76),
        ("multi_doc_synthesis", 0.99, 0.89),
        ("provenance_verification", 0.99, 0.70),
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
        gate(&constant, 0.98, 0.85, 0.87);
        let dated = evaluate_chat_path(&ds, UpdatedAtMode::ValidFrom).expect("valid_from eval");
        gate(&dated, 0.98, 0.83, 0.86);
        assert_eq!(dated.now, CHAT_EVAL_NOW);
        assert_eq!(dated.updated_at_mode, "ValidFrom");
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
