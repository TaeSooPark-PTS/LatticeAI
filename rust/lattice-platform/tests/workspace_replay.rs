//! The workspace family: fixture replay for `workspace.py`, `invitations.py`
//! and `permissions.py`, plus the OpenAPI contract the three compose.
//!
//! Four test binaries collapsed into one — three of them a single
//! `replay_family` call each, all recompiling the same 40kB support module.

// The shared harness is written for every suite that includes it, so a
// helper this one does not call still reads as dead in this binary.
#[allow(dead_code)]
mod workspace_support;

use workspace_support::{
    load_http, openapi_fragment, to_openapi, Install, ADMIN_FIXTURE, WORKSPACE_FIXTURE,
};

#[tokio::test]
async fn workspace_replays_the_python_oracle() {
    let install = Install::start().await;
    let doc = load_http(WORKSPACE_FIXTURE);
    install.replay_family(&doc, "workspace.py").await;
}

#[tokio::test]
async fn invitations_replay_the_python_oracle() {
    let install = Install::start().await;
    let doc = load_http(WORKSPACE_FIXTURE);
    install.replay_family(&doc, "invitations.py").await;
}

#[tokio::test]
async fn permissions_replay_the_python_oracle() {
    let install = Install::start().await;
    let doc = load_http(ADMIN_FIXTURE);
    install.replay_family(&doc, "permissions.py").await;
}

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
