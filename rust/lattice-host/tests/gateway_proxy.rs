//! Gateway integration tests against a pure-Rust fake worker.

mod common;

use std::sync::Arc;
use std::time::{Duration, Instant};

use common::{client, client_no_redirect, json, FakeWorker, FixedProvider, TestGateway};

async fn harness(worker: &FakeWorker) -> TestGateway {
    TestGateway::start(Arc::new(FixedProvider::new(worker.origin(), worker.port()))).await
}

#[tokio::test]
async fn host_health_reports_host_liveness_and_worker_health() {
    let worker = FakeWorker::start_with_health(false).await;
    let gateway = harness(&worker).await;

    let response = client()
        .get(gateway.url("/host/health"))
        .send()
        .await
        .expect("host health");
    assert_eq!(response.status(), 200, "the host itself is up");
    let body = json(response).await;
    assert_eq!(body["host"], "ok");
    assert_eq!(body["status"], "degraded");
    assert_eq!(body["worker_healthy"], false);

    // The gate flips as soon as the worker starts answering 2xx.
    worker.set_healthy(true);
    let body = json(
        client()
            .get(gateway.url("/host/health"))
            .send()
            .await
            .expect("host health"),
    )
    .await;
    assert_eq!(body["status"], "ok");
    assert_eq!(body["worker_healthy"], true);
    assert_eq!(body["worker_origin"], worker.origin());

    gateway.stop().await;
    worker.shutdown();
}

#[tokio::test]
async fn host_status_exposes_the_worker_snapshot() {
    let worker = FakeWorker::start().await;
    let gateway = harness(&worker).await;

    let body = json(
        client()
            .get(gateway.url("/host/status"))
            .send()
            .await
            .expect("status"),
    )
    .await;
    assert_eq!(body["worker"]["port"], worker.port());
    assert_eq!(body["worker"]["command"], "fake worker");
    assert_eq!(body["gateway"]["worker_origin"], worker.origin());
    assert!(body["gateway"]["version"].is_string());

    gateway.stop().await;
    worker.shutdown();
}

#[tokio::test]
async fn the_host_namespace_is_never_proxied() {
    let worker = FakeWorker::start().await;
    let gateway = harness(&worker).await;

    let response = client()
        .get(gateway.url("/host/bogus"))
        .send()
        .await
        .expect("request");
    assert_eq!(response.status(), 404);
    let body = json(response).await;
    assert_eq!(body["error"], "unknown_host_route");
    assert_eq!(
        worker.request_count(),
        0,
        "nothing under /host may reach the worker"
    );

    gateway.stop().await;
    worker.shutdown();
}

#[tokio::test]
async fn the_native_search_namespace_is_answered_here_not_proxied() {
    let worker = FakeWorker::start().await;
    // Pinned at a store that does not exist, so the answer depends on the
    // router rather than on whatever brain the developer happens to have.
    let dir = tempfile::tempdir().expect("tempdir");
    let gateway = TestGateway::start_with_db(
        Arc::new(FixedProvider::new(worker.origin(), worker.port())),
        dir.path().join("knowledge_graph.sqlite"),
    )
    .await;

    // An unknown path under the namespace names the lanes that do exist …
    let response = client()
        .get(gateway.url("/rust/search/telepathy"))
        .send()
        .await
        .expect("request");
    assert_eq!(response.status(), 404);
    assert_eq!(json(response).await["error"], "unknown_search_route");

    // … and a real lane is served natively (here: honestly, "no brain yet").
    for lane in ["hybrid", "keyword", "vector"] {
        let response = client()
            .get(gateway.url(&format!("/rust/search/{lane}")))
            .query(&[("q", "anything")])
            .send()
            .await
            .expect("request");
        assert_eq!(response.status(), 404, "{lane}");
        assert_eq!(json(response).await["error"], "brain_not_found");
    }
    assert_eq!(worker.request_count(), 0, "nothing crossed to the worker");

    gateway.stop().await;
    worker.shutdown();
}

