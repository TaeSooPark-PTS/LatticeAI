//! Query-time semantic embedding resolution over `POST /worker/embed`.
//!
//! When the Brain's persisted [`EmbeddingIndexContract`] (or stored vector
//! table) names a semantic provider rather than `lattice-local-hash-v1`, query
//! vectors must be embedded through the worker compute seam (`kind = "query"`)
//! so retrieval scores cosine similarity in the same vector space as ingest.
//!
//! Hash-indexed stores (`lattice-local-hash-v1` or empty stores) bypass the
//! seam completely — zero HTTP hops and byte-identical deterministic parity.
//! When a semantic contract is active and `/worker/embed` times out (>1500 ms
//! default) or errors, resolution returns a structured `degraded_reason` so
//! hybrid/chat/recall callers fall back cleanly to lexical/BM25F + graph.

use std::sync::Arc;
use std::time::Duration;

use lattice_core::db::Store;
use lattice_core::embedding_contract::EmbeddingIndexContract;
use lattice_core::graph_write::GraphWriter;
use lattice_core::pytext::clean_text;
use lattice_core::worker::WorkerSeamClient;
use rusqlite::Connection;
use serde_json::{json, Value};

use crate::bm25::QueryVectorOverride;
use crate::policy::{resolve_policy, rewrite_query};
use crate::vector_alignment::inspect_stored_embedding_models;

/// Default upper bound on query-time `/worker/embed` calls before degrading to lexical.
pub const DEFAULT_QUERY_EMBED_TIMEOUT_MS: u64 = 1_500;
/// Environment override for query-time `/worker/embed` timeout (milliseconds).
pub const QUERY_EMBED_TIMEOUT_ENV: &str = "LATTICEAI_QUERY_EMBED_TIMEOUT_MS";
const EMBED_PATH: &str = "/worker/embed";

/// Result of inspecting the store's embedding contract and (if semantic) embedding the query.
#[derive(Debug, Clone, Default)]
pub struct QueryEmbedResolution {
    /// Active non-hash semantic contract detected in the store, if any.
    pub semantic_contract: Option<EmbeddingIndexContract>,
    /// Query vector produced by `/worker/embed` (`kind = "query"`), when available.
    pub query_vector: Option<QueryVectorOverride>,
    /// Reason query embedding degraded when `semantic_contract.is_some()` and `query_vector.is_none()`.
    pub degraded_reason: Option<String>,
}

impl QueryEmbedResolution {
    /// True when the store uses a semantic contract and query embedding degraded.
    pub fn is_semantic_degraded(&self) -> bool {
        self.semantic_contract.is_some() && self.query_vector.is_none()
    }
}

/// Inspect `graph_meta` (`embedding_index_contract`) and `vector_embeddings` to
/// determine whether query-time semantic embedding via `/worker/embed` is needed.
///
/// Returns `None` for empty stores and `lattice-local-hash-v1` stores so the
/// synchronous in-process hash embedder runs with zero network overhead.
pub fn active_semantic_contract(conn: &Connection) -> Option<EmbeddingIndexContract> {
    if let Some(contract) = GraphWriter::read_index_contract(conn) {
        if contract.is_semantic() && !contract.model_id.is_empty() && contract.dimension > 0 {
            return Some(contract);
        }
        return None;
    }
    let stored = inspect_stored_embedding_models(conn).ok()?;
    let semantic = stored.into_iter().find(|row| {
        row.row_count > 0 && row.dim > 0 && !EmbeddingIndexContract::is_hash_model_id(&row.model_id)
    })?;
    Some(EmbeddingIndexContract::from_semantic(
        "semantic",
        &semantic.model_id,
        semantic.dim as usize,
    ))
}

/// Effective query text used by `service_hybrid_search` / `hybrid_search` when
/// policy rewriting applies (`has_pinned_weights == false`).
pub fn effective_hybrid_query(query: &str, has_pinned_weights: bool) -> String {
    let trimmed = query.trim();
    if trimmed.is_empty() || has_pinned_weights {
        return trimmed.to_string();
    }
    let policy = resolve_policy(trimmed);
    if !policy.search_query.is_empty() && policy.search_query != trimmed {
        policy.search_query
    } else {
        trimmed.to_string()
    }
}

/// Effective query text used by `hybrid_bm25_vector_graph_search`.
pub fn effective_graphrag_query(query: &str) -> String {
    let cleaned = clean_text(query);
    let (rewritten, _) = rewrite_query(&cleaned);
    if rewritten.trim().is_empty() {
        cleaned
    } else {
        rewritten
    }
}

fn query_embed_timeout() -> Duration {
    let ms = std::env::var(QUERY_EMBED_TIMEOUT_ENV)
        .ok()
        .and_then(|raw| raw.trim().parse::<u64>().ok())
        .unwrap_or(DEFAULT_QUERY_EMBED_TIMEOUT_MS)
        .clamp(50, 15_000);
    Duration::from_millis(ms)
}

