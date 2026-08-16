//! `latticeai/api/memory.py` — the Memory Manager and the Self-Model, natively.
//!
//! Sixteen routes over `MemoryService` (`latticeai/services/memory_service/*`)
//! and `SelfModelService`. The service reads six tiers from three real stores —
//! Workspace OS state, the durable conversation table, and the knowledge graph
//! — and this package reads exactly those three, in place, so a live install
//! migrates rather than starting over (WAVE2_COMMON rule 10).
//!
//! This module is also where the six brain families keep what they share:
//! [`shared`] (the state handle, the guards and every refusal shape),
//! [`wsos`] (Workspace OS state) and [`kg`] (the knowledge-graph reads).
//! Splitting them out is not decoration — `scripts/check_max_file_lines.mjs`
//! caps every `.rs` file at 1,000 lines, and one family's worth of ported
//! service logic already fills a file on its own.
//!
//! **Writes.** `prune` / `compact` / `clear` delete Workspace OS memories,
//! which is RUST_PLATFORM state and therefore native. `rebuild` and every
//! Self-Model write touch `nodes`/`edges`/`vector_embeddings`, and those are
//! native too since v11.7.0: `rebuild` through
//! [`graph_native::dispatch`] onto [`lattice_core::graph_write::GraphWriter`],
//! the Self-Model's four through [`self_model_write`], which owns the write
//! side of `lattice_brain/self_model.py` because a proposal is a review item
//! as well as a node.

pub mod brief;
pub mod graph_native;
pub mod kg;
#[cfg(test)]
mod kg_tests;
pub mod recall;
pub mod routes;
pub mod self_model;
pub mod self_model_write;
pub mod service;
pub mod shared;
pub mod wsos;

pub use routes::{router, MOUNTED};
pub use shared::BrainState;
