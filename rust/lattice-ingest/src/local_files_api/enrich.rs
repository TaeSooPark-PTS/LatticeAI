//! Shared extract / parse / embed / chunk helpers for native ingest doors.
//!
//! The worker already serves `POST /worker/parse` (filename + base64 → text)
//! and `POST /worker/embed` (`texts` is a list). These helpers are the only
//! place the upload and browser doors talk to those seams, so a seam failure
//! degrades the same way at every call site: empty extract, no supplied
//! vectors, never a 500.

use lattice_core::embeddings::EmbeddingIndexContract;
use lattice_core::graph_write::types::{ChunkPiece, ExtractReply, SuppliedVector};
use lattice_core::worker::WorkerSeamClient;
use serde_json::{json, Map, Value};

use crate::chunk::{chunk_meta_fields, typed_chunks_default};
use crate::strategy::chunk_strategy_for;

pub(crate) use super::upload_parse::{encode_base64, mime_hint, needs_parse, unwrap_upload};

pub(crate) const EXTRACT_PATH: &str = "/worker/extract";
pub(crate) const EMBED_PATH: &str = "/worker/embed";
pub(crate) const PARSE_PATH: &str = "/worker/parse";

/// `POST /worker/parse` — text out, or `None` when the seam is down / refuses.
pub(crate) async fn parse_via_seam(
    seam: Option<&WorkerSeamClient>,
    filename: &str,
    bytes: &[u8],
) -> Option<Map<String, Value>> {
    let seam = seam?;
    let payload = seam
        .post_json(
            PARSE_PATH,
            &json!({
                "filename": filename,
                "content_b64": encode_base64(bytes),
            }),
        )
        .await
        .ok()?;
    payload.as_object().cloned()
}

/// Text the extract/embed chain should see. Prefers `content`, then `preview`.
pub(crate) fn parsed_text(parsed: &Map<String, Value>) -> String {
    parsed
        .get("content")
        .and_then(Value::as_str)
        .filter(|text| !text.trim().is_empty())
        .or_else(|| {
            parsed
                .get("preview")
                .and_then(Value::as_str)
                .filter(|text| !text.trim().is_empty())
        })
        .unwrap_or("")
        .to_string()
}

/// Whether `/worker/extract` succeeded alongside its extracted reply.
pub(crate) async fn extract_via_seam_status(
    seam: Option<&WorkerSeamClient>,
    text: &str,
    kind: &str,
) -> (ExtractReply, bool) {
    if text.trim().is_empty() {
        return (ExtractReply::default(), true);
    }
    let Some(seam) = seam else {
        return (ExtractReply::default(), false);
    };
    match seam
        .post_json(EXTRACT_PATH, &json!({"text": text, "kind": kind}))
        .await
    {
        Ok(payload) => (ExtractReply::from_json(&payload), true),
        Err(_) => (ExtractReply::default(), false),
    }
}

/// Verified embedding batch returned by `/worker/embed` together with its contract.
#[derive(Debug, Clone)]
pub(crate) struct SeamEmbedBatch {
    pub contract: EmbeddingIndexContract,
    pub rows: Vec<Vec<f64>>,
}

/// Outcome of calling `/worker/embed`, distinguishing worker unavailability
/// from malformed/dimension-mismatched vectors.
#[derive(Debug, Clone)]
pub(crate) enum SeamEmbedOutcome {
    Unavailable,
    Rejected {
        reason: String,
        contract: Option<EmbeddingIndexContract>,
    },
    Ready(SeamEmbedBatch),
}

impl SeamEmbedOutcome {
    pub fn contract(&self) -> Option<&EmbeddingIndexContract> {
        match self {
            Self::Ready(batch) => Some(&batch.contract),
            Self::Rejected { contract, .. } => contract.as_ref(),
            Self::Unavailable => None,
        }
    }

    pub fn batch(&self) -> Option<&SeamEmbedBatch> {
        match self {
            Self::Ready(batch) => Some(batch),
            _ => None,
        }
    }
}

