#[allow(dead_code)]
#[path = "portability_harness/mod.rs"]
mod harness;

use harness::{
    assert_mounted_matches_fragment, family_cases, load_fixture, openapi_fragment, Install,
};
use lattice_platform::knowledge::network::MOUNTED as NETWORK_MOUNTED;
use lattice_platform::knowledge::network_boundary::MOUNTED as NETWORK_BOUNDARY_MOUNTED;
use lattice_platform::knowledge::portability::MOUNTED as PORTABILITY_MOUNTED;
use lattice_platform::knowledge::voice::{KEEP as VOICE_KEEP, MOUNTED as VOICE_MOUNTED};
use lattice_platform::toolsurface::computer_use::MOUNTED as COMPUTER_USE_MOUNTED;
use lattice_platform::workspaceos::project_sessions::MOUNTED as PROJECT_SESSIONS_MOUNTED;
use lattice_platform::workspaceos::realtime::MOUNTED as REALTIME_MOUNTED;

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
    assert_mounted_matches_fragment(PORTABILITY_MOUNTED, &spec, |op| {
        op.contains("/api/knowledge-graph/") || op.contains("/api/brain/")
    });
}

#[tokio::test]
async fn network_fixtures_replay() {
    let install = Install::start().await;
    let fixture = load_fixture("platform_misc.json");
    harness::replay_family(&install, &fixture, "network.py").await;
}

#[test]
fn network_routes_match_the_committed_contract() {
    let spec = openapi_fragment("portability_network.json");
    assert_mounted_matches_fragment(NETWORK_MOUNTED, &spec, |op| op.contains("/network/"));
}

#[tokio::test]
async fn network_boundary_fixtures_replay() {
    let install = Install::start().await;
    let fixture = load_fixture("platform_misc.json");
    harness::replay_family(&install, &fixture, "network_boundary.py").await;
}

#[test]
fn network_boundary_routes_match_the_committed_contract() {
    let spec = openapi_fragment("portability_network.json");
    assert_mounted_matches_fragment(NETWORK_BOUNDARY_MOUNTED, &spec, |op| {
        op.contains("/api/network-boundary")
    });
}

#[tokio::test]
async fn computer_use_fixtures_replay() {
    let install = Install::start().await;
    let fixture = load_fixture("tools_misc.json");
    harness::replay_family(&install, &fixture, "computer_use.py").await;
}

#[test]
fn computer_use_routes_match_the_committed_contract() {
    let spec = openapi_fragment("portability_network.json");
    assert_mounted_matches_fragment(COMPUTER_USE_MOUNTED, &spec, |op| {
        op.contains("/cu/")
            || op.contains("/tools/chrome_status")
            || op.contains("/tools/computer_use_status")
    });
}

#[tokio::test]
async fn project_sessions_fixtures_replay() {
    let install = Install::start().await;
    let fixture = load_fixture("tools_misc.json");
    let client = reqwest::Client::builder()
        .no_proxy()
        .timeout(std::time::Duration::from_secs(10))
        .build()
        .unwrap();
    let seeded = client
        .post(format!("{}/api/projects", install.origin))
        .header("host", "127.0.0.1:4825")
        .header("origin", "http://127.0.0.1:4825")
        .header("cookie", &install.owner_cookie)
        .header("content-type", "application/json")
        .body(r#"{"title":"Fixture project","goal":"Capture the project-session contract."}"#)
        .send()
        .await
        .unwrap();
    assert_eq!(seeded.status().as_u16(), 200);
    let created: serde_json::Value = seeded.json().await.unwrap();
    let mut symbols = std::collections::HashMap::new();
    if let Some(id) = created.get("id").and_then(|v| v.as_str()) {
        symbols.insert("$project_id".into(), id.to_string());
    }
    harness::replay_family_with(&install, &fixture, "project_sessions.py", &mut symbols).await;
}

#[test]
fn project_sessions_routes_match_the_committed_contract() {
    let spec = openapi_fragment("portability_network.json");
    assert_mounted_matches_fragment(PROJECT_SESSIONS_MOUNTED, &spec, |op| {
        op.contains("/api/projects")
    });
}

#[tokio::test]
async fn realtime_fixtures_replay() {
    let install = Install::start().await;
    let fixture = load_fixture("tools_misc.json");
    harness::replay_family(&install, &fixture, "realtime.py").await;
}

#[test]
fn realtime_routes_match_the_committed_contract() {
    let spec = openapi_fragment("portability_network.json");
    assert_mounted_matches_fragment(REALTIME_MOUNTED, &spec, |op| op.contains("/realtime/"));
}

#[test]
fn activity_page_is_not_claimed() {
    assert!(
        !REALTIME_MOUNTED.iter().any(|(_, p)| *p == "/activity"),
        "/activity is STATIC and belongs to ui_redirects"
    );
}

#[test]
fn voice_router_claims_the_whole_surviving_family() {
    assert_eq!(VOICE_MOUNTED, &[("POST", "/api/capture/voice")]);
    assert!(
        VOICE_KEEP.is_empty(),
        "nothing in this family is answered by the worker any more"
    );
    let worker = openapi_fragment("worker_keep.json");
    let ops: Vec<&str> = worker["operation_order"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|v| v.as_str())
        .filter(|op| op.contains("/api/capture/voice"))
        .collect();
    assert!(
        ops.contains(&"POST /api/capture/voice"),
        "the native capture route keeps its committed contract: {ops:?}"
    );
}

#[test]
fn voice_keep_routes_are_not_in_the_r9_fragment() {
    let spec = openapi_fragment("portability_network.json");
    for op in spec["operation_order"].as_array().unwrap() {
        let op = op.as_str().unwrap();
        assert!(
            !op.contains("/api/capture/voice"),
            "KEEP voice route leaked into the R9 fragment: {op}"
        );
    }
}
