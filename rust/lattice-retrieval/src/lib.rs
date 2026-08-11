//! `lattice-retrieval` — the native hybrid search, proven equal to Python's.
//!
//! Phase 1 of `docs/v11.4.0_RUST_FOUNDATION_PLAN.md` ports the graph-layer
//! engines — `search()` (keyword), `vector_search()` (brute-force cosine) and
//! `hybrid_search()` (two-channel alpha fusion) — in their **default** env
//! configuration: brute backend, RRF off, graph expansion off, image late fusion
//! off, cross-encoder rerank off. Anything outside that envelope stays with the
//! Python worker, which is why the ported functions report the same honesty
//! blocks (`recall`, `index`, `vector.degraded`) rather than quietly narrowing
//! the claim.
//!
//! The proof lives in `tests/parity.rs`: the same committed database, the same
//! queries, and the same golden files that
//! `tests/unit/test_rust_parity_contract.py` re-asserts on the Python side.

pub mod concepts;
pub mod hybrid;
pub mod keyword;
pub mod policy;
pub mod shape;
pub mod vector;

pub use concepts::topic_candidates;
pub use hybrid::{hybrid_search, HybridOptions};
pub use keyword::search;
pub use policy::{classify_query, resolve_policy, rewrite_query, Policy};
pub use vector::vector_search;
