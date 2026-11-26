use std::path::Path;
use std::time::Duration;

use lattice_core::embeddings::EmbeddingIndexContract;
use lattice_core::graph_write::types::{
    ChunkPiece, IngestContentRequest, IngestionRecord, SuppliedVector,
};
use lattice_core::graph_write::GraphWriter;
use lattice_core::worker::WorkerSeamClient;
use lattice_core::IngestStatus;
use serde_json::{json, Map, Value};

use crate::local_files_api::enrich::{self, SeamEmbedBatch, SeamEmbedOutcome};

pub const PIPELINE_NAME: &str = "unified-ingestion";
pub const NOTE_SOURCE_TYPE: &str = "note";
pub const DEFAULT_TIMEOUT: Duration = Duration::from_secs(30);
pub const INGEST_INFLIGHT: usize = 4;

#[derive(Debug)]
pub enum NoteIngestError {
    Empty,
    Write(String),
    Task(String),
    Client(String),
}

impl std::fmt::Display for NoteIngestError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            NoteIngestError::Empty => write!(
                formatter,
                "empty content: note ingestion requires non-empty text"
            ),
            NoteIngestError::Write(detail) => {
                write!(formatter, "the Brain refused the note: {detail}")
            }
            NoteIngestError::Task(detail) => {
                write!(formatter, "the note write did not finish: {detail}")
            }
            NoteIngestError::Client(detail) => {
                write!(formatter, "enrichment client: {detail}")
            }
        }
    }
}

impl std::error::Error for NoteIngestError {}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NoteSubmission {
    pub title: String,
    pub content: String,
    pub source: Option<String>,
    pub metadata: Map<String, Value>,
}

impl NoteSubmission {
    pub fn from_watched_file(
        root: &Path,
        relative_path: &str,
        content: &str,
        watch_id: Option<&str>,
    ) -> Self {
        let path = root.join(relative_path);
        let title = Path::new(relative_path)
            .file_name()
            .map(|name| name.to_string_lossy().into_owned())
            .unwrap_or_else(|| relative_path.to_string());
        let mut metadata = Map::new();
        metadata.insert("relative_path".into(), Value::from(relative_path));
        metadata.insert("path".into(), Value::from(path.display().to_string()));
        metadata.insert("folder_watch".into(), Value::Bool(true));
        metadata.insert("detected_by".into(), Value::from("lattice-ingest"));
        if let Some(watch_id) = watch_id {
            metadata.insert("watch_id".into(), Value::from(watch_id));
        }
        Self {
            title,
            content: content.to_string(),
            source: Some(path.display().to_string()),
            metadata,
        }
    }

    pub fn content_request(
        &self,
        owner: Option<&str>,
        workspace_id: Option<&str>,
    ) -> IngestContentRequest {
        IngestContentRequest {
            source_type: NOTE_SOURCE_TYPE.to_string(),
            title: self.title.clone(),
            text: self.content.clone(),
            source_uri: self.source.clone(),
            owner: owner.filter(|value| !value.is_empty()).map(str::to_string),
            workspace_id: workspace_id
                .filter(|value| !value.is_empty())
                .map(str::to_string),
            metadata: self.metadata.clone(),
            node_type: Some("Document".to_string()),
            ..Default::default()
        }
    }

