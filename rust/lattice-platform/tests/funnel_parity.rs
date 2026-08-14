//! Replay `funnel_metrics.py` records from `admin.json`.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
#[path = "admin_support/mod.rs"]
mod admin_support;

use admin_support::*;
use axum::Router;
use lattice_platform::funnel_metrics::{router as funnel_router, FunnelState};

#[tokio::test(flavor = "multi_thread")]
async fn funnel_fixtures_replay() {
    let fixture = load_fixture("admin.json");
    let mut checked = 0usize;
    for case in fixture["fixtures"].as_array().unwrap() {
        if case["family"] != "funnel_metrics.py" {
            continue;
        }
        replay_one(case).await;
        checked += 1;
    }
    for case in fixture["trusted_local_owner_fixtures"].as_array().unwrap() {
        if case["family"] != "funnel_metrics.py" {
            continue;
        }
        replay_one(case).await;
        checked += 1;
    }
    assert!(checked >= 3, "lost funnel cases: {checked}");
}

async fn replay_one(case: &serde_json::Value) {
    let name = format!("{}/{}", case["name"], case["branch"]);
    let dir = tempfile::tempdir().unwrap();
    let (auth, owner, member) = install_auth(dir.path(), true);
    let state = FunnelState::new(auth, dir.path());
    let app: Router = funnel_router(state);
    let (origin, handle) = serve(app).await;
    let answer = issue(
        &origin,
        case["method"].as_str().unwrap(),
        case["path"].as_str().unwrap(),
        &case["query"],
        &case["request_headers"],
        &case["request_body"],
        &owner,
        &member,
    )
    .await;
    handle.abort();
    assert_case(&name, case, &answer);
}
