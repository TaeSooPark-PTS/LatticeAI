mod common;

use std::path::PathBuf;
use std::sync::Arc;

use common::{
    client, fake_worker_allowlist, json, test_agent_root, FakeWorker, FixedProvider, TestGateway,
};
use lattice_host::gateway::{mounts, GatewayState};

const GUARDED: [&str; 8] = [
    "/rust/search/keyword?q=x",
    "/rust/graph/search?q=x",
    "/rust/history",
    "/rust/context/assemble?q=x",
    "/rust/agent/contract",
    "/host/status",
    "/host/diagnostics",
    "/host/jobs",
];

async fn gateway(worker: &FakeWorker, name: &str) -> TestGateway {
    let root = test_agent_root(name);
    let state = GatewayState::new(Arc::new(FixedProvider::new(worker.origin(), worker.port())))
        .expect("gateway state")
        .with_allowlist(fake_worker_allowlist())
        .with_db_path(PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join("no-such-brain.sqlite"))
        .with_agent_root(root.clone())
        .with_agent_runs_dir(root.join("rust_agent_runs"))
        .with_jobs(mounts::scheduler(&worker.origin(), client()));
    TestGateway::start_with_state(state).await
}

#[tokio::test]
async fn an_open_posture_serves_every_native_lane() {
    let worker = FakeWorker::start().await;
    worker.set_posture(false, false);
    let gateway = gateway(&worker, "posture-open").await;

    for path in GUARDED {
        let response = client()
            .get(gateway.url(path))
            .send()
            .await
            .expect("request");
        assert_ne!(
            response.status(),
            401,
            "{path} must be served while the worker's posture is open"
        );
    }

    gateway.stop().await;
    worker.shutdown();
}

#[tokio::test]
async fn a_worker_that_requires_authentication_closes_the_native_lanes() {
    let worker = FakeWorker::start().await;
    worker.set_posture(true, false);
    let gateway = gateway(&worker, "posture-closed").await;

    for path in GUARDED {
        let response = client()
            .get(gateway.url(path))
            .send()
            .await
            .expect("request");
        assert_eq!(response.status(), 401, "{path} must refuse");
        let body = json(response).await;
        assert_eq!(body["error"], "native_lane_requires_open_posture");
        assert_eq!(body["posture"], "closed");
        assert!(body["path"].as_str().unwrap_or_default().starts_with("/"));
    }

    let response = client()
        .get(gateway.url("/host/health"))
        .send()
        .await
        .expect("request");
    assert_eq!(response.status(), 200);
    assert_eq!(json(response).await["host"], "ok");

    gateway.stop().await;
    worker.shutdown();
}

#[tokio::test]
async fn a_reachable_worker_closes_them_too() {
    let worker = FakeWorker::start().await;
    worker.set_posture(false, true);
    let gateway = gateway(&worker, "posture-reachable").await;

    let response = client()
        .get(gateway.url("/rust/graph/search?q=x"))
        .send()
        .await
        .expect("request");
    assert_eq!(response.status(), 401);
    assert_eq!(json(response).await["posture"], "closed");

    gateway.stop().await;
    worker.shutdown();
}

#[tokio::test]
async fn a_worker_that_states_no_posture_fails_closed() {
    let worker = FakeWorker::start().await;
    worker.drop_posture();
    let gateway = gateway(&worker, "posture-silent").await;

    let response = client()
        .get(gateway.url("/rust/search/keyword?q=x"))
        .send()
        .await
        .expect("request");
    assert_eq!(
        response.status(),
        401,
        "an unreadable posture is refused, never assumed open"
    );
    let body = json(response).await;
    assert_eq!(body["error"], "native_lane_requires_open_posture");
    assert_eq!(body["posture"], "unknown");

    gateway.stop().await;
    worker.shutdown();
}

#[tokio::test]
async fn an_unreachable_worker_fails_closed_without_reaching_the_proxy() {
    let worker = FakeWorker::start().await;
    let gateway = gateway(&worker, "posture-dead").await;
    worker.shutdown();

    let response = client()
        .get(gateway.url("/rust/graph/search?q=x"))
        .send()
        .await
        .expect("request");
    assert_eq!(response.status(), 401);
    assert_eq!(json(response).await["posture"], "unknown");

    gateway.stop().await;
}

#[tokio::test]
async fn a_closed_posture_still_proxies_the_worker_s_own_routes() {
    let worker = FakeWorker::start().await;
    worker.set_posture(true, false);
    let gateway = gateway(&worker, "posture-proxy").await;

    let response = client()
        .get(gateway.url("/api/memory"))
        .send()
        .await
        .expect("request");
    assert_eq!(response.status(), 200);
    assert_eq!(
        response.text().await.expect("body"),
        "worker saw /api/memory"
    );

    let response = client()
        .get(gateway.url("/rust/telepathy"))
        .send()
        .await
        .expect("request");
    assert_eq!(response.status(), 404);
    assert_eq!(json(response).await["error"], "unknown_native_route");

    gateway.stop().await;
    worker.shutdown();
}
