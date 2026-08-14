//! Replay browser validation / private-host / auth branches.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use axum::Router;
use lattice_auth::{AuthConfig, AuthState, Clock, OrderedMap};
use lattice_core::db::RuntimeConfig;
use lattice_core::worker::WorkerSeamClient;
use lattice_ingest::browser_api::{self, BrowserState};
use serde_json::Value;

fn seed_users(dir: &Path) {
    let email = "owner@lattice.test";
    let mut owner = OrderedMap::new();
    owner.insert("password", serde_json::json!("x"));
    owner.insert("name", serde_json::json!("owner"));
    owner.insert("nickname", serde_json::json!("owner"));
    owner.insert("role", serde_json::json!("admin"));
    owner.insert("disabled", serde_json::json!(false));
    owner.insert("id", serde_json::json!(lattice_auth::stable_user_id(email)));
    owner.insert("email", serde_json::json!(email));
    let mut users = OrderedMap::new();
    users.insert(email, serde_json::to_value(owner).unwrap());
    std::fs::write(
        dir.join("users.json"),
        lattice_auth::pyjson::dumps_indent2(&users).expect("users"),
    )
    .expect("write users");
}

async fn boot(data: &Path) -> (String, String) {
    seed_users(data);
    let mut env = HashMap::new();
    env.insert("LATTICEAI_REQUIRE_AUTH".into(), "1".into());
    env.insert("LATTICEAI_HOST".into(), "127.0.0.1".into());
    env.insert("LATTICEAI_PORT".into(), "4825".into());
    env.insert("LATTICEAI_RATE_LIMIT".into(), "0".into());
    env.insert(
        "LATTICEAI_DATA_DIR".into(),
        data.to_string_lossy().into_owned(),
    );
    let mut config = AuthConfig::from_map(&env, None);
    config.data_dir = data.to_path_buf();
    let auth = AuthState::with_clock(config, Clock::frozen(1_786_000_000.0));
    let token = auth
        .sessions()
        .create("user:owner", Some("owner@lattice.test"));

    // A tiny FakeWorker so the 503 branch is not the only one we can hit.
    let worker = spawn_fake_worker().await;
    let seam = WorkerSeamClient::new(&worker).unwrap();
    let runtime = RuntimeConfig::resolve(
        Some(data.to_str().unwrap()),
        None,
        Some(worker.as_str()),
        Some(data),
    );
    let state = BrowserState::new(auth, runtime).with_seam(seam);
    let app = browser_api::router(Arc::new(state));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr: SocketAddr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        let _ = axum::serve(
            listener,
            app.into_make_service_with_connect_info::<SocketAddr>(),
        )
        .await;
    });
    (format!("http://{addr}"), token)
}

async fn spawn_fake_worker() -> String {
    use axum::extract::Json;
    use axum::routing::post;
    let app = Router::new()
        .route(
            "/api/browser/ingest-current-tab",
            post(|Json(body): Json<Value>| async move {
                axum::Json(serde_json::json!({
                    "status": "ok",
                    "source_type": "browser_tab",
                    "title": body.get("title").cloned().unwrap_or(Value::Null),
                    "duplicate": false,
                    "capture_quality": {"status": "ok", "reason": null, "reason_codes": [], "suggestions": []}
                }))
            }),
        )
        .route(
            "/knowledge-graph/ingest",
            post(|| async move {
                axum::Json(serde_json::json!({"status": "ok", "source_type": "web_url"}))
            }),
        )
        .route(
            "/worker/extract",
            post(|| async move {
                axum::Json(serde_json::json!({
                    "concepts": [],
                    "triples": [],
                    "semantic": []
                }))
            }),
        )
        .route(
            "/worker/embed",
            post(|Json(body): Json<Value>| async move {
                let model = lattice_core::embeddings::LocalEmbeddingModel::from_env();
                let vectors: Vec<Vec<f64>> = body["texts"]
                    .as_array()
                    .cloned()
                    .unwrap_or_default()
                    .iter()
                    .map(|text| model.embed(text.as_str().unwrap_or("")))
                    .collect();
                axum::Json(serde_json::json!({
                    "vectors": vectors,
                    "dim": model.dim(),
                    "model_id": model.model_id(),
                    "kind": body["kind"].as_str().unwrap_or("passage"),
                }))
            }),
        );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        let _ = axum::serve(listener, app.into_make_service()).await;
    });
    format!("http://{addr}")
}

