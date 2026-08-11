//! The native `/rust/search/*` lanes, over HTTP, against the committed goldens.
//!
//! `lattice-retrieval`'s own suite proves the engines equal Python's. This one
//! proves the *gateway* does not lose that on the way out: same store, same
//! queries, same golden files, but read through the router, the parameter
//! parser, the JSON encoder and a real socket. A response that differs from the
//! golden here means the front door changed the answer.
//!
//! The clock is pinned with the documented `now` parameter (the manifest's
//! `frozen_now`), which is the only way a recency-decayed ranking can be
//! compared to a file at all. `now` is reachable solely from this machine — the
//! gateway refuses to bind off loopback — and one test asserts that leaving it
//! out really does fall back to the system clock rather than to a zero.

mod common;

use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};

use common::{client, json, FixedProvider, TestGateway};
use serde_json::Value;

fn fixtures() -> PathBuf {
    [env!("CARGO_MANIFEST_DIR"), "..", "fixtures"]
        .iter()
        .collect()
}

fn read_json(path: PathBuf) -> Value {
    let raw = std::fs::read_to_string(&path)
        .unwrap_or_else(|err| panic!("missing fixture {}: {err}", path.display()));
    serde_json::from_str(&raw).expect("fixture must be valid JSON")
}

fn manifest() -> &'static Value {
    static MANIFEST: OnceLock<Value> = OnceLock::new();
    MANIFEST.get_or_init(|| read_json(fixtures().join("golden").join("manifest.json")))
}

/// The data directory the gateway will resolve, prepared exactly once.
///
/// The committed store is copied under the product's own file name so the
/// resolution path (`LATTICEAI_DATA_DIR` → `knowledge_graph.sqlite`) is the one
/// under test, and so a stray `-wal` sidecar can never land next to a checked-in
/// fixture. Once, because the environment is process-global and these tests run
/// in parallel.
fn data_dir() -> &'static Path {
    static DIR: OnceLock<PathBuf> = OnceLock::new();
    DIR.get_or_init(|| {
        let dir = PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join("gateway_search");
        std::fs::create_dir_all(&dir).expect("test data dir");
        for suffix in ["-wal", "-shm"] {
            let _ = std::fs::remove_file(dir.join(format!("knowledge_graph.sqlite{suffix}")));
        }
        let source = fixtures().join(manifest()["store"].as_str().expect("store name"));
        std::fs::copy(&source, dir.join("knowledge_graph.sqlite")).expect("copy fixture store");
        for (key, value) in manifest()["pinned_env"].as_object().expect("pinned env") {
            std::env::set_var(key, value.as_str().expect("env value"));
        }
        std::env::set_var("LATTICEAI_DATA_DIR", &dir);
        dir
    })
    .as_path()
}

/// A gateway over the fixture store. Its "worker" is a port nothing listens on:
/// if a search ever reached the proxy instead of the native lane, the 502 would
/// say so immediately.
async fn gateway() -> TestGateway {
    let _ = data_dir();
    TestGateway::start(Arc::new(FixedProvider::new(
        "http://127.0.0.1:1".to_string(),
        1,
    )))
    .await
}

fn frozen_now() -> &'static str {
    manifest()["frozen_now"].as_str().expect("frozen clock")
}

/// Every golden whose parameters this front door can actually express.
///
/// The `ws_*` cases pin workspace scoping, and these routes deliberately have
/// none — they are the trusted local owner's path. Skipping them here is not a
/// gap: `lattice-retrieval`'s suite covers them against the same goldens.
fn unscoped_goldens(engine: &str) -> Vec<(String, Value)> {
    manifest()["queries"]
        .as_array()
        .expect("queries")
        .iter()
        .filter(|spec| spec.get("allowed").is_none() && spec.get("legacy").is_none())
        .map(|spec| {
            let key = spec["key"].as_str().expect("key").to_string();
            let golden = read_json(
                fixtures()
                    .join("golden")
                    .join(format!("{engine}__{key}.json")),
            );
            (key, golden)
        })
        .collect()
}

