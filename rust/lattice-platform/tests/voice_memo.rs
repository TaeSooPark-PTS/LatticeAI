//! N8: a text-only voice memo must keep the caller's words.

use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use axum::routing::post;
use axum::{Json, Router};
use lattice_auth::{AuthConfig, AuthState, Clock};
use lattice_core::db::Store;
use lattice_core::graph_write::GraphWriter;
use lattice_core::worker::WorkerSeamClient;
use lattice_platform::voice::{self, VoiceState};
use serde_json::{json, Value};

async fn unavailable_asr(Json(_body): Json<Value>) -> Json<Value> {
    Json(json!({
        "status": "unavailable",
        "text": "",
        "provider": "",
        "detail": "no local transcriber"
    }))
}

async fn stand_in_asr() -> String {
    let app = Router::new().route("/worker/asr", post(unavailable_asr));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind asr");
    let addr: SocketAddr = listener.local_addr().expect("addr");
    tokio::spawn(async move {
        let _ = axum::serve(listener, app.into_make_service()).await;
    });
    format!("http://{addr}")
}

struct Install {
    origin: String,
    db: PathBuf,
    _dir: tempfile::TempDir,
    _handle: tokio::task::JoinHandle<()>,
}

impl Install {
    async fn start() -> Self {
        let dir = tempfile::tempdir().expect("tempdir");
        let data = dir.path().to_path_buf();
        let mut env = HashMap::new();
        env.insert(
            "LATTICEAI_DATA_DIR".into(),
            data.to_string_lossy().into_owned(),
        );
        env.insert("LATTICEAI_HOST".into(), "127.0.0.1".into());
        let mut config = AuthConfig::from_map(&env, None);
        config.data_dir = data.clone();
        let auth = AuthState::with_clock(config, Clock::system());

        let db = data.join("knowledge_graph.sqlite");
        let store = Arc::new(Store::open(&db).expect("store"));
        let graph = GraphWriter::open(Arc::clone(&store), data.join("knowledge_graph_blobs"))
            .expect("graph");
        let asr = stand_in_asr().await;
        let state = VoiceState {
            auth: Some(auth),
            graph: Some(graph),
            seam: Some(WorkerSeamClient::new(&asr).expect("seam")),
        };
        let app = voice::router_with(state);
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind");
        let addr: SocketAddr = listener.local_addr().expect("addr");
        let handle = tokio::spawn(async move {
            let _ = axum::serve(listener, app.into_make_service()).await;
        });
        Self {
            origin: format!("http://{addr}"),
            db,
            _dir: dir,
            _handle: handle,
        }
    }
}

#[tokio::test]
async fn a_text_only_voice_memo_keeps_the_callers_words() {
    let install = Install::start().await;
    let client = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(15))
        .build()
        .expect("client");
    let response = client
        .post(format!("{}/api/capture/voice", install.origin))
        .header("content-type", "application/json")
        .body(
            serde_json::to_vec(&json!({
                "title": "memo",
                "text": "buy oat milk tomorrow"
            }))
            .unwrap(),
        )
        .send()
        .await
        .expect("request");
    assert_eq!(response.status().as_u16(), 200);
    let body: Value = response.json().await.expect("json");
    assert_eq!(body["transcription"], json!("unavailable"));
    assert_eq!(body["text"], json!("buy oat milk tomorrow"));
    assert!(
        body["node_id"].as_str().is_some_and(|id| !id.is_empty()),
        "{body}"
    );

    let conn = lattice_core::db::open_read_only(&install.db).expect("ro");
    let found: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM nodes WHERE type='Audio' AND (title=? OR summary LIKE ?)",
            rusqlite::params!["memo", "%buy oat milk tomorrow%"],
            |row| row.get(0),
        )
        .expect("count");
    assert_eq!(found, 1, "the memo must be persisted and findable");
}

#[tokio::test]
async fn an_empty_unavailable_transcript_does_not_insert_a_node() {
    let install = Install::start().await;
    let client = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(15))
        .build()
        .expect("client");
    let response = client
        .post(format!("{}/api/capture/voice", install.origin))
        .header("content-type", "application/json")
        .body(serde_json::to_vec(&json!({"title": "silence"})).unwrap())
        .send()
        .await
        .expect("request");
    assert_eq!(response.status().as_u16(), 200);
    let body: Value = response.json().await.expect("json");
    assert_eq!(body["transcription"], json!("unavailable"));
    assert_eq!(body["text"], json!(""));
    assert!(body["node_id"].is_null(), "{body}");

    let conn = lattice_core::db::open_read_only(&install.db).expect("ro");
    let found: i64 = conn
        .query_row("SELECT COUNT(*) FROM nodes WHERE type='Audio'", [], |row| {
            row.get(0)
        })
        .expect("count");
    assert_eq!(found, 0, "empty content nodes must not be inserted");
}
