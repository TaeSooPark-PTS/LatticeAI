#[test]
fn the_kg_routes_include_native_ingest_whose_spec_stays_in_worker_keep() {
    assert_eq!(lattice_retrieval::knowledge_graph_api::MOUNTED.len(), 15);
    assert!(lattice_retrieval::knowledge_graph_api::MOUNTED
        .contains(&("POST", "/knowledge-graph/ingest")));
    assert!(lattice_retrieval::knowledge_graph_api::MOUNTED
        .iter()
        .any(|(_, path)| *path == "/knowledge-graph/neighbors/*node_id"));
}
