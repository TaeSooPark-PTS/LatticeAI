use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum IngestStatus {
    #[default]
    Complete,
    Partial,
    Pending,
    Failed,
    ReindexRequired,
}

impl IngestStatus {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Complete => "complete",
            Self::Partial => "partial",
            Self::Pending => "pending",
            Self::Failed => "failed",
            Self::ReindexRequired => "reindex_required",
        }
    }

    pub fn is_degraded(&self) -> bool {
        !matches!(self, Self::Complete)
    }
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct NodeSpec {
    pub id: String,
    pub node_type: String,
    pub title: String,
    #[serde(default)]
    pub summary: String,
    #[serde(default)]
    pub metadata: Map<String, Value>,
    #[serde(default)]
    pub raw: Map<String, Value>,
    #[serde(default)]
    pub owner: Option<String>,
    #[serde(default)]
    pub workspace_id: Option<String>,
    #[serde(default)]
    pub visibility: Option<String>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct EdgeSpec {
    pub from_node: String,
    pub to_node: String,
    pub edge_type: String,
    #[serde(default = "one")]
    pub weight: f64,
    #[serde(default)]
    pub metadata: Map<String, Value>,
    #[serde(default)]
    pub legacy_label: Option<String>,
}

fn one() -> f64 {
    1.0
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct ChunkPiece {
    pub text: String,
    #[serde(default)]
    pub fields: Map<String, Value>,
    #[serde(default)]
    pub embedding: Option<SuppliedVector>,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct ChunkParentPiece {
    pub text: String,
    #[serde(default)]
    pub fields: Map<String, Value>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ConceptSpec {
    pub text: String,
    pub node_type: String,
}

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

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct SemanticSpec {
    pub item_type: String,
    pub title: String,
    #[serde(default)]
    pub summary: String,
    #[serde(default)]
    pub raw: Map<String, Value>,
}

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
    #[serde(default)]
    pub node_type: Option<String>,
    #[serde(default)]
    pub chunks: Vec<ChunkPiece>,
    #[serde(default)]
    pub parents: Vec<ChunkParentPiece>,
    #[serde(default)]
    pub concepts: Vec<ConceptSpec>,
    #[serde(default)]
    pub triples: Vec<TripleSpec>,
    #[serde(default)]
    pub semantic: Vec<SemanticSpec>,
    #[serde(default)]
    pub embedding: Option<SuppliedVector>,
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct IngestFileRequest {
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
    #[serde(default)]
    pub structure: Map<String, Value>,
    #[serde(default)]
    pub structure_nodes: Vec<StructureChild>,
    #[serde(default)]
    pub chunks: Vec<ChunkPiece>,
    #[serde(default)]
    pub parents: Vec<ChunkParentPiece>,
    #[serde(default)]
    pub concepts: Vec<ConceptSpec>,
    #[serde(default)]
    pub triples: Vec<TripleSpec>,
    #[serde(default)]
    pub semantic: Vec<SemanticSpec>,
    #[serde(default)]
    pub embedding: Option<SuppliedVector>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct StructureChild {
    pub kind: String,
    #[serde(default)]
    pub payload: Map<String, Value>,
    #[serde(default)]
    pub topics: Vec<String>,
}

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
    #[serde(default)]
    pub embedding: Option<SuppliedVector>,
}

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

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct PromotionCandidate {
    #[serde(default)]
    pub id: Option<String>,
    pub label: String,
    pub importance: f64,
    #[serde(default)]
    pub aliases: Vec<String>,
    #[serde(default)]
    pub sources: Vec<String>,
    #[serde(default)]
    pub proposed_at: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct CurateRequest {
    #[serde(default = "two_hundred")]
    pub max_documents: i64,
    #[serde(default = "eight")]
    pub max_new_nodes: i64,
    #[serde(default)]
    pub review_mode: Option<bool>,
    #[serde(default)]
    pub overlay: CuratorOverlay,
}

fn two_hundred() -> i64 {
    200
}
fn eight() -> i64 {
    8
}

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

#[derive(Debug, Clone)]
pub struct VectorItem {
    pub item_id: String,
    pub item_type: String,
    pub source_node: String,
    pub text: String,
    pub metadata: Map<String, Value>,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq)]
pub struct SuppliedVector {
    pub values: Vec<f64>,
    #[serde(default)]
    pub model_id: String,
    #[serde(default)]
    pub dim: usize,
}

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
    pub fn from_json(value: &Value) -> Self {
        serde_json::from_value(value.clone()).unwrap_or_default()
    }
}