fn parse_query_embed_payload(payload: &Value) -> Result<QueryVectorOverride, String> {
    let model_id = payload
        .get("model_id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    if model_id.is_empty() {
        return Err("worker embed response missing model_id".into());
    }
    let dim = payload.get("dim").and_then(Value::as_u64).unwrap_or(0) as usize;
    if dim == 0 {
        return Err("worker embed response missing positive dim".into());
    }
    let first = payload
        .get("vectors")
        .and_then(Value::as_array)
        .and_then(|rows| rows.first())
        .and_then(Value::as_array)
        .ok_or_else(|| "worker embed response missing vectors[0]".to_string())?;
    let mut vector = Vec::with_capacity(first.len());
    for value in first {
        let Some(num) = value.as_f64().filter(|n| n.is_finite()) else {
            return Err("worker embed vector contains non-finite float".into());
        };
        vector.push(num);
    }
    if vector.len() != dim {
        return Err(format!(
            "worker embed vector length {} != declared dim {}",
            vector.len(),
            dim
        ));
    }
    let provider = payload
        .get("provider")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim();
    let is_hash_fallback = provider.eq_ignore_ascii_case("hash")
        || EmbeddingIndexContract::is_hash_model_id(&model_id);
    Ok(QueryVectorOverride {
        model_id,
        vector,
        is_hash_fallback,
    })
}

/// Resolve a semantic query vector via `POST /worker/embed` (`kind = "query"`)
/// for an already-inspected [`EmbeddingIndexContract`].
pub async fn resolve_query_embedding_with_contract(
    contract: Option<EmbeddingIndexContract>,
    seam: Option<&WorkerSeamClient>,
    query: &str,
) -> QueryEmbedResolution {
    let trimmed = query.trim().to_string();
    if trimmed.is_empty() {
        return QueryEmbedResolution::default();
    }
    let Some(contract) = contract else {
        return QueryEmbedResolution::default();
    };
    let Some(seam) = seam else {
        return QueryEmbedResolution {
            semantic_contract: Some(contract),
            query_vector: None,
            degraded_reason: Some("query_embed_seam_unavailable".into()),
        };
    };

    let client = seam.clone().with_timeout(query_embed_timeout());
    let body = json!({
        "texts": [trimmed],
        "kind": "query",
    });
    match client.post_json(EMBED_PATH, &body).await {
        Ok(payload) => match parse_query_embed_payload(&payload) {
            Ok(query_vector) => QueryEmbedResolution {
                semantic_contract: Some(contract),
                query_vector: Some(query_vector),
                degraded_reason: None,
            },
            Err(reason) => QueryEmbedResolution {
                semantic_contract: Some(contract),
                query_vector: None,
                degraded_reason: Some(format!("query_embed_invalid: {reason}")),
            },
        },
        Err(err) => QueryEmbedResolution {
            semantic_contract: Some(contract),
            query_vector: None,
            degraded_reason: Some(format!("query_embed_failed: {err}")),
        },
    }
}

/// Resolve a semantic query vector via `POST /worker/embed` (`kind = "query"`)
/// whenever `store` carries a non-hash semantic index contract.
pub async fn resolve_query_embedding(
    store: &Arc<Store>,
    seam: Option<&WorkerSeamClient>,
    query: &str,
) -> QueryEmbedResolution {
    let trimmed = query.trim().to_string();
    if trimmed.is_empty() {
        return QueryEmbedResolution::default();
    }
    let store_ref = Arc::clone(store);
    let contract = tokio::task::spawn_blocking(move || {
        store_ref
            .with_read_conn(|conn| Ok(active_semantic_contract(conn)))
            .ok()
            .flatten()
    })
    .await
    .ok()
    .flatten();

    resolve_query_embedding_with_contract(contract, seam, &trimmed).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_or_hash_store_reports_no_semantic_contract() {
        let dir = tempfile::tempdir().unwrap();
        let db = dir.path().join("kg.sqlite");
        let store = Arc::new(Store::open(&db).unwrap());
        let _writer = GraphWriter::open(Arc::clone(&store), dir.path().join("blobs")).unwrap();
        let hash = EmbeddingIndexContract::from_hash("lattice-local-hash-v1", 384);
        let conn = Connection::open(&db).unwrap();
        conn.execute(
            "INSERT OR REPLACE INTO graph_meta(key, value) VALUES (?, ?)",
            rusqlite::params![
                "embedding_index_contract",
                serde_json::to_string(&hash).unwrap()
            ],
        )
        .unwrap();
        drop(conn);
        store
            .with_read_conn(|conn| {
                assert!(active_semantic_contract(conn).is_none());
                Ok(())
            })
            .unwrap();
    }

    #[test]
    fn semantic_contract_in_graph_meta_is_detected() {
        let dir = tempfile::tempdir().unwrap();
        let db = dir.path().join("kg.sqlite");
        let store = Arc::new(Store::open(&db).unwrap());
        let _writer = GraphWriter::open(Arc::clone(&store), dir.path().join("blobs")).unwrap();
        let gemma = EmbeddingIndexContract::from_semantic(
            "huggingface",
            "huggingface:google/embeddinggemma-2:768",
            768,
        );
        let conn = Connection::open(&db).unwrap();
        conn.execute(
            "INSERT OR REPLACE INTO graph_meta(key, value) VALUES (?, ?)",
            rusqlite::params![
                "embedding_index_contract",
                serde_json::to_string(&gemma).unwrap()
            ],
        )
        .unwrap();
        drop(conn);
        store
            .with_read_conn(|conn| {
                let detected = active_semantic_contract(conn).expect("semantic");
                assert_eq!(detected.model_id, gemma.model_id);
                assert_eq!(detected.dimension, 768);
                Ok(())
            })
            .unwrap();
    }
}
