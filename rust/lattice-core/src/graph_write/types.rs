//! The shapes the write engine takes in and hands back.
//!
//! Two rules decide what is a field here and what is not.
//!
//! **Rust owns rows; the worker owns compute.** Anything that needs a model or
//! a parser — the chunk boundaries, the extracted concepts and triples, the
//! semantic (Task/Decision) items, the curator's topic overlay, a document's
//! parsed structure — arrives as *data* on these requests. The engine never
//! calls a model; it writes what it is given, and every id it derives from that
//! input is derived exactly as Python derives it.
//!
//! **Every request deserializes from the parity scenario as-is.** The golden
//! generator records the arguments Python's own call used, so the fields below
//! are named after the Python parameters rather than after what would read
//! nicely in Rust. `#[serde(default)]` throughout, because Python's keyword
//! arguments all have defaults.

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

/// One node the way `_upsert_node` takes it.
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct NodeSpec {
    /// `node_id`.
    pub id: String,
    /// `node_type` — the free-string legacy label, normalized on projection.
    pub node_type: String,
    /// `title`, truncated to 240 **characters** on write.
    pub title: String,
    /// `summary`, truncated to 1000 characters on write.
    #[serde(default)]
    pub summary: String,
    #[serde(default)]
    pub metadata: Map<String, Value>,
    /// `raw` — `None` and `{}` are the same `"{}"` in Python's `_json`.
    #[serde(default)]
    pub raw: Map<String, Value>,
    #[serde(default)]
    pub owner: Option<String>,
    #[serde(default)]
    pub workspace_id: Option<String>,
    #[serde(default)]
    pub visibility: Option<String>,
}

/// One edge the way `_upsert_edge` takes it.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct EdgeSpec {
    pub from_node: String,
    pub to_node: String,
    pub edge_type: String,
    #[serde(default = "one")]
    pub weight: f64,
    #[serde(default)]
    pub metadata: Map<String, Value>,
    /// Import paths pass this to keep two distinct legacy labels between one
    /// pair as two distinct `edges_v2` rows.
    #[serde(default)]
    pub legacy_label: Option<String>,
}

fn one() -> f64 {
    1.0
}

/// One `typed_chunks` piece: the text, plus `typed_chunk_meta_fields`' output.
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct ChunkPiece {
    pub text: String,
    #[serde(default)]
    pub fields: Map<String, Value>,
    /// Optional provider vector for this chunk's `vector_embeddings` row.
    #[serde(default)]
    pub embedding: Option<SuppliedVector>,
}

/// One extracted concept, already classified by `_classify_node_type`.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ConceptSpec {
    pub text: String,
    pub node_type: String,
}

/// One extracted `(subject, relation, object)` triple with its evidence class.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct TripleSpec {
    pub subject: String,
    pub object: String,
    pub relation: String,
    #[serde(default = "one")]
    pub weight: f64,
    #[serde(default)]
    pub context: String,
    #[serde(default)]
    pub evidence: String,
    #[serde(default)]
    pub confidence: Option<f64>,
}

/// One `_semantic_items` entry — an explicit Task or Decision line.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct SemanticSpec {
    pub item_type: String,
    pub title: String,
    #[serde(default)]
    pub summary: String,
    /// The raw item dict, stored verbatim in `nodes.raw_json`.
    #[serde(default)]
    pub raw: Map<String, Value>,
}

/// `ingest_source` — the unified text/web/note door.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct IngestContentRequest {
    #[serde(default)]
    pub source_type: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub text: String,
    #[serde(default)]
    pub source_uri: Option<String>,
    #[serde(default)]
    pub owner: Option<String>,
    #[serde(default)]
    pub workspace_id: Option<String>,
    #[serde(default)]
    pub permissions: Map<String, Value>,
    #[serde(default)]
    pub captured_at: Option<String>,
    #[serde(default)]
    pub modified_at: Option<String>,
    #[serde(default)]
    pub conversation_id: Option<String>,
    #[serde(default)]
    pub metadata: Map<String, Value>,
    /// Defaults to `Document`; a door that knows its material is something
    /// else (`Audio` for a recording) says so here.
    #[serde(default)]
    pub node_type: Option<String>,
    #[serde(default)]
    pub chunks: Vec<ChunkPiece>,
    #[serde(default)]
    pub concepts: Vec<ConceptSpec>,
    #[serde(default)]
    pub triples: Vec<TripleSpec>,
    #[serde(default)]
    pub semantic: Vec<SemanticSpec>,
    /// Optional provider vector for the primary content node.
    #[serde(default)]
    pub embedding: Option<SuppliedVector>,
}

