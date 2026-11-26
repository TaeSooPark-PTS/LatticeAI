pub mod bm25_stats;
pub mod db;
pub mod embedding_contract;
pub mod embedding_registry;
pub mod embeddings;
pub mod entity_resolution;
pub mod entity_resolution_ext;
pub mod graph_write;
pub mod integrity;
pub mod learning_loop;
pub mod messages;
pub mod paths;
pub mod pytext;
pub mod read;
pub mod redact;
pub mod temporal;
pub mod temporal_state;
pub mod tokens;
pub mod worker;

pub use db::{open_read_only, CoreError};
pub use embeddings::{
    EmbeddingIndexContract, FallbackTextEmbedder, LocalEmbeddingModel, DEFAULT_FALLBACK_DIM,
};
pub use graph_write::pyaux::{sha256_bytes, sha256_text};
pub use graph_write::{
    read_lifecycle_from_metadata, IngestStatus, KnowledgeLifecycleMetadata,
    KnowledgeLifecycleState, KnowledgeLifecycleUpdate, LifecycleHistoryEntry,
};
pub use paths::{data_dir, graph_db_path, resolve_data_dir, DB_FILE_NAME};
pub use pytext::{clean_text, parse_iso, recency_score, round6, safe_loads, truncate_chars};
pub use read::{
    column_json, filter_scoped_nodes, scoped_workspace_visible, sql_json, workspace_is_default,
    workspace_membership_sql, workspaces_of, NodeRow, VectorRow, DEFAULT_WORKSPACE_ID,
};
pub use redact::{
    detect_sensitive_findings, is_secret_key, redact_pii_and_secrets, redact_pii_and_secrets_value,
    redact_pii_text, redact_secret_text, redact_secrets, redact_structure, SensitiveFinding,
    PII_REDACTION, REDACTION,
};
