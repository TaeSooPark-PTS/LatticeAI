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
//! Self-Model write touch `nodes`/`edges`/`vector_embeddings`, which belong to
//! the Python single writer, so they are delegated over
//! `POST /worker/graph/mutate` (WAVE2_COMMON rule 6).

#![allow(
    dead_code,
    unused_imports,
    unused_variables,
    unused_assignments,
    unused_mut,
    private_interfaces,
    clippy::result_large_err,
    clippy::needless_lifetimes,
    clippy::too_many_arguments,
    clippy::type_complexity,
    clippy::collapsible_if,
    clippy::needless_as_bytes,
    clippy::redundant_closure,
    clippy::needless_return,
    clippy::manual_clamp,
    clippy::ptr_arg,
    clippy::unnecessary_sort_by,
    clippy::result_unit_err,
    clippy::useless_vec,
    clippy::uninlined_format_args,
    clippy::manual_contains,
    clippy::needless_borrows_for_generic_args,
    clippy::implicit_clone,
    clippy::unnecessary_map_or,
    clippy::match_like_matches_macro,
    clippy::manual_range_contains,
    clippy::derivable_impls,
    clippy::needless_pass_by_ref_mut,
    clippy::redundant_guards,
    clippy::map_identity,
    clippy::iter_overeager_cloned,
    clippy::explicit_auto_deref,
    clippy::bool_comparison,
    clippy::nonminimal_bool,
    clippy::if_same_then_else,
    clippy::question_mark,
    clippy::single_char_pattern,
    clippy::manual_pattern_char_comparison,
    clippy::manual_is_ascii_check,
    clippy::repeat_once,
    clippy::unused_self,
    clippy::module_inception
)]
pub mod brief;
pub mod graph_native;
pub mod kg;
#[cfg(test)]
mod kg_tests;
pub mod recall;
pub mod routes;
pub mod self_model;
pub mod service;
pub mod shared;
pub mod wsos;

pub use routes::{router, MOUNTED};
pub use shared::BrainState;
