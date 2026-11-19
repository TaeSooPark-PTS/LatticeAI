//! Where a watched note lands: the native write engine, in this process.
//!
//! Until v11.7.0 this module was the *delegation* seam — it posted
//! `type="note"` to `POST {origin}/knowledge-graph/ingest` and let the Python
//! worker be the single writer. That contract expired with v11.6.0: the worker
//! stopped serving product routes, `/knowledge-graph/ingest` left
//! `rust/fixtures/worker_allowlist.json`, and every note this crate detected
//! was posted into a 404. The watcher reported success because the *scan* had
//! succeeded; nothing reached the Brain.
//!
//! So the delegation is gone and the write is native, through the same
//! `GraphWriter` door the gateway's own `POST /knowledge-graph/ingest` handler
//! uses for a note (`lattice_retrieval::knowledge_graph_api::ingest`), with the
//! same enrichment chain the garden and browser doors use:
//!
//! 1. `POST /worker/extract` (W5) for concepts / triples / semantic items — the
//!    one part that is genuinely model compute and still belongs to the worker;
//!    it runs **alongside** the embed call, not in front of it;
//! 2. **one** `POST /worker/embed` (W2) whose `texts` is the document vector
//!    followed by every retrieval chunk — the reply's `(model_id, dim)` is the
//!    authority on which index those rows are filed under;
//! 3. [`GraphWriter::ingest_content`] with `source_type="note"`, then
//!    [`GraphWriter::write_vectors_with`] carrying the same supplied vectors
//!    so the native hash model is not asked to re-derive them. The incremental
//!    sync still runs **only** when the seam's embedder is the one this process
//!    would write with. `record_ingestion` then stamps provenance like every
//!    other source.
//!
//! Steps 1 and 2 and the chunking go through [`crate::local_files_api::enrich`],
//! which is the one place the upload, browser and watch doors talk to those
//! seams — so a note the watcher finds is chunked, embedded and degraded exactly
//! like the same file dropped on `POST /upload/document`.
//!
//! Every seam call is best-effort: an unreachable worker costs the note its
//! concepts and its provider vectors, not its place in the Brain. That is a
//! strict improvement on the seam it replaces, which lost the note entirely.
//!
//! [`crate::local_files_api::ingest`] is the production caller: the vault-watch
//! poller hands it every file a scan reported as new or changed.

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

/// `IngestionPipeline(pipeline_name=…)` — the name the provenance row carries.
pub const PIPELINE_NAME: &str = "unified-ingestion";
/// The `source_type` a watched note is filed under, as `_ingest_text` files it.
pub const NOTE_SOURCE_TYPE: &str = "note";
/// How long one enrichment call may take before the note lands without it.
pub const DEFAULT_TIMEOUT: Duration = Duration::from_secs(30);
/// How many folder / watch files may parse+embed+write at once.
///
/// Writes still serialize on the single SQLite writer; the overlap is the
/// parse / extract / embed seam, which is what made a folder of PDFs feel
/// single-threaded. Stamp-skip stays free because it never enters the pool.
pub const INGEST_INFLIGHT: usize = 4;

/// Why a note did not reach the Brain.
///
/// There is deliberately no transport variant any more: the write happens in
/// this process, so "the worker was unreachable" is no longer a way to lose a
/// note. It is a way to lose the note's *concepts*, which is not a failure.
#[derive(Debug)]
pub enum NoteIngestError {
    /// The note carried no text — Python's `_ingest_text` raises on this too.
    Empty,
    /// The write engine refused (`CoreError`).
    Write(String),
    /// The blocking write task did not finish.
    Task(String),
    /// A seam client could not be built from the origin it was given.
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

/// One note to hand to the worker.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NoteSubmission {
    /// Human title — the filename, for a watched file.
    pub title: String,
    /// The extracted text.
    pub content: String,
    /// Where it came from, echoed into the node metadata.
    pub source: Option<String>,
    /// Extra provenance merged into the item metadata.
    pub metadata: Map<String, Value>,
}

impl NoteSubmission {
    /// A note for one file a watch detected.
    ///
    /// The metadata carries what the Python watcher carries — the root-relative
    /// path, the `folder_watch` marker and the watch id — plus the absolute
    /// path and the fact that a Rust scanner found it, so the provenance says
    /// who noticed rather than implying the worker did.
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

    /// The exact write request — a pure function, so a test can pin what the
    /// Brain is asked for without opening a database.
    ///
    /// `node_type` is `Document` and `source_type` is `note`, which is what the
    /// retired route produced for `type="note"`: the request body went to
    /// `IngestionItem(source_type="note", …)`, `_ingest_text` routed it to
    /// `ingest_source`, and that door's default node type is `Document`.
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

    /// `title\ncontent`, the text the extract and embed seams are asked about.
    ///
    /// The same join the garden and gateway note doors use, so a note captured
    /// by the watcher and the same note pasted into the Brain produce the same
    /// concepts rather than two subtly different subgraphs.
    pub fn enrichment_text(&self) -> String {
        if self.title.is_empty() {
            self.content.clone()
        } else {
            format!("{}\n{}", self.title, self.content)
        }
    }
}

