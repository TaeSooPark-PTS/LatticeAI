//! Mounted index routes vs the knowledge_search fragment.

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
fn mounted_index_routes_are_in_the_committed_contract() {
    let spec = fragment();
    let expected: Vec<String> = spec["operation_order"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect();
    for (method, path) in lattice_jobs::index_api::MOUNTED {
        // W3b: POST drain/rebuild are native product routes. Their spec stays
        // in worker_keep.json so fragment byte-composition stays identical.
        if *path == "/api/index/drain" || *path == "/api/index/rebuild" {
            continue;
        }
        let key = format!("{method} {}", to_openapi(path));
        assert!(
            expected.contains(&key),
            "{key} is not in knowledge_search.json"
        );
    }
    // GET queue/status live in knowledge_search.json. POST drain/rebuild are
    // now native (W3b) but their spec stays in worker_keep.json so fragment
    // byte-composition stays identical.
    let native: Vec<_> = lattice_jobs::index_api::MOUNTED
        .iter()
        .copied()
        .filter(|(_, p)| expected.iter().any(|key| key.ends_with(*p)))
        .collect();
    assert_eq!(native.len(), 2);
    assert!(lattice_jobs::index_api::MOUNTED.contains(&("POST", "/api/index/drain")));
    assert!(lattice_jobs::index_api::MOUNTED.contains(&("POST", "/api/index/rebuild")));
}
