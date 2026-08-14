//! Knowledge-graph route table vs the knowledge_search fragment.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
#[test]
fn the_kg_routes_include_native_ingest_whose_spec_stays_in_worker_keep() {
    // W3b: POST /knowledge-graph/ingest is served natively. Its OpenAPI
    // operation still lives in worker_keep.json so fragment byte-composition
    // stays identical — do not move the op between fragments.
    assert_eq!(lattice_retrieval::knowledge_graph_api::MOUNTED.len(), 15);
    assert!(lattice_retrieval::knowledge_graph_api::MOUNTED
        .contains(&("POST", "/knowledge-graph/ingest")));
    assert!(lattice_retrieval::knowledge_graph_api::MOUNTED
        .iter()
        .any(|(_, path)| *path == "/knowledge-graph/neighbors/*node_id"));
}
