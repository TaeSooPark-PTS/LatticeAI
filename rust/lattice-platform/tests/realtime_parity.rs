//! Fixture replay + OpenAPI contract for realtime presence/feed/SSE (WP-R9).

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
#[path = "portability_harness.rs"]
mod harness;

use harness::{assert_mounted_matches_fragment, load_fixture, openapi_fragment, Install};
use lattice_platform::realtime::MOUNTED;

#[tokio::test]
async fn realtime_fixtures_replay() {
    let install = Install::start().await;
    let fixture = load_fixture("tools_misc.json");
    harness::replay_family(&install, &fixture, "realtime.py").await;
}

#[test]
fn realtime_routes_match_the_committed_contract() {
    let spec = openapi_fragment("portability_network.json");
    assert_mounted_matches_fragment(MOUNTED, &spec, |op| op.contains("/realtime/"));
}

#[test]
fn activity_page_is_not_claimed() {
    assert!(
        !MOUNTED.iter().any(|(_, p)| *p == "/activity"),
        "/activity is STATIC and belongs to ui_redirects"
    );
}
