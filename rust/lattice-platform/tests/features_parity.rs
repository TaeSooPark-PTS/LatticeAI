//! Replay `features.py` records from `platform_misc.json`.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
#[path = "admin_support/mod.rs"]
mod admin_support;

use admin_support::*;
use axum::Router;
use lattice_platform::features::{router as features_router, FeaturesState};

#[tokio::test(flavor = "multi_thread")]
async fn features_fixtures_replay() {
    std::env::set_var("LATTICEAI_VECTOR_INDEX", "brute");
    let fixture = load_fixture("platform_misc.json");
    let mut checked = 0usize;
    for case in fixture["fixtures"].as_array().unwrap() {
        if case["family"] != "features.py" {
            continue;
        }
        replay_one(case).await;
        checked += 1;
    }
    for case in fixture["trusted_local_owner_fixtures"].as_array().unwrap() {
        if case["family"] != "features.py" {
            continue;
        }
        replay_one(case).await;
        checked += 1;
    }
    assert!(checked >= 10, "lost feature cases: {checked}");
}

async fn replay_one(case: &serde_json::Value) {
    let name = format!("{}/{}", case["name"], case["branch"]);
    let dir = tempfile::tempdir().unwrap();
    let (auth, owner, member) = install_auth(dir.path(), true);
    let state = FeaturesState::new(auth, dir.path());
    let app: Router = features_router(state);
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
