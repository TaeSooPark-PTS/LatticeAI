//! Replay `admin.py` records from `rust/fixtures/http/admin.json`.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
#[path = "admin_support/mod.rs"]
mod admin_support;

use admin_support::*;
use axum::Router;
use lattice_platform::admin::{
    default_product_hardening_probed, router as admin_router, AdminState,
};
use serde_json::json;

fn graph_stats() -> serde_json::Value {
    json!({
        "db_path": "/tmp/kg.sqlite",
        "schema_version": 1,
        "v2_schema_available": true,
        "nodes": {"Conversation": 1, "Memory": 1, "Person": 1, "Workflow": 1},
        "edges": {"HAS_EVENT": 2, "TRIGGERED": 2},
        "local_sources": 0,
        "local_file_status": {},
        "v2": {
            "schema_version": 2,
            "embed_dim": 384,
            "nodes": 4,
            "edges": 4,
            "by_node_type": {"CONCEPT": 1, "CONVERSATION": 1, "PERSON": 1, "WORKFLOW": 1},
            "by_edge_type": {"HAS_EVENT": 2, "TRIGGERED": 2}
        },
        "total_nodes": 0,
        "total_edges": 0
    })
}

#[tokio::test(flavor = "multi_thread")]
async fn admin_fixtures_replay() {
    let fixture = load_fixture("admin.json");
    let mut checked = 0usize;
    for case in fixture["fixtures"].as_array().unwrap() {
        if case["family"] != "admin.py" {
            continue;
        }
        replay_one(case, true).await;
        checked += 1;
    }
    for case in fixture["trusted_local_owner_fixtures"].as_array().unwrap() {
        if case["family"] != "admin.py" {
            continue;
        }
        replay_one(case, true).await;
        checked += 1;
    }
    assert!(checked >= 60, "lost admin cases: {checked}");
}

async fn replay_one(case: &serde_json::Value, require_auth: bool) {
    let name = format!("{}/{}", case["name"], case["branch"]);
    let dir = tempfile::tempdir().unwrap();
    seed_chat_history(dir.path());
    if case["name"] == "admin_audit" || case["name"] == "admin_log_retention" {
        seed_audit(dir.path(), &eight_audit_events());
    } else {
        seed_audit_base(dir.path());
    }
    let (auth, owner, member) = install_auth(dir.path(), require_auth);
    let hardening_dir = dir.path().to_path_buf();
    let hardening_host = auth.config().host.clone();
    let hardening_port = auth.config().port;
    let hardening_auth = auth.config().require_auth;
    let mut state = AdminState::new(auth, dir.path());
    state.graph_stats = std::sync::Arc::new(|| Ok(graph_stats()));
    // `/admin/product-hardening` probes PATH for `python3` and `docker`. The
    // Python oracle recorded both as `true` on a laptop that had them, so the
    // default probe fails the fixture on any machine that does not — a
    // property of the runner, not of the handler. Same function, same
    // document; only the probe is stubbed, so everything else in the envelope
    // is still compared against what the product builds.
    state.hardening = Some(std::sync::Arc::new(move || {
        default_product_hardening_probed(
            &hardening_dir,
            &hardening_host,
            hardening_port,
            hardening_auth,
            &|_executable| true,
        )
    }));
    let app: Router = admin_router(state);
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
