//! N7: a folder-ingest token is redeemable at `/permissions/approve`.
//!
//! Folder ingest mints in `LocalApprovals`. The Act inbox / VS Code retry
//! path redeems at `/permissions/approve/{token}`. Those used to be two
//! tables, so the approve call 404'd and the retry 403'd. This process
//! holds one table.

mod common;

use std::path::PathBuf;
use std::sync::Arc;

use common::{client, FakeWorker, FixedProvider, TestGateway};
use lattice_core::db::RuntimeConfig;
use lattice_host::gateway::onedoor::OneDoorState;
use lattice_host::gateway::posture::Posture;
use lattice_host::gateway::GatewayState;
use serde_json::{json, Value};

async fn product_gateway(name: &str) -> (TestGateway, PathBuf, PathBuf) {
    let worker = FakeWorker::start().await;
    let scratch = PathBuf::from(env!("CARGO_TARGET_TMPDIR"))
        .join("folder_ingest_approval")
        .join(name);
    let _ = std::fs::remove_dir_all(&scratch);
    let data_dir = scratch.join("data");
    let agent_root = scratch.join("agent_workspace");
    std::fs::create_dir_all(&data_dir).expect("data");
    std::fs::create_dir_all(&agent_root).expect("agent");

    std::env::remove_var("LATTICEAI_REQUIRE_AUTH");
    std::env::set_var("LATTICEAI_HOST", "127.0.0.1");
    std::env::set_var("LATTICEAI_DATA_DIR", &data_dir);

    let folder = scratch.join("notes");
    std::fs::create_dir_all(&folder).expect("folder");
    std::fs::write(
        folder.join("standup.md"),
        "ship the backup snapshot today\n",
    )
    .expect("note");

    let config = RuntimeConfig::resolve(
        Some(&data_dir.to_string_lossy()),
        None,
        Some(&worker.origin()),
        None,
    );
    let loop_config = lattice_agent::LoopConfig {
        worker_origin: worker.origin(),
        runs_dir: scratch.join("rust_agent_runs"),
        client: Some(client()),
        proposals: Some(Arc::new(lattice_agent::proposals::JsonProposalStore::new(
            scratch.join("proposals"),
        ))),
        hooks: None,
    };
    let product = OneDoorState::open_with_config(
        config,
        &worker.origin(),
        client(),
        &agent_root,
        loop_config,
    )
    .expect("product");
    let state = GatewayState::new(Arc::new(FixedProvider::new(worker.origin(), worker.port())))
        .expect("gateway")
        .with_db_path(data_dir.join("knowledge_graph.sqlite"))
        .with_agent_root(&agent_root)
        .with_agent_runs_dir(scratch.join("rust_agent_runs"))
        .with_pinned_posture(Posture::Open)
        .with_product(Arc::new(product));
    let gateway = TestGateway::start_with_state(state).await;
    // The worker task is detached when `FakeWorker` drops; the accept loop
    // stays up for the gateway's health probe.
    drop(worker);
    (gateway, scratch, folder)
}

async fn post_json(gateway: &TestGateway, path: &str, body: Value) -> (u16, Value) {
    let response = client()
        .post(gateway.url(path))
        .header("content-type", "application/json")
        .body(body.to_string())
        .send()
        .await
        .expect("request");
    let status = response.status().as_u16();
    let text = response.text().await.unwrap_or_default();
    (
        status,
        serde_json::from_str(&text).unwrap_or_else(|_| json!({"raw": text})),
    )
}

#[tokio::test]
async fn trusted_owner_redeems_a_folder_ingest_token_at_permissions_approve() {
    let (gateway, scratch, folder) = product_gateway("redeem").await;
    let folder_s = folder.to_string_lossy().to_string();

    let (probe_status, probe) = post_json(
        &gateway,
        "/api/ingestion/folder",
        json!({"path": folder_s, "approved": false}),
    )
    .await;
    assert_eq!(probe_status, 200, "{probe}");
    assert_eq!(probe["permission_required"], json!(true), "{probe}");
    let token = probe["approval_token"].as_str().expect("token").to_string();

    let (pending_status, pending) = {
        let response = client()
            .get(gateway.url("/permissions/pending"))
            .send()
            .await
            .expect("pending");
        let status = response.status().as_u16();
        let text = response.text().await.unwrap_or_default();
        (
            status,
            serde_json::from_str::<Value>(&text).unwrap_or_else(|_| json!({"raw": text})),
        )
    };
    assert_eq!(pending_status, 200, "{pending}");
    assert!(
        pending["count"].as_u64().unwrap_or(0) >= 1,
        "the probe token must land in the Act inbox: {pending}"
    );

    let (approve_status, approved) = post_json(
        &gateway,
        &format!("/permissions/approve/{token}"),
        json!({}),
    )
    .await;
    assert_eq!(approve_status, 200, "{approved}");
    assert_eq!(approved["ok"], json!(true), "{approved}");
    assert_eq!(approved["action"], json!("read"), "{approved}");

    let (run_status, body) = post_json(
        &gateway,
        "/api/ingestion/folder",
        json!({
            "path": folder_s,
            "approved": true,
            "approval_token": token,
            "background": false
        }),
    )
    .await;
    assert_eq!(run_status, 200, "{body}");
    assert_eq!(body["status"], json!("completed"), "{body}");
    assert!(body["ingested"].as_u64().unwrap_or(0) >= 1, "{body}");

    let db = scratch.join("data").join("knowledge_graph.sqlite");
    let conn = lattice_core::db::open_read_only(&db).expect("ro");
    let found: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM nodes WHERE summary LIKE ?",
            ["%ship the backup snapshot today%"],
            |row| row.get(0),
        )
        .expect("count");
    assert!(found >= 1, "the ingested note must land in the Brain");
}
