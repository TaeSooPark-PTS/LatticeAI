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
//! 2. `POST /worker/embed` (W2) for the passage vector and, in one batch, for
//!    every retrieval chunk; the reply's `(model_id, dim)` is the authority on
//!    which index those rows are filed under;
//! 3. [`GraphWriter::ingest_content`] with `source_type="note"`, then the
//!    incremental `write_vectors` sync **only** when the seam's embedder is the
//!    one this process would write with, then `record_ingestion` so the note
//!    carries provenance like every other source.
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

use lattice_core::graph_write::types::{IngestContentRequest, IngestionRecord};
use lattice_core::graph_write::GraphWriter;
use lattice_core::worker::WorkerSeamClient;
use serde_json::{json, Map, Value};

use crate::local_files_api::enrich;

/// `IngestionPipeline(pipeline_name=…)` — the name the provenance row carries.
pub const PIPELINE_NAME: &str = "unified-ingestion";
/// The `source_type` a watched note is filed under, as `_ingest_text` files it.
pub const NOTE_SOURCE_TYPE: &str = "note";
/// How long one enrichment call may take before the note lands without it.
pub const DEFAULT_TIMEOUT: Duration = Duration::from_secs(30);

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
}

impl NoteReceipt {
    /// The receipt as a caller reports it.
    ///
    /// A `serde_json::Map` rather than an `OrderedMap`, for the same reason
    /// [`lattice_core::graph_write::ingest::IngestOutcome::to_json`] is one:
    /// this is a value a caller embeds, not an HTTP body it renders, and
    /// `Value::Object` re-sorts an ordered map the moment it is converted.
    pub fn to_json(&self) -> Value {
        json!({
            "status": "ok",
            "node_id": self.node_id,
            "chunk_count": self.chunk_count,
            "duplicate": self.duplicate,
            "provenance_id": self.provenance_id,
            "indexed": self.indexed,
        })
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
        let extracted = enrich::extract_via_seam(self.seam.as_ref(), &enrichment, "document").await;
        let embedding = enrich::embed_via_seam(self.seam.as_ref(), &enrichment).await;
        // The seam owns the embedding provider, so it — not a native guess — is
        // the authority on the index identity. Agreement is the licence to run
        // the native incremental sync; a mismatch leaves the node as backlog
        // rather than writing a row `similarity()` would refuse.
        let native_agrees = match embedding.as_ref() {
            Some(vector) => {
                vector.model_id == self.graph.embedder().model_id()
                    && vector.dim == self.graph.embedder().dim()
            }
            None => true,
        };
        // Retrieval chunks, through the *same* helpers the upload and browser
        // doors use (`local_files_api::enrich`) rather than a fourth copy of
        // the chunk → batch-embed → attach chain. A watched note is therefore
        // as searchable as the same file dragged onto the upload door.
        let mime = enrich::mime_hint(&note.title).unwrap_or_default();
        let chunks = enrich::chunk_pieces_for(&note.content, &note.title, &mime);
        let chunk_texts: Vec<String> = chunks.iter().map(|piece| piece.text.clone()).collect();
        let chunk_batch = enrich::embed_texts_via_seam(self.seam.as_ref(), &chunk_texts).await;
        let chunks_agree = match chunk_batch.as_ref() {
            Some((model_id, dim, _)) => enrich::model_agrees(&self.graph, model_id, *dim),
            None => true,
        };
        let chunks = enrich::attach_chunk_embeddings(chunks, chunk_batch, chunks_agree);

        let mut request = note.content_request(owner, workspace_id);
        request.concepts = extracted.concepts;
        request.triples = extracted.triples;
        request.semantic = extracted.semantic;
        request.embedding = embedding;
        request.chunks = chunks;

        let graph = self.graph.clone();
        let title = note.title.clone();
        let source_uri = note.source.clone();
        let metadata = note.metadata.clone();
        let owner = request.owner.clone();
        let workspace_id = request.workspace_id.clone();
        let written = tokio::task::spawn_blocking(move || {
            let outcome = graph.ingest_content(&request)?;
            let indexed = native_agrees
                && !outcome.node_id.is_empty()
                && graph.write_vectors(&outcome.node_id).status != "failed";
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

#[cfg(test)]
mod tests {
    use super::*;

    use std::sync::Arc;

    use lattice_core::db::Store;

    fn writer(dir: &Path) -> GraphWriter {
        let store = Arc::new(Store::open(&dir.join("knowledge_graph.sqlite")).expect("store"));
        GraphWriter::open(store, dir.join("knowledge_graph_blobs")).expect("writer")
    }

    #[test]
    fn a_watched_file_becomes_a_note_with_its_provenance() {
        let note = NoteSubmission::from_watched_file(
            Path::new("/brain/notes"),
            "회의/2026-08-11.md",
            "결정 사항",
            Some("watch_abc"),
        );
        assert_eq!(note.title, "2026-08-11.md");
        assert_eq!(note.content, "결정 사항");
        assert_eq!(note.enrichment_text(), "2026-08-11.md\n결정 사항");
        let request = note.content_request(Some("owner@lattice.test"), Some("personal"));
        assert_eq!(request.source_type, "note");
        assert_eq!(request.node_type.as_deref(), Some("Document"));
        assert_eq!(request.title, "2026-08-11.md");
        assert_eq!(request.text, "결정 사항");
        assert_eq!(request.owner.as_deref(), Some("owner@lattice.test"));
        assert_eq!(request.workspace_id.as_deref(), Some("personal"));
        assert_eq!(
            request.source_uri.as_deref(),
            Some("/brain/notes/회의/2026-08-11.md")
        );
        assert_eq!(
            request.metadata["relative_path"],
            Value::from("회의/2026-08-11.md")
        );
        assert_eq!(
            request.metadata["path"],
            Value::from("/brain/notes/회의/2026-08-11.md")
        );
        assert_eq!(request.metadata["folder_watch"], Value::Bool(true));
        assert_eq!(request.metadata["watch_id"], Value::from("watch_abc"));
        assert_eq!(
            request.metadata["detected_by"],
            Value::from("lattice-ingest")
        );
    }

    #[test]
    fn without_a_watch_id_the_key_is_absent_rather_than_null() {
        let note = NoteSubmission::from_watched_file(Path::new("/root"), "a.txt", "x", None);
        let request = note.content_request(None, None);
        assert!(request.metadata.get("watch_id").is_none());
        assert_eq!(request.source_uri.as_deref(), Some("/root/a.txt"));
        // An empty attribution is as absent as a missing one, so a watcher with
        // no signed-in user never writes `owner: ""` onto a node.
        let empty = note.content_request(Some(""), Some(""));
        assert_eq!(empty.owner, None);
        assert_eq!(empty.workspace_id, None);
        let bare = NoteSubmission {
            title: String::new(),
            content: "c".into(),
            source: None,
            metadata: Map::new(),
        };
        assert_eq!(bare.content_request(None, None).source_uri, None);
        assert_eq!(bare.enrichment_text(), "c", "no title, no leading newline");
    }

    #[test]
    fn errors_say_what_went_wrong_in_words() {
        assert!(NoteIngestError::Empty.to_string().contains("non-empty"));
        assert!(NoteIngestError::Write("locked".into())
            .to_string()
            .contains("locked"));
        assert!(NoteIngestError::Task("panicked".into())
            .to_string()
            .contains("did not finish"));
        assert!(NoteIngestError::Client("bad origin".into())
            .to_string()
            .contains("enrichment client"));
        assert!(format!("{:?}", NoteIngestError::Empty).contains("Empty"));
    }

    #[tokio::test]
    async fn a_note_lands_in_the_brain_with_no_worker_in_reach() {
        let dir = tempfile::tempdir().expect("tempdir");
        let graph = writer(dir.path());
        // No seam bound at all: the point of the fix is that an unreachable
        // worker costs the note its concepts, never its place in the Brain.
        let ingestor = NoteIngestor::new(graph.clone());
        assert!(ingestor.seam().is_none());
        let note = NoteSubmission::from_watched_file(
            dir.path(),
            "meeting.md",
            "가중치 정리 결정",
            Some("watch_1"),
        );
        let receipt = ingestor
            .ingest_note(&note, Some("owner@lattice.test"), Some("personal"))
            .await
            .expect("the note lands");
        assert!(receipt.node_id.starts_with("webdoc:"), "{receipt:?}");
        assert!(!receipt.duplicate);
        assert!(receipt.provenance_id.is_some());

        let node_id = receipt.node_id.clone();
        let (node_type, title, metadata): (String, String, String) = graph
            .store()
            .with_read_conn(|conn| {
                Ok(conn
                    .query_row(
                        "SELECT type, title, metadata_json FROM nodes WHERE id = ?1",
                        [&node_id],
                        |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
                    )
                    .expect("the node row"))
            })
            .expect("read");
        assert_eq!(node_type, "Document");
        assert_eq!(title, "meeting.md");
        let metadata: Value = serde_json::from_str(&metadata).expect("node metadata");
        assert_eq!(metadata["workspace_id"], "personal");
        assert_eq!(metadata["owner"], "owner@lattice.test");
        assert_eq!(metadata["source_type"], "note");
        assert_eq!(metadata["folder_watch"], Value::Bool(true));
        assert_eq!(metadata["watch_id"], "watch_1");
        let provenance: i64 = graph
            .store()
            .with_read_conn(|conn| {
                Ok(conn
                    .query_row(
                        "SELECT COUNT(*) FROM ingestion_provenance WHERE source_type='note'",
                        [],
                        |row| row.get(0),
                    )
                    .unwrap_or(0))
            })
            .expect("read");
        assert_eq!(provenance, 1, "a watched note carries where it came from");

        // Re-scanning the same file is idempotent and says so.
        let again = ingestor
            .ingest_note(&note, Some("owner@lattice.test"), Some("personal"))
            .await
            .expect("the second pass lands too");
        assert_eq!(again.node_id, receipt.node_id);
        assert!(again.duplicate, "the same note is not a second node");

        let json = receipt.to_json();
        assert_eq!(json["status"], "ok");
        assert_eq!(json["node_id"], receipt.node_id);
        assert_eq!(json["duplicate"], false);
        assert_eq!(json["provenance_id"], json!(receipt.provenance_id));
        assert_eq!(json["indexed"], receipt.indexed);
        // The note is chunked by the *same* helper the upload door uses, so it
        // is retrievable by passage and not only as one whole document.
        assert!(receipt.chunk_count >= 1, "{receipt:?}");
        assert_eq!(json["chunk_count"], receipt.chunk_count);
        let chunks: i64 = graph
            .store()
            .with_read_conn(|conn| {
                Ok(conn
                    .query_row("SELECT COUNT(*) FROM chunks", [], |row| row.get(0))
                    .unwrap_or(0))
            })
            .expect("read");
        assert_eq!(chunks as usize, receipt.chunk_count);
    }

    #[tokio::test]
    async fn an_empty_note_is_refused_rather_than_written() {
        let dir = tempfile::tempdir().expect("tempdir");
        let graph = writer(dir.path());
        let ingestor = NoteIngestor::new(graph.clone());
        assert!(
            Arc::ptr_eq(ingestor.graph().store(), graph.store()),
            "cloning the writer shares the store, so there is still one writer"
        );
        let blank = NoteSubmission::from_watched_file(dir.path(), "blank.md", "   \n", None);
        let refusal = ingestor
            .ingest_note(&blank, None, None)
            .await
            .expect_err("whitespace is not a note");
        assert!(matches!(refusal, NoteIngestError::Empty));
        let nodes: i64 = graph
            .store()
            .with_read_conn(|conn| {
                Ok(conn
                    .query_row("SELECT COUNT(*) FROM nodes", [], |row| row.get(0))
                    .unwrap_or(0))
            })
            .expect("read");
        assert_eq!(nodes, 0, "a refused note writes nothing at all");
    }

    #[tokio::test]
    async fn a_bad_worker_origin_is_named_rather_than_swallowed() {
        let dir = tempfile::tempdir().expect("tempdir");
        let ingestor = NoteIngestor::new(writer(dir.path()));
        let wired = ingestor
            .clone()
            .with_worker_origin("http://127.0.0.1:9")
            .expect("a loopback origin builds");
        assert_eq!(
            wired.seam().map(WorkerSeamClient::origin),
            Some("http://127.0.0.1:9")
        );
        assert_eq!(
            wired.seam().map(WorkerSeamClient::timeout),
            Some(DEFAULT_TIMEOUT)
        );
        // …and a note still lands while that origin refuses every connection.
        let note = NoteSubmission::from_watched_file(dir.path(), "n.md", "본문", None);
        let receipt = wired
            .ingest_note(&note, None, None)
            .await
            .expect("an unreachable seam is not a lost note");
        assert!(receipt.node_id.starts_with("webdoc:"));
        assert!(
            receipt.indexed,
            "a seam that answered nothing supplied no vector to disagree with, \
             so the native incremental sync is licensed and runs"
        );
    }
}