/// What one native note ingest produced.
#[derive(Debug, Clone, PartialEq)]
pub struct NoteReceipt {
    /// The `Document` node the note became.
    pub node_id: String,
    /// Retrieval chunks written beside it.
    pub chunk_count: usize,
    /// Whether that node already existed — a re-scan of an unchanged note.
    pub duplicate: bool,
    /// The provenance row, when one was recorded.
    pub provenance_id: Option<String>,
    /// Whether the vector index was synced for this node.
    pub indexed: bool,
    /// `Section` nodes written for this document's own outline (v12.0.0).
    /// Zero is honest in both directions — a document with no headings, or an
    /// overlay the writer refused — which is why it is reported at all.
    pub sections: usize,
    /// Granular lifecycle/enrichment status (`complete`, `partial`, `reindex_required`, ...).
    pub ingest_status: IngestStatus,
    /// Whether vectors came from a semantic provider, hash fallback, or none.
    pub embedding_mode: String,
    /// True only when real semantic embeddings landed.
    pub semantic: bool,
    /// Reasons ingestion degraded (e.g. `extract_seam_unavailable`, `embed_seam_unavailable`).
    pub degraded_reasons: Vec<String>,
    /// Active/supplied embedding index contract, if known.
    pub contract: Option<EmbeddingIndexContract>,
}

impl NoteReceipt {
    /// The receipt as a caller reports it.
    ///
    /// A `serde_json::Map` rather than an `OrderedMap`, for the same reason
    /// [`lattice_core::graph_write::ingest::IngestOutcome::to_json`] is one:
    /// this is a value a caller embeds, not an HTTP body it renders, and
    /// `Value::Object` re-sorts an ordered map the moment it is converted.
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

/// The native note door a watch hands its detected files to.
///
/// Holds the one `GraphWriter` the process writes with (cloning it shares the
/// `Arc<Store>`, so two handles are still one writer) and, optionally, the
/// compute seam. Without the seam the note still lands — see the module docs.
#[derive(Debug, Clone)]
pub struct NoteIngestor {
    graph: GraphWriter,
    seam: Option<WorkerSeamClient>,
}

impl NoteIngestor {
    /// An ingestor with no enrichment seam: notes land, concepts do not.
    pub fn new(graph: GraphWriter) -> Self {
        Self { graph, seam: None }
    }

    /// Bind an existing seam client (the gateway's, sharing its pool).
    pub fn with_seam(mut self, seam: WorkerSeamClient) -> Self {
        self.seam = Some(seam);
        self
    }

    /// Bind a seam built from a worker origin, capped at [`DEFAULT_TIMEOUT`].
    ///
    /// `no_proxy` matters for the same reason it does in the host: a
    /// machine-wide `HTTP_PROXY` must never intercept loopback traffic to our
    /// own worker. [`WorkerSeamClient::new`] sets it.
    pub fn with_worker_origin(self, origin: impl AsRef<str>) -> Result<Self, NoteIngestError> {
        let seam = WorkerSeamClient::new(origin)
            .map_err(|error| NoteIngestError::Client(error.to_string()))?
            .with_timeout(DEFAULT_TIMEOUT);
        Ok(self.with_seam(seam))
    }

    /// The writer this ingestor grows.
    pub fn graph(&self) -> &GraphWriter {
        &self.graph
    }

    /// The enrichment seam, when one is bound.
    pub fn seam(&self) -> Option<&WorkerSeamClient> {
        self.seam.as_ref()
    }

    /// Write one note into the Brain, enrichment included where it is reachable.
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
        let chunks = enrich::chunk_pieces_for(&note.content, &note.title, &mime);
        // One embed body: document text, then every chunk. Two HTTP round-trips
        // against a single-executor worker was the ingest-time tax the drain
        // path had already stopped paying.
        let mut embed_texts = Vec::with_capacity(chunks.len() + 1);
        embed_texts.push(enrichment.clone());
        embed_texts.extend(chunks.iter().map(|piece| piece.text.clone()));
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

        let graph = self.graph.clone();
        let title = note.title.clone();
        let source_uri = note.source.clone();
        let mut metadata = note.metadata.clone();
        let owner = request.owner.clone();
        let workspace_id = request.workspace_id.clone();
        // The chunker's own `heading_path` per piece, kept because `request`
        // is about to move into the write task and the outline is derived from
        // it *after* the writer has answered with the chunk ids.
        let outline_chunks = request.chunks.clone();
        let written = tokio::task::spawn_blocking(move || {
            let prior_contract = graph.current_index_contract().ok().flatten();
            let outcome = graph.ingest_content(&request)?;
            // The document's own outline as graph structure (v12.0.0). Written
            // after the content so the sections can point at real chunk rows,
            // and best-effort: an overlay that fails costs the note its
            // outline, never its place in the Brain.
            let overlay = crate::sections::build_overlay(
                &outline_chunks,
                &outcome.chunk_ids,
                &outcome.node_id,
                request.owner.as_deref(),
                request.workspace_id.as_deref(),
            );
            // The count is reported on the receipt rather than discarded: a
            // silent `let _ =` here is how an outline that never landed looks
            // exactly like a document that has no headings.
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
            // Provenance must never turn an already-persisted note into a
            // failure: the node is in the Brain either way.
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

/// Split the single embed reply into the document vector and the chunk batch.
///
/// Index 0 is the document. The rest are chunks in the same order
/// [`enrich::chunk_pieces_for`] produced them. Real semantic provider batches
/// are preserved with their own `model_id` and dimension rather than filtered
/// against the local hash embedder.
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

/// `(item_id, vector)` pairs `write_vectors_with` should file without hashing.
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
