//! Replay `setup.py` records from `platform_misc.json`.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
#[path = "admin_support/mod.rs"]
mod admin_support;

use admin_support::*;
use axum::Router;
use lattice_platform::setup::{router as setup_router, DemoStore, SetupState};

#[tokio::test(flavor = "multi_thread")]
async fn setup_fixtures_replay() {
    let fixture = load_fixture("platform_misc.json");
    let mut checked = 0usize;
    for case in fixture["fixtures"].as_array().unwrap() {
        if case["family"] != "setup.py" {
            continue;
        }
        replay_one(case).await;
        checked += 1;
    }
    assert!(checked >= 20, "lost setup cases: {checked}");
}

async fn replay_one(case: &serde_json::Value) {
    let name = format!("{}/{}", case["name"], case["branch"]);
    let dir = tempfile::tempdir().unwrap();
    let (auth, owner, member) = install_auth(dir.path(), true);
    let mut state = SetupState::new(auth, dir.path());
    let needs_corpus = matches!(
        (
            case["name"].as_str().unwrap_or(""),
            case["branch"].as_str().unwrap_or("")
        ),
        ("demo_corpus_status", "happy_installed")
            | ("demo_corpus_install", "happy_idempotent")
            | ("demo_corpus_remove", "happy")
    );
    if needs_corpus {
        let store = state.demo.clone().unwrap_or_else(DemoStore::new);
        for (id, title, _) in lattice_platform::setup::demo_documents() {
            store.ingest(id, title, Some("personal"));
        }
        state.demo = Some(store);
    }
    let app: Router = setup_router(state);
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