#[tokio::test]
async fn browser_replay_validation_and_private_hosts() {
    let data = tempfile::tempdir().unwrap();
    let (origin, token) = boot(data.path()).await;
    let client = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(10))
        .build()
        .unwrap();

    let denied = client
        .post(format!("{origin}/api/browser/read-url"))
        .header("content-type", "application/json")
        .body(serde_json::to_vec(&serde_json::json!({"url": "https://example.com"})).unwrap())
        .send()
        .await
        .unwrap();
    assert_eq!(denied.status().as_u16(), 401);

    let missing = client
        .post(format!("{origin}/api/browser/read-url"))
        .header("cookie", format!("session_token={token}"))
        .header("content-type", "application/json")
        .body(serde_json::to_vec(&serde_json::json!({})).unwrap())
        .send()
        .await
        .unwrap();
    assert_eq!(missing.status().as_u16(), 422);

    let scheme = client
        .post(format!("{origin}/api/browser/read-url"))
        .header("cookie", format!("session_token={token}"))
        .header("content-type", "application/json")
        .body(serde_json::to_vec(&serde_json::json!({"url": "ftp://example.com"})).unwrap())
        .send()
        .await
        .unwrap();
    assert_eq!(scheme.status().as_u16(), 400);
    let body: Value = json_of(scheme).await;
    assert!(body["detail"].as_str().unwrap().contains("http(s)"));

    for url in [
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://192.168.0.1/",
        "http://localhost/",
    ] {
        let response = client
            .post(format!("{origin}/api/browser/read-url"))
            .header("cookie", format!("session_token={token}"))
            .header("content-type", "application/json")
            .body(serde_json::to_vec(&serde_json::json!({"url": url})).unwrap())
            .send()
            .await
            .unwrap();
        assert_eq!(response.status().as_u16(), 422, "{url}");
        let body: Value = json_of(response).await;
        assert!(
            body["detail"].as_str().unwrap().contains("private")
                || body["detail"].as_str().unwrap().contains("Local"),
            "{url} {}",
            body
        );
    }

    let tab = client
        .post(format!("{origin}/api/browser/ingest-current-tab"))
        .header("cookie", format!("session_token={token}"))
        .header("content-type", "application/json")
        .body(
            serde_json::to_vec(&serde_json::json!({
                "url": "https://example.com/handbook",
                "title": "Lattice handbook — retrieval",
                "text": "The lexical channel scores one over rank."
            }))
            .unwrap(),
        )
        .send()
        .await
        .unwrap();
    assert_eq!(tab.status().as_u16(), 200);
    let body: Value = json_of(tab).await;
    assert_eq!(body["source_type"], "browser_tab");
    assert_eq!(body["status"], "ok");
    assert!(body["node_id"]
        .as_str()
        .is_some_and(|id| id.starts_with("webdoc:")));

    let empty = client
        .post(format!("{origin}/api/browser/ingest-current-tab"))
        .header("cookie", format!("session_token={token}"))
        .header("content-type", "application/json")
        .body(
            serde_json::to_vec(&serde_json::json!({"url": "https://example.com", "text": ""}))
                .unwrap(),
        )
        .send()
        .await
        .unwrap();
    assert_eq!(empty.status().as_u16(), 400);
}

async fn json_of(response: reqwest::Response) -> Value {
    let text = response.text().await.unwrap();
    serde_json::from_str(&text).unwrap_or_else(|err| panic!("not JSON ({err}): {text}"))
}