/// Granular enrichment and embedding telemetry attached to ingest receipts.
#[derive(Debug, Clone)]
pub(crate) struct EnrichmentTelemetry {
    pub ingest_status: lattice_core::IngestStatus,
    pub embedding_mode: String,
    pub semantic: bool,
    pub degraded_reasons: Vec<String>,
    pub contract: Option<EmbeddingIndexContract>,
}

impl EnrichmentTelemetry {
    pub fn apply_to_json(&self, object: &mut Map<String, Value>) {
        object.insert(
            "ingest_status".into(),
            Value::String(self.ingest_status.as_str().to_string()),
        );
        object.insert(
            "embedding_mode".into(),
            Value::String(self.embedding_mode.clone()),
        );
        object.insert("semantic".into(), Value::Bool(self.semantic));
        object.insert("degraded_reasons".into(), json!(self.degraded_reasons));
        if let Some(contract) = &self.contract {
            if let Ok(value) = serde_json::to_value(contract) {
                object.insert("embedding_contract".into(), value);
            }
        }
    }

    pub fn apply_to_metadata(&self, metadata: &mut Map<String, Value>) {
        self.apply_to_json(metadata);
    }
}

fn push_unique(reasons: &mut Vec<String>, reason: impl Into<String>) {
    let value = reason.into();
    if !reasons.contains(&value) {
        reasons.push(value);
    }
}

pub(crate) fn build_enrichment_telemetry(
    parse_failed: bool,
    extract_ok: bool,
    doc_embed: Option<&SeamEmbedOutcome>,
    chunk_embed: Option<&SeamEmbedOutcome>,
    reindex_required: bool,
    written_vectors: bool,
    contract_override: Option<EmbeddingIndexContract>,
) -> EnrichmentTelemetry {
    let mut degraded_reasons = Vec::new();
    if parse_failed {
        push_unique(&mut degraded_reasons, "parse_seam_unavailable");
    }
    if !extract_ok {
        push_unique(&mut degraded_reasons, "extract_seam_unavailable");
    }

    let mut semantic = false;
    let mut saw_hash_batch = false;
    let mut detected_contract = None;

    for outcome in [doc_embed, chunk_embed].into_iter().flatten() {
        if detected_contract.is_none() {
            detected_contract = outcome.contract().cloned();
        }
        match outcome {
            SeamEmbedOutcome::Unavailable => {
                push_unique(&mut degraded_reasons, "embed_seam_unavailable");
            }
            SeamEmbedOutcome::Rejected { reason, .. } => {
                push_unique(
                    &mut degraded_reasons,
                    format!("embed_contract_invalid:{reason}"),
                );
            }
            SeamEmbedOutcome::Ready(batch) => {
                detected_contract = Some(batch.contract.clone());
                if batch.contract.is_semantic() {
                    semantic = true;
                } else {
                    saw_hash_batch = true;
                    push_unique(&mut degraded_reasons, "hash_fallback_embedding");
                }
            }
        }
    }

    if reindex_required {
        push_unique(&mut degraded_reasons, "embedding_contract_reindex_required");
    }
    if !written_vectors && !parse_failed {
        push_unique(&mut degraded_reasons, "vectors_not_indexed");
    }

    let contract = contract_override.or(detected_contract);
    let embedding_mode = if semantic {
        "semantic"
    } else if written_vectors || saw_hash_batch {
        if !semantic && written_vectors {
            push_unique(&mut degraded_reasons, "hash_fallback_embedding");
        }
        "hash_fallback"
    } else {
        "none"
    }
    .to_string();

    let ingest_status = if reindex_required {
        lattice_core::IngestStatus::ReindexRequired
    } else if !degraded_reasons.is_empty() || !written_vectors {
        lattice_core::IngestStatus::Partial
    } else {
        lattice_core::IngestStatus::Complete
    };

    EnrichmentTelemetry {
        ingest_status,
        embedding_mode,
        semantic,
        degraded_reasons,
        contract,
    }
}

