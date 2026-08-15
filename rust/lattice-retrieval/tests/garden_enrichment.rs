//! Garden ingest now supplies per-chunk vectors (F-ING part 2).

use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::Path;
use std::sync::Arc;

use axum::extract::Json;
use axum::routing::post;
use axum::Router;
use lattice_auth::{AuthConfig, AuthState, Clock, OrderedMap};
use lattice_core::db::{RuntimeConfig, Store};
use lattice_core::graph_write::GraphWriter;
use lattice_core::worker::WorkerSeamClient;
use lattice_retrieval::garden_api;
use lattice_retrieval::memory_api::shared::BrainState;
use serde_json::{json, Value};

fn seed_users(dir: &Path) {
    let email = "owner@fixture.local";
    let mut owner = OrderedMap::new();
    owner.insert("password", json!("x"));
    owner.insert("nickname", json!("owner"));
    owner.insert("role", json!("admin"));
    owner.insert("disabled", json!(false));
    owner.insert("id", json!(lattice_auth::stable_user_id(email)));
    owner.insert("email", json!(email));
    let mut users = OrderedMap::new();
    users.insert(email, serde_json::to_value(owner).unwrap());
    std::fs::write(
        dir.join("users.json"),
        lattice_auth::pyjson::dumps_indent2(&users).expect("users"),
    )
    .expect("write users");
}

async fn spawn_worker() -> String {
    let app = Router::new()
        .route(
            "/worker/extract",
            post(|Json(_body): Json<Value>| async move {
                axum::Json(json!({
                    "concepts": [{"text": "Lattice", "node_type": "Concept"}],
                    "triples": [],
                    "semantic": [],
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
                axum::Json(json!({
                    "vectors": vectors,
                    "dim": model.dim(),
                    "model_id": model.model_id(),
                    "kind": "passage",
                }))
            }),
        );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind");
    let addr = listener.local_addr().expect("addr");
    tokio::spawn(async move {
        let _ = axum::serve(listener, app.into_make_service()).await;
    });
    format!("http://{addr}")
}

#[tokio::test]
async fn a_garden_note_writes_chunk_vectors_when_the_embedder_agrees() {
    let data = tempfile::tempdir().expect("data");
    seed_users(data.path());
    let mut env = HashMap::new();
    env.insert("LATTICEAI_REQUIRE_AUTH".into(), "1".into());
    env.insert(
        "LATTICEAI_DATA_DIR".into(),
        data.path().to_string_lossy().into_owned(),
    );
    let mut config = AuthConfig::from_map(&env, None);
    config.data_dir = data.path().to_path_buf();
    let auth = AuthState::with_clock(config, Clock::frozen(1_786_000_000.0));
    let token = auth
        .sessions()
        .create("user:owner", Some("owner@fixture.local"));
    let db = data.path().join("knowledge_graph.sqlite");
    let store = Arc::new(Store::open(&db).expect("store"));
    let graph = GraphWriter::open(
        Arc::clone(&store),
        data.path().join("knowledge_graph_blobs"),
    )
    .expect("graph");
    let worker = spawn_worker().await;
    let runtime = RuntimeConfig::resolve(
        Some(data.path().to_str().unwrap()),
        None,
        Some(worker.as_str()),
        Some(data.path()),
    );
    let state = BrainState::new(auth, runtime, store)
        .with_graph(graph)
        .with_seam(WorkerSeamClient::new(&worker).expect("seam"))
        .with_brain_dir(data.path().join("brain"));
    let app = garden_api::router(state);
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind");
    let addr: SocketAddr = listener.local_addr().expect("addr");
    tokio::spawn(async move {
        let _ = axum::serve(
            listener,
            app.into_make_service_with_connect_info::<SocketAddr>(),
        )
        .await;
    });
    let client = reqwest::Client::builder()
        .no_proxy()
        .build()
        .expect("client");
    let response = client
        .post(format!("http://{addr}/garden"))
        .header("cookie", format!("session_token={token}"))
        .header("content-type", "application/json")
        .body(
            serde_json::to_vec(&json!({
                "raw_data": "A garden note about Lattice retrieval."
            }))
            .unwrap(),
        )
        .send()
        .await
        .expect("garden");
    assert_eq!(response.status().as_u16(), 200);
    let body: Value = serde_json::from_str(&response.text().await.expect("text")).expect("json");
    assert_eq!(body["status"], "saved");
    assert_eq!(body["graph"], "ok");
    let conn = rusqlite::Connection::open(&db).expect("db");
    let chunks: i64 = conn
        .query_row("SELECT COUNT(*) FROM chunks", [], |row| row.get(0))
        .expect("chunks");
    assert!(chunks >= 1, "garden ingest must write chunk rows");
    let vectors: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM vector_embeddings WHERE item_type='chunk'",
            [],
            |row| row.get(0),
        )
        .expect("vectors");
    assert!(vectors >= 1, "chunk rows must carry supplied vectors");
}
