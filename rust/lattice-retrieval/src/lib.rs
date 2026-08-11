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

//! v11.5.0 (Phase 2b/3b of `docs/v11.5.0_RUST_COMPLETE_PLAN.md`) widens the
//! crate past search: the knowledge graph's relationship and traversal reads
//! ([`graph_reads`]), the service layer that composes three channels into one
//! answer ([`service`], [`service_hybrid`]), the durable conversation-history
//! reads ([`history`]), the budgeted context assembler ([`context`]), and the
//! loopback [`routes`] that expose all of it over HTTP. Same proof, same
//! goldens, same two-sided contract test.

pub mod concepts;
pub mod context;
pub mod graph_reads;
pub mod history;
pub mod hybrid;
pub mod keyword;
pub mod policy;
pub mod routes;
pub mod service;
pub mod service_hybrid;
pub mod shape;
pub mod vector;

pub use concepts::topic_candidates;
pub use context::{assemble_context, ContextRequest};
pub use graph_reads::{relationship_search, traverse, RelationshipQuery, TraverseOptions};
pub use history::{history, HistoryScope};
pub use hybrid::{hybrid_search, HybridOptions};
pub use keyword::search;
pub use policy::{class_weights, classify_query, resolve_policy, rewrite_query, Policy};
pub use routes::router;
pub use service::{graph_search, GraphSearchOptions};
pub use service_hybrid::{service_hybrid_search, ServiceHybridOptions};
pub use vector::vector_search;