/// `ingest_document` — the upload door, with its blob sidecar.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct IngestFileRequest {
    /// Path to the file being ingested. The engine reads it, hashes it, and
    /// copies it into `knowledge_graph_blobs/<aa>/<sha256><ext>`.
    #[serde(default)]
    pub path: std::path::PathBuf,
    #[serde(default)]
    pub original_filename: Option<String>,
    #[serde(default)]
    pub mime_type: Option<String>,
    #[serde(default)]
    pub uploader: Option<String>,
    #[serde(default)]
    pub conversation_id: Option<String>,
    /// The parser's result. `extracted["content"]` (or `["preview"]`) is the
    /// text; every other key is kept under `metadata.extracted`.
    #[serde(default)]
    pub extracted: Map<String, Value>,
    #[serde(default)]
    pub source_type: Option<String>,
    #[serde(default)]
    pub source_uri: Option<String>,
    #[serde(default)]
    pub captured_at: Option<String>,
    #[serde(default)]
    pub modified_at: Option<String>,
    #[serde(default)]
    pub owner: Option<String>,
    #[serde(default)]
    pub workspace_id: Option<String>,
    #[serde(default)]
    pub permissions: Map<String, Value>,
    /// `_document_structure`'s output — parser compute, so it crosses the seam
    /// as data. Structure *nodes* (slides/pages/sheets/images) are written from
    /// [`IngestFileRequest::structure_nodes`].
    #[serde(default)]
    pub structure: Map<String, Value>,
    /// Pre-resolved structure children. Empty for formats with no structure.
    #[serde(default)]
    pub structure_nodes: Vec<StructureChild>,
    #[serde(default)]
    pub chunks: Vec<ChunkPiece>,
    #[serde(default)]
    pub concepts: Vec<ConceptSpec>,
    #[serde(default)]
    pub triples: Vec<TripleSpec>,
    #[serde(default)]
    pub semantic: Vec<SemanticSpec>,
    /// Optional provider vector for the Document node.
    #[serde(default)]
    pub embedding: Option<SuppliedVector>,
}

/// One slide / page / sheet / image node hung off an ingested document.
///
/// `_ingest_structure_nodes` derives the topics per slide and per page with
/// `_topic_candidates`, which is NLP; the caller supplies them here.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct StructureChild {
    /// `slide` | `page` | `sheet` | `image`.
    pub kind: String,
    /// The structure dict as the parser produced it (stored as metadata).
    #[serde(default)]
    pub payload: Map<String, Value>,
    /// Topic labels for this child (slides and pages only).
    #[serde(default)]
    pub topics: Vec<String>,
}

/// `ingest_message` — one chat turn's graph half.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct IngestMessageRequest {
    #[serde(default)]
    pub role: String,
    #[serde(default)]
    pub content: String,
    #[serde(default)]
    pub user_email: Option<String>,
    #[serde(default)]
    pub user_nickname: Option<String>,
    #[serde(default)]
    pub source: Option<String>,
    #[serde(default)]
    pub conversation_id: Option<String>,
    #[serde(default)]
    pub workspace_id: Option<String>,
    #[serde(default)]
    pub raw: Option<Map<String, Value>>,
    #[serde(default)]
    pub chunks: Vec<ChunkPiece>,
    #[serde(default)]
    pub concepts: Vec<ConceptSpec>,
    #[serde(default)]
    pub triples: Vec<TripleSpec>,
    #[serde(default)]
    pub semantic: Vec<SemanticSpec>,
    /// Optional provider vector for the Message / AIResponse node.
    #[serde(default)]
    pub embedding: Option<SuppliedVector>,
}

/// `ingest_event` — an analytics/system event as a first-class node.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct IngestEventRequest {
    #[serde(default)]
    pub event_type: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub user_email: Option<String>,
    #[serde(default)]
    pub user_nickname: Option<String>,
    #[serde(default)]
    pub source: Option<String>,
    #[serde(default)]
    pub conversation_id: Option<String>,
    #[serde(default)]
    pub workspace_id: Option<String>,
    #[serde(default)]
    pub metadata: Map<String, Value>,
}

/// `record_provenance` — where an ingested node came from.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct IngestionRecord {
    pub node_id: String,
    pub source_type: String,
    #[serde(default = "unified_ingestion")]
    pub pipeline: String,
    #[serde(default)]
    pub source_uri: Option<String>,
    #[serde(default)]
    pub content_hash: Option<String>,
    #[serde(default)]
    pub title: Option<String>,
    #[serde(default)]
    pub owner: Option<String>,
    #[serde(default)]
    pub workspace_id: Option<String>,
    #[serde(default)]
    pub captured_at: Option<String>,
    #[serde(default)]
    pub modified_at: Option<String>,
    #[serde(default)]
    pub embedded: bool,
    #[serde(default)]
    pub linked: bool,
    #[serde(default)]
    pub duplicate: bool,
    #[serde(default)]
    pub agent_used: Option<String>,
    #[serde(default)]
    pub chunk_count: i64,
    #[serde(default)]
    pub permissions: Map<String, Value>,
    #[serde(default)]
    pub metadata: Map<String, Value>,
}

