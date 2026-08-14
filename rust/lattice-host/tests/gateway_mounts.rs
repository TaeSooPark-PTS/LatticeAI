//! Every mounted namespace, answered through the gateway (v11.5.0 §2a).
//!
//! `lattice-retrieval`, `lattice-ingest`, `lattice-agent` and `lattice-jobs`
//! each prove their own behaviour in their own suite. What this file proves is
//! the thing none of them can: that the host *mounted* them, ahead of the
//! reverse proxy, and that the proxy is still what everything else falls
//! through to.
//!
//! The fake worker is the instrument. Every native assertion also asserts that
//! the worker saw nothing — a route that quietly became a proxy hop would still
//! return plausible JSON, and only the request count says which crate answered.

mod common;

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use common::{
    client, fake_worker_allowlist, json, test_agent_root, FakeWorker, FixedProvider, TestGateway,
};
use lattice_host::gateway::{mounts, GatewayState};

/// `rust/fixtures` — the committed parity store the retrieval routes read.
fn fixture_store() -> PathBuf {
    [env!("CARGO_MANIFEST_DIR"), "..", "fixtures"]
        .iter()
        .collect::<PathBuf>()
        .join("parity_store.sqlite")
}

/// A copy of the fixture store under this suite's own name, so a `-wal`
/// sidecar can never land next to a checked-in file.
fn store_copy(name: &str) -> PathBuf {
    let dir = PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join("gateway_mounts");
    std::fs::create_dir_all(&dir).expect("test data dir");
    let path = dir.join(format!("{name}.sqlite"));
    for suffix in ["-wal", "-shm"] {
        let _ = std::fs::remove_file(dir.join(format!("{name}.sqlite{suffix}")));
    }
    std::fs::copy(fixture_store(), &path).expect("copy fixture store");
    path
}

/// A gateway with every mount wired: the fixture store, a private agent root,
/// and (optionally) a scheduler pointed at the fake worker.
async fn gateway(worker: &FakeWorker, name: &str, jobs: bool) -> TestGateway {
    let mut state = GatewayState::new(Arc::new(FixedProvider::new(worker.origin(), worker.port())))
        .expect("gateway state")
        // The fake worker's own surface: since v11.6.0 the fall-through is an
        // allowlist, and these suites test the proxy's mechanics rather than
        // which paths the real worker owns (`binary_frontdoor.rs` does that).
        .with_allowlist(fake_worker_allowlist())
        .with_db_path(store_copy(name))
        .with_agent_root(test_agent_root(name))
        // Never the real `~/.ltcai/rust_agent_runs`: a paused approval is a
        // real file, and a test must not write into the user's store.
        .with_agent_runs_dir(test_agent_root(name).join("rust_agent_runs"));
    if jobs {
        state = state.with_jobs(mounts::scheduler(&worker.origin(), client()));
    }
    TestGateway::start_with_state(state).await
}

