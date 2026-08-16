//! Mounted routes == `rust/fixtures/openapi/memory_brain.json`.

use lattice_retrieval::brain_api;
use lattice_retrieval::chronicle_api;
use lattice_retrieval::command_center_api;
use lattice_retrieval::evidence_api;
use lattice_retrieval::garden_api;
use lattice_retrieval::memory_api;
use serde_json::Value;
use std::path::PathBuf;

fn fragment() -> Value {
    let path: PathBuf = [
        env!("CARGO_MANIFEST_DIR"),
        "..",
        "fixtures",
        "openapi",
        "memory_brain.json",
    ]
    .iter()
    .collect();
    serde_json::from_str(&std::fs::read_to_string(&path).expect("fragment")).expect("json")
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

fn all_mounted() -> Vec<(&'static str, &'static str)> {
    let mut out = Vec::new();
    out.extend_from_slice(memory_api::MOUNTED);
    out.extend_from_slice(brain_api::MOUNTED);
    out.extend_from_slice(garden_api::MOUNTED);
    out.extend_from_slice(chronicle_api::MOUNTED);
    out.extend_from_slice(command_center_api::MOUNTED);
    out.extend_from_slice(evidence_api::MOUNTED);
    out
}

#[test]
fn mounted_routes_match_the_committed_contract() {
    let spec = fragment();
    let mut expected: Vec<String> = spec["operation_order"]
        .as_array()
        .expect("operation_order")
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect();
    let mut actual: Vec<String> = all_mounted()
        .iter()
        .map(|(m, p)| format!("{m} {}", to_openapi(p)))
        .collect();
    expected.sort();
    actual.sort();
    assert_eq!(
        actual, expected,
        "router and rust/fixtures/openapi/memory_brain.json disagree"
    );

    for (key, param) in spec["greedy_path_params"].as_object().unwrap() {
        let path = key.split_once(' ').unwrap().1;
        assert!(
            all_mounted().iter().any(|(_, p)| {
                to_openapi(p) == path && p.contains(&format!("*{}", param.as_str().unwrap()))
            }),
            "{key} matches slashes in Python; mount it as /*{param}"
        );
    }
}
