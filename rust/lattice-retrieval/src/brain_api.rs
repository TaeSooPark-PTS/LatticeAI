//! `latticeai/api/brain_intelligence.py` — health, insights, garden, quality.
//!
//! Thirteen routes over `BrainIntelligenceService`. Reads are native against
//! the same graph the Memory family already samples. Writes that stage review
//! proposals land in Workspace OS state (RUST_PLATFORM), through [`desk`] —
//! the one writer of `review_items` in this crate. The one graph stamp
//! (`resolve_contradiction`) is native since v11.7.0
//! (`GraphWriter::stamp_node_validity`).

pub mod consistency;
pub mod desk;
pub mod digest;
pub mod health;
pub mod proactive;
pub mod proposals;
pub mod pyutil;
pub mod quality;
pub mod routes;
pub mod sampling;

pub use routes::{router, MOUNTED};
