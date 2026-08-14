//! Replay GET /api/index/{queue,status} auth branches.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use lattice_auth::{AuthConfig, AuthState, Clock, OrderedMap};
use lattice_core::db::{RuntimeConfig, Store};
use lattice_jobs::index_api::{self, IndexApiState};
use serde_json::Value;

fn fixture() -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("fixtures")
        .join("http")
        .join("knowledge_search.json");
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

#[tokio::test]
async fn index_reads_replay_auth_denials_and_serve_200() {
    let data = tempfile::tempdir().unwrap();
    let db = data.path().join("knowledge_graph.sqlite");
    let conn = rusqlite::Connection::open(&db).unwrap();
    conn.execute_batch(
        "CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT, title TEXT, summary TEXT,
            metadata_json TEXT, created_at TEXT, updated_at TEXT);
         CREATE TABLE chunks(id TEXT PRIMARY KEY, source_node TEXT, text TEXT,
            metadata_json TEXT, created_at TEXT);
         CREATE TABLE vector_embeddings(item_id TEXT PRIMARY KEY, item_type TEXT,
            source_node TEXT, embedding BLOB, embedding_dim INT, embedding_model TEXT,
            text_hash TEXT, metadata_json TEXT, indexed_at TEXT);
         CREATE TABLE vector_jobs(id INTEGER PRIMARY KEY, status TEXT);
         CREATE TABLE vector_index_operations(id INTEGER PRIMARY KEY, operation TEXT,
            status TEXT, requested_at TEXT, started_at TEXT, completed_at TEXT,
            items_total INT, items_indexed INT, items_skipped INT, error_message TEXT,
            metadata_json TEXT);
         CREATE TABLE graph_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);",
    )
    .unwrap();
    drop(conn);

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
        data.path().join("users.json"),
        lattice_auth::pyjson::dumps_indent2(&users).expect("users"),
    )
    .unwrap();

    let mut env = HashMap::new();
    env.insert("LATTICEAI_REQUIRE_AUTH".into(), "1".into());
    env.insert("LATTICEAI_HOST".into(), "127.0.0.1".into());
    env.insert("LATTICEAI_PORT".into(), "4825".into());
    env.insert("LATTICEAI_RATE_LIMIT".into(), "0".into());
    env.insert(
        "LATTICEAI_DATA_DIR".into(),
        data.path().to_string_lossy().into_owned(),
    );
    let mut config = AuthConfig::from_map(&env, None);
    config.data_dir = data.path().to_path_buf();
    let auth = AuthState::with_clock(config, Clock::frozen(1_786_000_000.0));
    let token = auth
        .sessions()
        .create("user:owner", Some("owner@lattice.test"));

    let runtime = RuntimeConfig::resolve(
        Some(data.path().to_str().unwrap()),
        None,
        None,
        Some(data.path()),
    );
    let store = Arc::new(Store::open(&db).unwrap());
    let state = Arc::new(IndexApiState::new(auth, Some(store), runtime));
    let app = index_api::router(state);

    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr: SocketAddr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        let _ = axum::serve(
            listener,
            app.into_make_service_with_connect_info::<SocketAddr>(),
        )
        .await;
    });
    let origin = format!("http://{addr}");
    let client = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(10))
        .build()
        .unwrap();

    let denied = client
        .get(format!("{origin}/api/index/queue"))
        .send()
        .await
        .unwrap();
    assert_eq!(denied.status().as_u16(), 401);

    let ok = client
        .get(format!("{origin}/api/index/queue"))
        .header("cookie", format!("session_token={token}"))
        .send()
        .await
        .unwrap();
    assert_eq!(ok.status().as_u16(), 200);
    let body: Value = json_of(ok).await;
    assert!(body.get("available").is_some());
    assert!(body.get("pending").is_some());
    assert!(body.get("counts").is_some());

    let status = client
        .get(format!("{origin}/api/index/status"))
        .header("cookie", format!("session_token={token}"))
        .send()
        .await
        .unwrap();
    assert_eq!(status.status().as_u16(), 200);
    let body: Value = json_of(status).await;
    assert!(body.get("status").is_some());
    assert!(body.get("storage").is_some());

    // Fixture cases we explicitly do not claim.
    let root = fixture();
    for case in root["cases"].as_array().unwrap() {
        if case["family"].as_str() != Some("index_jobs") {
            continue;
        }
        let name = case["name"].as_str().unwrap();
        assert!(
            matches!(
                name,
                "queue"
                    | "index_status"
                    | "drain"
                    | "index_rebuild"
                    | "queue_auth_denied"
                    | "drain_auth_denied"
            ),
            "unexpected index_jobs case {name}"
        );
    }
}

async fn json_of(response: reqwest::Response) -> Value {
    let text = response.text().await.unwrap();
    serde_json::from_str(&text).unwrap_or_else(|err| panic!("not JSON ({err}): {text}"))
}
