//! Mounted workspace-family routes == `rust/fixtures/openapi/workspace.json`.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
mod workspace_support;

use workspace_support::{openapi_fragment, to_openapi};

#[test]
fn mounted_routes_match_the_committed_contract() {
    let spec = openapi_fragment("workspace.json");
    let mut expected: Vec<String> = spec["operation_order"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect();
    let mut actual: Vec<String> = Vec::new();
    for table in [
        lattice_platform::invitations::MOUNTED,
        lattice_platform::permissions::MOUNTED,
        lattice_platform::workspace::MOUNTED,
    ] {
        for (method, path) in table {
            actual.push(format!("{method} {}", to_openapi(path)));
        }
    }
    expected.sort();
    actual.sort();
    assert_eq!(
        actual, expected,
        "router and rust/fixtures/openapi/workspace.json disagree"
    );

    for (key, param) in spec["greedy_path_params"].as_object().unwrap() {
        let path = key.split_once(' ').unwrap().1;
        let param = param.as_str().unwrap();
        assert!(
            lattice_platform::workspace::MOUNTED
                .iter()
                .any(|(_, p)| { to_openapi(p) == path && p.contains(&format!("*{param}")) }),
            "{key} matches slashes in Python; mount it as /*{param}"
        );
    }
}
