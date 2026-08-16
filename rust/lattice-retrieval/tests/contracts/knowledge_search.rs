//! Mounted (method, path) set vs `rust/fixtures/openapi/knowledge_search.json`.
//!
//! The family is split across three crates. This test owns the retrieval
//! slice and pins the rest of the fragment as sibling routes so the union
//! cannot drift without a failing assertion.

use serde_json::Value;
use std::path::PathBuf;

fn fragment() -> Value {
    let path: PathBuf = [
        env!("CARGO_MANIFEST_DIR"),
        "..",
        "fixtures",
        "openapi",
        "knowledge_search.json",
    ]
    .iter()
    .collect();
    serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap()
}

fn to_openapi(path: &str) -> String {
    path.split('/')
        .map(
            |seg| match seg.strip_prefix(':').or_else(|| seg.strip_prefix('*')) {
                Some(name) => format!("{{{name}}}"),
                None => seg.to_string(),
            },
        )
        .collect::<Vec<_>>()
        .join("/")
}

/// Routes this crate does *not* mount; they live in lattice-jobs / lattice-ingest.
const SIBLING: &[(&str, &str)] = &[
    ("POST", "/api/browser/ingest-current-tab"),
    ("POST", "/api/browser/read-url"),
    ("GET", "/api/index/queue"),
    ("GET", "/api/index/status"),
    ("POST", "/api/ingestion/folder"),
    ("GET", "/api/ingestion/interop"),
    ("POST", "/api/ingestion/interop"),
    ("GET", "/api/ingestion/jobs"),
    ("GET", "/api/ingestion/jobs/{job_id}"),
    ("POST", "/api/ingestion/jobs/{job_id}/resume"),
    ("POST", "/api/ingestion/obsidian"),
    ("DELETE", "/api/ingestion/watch"),
    ("GET", "/api/ingestion/watch"),
    ("POST", "/api/ingestion/watch"),
    ("GET", "/api/local-agent/status"),
    ("POST", "/knowledge-graph/local/audit"),
    ("GET", "/knowledge-graph/local/health"),
    ("POST", "/knowledge-graph/local/index"),
    ("GET", "/knowledge-graph/local/roots"),
    ("GET", "/knowledge-graph/local/sources"),
    ("POST", "/knowledge-graph/local/tree"),
    ("GET", "/knowledge-graph/local/watch/status"),
    ("POST", "/knowledge-graph/local/watch/stop"),
    ("GET", "/local/list"),
    ("POST", "/local/list"),
    ("POST", "/local/read"),
    ("GET", "/local/serve"),
    ("POST", "/local/write"),
];

#[test]
fn mounted_routes_match_the_committed_contract() {
    let spec = fragment();
    let mut expected: Vec<String> = spec["operation_order"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect();

    let mut actual: Vec<String> = lattice_retrieval::search_api::MOUNTED
        .iter()
        .chain(lattice_retrieval::knowledge_graph_api::MOUNTED.iter())
        // Native (W3b) but the spec stays in worker_keep.json.
        .filter(|(_, path)| *path != "/knowledge-graph/ingest")
        .map(|(m, p)| format!("{m} {}", to_openapi(p)))
        .chain(SIBLING.iter().map(|(m, p)| format!("{m} {p}")))
        .collect();

    expected.sort();
    actual.sort();
    assert_eq!(
        actual, expected,
        "router and rust/fixtures/openapi/knowledge_search.json disagree"
    );

    for (key, param) in spec["greedy_path_params"].as_object().unwrap() {
        let path = key.split_once(' ').unwrap().1;
        assert!(
            lattice_retrieval::knowledge_graph_api::MOUNTED
                .iter()
                .any(|(_, p)| {
                    to_openapi(p) == path && p.contains(&format!("*{}", param.as_str().unwrap()))
                }),
            "{key} matches slashes in Python; mount it as /*{param}"
        );
    }
}
