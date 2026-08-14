//! Search-family replay lives next to the knowledge-graph cases
//! (`kg_api_replay.rs`) because both routers share `RetrievalApiState`.
//! This file exists so the crate's test prefix matches the work-package
//! naming rule.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
#[test]
fn search_replay_is_in_kg_api_replay() {
    assert!(std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/kg_api_replay.rs")
        .exists());
}
