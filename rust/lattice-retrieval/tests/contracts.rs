//! The committed OpenAPI fragments, versus the route tables this crate mounts.
//!
//! Three contract checks that were three test binaries. Each keeps its own
//! module — they all spell `fragment()`, `to_openapi()` and
//! `mounted_routes_match_the_committed_contract`, and the module path is what
//! tells them apart in the test list.

#[path = "contracts/knowledge_graph_routes.rs"]
mod knowledge_graph_routes;
#[path = "contracts/knowledge_search.rs"]
mod knowledge_search;
#[path = "contracts/memory_brain.rs"]
mod memory_brain;
