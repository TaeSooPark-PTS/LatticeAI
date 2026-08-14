//! Mounted routes == `models_misc.json`, and worker_tools paths stay unclaimed.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
#[path = "models_catalog_support.rs"]
mod support;

use lattice_platform::models_catalog::{self, keep_worker_paths, MOUNTED as CATALOG};
use lattice_platform::permission_mode::MOUNTED as PERMISSION;
use lattice_platform::workflow_designer::MOUNTED as WORKFLOW;

#[test]
fn mounted_routes_match_the_committed_contract() {
    let spec = support::openapi_fragment("models_misc.json");
    let mut expected: Vec<String> = spec["operation_order"]
        .as_array()
        .expect("operation_order")
        .iter()
        .map(|value| value.as_str().unwrap().to_string())
        .collect();
    let mut actual: Vec<String> = PERMISSION
        .iter()
        .chain(CATALOG.iter())
        .chain(WORKFLOW.iter())
        .map(|(method, path)| format!("{method} {}", support::to_openapi(path)))
        .collect();
    expected.sort();
    actual.sort();
    assert_eq!(
        actual, expected,
        "router and rust/fixtures/openapi/models_misc.json disagree"
    );

    let greedy = spec["greedy_path_params"].as_object().expect("greedy");
    for (key, param) in greedy {
        let path = key.split_once(' ').unwrap().1;
        let param = param.as_str().unwrap();
        assert!(
            PERMISSION
                .iter()
                .chain(CATALOG.iter())
                .chain(WORKFLOW.iter())
                .any(|(_, mounted)| {
                    support::to_openapi(mounted) == path && mounted.contains(&format!("*{param}"))
                }),
            "{key} matches slashes in Python; mount it as /*{param}"
        );
    }
}

#[test]
fn worker_keep_and_worker_tools_routes_are_not_mounted() {
    let worker_tools = support::fixture("worker_tools.json");
    let mut forbidden = std::collections::BTreeSet::new();
    for row in worker_tools["fixtures"].as_array().expect("fixtures") {
        forbidden.insert(format!(
            "{} {}",
            row["method"].as_str().unwrap(),
            row["path"].as_str().unwrap()
        ));
    }
    for (method, path) in keep_worker_paths() {
        forbidden.insert(format!("{method} {path}"));
    }
    forbidden.insert("GET /workflows".into());

    let mounted: Vec<String> = PERMISSION
        .iter()
        .chain(CATALOG.iter())
        .chain(WORKFLOW.iter())
        .map(|(method, path)| format!("{method} {path}"))
        .collect();
    for key in &forbidden {
        assert!(
            !mounted.iter().any(|row| row == key),
            "R10 must not claim worker/static route {key}"
        );
    }
    assert!(
        !mounted.iter().any(|row| row == "GET /models"),
        "GET /models is KEEP_WORKER"
    );
    assert!(
        !mounted.iter().any(|row| row.contains("prepare-model")),
        "prepare-model is KEEP_WORKER"
    );
    let _ = models_catalog::CLOUD_VERIFY_TTL_SECONDS;
}