#[tokio::test]
async fn the_graph_namespace_is_answered_natively() {
    let worker = FakeWorker::start().await;
    let gateway = gateway(&worker, "graph", false).await;

    let response = client()
        .get(gateway.url("/rust/graph/search"))
        .query(&[("q", "ranking"), ("limit", "3")])
        .send()
        .await
        .expect("request");
    assert_eq!(response.status(), 200);
    let body = json(response).await;
    let matches = body["matches"]
        .as_array()
        .unwrap_or_else(|| panic!("graph_search answers its own shape: {body}"));
    assert!(
        !matches.is_empty(),
        "the fixture answers this query: {body}"
    );
    assert!(
        matches[0]["graph_context"].is_array(),
        "each match carries why the graph reached it: {body}"
    );

    // The relationship and traversal reads are mounted from the same factory.
    let relationships = json(
        client()
            .get(gateway.url("/rust/graph/relationships"))
            .send()
            .await
            .expect("request"),
    )
    .await;
    assert!(relationships["relationships"].is_array());

    // …and so is the history family, on both verbs.
    let history = client()
        .post(gateway.url("/rust/history/search"))
        .header("content-type", "application/json")
        .body(r#"{"q": "회의"}"#)
        .send()
        .await
        .expect("request");
    assert_eq!(history.status(), 200);

    let context = client()
        .post(gateway.url("/rust/context/assemble"))
        .header("content-type", "application/json")
        .body(r#"{"query": "ranking", "max_tokens": 200}"#)
        .send()
        .await
        .expect("request");
    assert_eq!(context.status(), 200);

    assert_eq!(
        worker.proxied_count(),
        0,
        "nothing under /rust may reach the worker"
    );
    gateway.stop().await;
    worker.shutdown();
}

#[tokio::test]
async fn the_ingest_namespace_chunks_without_touching_the_worker() {
    let worker = FakeWorker::start().await;
    let gateway = gateway(&worker, "ingest", false).await;

    let response = client()
        .post(gateway.url("/rust/ingest/chunk"))
        .header("content-type", "application/json")
        .body(
            r##"{"text": "# 제목\n\n본문 한 줄.\n\nAnother paragraph.", "strategy": "markdown"}"##,
        )
        .send()
        .await
        .expect("request");
    assert_eq!(response.status(), 200);
    let body = json(response).await;
    assert_eq!(body["dry_run"], true, "the ingest surface never writes");
    assert_eq!(body["strategy"], "markdown");
    let chunks = body["chunks"].as_array().expect("chunks array");
    assert!(!chunks.is_empty(), "the chunker answered: {body}");
    assert!(chunks[0]["text"].is_string());
    assert!(chunks[0]["chunk_id"]
        .as_str()
        .unwrap_or_default()
        .starts_with("chunk:"));

    assert_eq!(worker.proxied_count(), 0, "chunking is a local computation");
    gateway.stop().await;
    worker.shutdown();
}

#[tokio::test]
async fn the_agent_kernel_answers_its_contract_and_refuses_mutation() {
    let worker = FakeWorker::start().await;
    let gateway = gateway(&worker, "agent", false).await;

    let body = json(
        client()
            .get(gateway.url("/rust/agent/contract"))
            .send()
            .await
            .expect("request"),
    )
    .await;
    assert!(
        body["modes"].is_object(),
        "the mode contract is JSON: {body}"
    );
    assert!(body["contract"].is_object());
    assert!(body["default_mode"].is_string());

    // A preflight is a decision, and the decision arrives without the worker.
    let preflight = client()
        .post(gateway.url("/rust/agent/preflight"))
        .header("content-type", "application/json")
        .body(r#"{"mode": "strict", "calls": [{"tool": "write_file", "args": {"path": "a.txt"}}]}"#)
        .send()
        .await
        .expect("request");
    assert_eq!(preflight.status(), 200);
    let decisions = json(preflight).await;
    assert_eq!(decisions["mode"], "strict");
    let calls = decisions["calls"].as_array().expect("one verdict per call");
    assert_eq!(calls.len(), 1, "{decisions}");
    assert!(calls[0].get("auto_approve").is_some(), "{decisions}");

    assert_eq!(worker.proxied_count(), 0, "the kernel is native");
    gateway.stop().await;
    worker.shutdown();
}

/// The loop routes mount alongside the kernel ones, and the approvals lane
/// answers natively — from the host's own store, without a worker hop.
#[tokio::test]
async fn the_agent_loop_routes_are_mounted_and_answer_natively() {
    let worker = FakeWorker::start().await;
    let gateway = gateway(&worker, "agent-loop", false).await;

    let pending = json(
        client()
            .get(gateway.url("/rust/agent/approvals"))
            .send()
            .await
            .expect("request"),
    )
    .await;
    assert_eq!(pending["pending"], serde_json::json!([]), "{pending}");

    // A resume for a run that was never paused is a 404 from *here*, not a
    // proxied 404 from the worker.
    let missing = client()
        .post(gateway.url("/rust/agent/resume"))
        .header("content-type", "application/json")
        .body(r#"{"run_id": "not-a-real-run", "approval_token": "x"}"#)
        .send()
        .await
        .expect("request");
    assert_eq!(missing.status(), 404);

    // A run with no message is refused before any worker call is attempted.
    let invalid = client()
        .post(gateway.url("/rust/agent/run"))
        .header("content-type", "application/json")
        .body(r#"{"message": "   "}"#)
        .send()
        .await
        .expect("request");
    assert_eq!(invalid.status(), 400);

    assert_eq!(worker.proxied_count(), 0, "the loop routes are native");
    gateway.stop().await;
    worker.shutdown();
}

#[tokio::test]
async fn host_jobs_reports_manual_only_until_the_timer_is_spawned() {
    let worker = FakeWorker::start().await;
    let gateway = gateway(&worker, "jobs-mounted", true).await;

    let body = json(
        client()
            .get(gateway.url("/host/jobs"))
            .send()
            .await
            .expect("request"),
    )
    .await;
    assert_eq!(
        body["enabled"], false,
        "mounted is not started: {body} — `enabled` means the timer runs"
    );
    assert_eq!(body["worker_origin"], worker.origin());
    assert!(body["interval"].as_u64().unwrap_or(0) >= 5);
    assert!(body["last_tick"].is_null(), "nothing has ticked");

    gateway.stop().await;
    worker.shutdown();
}

#[tokio::test]
async fn a_spawned_scheduler_reports_enabled_and_records_its_tick() {
    let worker = FakeWorker::start().await;
    let scheduler = mounts::scheduler(&worker.origin(), client());
    let state = GatewayState::new(Arc::new(FixedProvider::new(worker.origin(), worker.port())))
        .expect("gateway state")
        // The fake worker's own surface: since v11.6.0 the fall-through is an
        // allowlist, and these suites test the proxy's mechanics rather than
        // which paths the real worker owns (`binary_frontdoor.rs` does that).
        .with_allowlist(fake_worker_allowlist())
        .with_db_path(store_copy("jobs-running"))
        .with_agent_root(test_agent_root("jobs-running"))
        .with_jobs(Arc::clone(&scheduler));
    let gateway = TestGateway::start_with_state(state).await;

    let (stop_tx, stop_rx) = tokio::sync::oneshot::channel::<()>();
    let handle = Arc::clone(&scheduler).spawn(async move {
        let _ = stop_rx.await;
    });

    // The first tick runs immediately on spawn; poll rather than sleep on it.
    let mut body = serde_json::Value::Null;
    for _ in 0..200 {
        body = json(
            client()
                .get(gateway.url("/host/jobs"))
                .send()
                .await
                .expect("request"),
        )
        .await;
        if body["enabled"] == true && !body["last_tick"].is_null() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    assert_eq!(body["enabled"], true, "the timer is running: {body}");
    assert!(!body["last_tick"].is_null(), "it ticked once: {body}");
    // The fake worker answers text, not the drain payload — an honest failure
    // is what a tick against a worker that cannot drain must record.
    assert_eq!(body["last_tick"]["ok"], false);
    assert!(body["last_tick"]["error"].is_string());

    let _ = stop_tx.send(());
    let _ = tokio::time::timeout(Duration::from_secs(5), handle).await;
    gateway.stop().await;
    worker.shutdown();
}

#[tokio::test]
async fn an_unmounted_native_path_is_a_404_that_names_the_families() {
    let worker = FakeWorker::start().await;
    let gateway = gateway(&worker, "unknown", false).await;

    let response = client()
        .get(gateway.url("/rust/telepathy"))
        .send()
        .await
        .expect("request");
    assert_eq!(response.status(), 404);
    let body = json(response).await;
    assert_eq!(body["error"], "unknown_native_route");
    let families: Vec<&str> = body["namespaces"]
        .as_array()
        .expect("namespaces")
        .iter()
        .map(|value| value.as_str().unwrap_or_default())
        .collect();
    for family in ["/rust/graph", "/rust/ingest", "/rust/agent"] {
        assert!(families.contains(&family), "{family} missing from {body}");
    }

    // /host is guarded the same way, and /host/jobs is absent when unwired.
    let jobs = client()
        .get(gateway.url("/host/jobs"))
        .send()
        .await
        .expect("request");
    assert_eq!(jobs.status(), 404);
    assert_eq!(json(jobs).await["error"], "unknown_host_route");

    assert_eq!(
        worker.proxied_count(),
        0,
        "no namespace leaked to the worker"
    );
    gateway.stop().await;
    worker.shutdown();
}

#[tokio::test]
async fn an_event_stream_is_marked_unbufferable_on_its_way_out() {
    let worker = FakeWorker::start().await;
    let gateway = gateway(&worker, "sse", false).await;

    let response = client()
        .get(gateway.url("/sse"))
        .send()
        .await
        .expect("request");
    assert_eq!(response.status(), 200);
    assert_eq!(
        response
            .headers()
            .get("x-accel-buffering")
            .and_then(|value| value.to_str().ok()),
        Some("no"),
        "a proxied SSE response must tell every hop in front of us not to buffer it"
    );

    // A plain JSON response is left exactly as the worker sent it.
    let plain = client()
        .get(gateway.url("/api/anything"))
        .send()
        .await
        .expect("request");
    assert!(plain.headers().get("x-accel-buffering").is_none());

    gateway.stop().await;
    worker.shutdown();
}

#[tokio::test]
async fn the_default_agent_root_is_the_one_the_worker_is_given() {
    // Not a route test: the mount decision. The host judges paths against the
    // same directory the supervisor hands the worker, or its verdicts are about
    // files nothing will ever touch.
    let home = Path::new("/home/u");
    assert_eq!(
        mounts::resolve_agent_root(None, Some(home)),
        home.join(".ltcai")
            .join("desktop-runtime")
            .join("agent_workspace")
    );
}