#[tokio::test]
async fn get_requests_are_proxied_with_path_query_and_headers() {
    let worker = FakeWorker::start().await;
    let gateway = harness(&worker).await;

    let response = client()
        .get(gateway.url("/api/notes?q=hello%20world&k=5"))
        .header("x-request-id", "abc-123")
        .header("authorization", "Bearer secret")
        .send()
        .await
        .expect("request");
    assert_eq!(response.status(), 200);
    assert_eq!(
        response
            .headers()
            .get("x-fake-worker")
            .and_then(|v| v.to_str().ok()),
        Some("1"),
        "response headers survive the proxy"
    );
    assert_eq!(
        response.text().await.expect("body"),
        "worker saw /api/notes"
    );

    let seen = worker.requests();
    assert_eq!(seen.len(), 1);
    assert_eq!(seen[0].method, "GET");
    assert_eq!(seen[0].path(), "/api/notes");
    assert_eq!(seen[0].query(), Some("q=hello%20world&k=5"));
    assert_eq!(seen[0].header("x-request-id"), Some("abc-123"));
    assert_eq!(seen[0].header("authorization"), Some("Bearer secret"));
    assert_ne!(
        seen[0].header("host"),
        Some("gateway"),
        "the Host header is rewritten for the upstream"
    );

    gateway.stop().await;
    worker.shutdown();
}

#[tokio::test]
async fn post_bodies_are_forwarded_and_responses_echoed() {
    let worker = FakeWorker::start().await;
    let gateway = harness(&worker).await;

    let payload = serde_json::json!({"question": "무엇을 기억하니?", "k": 5});
    let response = client()
        .post(gateway.url("/echo"))
        .header("content-type", "application/json")
        .body(serde_json::to_string(&payload).expect("serialise"))
        .send()
        .await
        .expect("request");
    assert_eq!(response.status(), 200);
    assert_eq!(
        response
            .headers()
            .get("x-echo-method")
            .and_then(|v| v.to_str().ok()),
        Some("POST")
    );
    let echoed = json(response).await;
    assert_eq!(echoed, payload);

    let seen = worker.requests();
    assert_eq!(seen[0].method, "POST");
    assert_eq!(seen[0].header("content-type"), Some("application/json"));
    assert_eq!(
        seen[0].body_text(),
        serde_json::to_string(&payload).expect("serialise")
    );

    gateway.stop().await;
    worker.shutdown();
}

/// The invite gate, and every other credential a redirect carries.
///
/// Before 11.5.2 the proxy's client followed the 3xx itself and answered with
/// the *final* response, so `Set-Cookie` and `Location` were both destroyed:
/// `GET /?code=…` came back as the "no invite" page and the gate was a hard
/// dead end through the front door.
#[tokio::test]
async fn a_redirect_reaches_the_caller_with_its_cookie_and_location_intact() {
    let worker = FakeWorker::start().await;
    let gateway = harness(&worker).await;

    let response = client_no_redirect()
        .get(gateway.url("/redirect"))
        .send()
        .await
        .expect("request");
    assert_eq!(response.status(), 308, "the redirect is passed through");
    assert_eq!(
        response
            .headers()
            .get("location")
            .and_then(|v| v.to_str().ok()),
        Some("/app#/account"),
        "a relative Location is the browser's to resolve, untouched"
    );
    assert!(
        response
            .headers()
            .get("set-cookie")
            .and_then(|v| v.to_str().ok())
            .unwrap_or_default()
            .contains("lattice_invite=granted"),
        "the credential the redirect carried must survive the hop"
    );

    gateway.stop().await;
    worker.shutdown();
}

/// Starlette answers `/app` and `/static` with an *absolute* redirect built
/// from the Host it saw — which, after this hop replaced it, is the worker's
/// private port. Handed to the browser unchanged it would route around the
/// front door.
#[tokio::test]
async fn an_absolute_location_naming_the_worker_is_rewritten_to_the_front_door() {
    let worker = FakeWorker::start().await;
    let gateway = harness(&worker).await;

    let response = client_no_redirect()
        .get(gateway.url("/redirect-absolute"))
        .send()
        .await
        .expect("request");
    assert_eq!(response.status(), 308);
    let location = response
        .headers()
        .get("location")
        .and_then(|v| v.to_str().ok())
        .unwrap_or_default()
        .to_string();
    assert_eq!(
        location,
        format!("{}/app/", gateway.base),
        "the browser is sent back to the gateway, not to the worker's own port"
    );
    assert!(
        !location.contains(&worker.port().to_string()),
        "the internal port must not leak into a browser redirect: {location}"
    );

    // …and a redirect that names somewhere else entirely is left alone.
    let response = client_no_redirect()
        .get(gateway.url("/redirect-external"))
        .send()
        .await
        .expect("request");
    assert_eq!(response.status(), 302);
    assert_eq!(
        response
            .headers()
            .get("location")
            .and_then(|v| v.to_str().ok()),
        Some("https://idp.example/authorize?x=1")
    );

    gateway.stop().await;
    worker.shutdown();
}