fn unified_ingestion() -> String {
    "unified-ingestion".into()
}

/// `auto_build_graph_overlay`'s result — the curator's decision, computed by
/// the worker and applied here.
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct CuratorOverlay {
    #[serde(default)]
    pub promotions: Vec<PromotionCandidate>,
    #[serde(default)]
    pub skipped: Vec<Value>,
    #[serde(default)]
    pub candidates_total: i64,
    #[serde(default)]
    pub clustered_total: i64,
}

/// One would-be `Topic` node.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct PromotionCandidate {
    /// Present on a queued (reviewed) promotion; derived from the label on a
    /// fresh one.
    #[serde(default)]
    pub id: Option<String>,
    pub label: String,
    pub importance: f64,
    #[serde(default)]
    pub aliases: Vec<String>,
    #[serde(default)]
    pub sources: Vec<String>,
    /// Stamped when the promotion is parked for review.
    #[serde(default)]
    pub proposed_at: Option<String>,
}

/// `curate` — gated topic promotion, written or parked.
#[derive(Debug, Clone, Deserialize)]
pub struct CurateRequest {
    #[serde(default = "two_hundred")]
    pub max_documents: i64,
    #[serde(default = "eight")]
    pub max_new_nodes: i64,
    /// `None` falls back to `LATTICEAI_GRAPH_PROMOTION_REVIEW`.
    #[serde(default)]
    pub review_mode: Option<bool>,
    /// The worker's overlay for this store state.
    #[serde(default)]
    pub overlay: CuratorOverlay,
}

fn two_hundred() -> i64 {
    200
}
fn eight() -> i64 {
    8
}

/// `curate_noise` — the destructive one, dry-run by default.
#[derive(Debug, Clone, Deserialize)]
pub struct CurateNoiseRequest {
    #[serde(default = "yes")]
    pub dry_run: bool,
    #[serde(default = "point_eight")]
    pub max_df_ratio: f64,
    #[serde(default = "one_i64")]
    pub min_doc_frequency: i64,
    #[serde(default = "five")]
    pub min_corpus_docs: i64,
    #[serde(default = "yes")]
    pub normalize_verbs: bool,
    #[serde(default = "two_hundred")]
    pub max_removals: i64,
}

impl Default for CurateNoiseRequest {
    fn default() -> Self {
        Self {
            dry_run: true,
            max_df_ratio: 0.8,
            min_doc_frequency: 1,
            min_corpus_docs: 5,
            normalize_verbs: true,
            max_removals: 200,
        }
    }
}

fn yes() -> bool {
    true
}
fn point_eight() -> f64 {
    0.8
}
fn one_i64() -> i64 {
    1
}
fn five() -> i64 {
    5
}

/// `rebuild_vector_index` — derived index only, never graph content.
#[derive(Debug, Clone, Deserialize)]
pub struct RebuildRequest {
    #[serde(default)]
    pub full: bool,
    #[serde(default = "yes")]
    pub include_nodes: bool,
    #[serde(default = "yes")]
    pub include_chunks: bool,
}

impl Default for RebuildRequest {
    fn default() -> Self {
        Self {
            full: false,
            include_nodes: true,
            include_chunks: true,
        }
    }
}

/// `import_graph_data` — a logical artifact back into the store.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct ImportRequest {
    #[serde(default)]
    pub data: Map<String, Value>,
    #[serde(default = "merge")]
    pub mode: String,
    #[serde(default)]
    pub dry_run: bool,
}

fn merge() -> String {
    "merge".into()
}

/// One item on its way into `vector_embeddings`.
#[derive(Debug, Clone)]
pub struct VectorItem {
    pub item_id: String,
    pub item_type: String,
    pub source_node: String,
    pub text: String,
    pub metadata: Map<String, Value>,
}

/// A vector produced elsewhere — typically `POST /worker/embed` — that the
/// write engine files instead of re-deriving one with the native hash model.
///
/// Empty `model_id` / zero `dim` mean "use this process's embedder identity";
/// the values are still the caller's. The default ingest path never sets this,
/// so the parity goldens stay on the inline-hash door.
#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq)]
pub struct SuppliedVector {
    pub values: Vec<f64>,
    #[serde(default)]
    pub model_id: String,
    #[serde(default)]
    pub dim: usize,
}

/// What `POST /worker/extract` answers — field-for-field the three vectors
/// the ingest doors take as data.
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct ExtractReply {
    #[serde(default)]
    pub concepts: Vec<ConceptSpec>,
    #[serde(default)]
    pub triples: Vec<TripleSpec>,
    #[serde(default)]
    pub semantic: Vec<SemanticSpec>,
}

impl ExtractReply {
    /// Read the three fields out of a JSON object; anything else is empty.
    pub fn from_json(value: &Value) -> Self {
        serde_json::from_value(value.clone()).unwrap_or_default()
    }
}
