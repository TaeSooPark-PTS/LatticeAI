//! Mounted local-files + browser routes vs the knowledge_search fragment.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
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

#[test]
fn mounted_local_and_browser_routes_are_in_the_committed_contract() {
    let spec = fragment();
    let expected: Vec<String> = spec["operation_order"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect();
    let mut ours: Vec<String> = lattice_ingest::local_files_api::MOUNTED
        .iter()
        .chain(lattice_ingest::browser_api::MOUNTED.iter())
        .map(|(m, p)| format!("{m} {}", to_openapi(p)))
        .collect();
    ours.sort();
    for key in &ours {
        if key.ends_with("/upload/document") {
            // W3b native; spec stays in worker_keep.json.
            continue;
        }
        assert!(
            expected.contains(key),
            "{key} is not in knowledge_search.json"
        );
    }
    assert_eq!(lattice_ingest::local_files_api::MOUNTED.len(), 25);
    assert_eq!(lattice_ingest::browser_api::MOUNTED.len(), 2);
    assert!(!ours.iter().any(|k| k.contains("/api/ingestion/multimodal")));
}

#[test]
fn greedy_path_params_do_not_apply_to_this_slice() {
    let spec = fragment();
    let ours: Vec<String> = lattice_ingest::local_files_api::MOUNTED
        .iter()
        .chain(lattice_ingest::browser_api::MOUNTED.iter())
        .map(|(m, p)| format!("{m} {}", to_openapi(p)))
        .collect();
    for (key, _) in spec["greedy_path_params"].as_object().unwrap() {
        assert!(
            !ours.contains(&key.to_string()),
            "{key} is not this crate's to wildcard"
        );
    }
}