/// The worker's only way to know the front door's name: `Host` is hop-by-hop
/// and is replaced with the worker's own authority on the way out.
#[tokio::test]
async fn proxied_requests_state_who_asked_and_where() {
    let worker = FakeWorker::start().await;
    let gateway = harness(&worker).await;

    let response = client()
        .get(gateway.url("/api/notes"))
        // A caller's own claim must not be believed — the gateway states what
        // it observed.
        .header("x-forwarded-for", "203.0.113.9")
        .header("x-forwarded-host", "evil.example")
        .send()
        .await
        .expect("request");
    assert_eq!(response.status(), 200);

    let seen = worker.requests();
    let proxied = seen.last().expect("a request reached the worker");
    assert_eq!(
        proxied.header("x-forwarded-host"),
        Some(gateway.base.trim_start_matches("http://")),
        "the authority the caller actually asked for"
    );
    assert_eq!(proxied.header("x-forwarded-proto"), Some("http"));
    assert_eq!(
        proxied.header("x-forwarded-for"),
        Some("127.0.0.1"),
        "the peer the gateway observed, not the one the caller claimed"
    );

    gateway.stop().await;
    worker.shutdown();
}

#[tokio::test]
async fn upstream_status_codes_are_preserved() {
    let worker = FakeWorker::start().await;
    let gateway = harness(&worker).await;

    let response = client()
        .get(gateway.url("/teapot"))
        .send()
        .await
        .expect("request");
    assert_eq!(response.status(), 418);
    assert_eq!(response.text().await.expect("body"), "short and stout");

    gateway.stop().await;
    worker.shutdown();
}

#[tokio::test]
async fn a_dead_worker_yields_502_not_a_hang() {
    let worker = FakeWorker::start().await;
    let origin = worker.origin();
    let port = worker.port();
    worker.shutdown();

    let gateway = TestGateway::start(Arc::new(FixedProvider::new(origin, port))).await;
    let response = client()
        .get(gateway.url("/api/anything"))
        .send()
        .await
        .expect("request");
    assert_eq!(response.status(), 502);
    let body = json(response).await;
    assert_eq!(body["error"], "worker_unavailable");
    assert!(body["detail"]
        .as_str()
        .unwrap_or_default()
        .contains("failed"));
    assert_eq!(body["worker"]["port"], port);

    gateway.stop().await;
}

#[tokio::test]
async fn sse_events_stream_through_before_the_response_ends() {
    let worker = FakeWorker::start().await;
    let gateway = harness(&worker).await;

    let started = Instant::now();
    let response = client()
        .get(gateway.url("/sse"))
        .send()
        .await
        .expect("request");
    assert_eq!(response.status(), 200);
    assert_eq!(
        response
            .headers()
            .get("content-type")
            .and_then(|v| v.to_str().ok()),
        Some("text/event-stream")
    );

    // Read the first chunk only; the worker holds the stream open for another
    // 400 ms afterwards, so receiving it early proves nothing buffered it.
    let mut response = response;
    let first = response
        .chunk()
        .await
        .expect("first chunk")
        .expect("stream is not empty");
    let first_at = started.elapsed();
    assert_eq!(String::from_utf8_lossy(&first), "data: first\n\n");
    assert!(
        first_at < Duration::from_millis(300),
        "first SSE event arrived after {first_at:?} — something buffered it"
    );

    let mut rest = Vec::new();
    while let Some(chunk) = response.chunk().await.expect("chunk") {
        rest.extend_from_slice(&chunk);
    }
    assert_eq!(String::from_utf8_lossy(&rest), "data: second\n\n");
    assert!(
        started.elapsed() >= Duration::from_millis(400),
        "the stream really did stay open"
    );

    gateway.stop().await;
    worker.shutdown();
}

#[tokio::test]
async fn large_bodies_are_streamed_chunked_instead_of_buffered() {
    let worker = FakeWorker::start().await;
    let gateway = harness(&worker).await;

    // Over the buffering threshold, so the proxy must forward it as an
    // unknown-length stream rather than holding it in memory.
    let payload = "ㄱ".repeat(600_000); // ~1.8 MiB of UTF-8
    assert!(payload.len() > 1024 * 1024);
    let response = client()
        .post(gateway.url("/echo"))
        .header("content-type", "text/plain")
        .body(payload.clone())
        .send()
        .await
        .expect("request");
    assert_eq!(response.status(), 200);
    assert_eq!(response.text().await.expect("body"), payload);

    let seen = worker.requests();
    assert_eq!(
        seen[0].header("content-length"),
        None,
        "framing switched to chunked"
    );
    assert_eq!(
        seen[0].header("transfer-encoding"),
        Some("chunked"),
        "the upstream request really was streamed"
    );
    assert_eq!(seen[0].body_text(), payload);

    gateway.stop().await;
    worker.shutdown();
}
