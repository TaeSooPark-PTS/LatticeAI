//! Fixture replay + OpenAPI contract for project sessions (WP-R9).

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
#[path = "portability_harness.rs"]
mod harness;

use harness::{assert_mounted_matches_fragment, load_fixture, openapi_fragment, Install};
use lattice_platform::project_sessions::MOUNTED;

#[tokio::test]
async fn project_sessions_fixtures_replay() {
    let install = Install::start().await;
    // Seed the project the list/get/update fixtures read, then replay in order
    // so `$project_id` / `$project_created` bind from the live answers.
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
    assert_mounted_matches_fragment(MOUNTED, &spec, |op| op.contains("/api/projects"));
}
