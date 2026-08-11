//! `lattice-core` — the Brain's read layer and embedding arithmetic in Rust.
//!
//! Phase 1 of `docs/v11.4.0_RUST_FOUNDATION_PLAN.md`. Everything here is a 1:1
//! port of a named Python original; where the two runtimes could legitimately
//! differ (rounding, float accumulation order, tokenizer boundaries) the Python
//! behaviour is the contract and the Rust side is bent to match it, never the
//! other way round. The parity harness in `lattice-retrieval` is what proves it.

pub mod db;
pub mod embeddings;
pub mod paths;
pub mod pytext;
pub mod read;

pub use db::{open_read_only, CoreError};
pub use embeddings::LocalEmbeddingModel;
pub use paths::{data_dir, graph_db_path, resolve_data_dir, DB_FILE_NAME};
pub use pytext::{clean_text, parse_iso, recency_score, round6, safe_loads, truncate_chars};
pub use read::{column_json, filter_scoped_nodes, sql_json, workspaces_of, NodeRow, VectorRow};