fn parse_seam_contract(payload: &Value, values: &[Vec<f64>]) -> EmbeddingIndexContract {
    let top_dim = payload
        .get("dim")
        .and_then(Value::as_u64)
        .map(|d| d as usize)
        .filter(|&d| d > 0)
        .unwrap_or_else(|| {
            values
                .iter()
                .find(|row| !row.is_empty())
                .map(|row| row.len())
                .unwrap_or(0)
        });
    let top_model_id = payload
        .get("model_id")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let top_provider = payload.get("provider").and_then(Value::as_str);

    if let Some(contract_val) = payload.get("contract") {
        if let Ok(mut contract) =
            serde_json::from_value::<EmbeddingIndexContract>(contract_val.clone())
        {
            if contract.model_id.trim().is_empty() && !top_model_id.is_empty() {
                contract.model_id = top_model_id.to_string();
            }
            if contract.dimension == 0 {
                contract.dimension = top_dim;
            }
            if contract.provider.trim().is_empty() {
                contract.provider = top_provider
                    .map(str::to_string)
                    .unwrap_or_else(|| EmbeddingIndexContract::infer_provider(&contract.model_id));
            }
            return contract;
        }
    }

    let mut contract = EmbeddingIndexContract::from_parts(top_provider, top_model_id, top_dim);
    if let Some(rev) = payload
        .get("model_revision")
        .and_then(Value::as_str)
        .filter(|s| !s.trim().is_empty())
    {
        contract.model_revision = Some(rev.to_string());
    }
    if let Some(norm) = payload
        .get("normalization")
        .and_then(Value::as_str)
        .filter(|s| !s.trim().is_empty())
    {
        contract.normalization = norm.to_string();
    }
    if let Some(pm) = payload
        .get("passage_mode")
        .and_then(Value::as_str)
        .filter(|s| !s.trim().is_empty())
    {
        contract.passage_mode = pm.to_string();
    }
    if let Some(qm) = payload
        .get("query_mode")
        .and_then(Value::as_str)
        .filter(|s| !s.trim().is_empty())
    {
        contract.query_mode = qm.to_string();
    }
    if let Some(iv) = payload.get("index_version").and_then(Value::as_u64) {
        contract.index_version = iv as u32;
    }
    contract
}

pub(crate) async fn embed_texts_with_contract(
    seam: Option<&WorkerSeamClient>,
    texts: &[String],
) -> SeamEmbedOutcome {
    if texts.is_empty() {
        return SeamEmbedOutcome::Unavailable;
    }
    let Some(seam) = seam else {
        return SeamEmbedOutcome::Unavailable;
    };
    let Ok(payload) = seam
        .post_json(EMBED_PATH, &json!({"texts": texts, "kind": "passage"}))
        .await
    else {
        return SeamEmbedOutcome::Unavailable;
    };
    let Some(rows) = payload.get("vectors").and_then(Value::as_array) else {
        return SeamEmbedOutcome::Unavailable;
    };
    let mut values = Vec::with_capacity(rows.len());
    for row in rows {
        let Some(cells) = row.as_array() else {
            return SeamEmbedOutcome::Rejected {
                reason: "embedding row is not an array".into(),
                contract: None,
            };
        };
        let mut floats = Vec::with_capacity(cells.len());
        for cell in cells {
            let Some(value) = cell.as_f64() else {
                return SeamEmbedOutcome::Rejected {
                    reason: "embedding contains non-numeric value".into(),
                    contract: None,
                };
            };
            floats.push(value);
        }
        values.push(floats);
    }
    if values.iter().all(|row| row.is_empty()) {
        return SeamEmbedOutcome::Unavailable;
    }
    let contract = parse_seam_contract(&payload, &values);
    if contract.model_id.trim().is_empty() {
        return SeamEmbedOutcome::Rejected {
            reason: "embedding response missing model_id".into(),
            contract: Some(contract),
        };
    }
    if contract.dimension == 0 {
        return SeamEmbedOutcome::Rejected {
            reason: "embedding response missing dimension".into(),
            contract: Some(contract),
        };
    }
    for row in &values {
        if row.is_empty() {
            continue;
        }
        if let Err(err) = contract.validate_vector(row) {
            return SeamEmbedOutcome::Rejected {
                reason: err.to_string(),
                contract: Some(contract),
            };
        }
    }
    SeamEmbedOutcome::Ready(SeamEmbedBatch {
        contract,
        rows: values,
    })
}

