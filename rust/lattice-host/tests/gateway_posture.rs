//! The native lanes mirror the worker's access posture, and fail closed.
//!
//! Nothing here pins a posture: the gateway is asked to discover it the way it
//! does in the product, from the worker's own `GET /health`. The three cases
//! are the three that matter — the worker says "open", the worker says
//! "closed", and the worker does not say.

mod common;

use std::path::PathBuf;
use std::sync::Arc;

use common::{
    client, fake_worker_allowlist, json, test_agent_root, FakeWorker, FixedProvider, TestGateway,
};
use lattice_host::gateway::{mounts, GatewayState};

/// Every native path the guard must cover, one per mounted family plus the two
/// host routes that read the store or the scheduler.
const GUARDED: [&str; 7] = [
    "/rust/search/keyword?q=x",
    "/rust/graph/search?q=x",
    "/rust/history",
    "/rust/context/assemble?q=x",
    "/rust/agent/contract",
    "/host/status",
    "/host/jobs",
];

/// A gateway with every mount wired, discovering its posture from `worker`.
async fn gateway(worker: &FakeWorker, name: &str) -> TestGateway {
    let root = test_agent_root(name);
    let state = GatewayState::new(Arc::new(FixedProvider::new(worker.origin(), worker.port())))
        .expect("gateway state")
        // The fake worker's own surface: since v11.6.0 the fall-through is an
        // allowlist, and these suites test the proxy's mechanics rather than
        // which paths the real worker owns (`binary_frontdoor.rs` does that).
        .with_allowlist(fake_worker_allowlist())
        // A path that does not exist: the guard must answer before anything
        // touches the store, so what is (not) in it cannot change the verdict.
        .with_db_path(PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join("no-such-brain.sqlite"))
        .with_agent_root(root.clone())
        .with_agent_runs_dir(root.join("rust_agent_runs"))
        .with_jobs(mounts::scheduler(&worker.origin(), client()));
    TestGateway::start_with_state(state).await
}

#[tokio::test]
async fn an_open_posture_serves_every_native_lane() {
    let worker = FakeWorker::start().await;
    worker.set_posture(false, false); // trusted_local_owner
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
    worker.set_posture(true, false); // LATTICEAI_REQUIRE_AUTH=true
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

    // Liveness is not a secret: this is where a person reads *why*.
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
    worker.set_posture(false, true); // bound off loopback / public mode
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
    // The worker disappears *after* the gateway is up, which is the ordering a
    // crash produces.
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

/// The proxy is not posture-gated: an authenticated `/api/*` request is the
/// worker's own decision, and the front door must not start answering 401 for
/// routes it does not implement.
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

    // …and an unmounted native path is still a 404 that names the namespaces,
    // because a 404 discloses nothing about the store.
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