/// The HTTP parameters that reproduce a golden's Python call.
fn request_pairs(engine: &str, golden: &Value) -> Vec<(String, String)> {
    let params = &golden["params"];
    let mut pairs = vec![(
        "q".to_string(),
        golden["query"].as_str().expect("query").to_string(),
    )];
    let mut push = |name: &str, value: String| pairs.push((name.to_string(), value));
    match engine {
        "hybrid" => {
            push("now", frozen_now().to_string());
            push("top_k", params["top_k"].to_string());
            push("min_vector_score", params["min_vector_score"].to_string());
            if let Some(alpha) = params["alpha"].as_f64() {
                push("alpha", alpha.to_string());
            }
        }
        "keyword" => push("limit", params["limit"].to_string()),
        _ => {
            push("limit", params["limit"].to_string());
            push("min_score", params["min_score"].to_string());
        }
    }
    pairs
}

async fn assert_lane_matches_goldens(engine: &str, post: bool) {
    let gateway = gateway().await;
    let url = gateway.url(&format!("/rust/search/{engine}"));
    let goldens = unscoped_goldens(engine);
    assert!(goldens.len() >= 14, "the query set is the coverage");

    let mut mismatched: Vec<String> = Vec::new();
    for (key, golden) in &goldens {
        let pairs = request_pairs(engine, golden);
        let response = if post {
            let body: serde_json::Map<String, Value> = pairs
                .into_iter()
                .map(|(name, value)| (name, Value::String(value)))
                .collect();
            client()
                .post(&url)
                .header("content-type", "application/json")
                .body(serde_json::to_string(&body).expect("serialise"))
                .send()
                .await
        } else {
            client().get(&url).query(&pairs).send().await
        }
        .expect("request");
        assert_eq!(response.status(), 200, "{engine}/{key} must answer");
        let body = json(response).await;
        if body != golden["result"] {
            mismatched.push(format!("  {engine}/{key}"));
        }
    }
    assert!(
        mismatched.is_empty(),
        "{} of {} goldens differ over HTTP:\n{}",
        mismatched.len(),
        goldens.len(),
        mismatched.join("\n")
    );
    gateway.stop().await;
}

#[tokio::test]
async fn hybrid_over_get_equals_the_python_goldens() {
    assert_lane_matches_goldens("hybrid", false).await;
}

#[tokio::test]
async fn hybrid_over_post_equals_the_python_goldens() {
    assert_lane_matches_goldens("hybrid", true).await;
}

#[tokio::test]
async fn keyword_over_get_and_post_equals_the_python_goldens() {
    assert_lane_matches_goldens("keyword", false).await;
    assert_lane_matches_goldens("keyword", true).await;
}

#[tokio::test]
async fn vector_over_get_and_post_equals_the_python_goldens() {
    assert_lane_matches_goldens("vector", false).await;
    assert_lane_matches_goldens("vector", true).await;
}

#[tokio::test]
async fn the_defaults_alone_reproduce_the_default_golden() {
    let gateway = gateway().await;
    // No top_k, no alpha, no floor — only the question and the pinned clock.
    let body = json(
        client()
            .get(gateway.url("/rust/search/hybrid"))
            .query(&[("q", "hybrid retrieval ranking"), ("now", frozen_now())])
            .send()
            .await
            .expect("request"),
    )
    .await;
    let golden = read_json(fixtures().join("golden").join("hybrid__en_fact.json"));
    assert_eq!(
        body, golden["result"],
        "the documented defaults are Python's"
    );

    // `query` is the long spelling of `q`, and it must mean the same thing.
    let long = json(
        client()
            .get(gateway.url("/rust/search/hybrid"))
            .query(&[("query", "hybrid retrieval ranking"), ("now", frozen_now())])
            .send()
            .await
            .expect("request"),
    )
    .await;
    assert_eq!(long, body);
    gateway.stop().await;
}

#[tokio::test]
async fn without_now_the_ranking_uses_the_system_clock() {
    let gateway = gateway().await;
    let body = json(
        client()
            .get(gateway.url("/rust/search/hybrid"))
            .query(&[("q", "recent decisions last week")])
            .send()
            .await
            .expect("request"),
    )
    .await;
    assert_eq!(body["query_class"], "recency");
    let matches = body["matches"].as_array().expect("matches");
    assert!(!matches.is_empty(), "the fixture answers this query");
    for item in matches {
        assert!(
            item["scores"]["age_decay"].is_number(),
            "a recency query decays every hit: {item}"
        );
    }
    // The frozen clock is ten days behind the real one, so at least one decay
    // multiplier must differ from the golden's — otherwise `now` did nothing.
    let golden = read_json(fixtures().join("golden").join("hybrid__en_recency.json"));
    assert_ne!(
        body["matches"], golden["result"]["matches"],
        "the system clock produced the frozen ranking — `now` is not wired"
    );
    gateway.stop().await;
}

