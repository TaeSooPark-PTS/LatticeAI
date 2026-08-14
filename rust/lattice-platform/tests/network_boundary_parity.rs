//! Fixture replay + OpenAPI contract for the network-boundary dial (WP-R9).

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
#[path = "portability_harness.rs"]
mod harness;

use harness::{assert_mounted_matches_fragment, load_fixture, openapi_fragment, Install};
use lattice_platform::network_boundary::MOUNTED;

#[tokio::test]
async fn network_boundary_fixtures_replay() {
    let install = Install::start().await;
    let fixture = load_fixture("platform_misc.json");
    harness::replay_family(&install, &fixture, "network_boundary.py").await;
}

#[test]
fn network_boundary_routes_match_the_committed_contract() {
    let spec = openapi_fragment("portability_network.json");
    assert_mounted_matches_fragment(MOUNTED, &spec, |op| op.contains("/api/network-boundary"));
}
