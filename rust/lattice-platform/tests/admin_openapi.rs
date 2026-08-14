//! Mounted route set == `rust/fixtures/openapi/admin.json` (WP-I5).

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
#[path = "admin_support/mod.rs"]
mod admin_support;

use admin_support::{openapi_fragment, to_openapi};
use lattice_platform::admin::family_mounted;

#[test]
fn mounted_routes_match_the_committed_contract() {
    let spec = openapi_fragment("admin.json");
    let mut expected: Vec<String> = spec["operation_order"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect();
    let mut actual: Vec<String> = family_mounted()
        .iter()
        .map(|(m, p)| format!("{m} {}", to_openapi(p)))
        .collect();
    expected.sort();
    actual.sort();
    assert_eq!(
        actual, expected,
        "router and rust/fixtures/openapi/admin.json disagree"
    );

    for (key, param) in spec["greedy_path_params"].as_object().unwrap() {
        let path = key.split_once(' ').unwrap().1;
        assert!(
            family_mounted().iter().any(|(_, p)| {
                to_openapi(p) == path && p.contains(&format!("*{}", param.as_str().unwrap()))
            }),
            "{key} matches slashes in Python; mount it as /*{param}"
        );
    }
}