#[tokio::test]
async fn a_missing_store_is_an_honest_404() {
    let dir = tempfile::tempdir().expect("tempdir");
    let db = dir.path().join("knowledge_graph.sqlite");
    let gateway = TestGateway::start_with_db(
        Arc::new(FixedProvider::new("http://127.0.0.1:1".to_string(), 1)),
        &db,
    )
    .await;

    for lane in ["hybrid", "keyword", "vector"] {
        let response = client()
            .get(gateway.url(&format!("/rust/search/{lane}")))
            .query(&[("q", "anything")])
            .send()
            .await
            .expect("request");
        assert_eq!(response.status(), 404, "{lane} must not invent a brain");
        let body = json(response).await;
        assert_eq!(body["error"], "brain_not_found");
        assert_eq!(body["path"], db.display().to_string());
        assert!(body["detail"]
            .as_str()
            .unwrap_or_default()
            .contains("LATTICEAI_DATA_DIR"));
    }
    assert!(!db.exists(), "a read must never create the store");
    gateway.stop().await;
}

#[tokio::test]
async fn bad_input_is_a_422_that_names_the_field() {
    let gateway = gateway().await;
    // (lane, query string, the field the rejection must name)
    let cases: [(&str, &str, &str); 6] = [
        ("hybrid", "", "query"),
        ("hybrid", "?q=x&top_k=0", "top_k"),
        ("hybrid", "?q=x&alpha=1.5", "alpha"),
        ("hybrid", "?q=x&now=tomorrow", "now"),
        ("keyword", "?q=x&limit=101", "limit"),
        ("vector", "?q=x&min_score=-1", "min_score"),
    ];
    for (lane, pairs, field) in cases {
        let response = client()
            .get(gateway.url(&format!("/rust/search/{lane}{pairs}")))
            .send()
            .await
            .expect("request");
        assert_eq!(response.status(), 422, "{lane}{pairs}");
        let body = json(response).await;
        assert_eq!(body["error"], "invalid_request");
        assert_eq!(body["field"], field);
        assert!(!body["detail"].as_str().unwrap_or_default().is_empty());
    }

    // A body that is not a JSON object is the same class of mistake.
    let response = client()
        .post(gateway.url("/rust/search/hybrid"))
        .header("content-type", "application/json")
        .body("{not json")
        .send()
        .await
        .expect("request");
    assert_eq!(response.status(), 422);
    assert_eq!(json(response).await["field"], "body");
    gateway.stop().await;
}

#[tokio::test]
async fn the_search_namespace_is_never_proxied() {
    let worker = common::FakeWorker::start().await;
    let dir = tempfile::tempdir().expect("tempdir");
    let gateway = TestGateway::start_with_db(
        Arc::new(FixedProvider::new(worker.origin(), worker.port())),
        dir.path().join("knowledge_graph.sqlite"),
    )
    .await;

    for path in ["/rust/search", "/rust/search/", "/rust/search/bogus"] {
        let response = client().get(gateway.url(path)).send().await.expect("get");
        assert_eq!(response.status(), 404, "{path}");
        let body = json(response).await;
        assert_eq!(body["error"], "unknown_search_route");
        assert_eq!(
            body["available"].as_array().map(Vec::len),
            Some(3),
            "the 404 lists the lanes that do exist"
        );
    }
    // A real lane with no store is a 404 too, but a *different* one — and still
    // nothing crossed to the worker.
    let response = client()
        .get(gateway.url("/rust/search/hybrid"))
        .query(&[("q", "x")])
        .send()
        .await
        .expect("get");
    assert_eq!(json(response).await["error"], "brain_not_found");

    // Wrong verb on a real lane is a 405 from the router, not a proxied request.
    let response = client()
        .delete(gateway.url("/rust/search/hybrid"))
        .send()
        .await
        .expect("delete");
    assert_eq!(response.status(), 405);

    assert_eq!(
        worker.request_count(),
        0,
        "nothing under /rust/search may reach the worker"
    );
    gateway.stop().await;
    worker.shutdown();
}
