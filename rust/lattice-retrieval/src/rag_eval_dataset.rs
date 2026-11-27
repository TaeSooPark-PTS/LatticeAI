use std::collections::{BTreeMap, HashSet};
use std::path::Path;

use lattice_core::graph_write::schema::bootstrap;
use lattice_core::{CoreError, LocalEmbeddingModel};
use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};

use crate::bm25::{
    hybrid_bm25_vector_graph_search, GraphRagConfig, GraphRagOptions, QueryVectorOverride,
};
use crate::rag_eval::{ndcg_at_k, recall_at_k};
use crate::rag_eval_grounded::{EvalExecutionMode, EvalModelStatus, EvalRunMetadata};
use crate::service::Scope;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RagEvalDatasetDocument {
    pub doc_id: String,
    pub workspace_id: String,
    pub title: String,
    pub text: String,
    pub valid_from: Option<String>,
    pub valid_to: Option<String>,
    pub superseded_by: Option<String>,
    pub source_uri: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RagEvalDatasetQuery {
    pub id: String,
    pub category: String,
    pub workspace_id: String,
    pub as_of: Option<String>,
    pub query: String,
    pub expected_key_claims: Vec<String>,
    pub supporting_span_doc_ids: Vec<String>,
    pub acceptable_alternatives: Vec<String>,
    pub forbidden_or_trap_doc_ids: Vec<String>,
    pub expect_abstain: bool,
    pub abstention_condition: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VersionedRagDataset {
    pub dataset_version: String,
    pub schema_version: String,
    pub total_documents: usize,
    pub total_queries: usize,
    pub categories: Vec<String>,
    pub documents: Vec<RagEvalDatasetDocument>,
    pub queries: Vec<RagEvalDatasetQuery>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LiveModelTarget {
    pub model_id: String,
    pub model_version: String,
    pub provider: String,
    pub execution_location: String,
    pub endpoint_url: Option<String>,
    pub api_key: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VersionedDatasetEvalReport {
    pub metadata: EvalRunMetadata,
    pub total_queries: usize,
    pub answerable_queries: usize,
    pub workspace_boundary_queries: usize,
    pub workspace_boundary_leaks: usize,
    pub workspace_boundary_compliance: f64,
    pub recall_at_5: f64,
    pub recall_at_10: f64,
    pub mrr: f64,
    pub ndcg_at_10: f64,
    pub superseded_exclusion_rate: f64,
    pub duplication_rate: f64,
    pub category_counts: BTreeMap<String, usize>,
    pub live_model_status: EvalModelStatus,
    pub live_model_note: String,
}

pub fn load_versioned_dataset(path: &Path) -> Result<VersionedRagDataset, CoreError> {
    let raw = std::fs::read_to_string(path).map_err(|e| CoreError::Io(e.to_string()))?;
    let ds: VersionedRagDataset =
        serde_json::from_str(&raw).map_err(|e| CoreError::InvalidRequest(e.to_string()))?;
    if ds.queries.len() != ds.total_queries {
        return Err(CoreError::InvalidRequest(format!(
            "dataset query count mismatch: header={} actual={}",
            ds.total_queries,
            ds.queries.len()
        )));
    }
    Ok(ds)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UpdatedAtMode {
    Constant,
    ValidFrom,
}

pub const SEED_STAMP: &str = "2026-04-01T00:00:00";

fn updated_at_for(doc: &RagEvalDatasetDocument, mode: UpdatedAtMode) -> String {
    match (mode, doc.valid_from.as_deref()) {
        (UpdatedAtMode::ValidFrom, Some(valid_from)) if !valid_from.trim().is_empty() => {
            valid_from.trim().trim_end_matches('Z').to_string()
        }
        _ => SEED_STAMP.to_string(),
    }
}

pub fn seed_versioned_dataset(
    dataset: &VersionedRagDataset,
    embedder: &LocalEmbeddingModel,
    mode: UpdatedAtMode,
) -> Result<Connection, CoreError> {
    let mut conn = Connection::open_in_memory()?;
    {
        let tx = conn.transaction()?;
        bootstrap(&tx, SEED_STAMP)?;
        tx.commit()?;
    }
    let model_name = embedder.model_id().to_string();
    for doc in &dataset.documents {
        let stamp = updated_at_for(doc, mode);
        let meta = serde_json::json!({
            "workspace_id": doc.workspace_id,
            "source_uri": doc.source_uri,
            "valid_from": doc.valid_from,
            "valid_to": doc.valid_to,
            "superseded_by": doc.superseded_by,
        })
        .to_string();
        conn.execute(
            "INSERT INTO nodes(id, type, title, summary, raw_json, metadata_json, created_at, updated_at)
             VALUES (?1, 'Document', ?2, ?3, '{}', ?4, ?5, ?5)",
            params![doc.doc_id, doc.title, doc.text, meta, stamp],
        )?;
        conn.execute(
            "INSERT INTO nodes_v2(id, type, label, summary, attrs, workspace_id, superseded_by, valid_from, valid_to, importance_score, created_at, updated_at)
             VALUES (?1, 'Document', ?2, ?3, ?4, ?5, ?6, ?7, ?8, 0.8, ?9, ?9)",
            params![
                doc.doc_id,
                doc.title,
                doc.text,
                meta,
                doc.workspace_id,
                doc.superseded_by,
                doc.valid_from,
                doc.valid_to,
                stamp,
            ],
        )?;
        let vec_f32 = embedder.embed(&format!("{} {}", doc.title, doc.text));
        let mut blob = Vec::with_capacity(vec_f32.len() * 4);
        for v in vec_f32 {
            blob.extend_from_slice(&v.to_le_bytes());
        }
        conn.execute(
            "INSERT INTO vector_embeddings(item_id, item_type, source_node, text_hash, embedding, embedding_dim, embedding_model, metadata_json, indexed_at)
             VALUES (?1, 'node', ?1, 'h', ?2, 384, ?3, '{}', ?4)",
            params![doc.doc_id, blob, model_name, SEED_STAMP],
        )?;
    }
    Ok(conn)
}

pub fn evaluate_versioned_dataset_retrieval(
    dataset: &VersionedRagDataset,
    live_target: Option<&LiveModelTarget>,
) -> Result<VersionedDatasetEvalReport, CoreError> {
    let embedder = LocalEmbeddingModel::new(384);
    let model_name = embedder.model_id().to_string();
    let conn = seed_versioned_dataset(dataset, &embedder, UpdatedAtMode::Constant)?;

    let mut cat_counts: BTreeMap<String, usize> = BTreeMap::new();
    let mut answerable_n = 0usize;
    let mut ws_boundary_n = 0usize;
    let mut ws_boundary_leaks = 0usize;
    let mut r5_sum = 0.0;
    let mut r10_sum = 0.0;
    let mut mrr_sum = 0.0;
    let mut ndcg_sum = 0.0;
    let mut trap_total = 0usize;
    let mut trap_excluded = 0usize;
    let mut dup_sum = 0.0;

    for q in &dataset.queries {
        *cat_counts.entry(q.category.clone()).or_insert(0) += 1;
        let qvec = embedder.embed(&q.query);
        let include_historical = q.as_of.is_some();
        let opts = GraphRagOptions {
            limit: 10,
            scope: Scope::for_workspace(&q.workspace_id),
            include_historical,
            graph_config: GraphRagConfig::default(),
            query_embedding: Some(QueryVectorOverride {
                model_id: model_name.clone(),
                vector: qvec,
                is_hash_fallback: true,
            }),
            ..GraphRagOptions::default()
        };
        let res = hybrid_bm25_vector_graph_search(&conn, &embedder, &q.query, &opts)?;
        let hit_ids: Vec<String> = res
            .get("matches")
            .or_else(|| res.get("results"))
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|item| item.get("id").and_then(|s| s.as_str()).map(str::to_string))
                    .collect()
            })
            .unwrap_or_default();

        if q.category == "workspace_boundary" {
            ws_boundary_n += 1;
            if q.forbidden_or_trap_doc_ids
                .iter()
                .any(|f| hit_ids.iter().any(|h| h == f))
            {
                ws_boundary_leaks += 1;
            }
        }

        if !q.expect_abstain && !q.supporting_span_doc_ids.is_empty() {
            answerable_n += 1;
            r5_sum += recall_at_k(&hit_ids, &q.supporting_span_doc_ids, 5);
            r10_sum += recall_at_k(&hit_ids, &q.supporting_span_doc_ids, 10);
            ndcg_sum += ndcg_at_k(&hit_ids, &q.supporting_span_doc_ids, &[], 10);
            let rr = hit_ids
                .iter()
                .position(|id| q.supporting_span_doc_ids.iter().any(|e| e == id))
                .map(|p| 1.0 / (p as f64 + 1.0))
                .unwrap_or(0.0);
            mrr_sum += rr;

            let top5: Vec<&String> = hit_ids.iter().take(5).collect();
            let uniq: HashSet<&String> = top5.iter().copied().collect();
            if !top5.is_empty() {
                dup_sum += (top5.len() - uniq.len()) as f64 / top5.len() as f64;
            }
            for trap in &q.forbidden_or_trap_doc_ids {
                trap_total += 1;
                if !hit_ids.iter().any(|h| h == trap) {
                    trap_excluded += 1;
                }
            }
        }
    }

    let (live_status, live_note, exec_mode, exec_loc) = match live_target {
        None => (
            EvalModelStatus::Skipped,
            "Real-model generation skipped in fast retrieval-only CI run (explicitly marked skipped, never mislabeled as live LLM).".to_string(),
            EvalExecutionMode::RetrievalOnly,
            "local_retrieval_only".to_string(),
        ),
        Some(target) if target.endpoint_url.as_deref().unwrap_or("").trim().is_empty() => (
            EvalModelStatus::Unavailable,
            format!(
                "Real model '{}' ({}) unavailable: endpoint_url not configured.",
                target.model_id, target.provider
            ),
            EvalExecutionMode::RetrievalOnly,
            target.execution_location.clone(),
        ),
        Some(target) => (
            EvalModelStatus::Skipped,
            format!(
                "Live model '{}' configured at {} — execute via scheduled/manual real-model runner.",
                target.model_id,
                target.endpoint_url.as_deref().unwrap_or("")
            ),
            if target.execution_location == "cloud" {
                EvalExecutionMode::RealModelCloud
            } else {
                EvalExecutionMode::RealModelLocal
            },
            target.execution_location.clone(),
        ),
    };

    let denom = answerable_n.max(1) as f64;
    Ok(VersionedDatasetEvalReport {
        metadata: EvalRunMetadata {
            execution_mode: exec_mode,
            execution_location: exec_loc,
            dataset_version: dataset.dataset_version.clone(),
            prompt_version: "retrieval-only-v1".to_string(),
            embedding_model: model_name,
            embedding_version: "384d-hash-v1".to_string(),
            is_hash_embedding: true,
            config_params: serde_json::json!({
                "limit": 10,
                "dataset_schema": dataset.schema_version,
            }),
        },
        total_queries: dataset.queries.len(),
        answerable_queries: answerable_n,
        workspace_boundary_queries: ws_boundary_n,
        workspace_boundary_leaks: ws_boundary_leaks,
        workspace_boundary_compliance: if ws_boundary_n == 0 {
            1.0
        } else {
            (ws_boundary_n - ws_boundary_leaks) as f64 / ws_boundary_n as f64
        },
        recall_at_5: r5_sum / denom,
        recall_at_10: r10_sum / denom,
        mrr: mrr_sum / denom,
        ndcg_at_10: ndcg_sum / denom,
        superseded_exclusion_rate: if trap_total == 0 {
            1.0
        } else {
            trap_excluded as f64 / trap_total as f64
        },
        duplication_rate: dup_sum / denom,
        category_counts: cat_counts,
        live_model_status: live_status,
        live_model_note: live_note,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    #[test]
    fn evaluates_versioned_320_query_dataset_with_zero_workspace_leaks_and_honest_mode_labels() {
        let dataset_path =
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../datasets/rag_eval_v1.json");
        let ds = load_versioned_dataset(&dataset_path).expect("load rag_eval_v1.json");
        assert!(ds.total_queries >= 300, "got {}", ds.total_queries);
        assert_eq!(ds.categories.len(), 9);

        let report = evaluate_versioned_dataset_retrieval(&ds, None).expect("eval");
        assert_eq!(report.metadata.dataset_version, "rag-eval-v1.0.0");
        assert_eq!(
            report.metadata.execution_mode,
            EvalExecutionMode::RetrievalOnly
        );
        assert_eq!(report.live_model_status, EvalModelStatus::Skipped);
        assert_eq!(report.workspace_boundary_queries, 32);
        assert_eq!(report.workspace_boundary_leaks, 0);
        assert_eq!(report.workspace_boundary_compliance, 1.0);
        assert_eq!(report.superseded_exclusion_rate, 1.0);
        assert_eq!(report.duplication_rate, 0.0);
        assert!(
            report.recall_at_5 >= 0.96,
            "recall@5={}",
            report.recall_at_5
        );
        assert!(
            report.recall_at_10 >= 0.98,
            "recall@10={}",
            report.recall_at_10
        );
        assert!(report.mrr >= 0.82, "mrr={}", report.mrr);
        assert!(report.ndcg_at_10 >= 0.84, "ndcg@10={}", report.ndcg_at_10);
    }
}