    pub fn enrichment_text(&self) -> String {
        if self.title.is_empty() {
            self.content.clone()
        } else {
            format!("{}\n{}", self.title, self.content)
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct NoteReceipt {
    pub node_id: String,
    pub chunk_count: usize,
    pub duplicate: bool,
    pub provenance_id: Option<String>,
    pub indexed: bool,
    pub sections: usize,
    pub ingest_status: IngestStatus,
    pub embedding_mode: String,
    pub semantic: bool,
    pub degraded_reasons: Vec<String>,
    pub contract: Option<EmbeddingIndexContract>,
}

impl NoteReceipt {
    pub fn to_json(&self) -> Value {
        let mut obj = Map::new();
        obj.insert("status".into(), json!("ok"));
        obj.insert("node_id".into(), json!(self.node_id));
        obj.insert("chunk_count".into(), json!(self.chunk_count));
        obj.insert("duplicate".into(), json!(self.duplicate));
        obj.insert("provenance_id".into(), json!(self.provenance_id));
        obj.insert("indexed".into(), json!(self.indexed));
        obj.insert("sections".into(), json!(self.sections));
        obj.insert("ingest_status".into(), json!(self.ingest_status.as_str()));
        obj.insert("embedding_mode".into(), json!(self.embedding_mode));
        obj.insert("semantic".into(), json!(self.semantic));
        obj.insert("degraded_reasons".into(), json!(self.degraded_reasons));
        if let Some(contract) = &self.contract {
            if let Ok(value) = serde_json::to_value(contract) {
                obj.insert("embedding_contract".into(), value);
            }
        }
        Value::Object(obj)
    }
}

fn shares_source_between_items(metadata: &Map<String, Value>) -> bool {
    metadata.get("format").and_then(Value::as_str) == Some("ics")
}

#[derive(Debug, Clone)]
pub struct NoteIngestor {
    graph: GraphWriter,
    seam: Option<WorkerSeamClient>,
}

impl NoteIngestor {
    pub fn new(graph: GraphWriter) -> Self {
        Self { graph, seam: None }
    }

    pub fn with_seam(mut self, seam: WorkerSeamClient) -> Self {
        self.seam = Some(seam);
        self
    }

    pub fn with_worker_origin(self, origin: impl AsRef<str>) -> Result<Self, NoteIngestError> {
        let seam = WorkerSeamClient::new(origin)
            .map_err(|error| NoteIngestError::Client(error.to_string()))?
            .with_timeout(DEFAULT_TIMEOUT);
        Ok(self.with_seam(seam))
    }

    pub fn graph(&self) -> &GraphWriter {
        &self.graph
    }

    pub fn seam(&self) -> Option<&WorkerSeamClient> {
        self.seam.as_ref()
    }

    pub async fn ingest_note(
        &self,
        note: &NoteSubmission,
        owner: Option<&str>,
        workspace_id: Option<&str>,
    ) -> Result<NoteReceipt, NoteIngestError> {
        if note.content.trim().is_empty() {
            return Err(NoteIngestError::Empty);
        }
        let enrichment = note.enrichment_text();
        let mime = enrich::mime_hint(&note.title).unwrap_or_default();
        let chunk_set =
            crate::chunk_pieces::chunk_set_for(&note.content, &note.title, &mime, &note.title);
        let parents = chunk_set.parents.clone();
        let mut embed_texts = Vec::with_capacity(chunk_set.children.len() + 1);
        embed_texts.push(enrichment.clone());
        embed_texts.extend(chunk_set.embedding_inputs());
        let chunks = chunk_set.children;
        let ((extracted, extract_ok), embed_outcome) = tokio::join!(
            enrich::extract_via_seam_status(self.seam.as_ref(), &enrichment, "document"),
            enrich::embed_texts_with_contract(self.seam.as_ref(), &embed_texts),
        );
        let (embedding, chunk_batch) = split_note_embed(&embed_outcome, chunks.len());
        let chunks = enrich::attach_chunk_embeddings_from_batch(chunks, chunk_batch.as_ref(), true);

        let mut request = note.content_request(owner, workspace_id);
        request.concepts = extracted.concepts;
        request.triples = extracted.triples;
        request.semantic = extracted.semantic;
        request.embedding = embedding;
        request.chunks = chunks;
        request.parents = parents;

        let graph = self.graph.clone();
        let title = note.title.clone();
        let source_uri = note.source.clone();
        let mut metadata = note.metadata.clone();
        let owner = request.owner.clone();
        let workspace_id = request.workspace_id.clone();
        let outline_chunks = request.chunks.clone();
        let written = tokio::task::spawn_blocking(move || {
            let prior_contract = graph.current_index_contract().ok().flatten();
            let outcome = if shares_source_between_items(&request.metadata) {
                graph.ingest_content(&request)?
            } else {
                graph.ingest_content_superseding(&request)?
            };
            let overlay = crate::sections::build_overlay(
                &outline_chunks,
                &outcome.chunk_ids,
                &outcome.node_id,
                request.owner.as_deref(),
                request.workspace_id.as_deref(),
            );
            let sections = if overlay.is_empty() {
                0
            } else {
                match (
                    graph.upsert_nodes(&overlay.nodes),
                    graph.upsert_edges(&overlay.edges),
                ) {
                    (Ok(nodes), Ok(_)) => nodes.len(),
                    _ => 0,
                }
            };
            let supplied = supplied_for_note(
                &outcome.node_id,
                request.embedding.as_ref(),
                &outcome.chunk_ids,
                &request.chunks,
            );
            let vec_outcome = if !outcome.node_id.is_empty() {
                Some(graph.write_vectors_with(&outcome.node_id, &supplied))
            } else {
                None
            };
            let indexed = vec_outcome
                .as_ref()
                .is_some_and(|res| res.status != "failed");
            let incoming_contract = embed_outcome.contract().cloned();
            let prior_mismatch = prior_contract
                .as_ref()
                .zip(incoming_contract.as_ref())
                .is_some_and(|(prev, next)| !prev.is_compatible_with(next));
            let vec_reindex = vec_outcome.as_ref().is_some_and(|res| res.reindex_required);
            let active_contract = graph
                .current_index_contract()
                .ok()
                .flatten()
                .or(incoming_contract);
            let telemetry = enrich::build_enrichment_telemetry(
                false,
                extract_ok,
                Some(&embed_outcome),
                None,
                prior_mismatch || vec_reindex,
                indexed || !supplied.is_empty(),
                active_contract,
            );
            telemetry.apply_to_metadata(&mut metadata);
            let provenance = graph
                .record_ingestion(&IngestionRecord {
                    node_id: outcome.node_id.clone(),
                    source_type: NOTE_SOURCE_TYPE.to_string(),
                    pipeline: PIPELINE_NAME.to_string(),
                    source_uri,
                    content_hash: outcome.content_hash.clone(),
                    title: Some(title),
                    owner,
                    workspace_id,
                    captured_at: outcome.captured_at.clone(),
                    embedded: indexed,
                    duplicate: outcome.duplicate,
                    chunk_count: outcome.chunk_count as i64,
                    metadata,
                    ..Default::default()
                })
                .ok()
                .map(|row| row.id);
            Ok::<_, lattice_core::CoreError>(NoteReceipt {
                node_id: outcome.node_id,
                chunk_count: outcome.chunk_count,
                duplicate: outcome.duplicate,
                provenance_id: provenance,
                indexed,
                sections,
                ingest_status: telemetry.ingest_status,
                embedding_mode: telemetry.embedding_mode,
                semantic: telemetry.semantic,
                degraded_reasons: telemetry.degraded_reasons,
                contract: telemetry.contract,
            })
        })
        .await;
        match written {
            Ok(Ok(receipt)) => Ok(receipt),
            Ok(Err(error)) => Err(NoteIngestError::Write(error.to_string())),
            Err(error) => Err(NoteIngestError::Task(error.to_string())),
        }
    }
}

fn split_note_embed(
    outcome: &SeamEmbedOutcome,
    chunk_count: usize,
) -> (Option<SuppliedVector>, Option<SeamEmbedBatch>) {
    let SeamEmbedOutcome::Ready(batch) = outcome else {
        return (None, None);
    };
    let mut rows = batch.rows.clone();
    let document = if rows.is_empty() {
        None
    } else {
        let values = rows.remove(0);
        if values.is_empty() || batch.contract.validate_vector(&values).is_err() {
            None
        } else {
            Some(SuppliedVector {
                model_id: batch.contract.model_id.clone(),
                dim: batch.contract.dimension,
                values,
            })
        }
    };
    rows.truncate(chunk_count);
    let chunk_batch = if rows.iter().all(|row| row.is_empty()) {
        None
    } else {
        Some(SeamEmbedBatch {
            contract: batch.contract.clone(),
            rows,
        })
    };
    (document, chunk_batch)
}

fn supplied_for_note(
    node_id: &str,
    embedding: Option<&SuppliedVector>,
    chunk_ids: &[String],
    chunks: &[ChunkPiece],
) -> Vec<(String, SuppliedVector)> {
    let mut supplied = Vec::new();
    if let Some(vector) = embedding {
        if !vector.values.is_empty() {
            supplied.push((node_id.to_string(), vector.clone()));
        }
    }
    for (chunk_id, piece) in chunk_ids.iter().zip(chunks) {
        if let Some(vector) = &piece.embedding {
            if !vector.values.is_empty() {
                supplied.push((chunk_id.clone(), vector.clone()));
            }
        }
    }
    supplied
}

#[cfg(test)]
#[path = "worker_tests.rs"]
mod tests;
