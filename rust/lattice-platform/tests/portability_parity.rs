//! Fixture replay + OpenAPI contract for the portability family (WP-R9).

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
#[path = "portability_harness.rs"]
mod harness;

use harness::{
    assert_mounted_matches_fragment, family_cases, load_fixture, openapi_fragment, Install,
};
use lattice_platform::portability::MOUNTED;

#[tokio::test]
async fn portability_fixtures_replay() {
    let install = Install::start().await;
    let fixture = load_fixture("tools_misc.json");
    harness::replay_family(&install, &fixture, "portability.py").await;
    assert!(
        family_cases(&fixture, "portability.py").len() >= 50,
        "expected the captured portability surface"
    );
}

#[test]
fn portability_routes_match_the_committed_contract() {
    let spec = openapi_fragment("portability_network.json");
    assert_mounted_matches_fragment(MOUNTED, &spec, |op| {
        op.contains("/api/knowledge-graph/") || op.contains("/api/brain/")
    });
}