/// `typed_chunks` pieces for this document, no vectors yet.
pub(crate) fn chunk_pieces_for(text: &str, filename: &str, mime: &str) -> Vec<ChunkPiece> {
    if text.trim().is_empty() {
        return Vec::new();
    }
    let strategy = chunk_strategy_for(filename, mime);
    typed_chunks_default(text, strategy)
        .into_iter()
        .map(|chunk| ChunkPiece {
            text: chunk.text.clone(),
            fields: chunk_meta_fields(&chunk),
            embedding: None,
        })
        .collect()
}

/// Attach provider vectors when a verified batch is present and compatible.
pub(crate) fn attach_chunk_embeddings_from_batch(
    mut chunks: Vec<ChunkPiece>,
    batch: Option<&SeamEmbedBatch>,
    accept: bool,
) -> Vec<ChunkPiece> {
    if !accept {
        return chunks;
    }
    let Some(batch) = batch else {
        return chunks;
    };
    for (piece, values) in chunks.iter_mut().zip(&batch.rows) {
        if values.is_empty() || batch.contract.validate_vector(values).is_err() {
            continue;
        }
        piece.embedding = Some(SuppliedVector {
            model_id: batch.contract.model_id.clone(),
            dim: batch.contract.dimension,
            values: values.clone(),
        });
    }
    chunks
}

/// Extract a single `SuppliedVector` from row 0 of a verified batch.
pub(crate) fn first_vector_from_batch(batch: Option<&SeamEmbedBatch>) -> Option<SuppliedVector> {
    let batch = batch?;
    let values = batch.rows.first()?.clone();
    if values.is_empty() || batch.contract.validate_vector(&values).is_err() {
        return None;
    }
    Some(SuppliedVector {
        model_id: batch.contract.model_id.clone(),
        dim: batch.contract.dimension,
        values,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chunk_pieces_carry_strategy_fields() {
        let pieces = chunk_pieces_for("hello world from lattice", "note.md", "text/markdown");
        assert_eq!(pieces.len(), 1);
        assert_eq!(pieces[0].fields["strategy"], json!("markdown"));
        assert!(pieces[0].embedding.is_none());
    }

    #[test]
    fn attach_accepts_semantic_provider_vectors_and_rejects_dim_mismatch() {
        let pieces = chunk_pieces_for("hello world from lattice", "note.txt", "");
        let semantic_batch = SeamEmbedBatch {
            contract: EmbeddingIndexContract::from_semantic(
                "huggingface",
                "huggingface:google/embeddinggemma-2:768",
                4,
            ),
            rows: vec![vec![0.5, 0.5, 0.5, 0.5]],
        };
        let attached =
            attach_chunk_embeddings_from_batch(pieces.clone(), Some(&semantic_batch), true);
        let embedded = attached[0]
            .embedding
            .as_ref()
            .expect("semantic provider vector must not be dropped");
        assert_eq!(embedded.model_id, "huggingface:google/embeddinggemma-2:768");
        assert_eq!(embedded.dim, 4);

        let mismatched_batch = SeamEmbedBatch {
            contract: EmbeddingIndexContract::from_semantic(
                "openai",
                "openai:text-embedding-3-small",
                3,
            ),
            rows: vec![vec![1.0, 2.0]],
        };
        let skipped = attach_chunk_embeddings_from_batch(pieces, Some(&mismatched_batch), true);
        assert!(skipped[0].embedding.is_none());
    }
}
