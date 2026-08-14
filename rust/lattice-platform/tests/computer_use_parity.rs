//! Fixture replay + OpenAPI contract for the computer-use surface (WP-R9).

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
#[path = "portability_harness.rs"]
mod harness;

use harness::{assert_mounted_matches_fragment, load_fixture, openapi_fragment, Install};
use lattice_platform::computer_use::MOUNTED;

#[tokio::test]
async fn computer_use_fixtures_replay() {
    let install = Install::start().await;
    let fixture = load_fixture("tools_misc.json");
    harness::replay_family(&install, &fixture, "computer_use.py").await;
}

#[test]
fn computer_use_routes_match_the_committed_contract() {
    let spec = openapi_fragment("portability_network.json");
    assert_mounted_matches_fragment(MOUNTED, &spec, |op| {
        op.contains("/cu/")
            || op.contains("/tools/chrome_status")
            || op.contains("/tools/computer_use_status")
    });
}
