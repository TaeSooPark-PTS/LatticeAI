//! Batched `/worker/embed` during a native drain, against a fake worker.

#[allow(dead_code)]
mod common;

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use common::{client, json, FakeWorker};
use lattice_auth::{AuthConfig, AuthState, Clock};
use lattice_core::db::{RuntimeConfig, Store};
use lattice_core::graph_write::GraphWriter;
use lattice_core::worker::WorkerSeamClient;
use lattice_jobs::index_api::{self, IndexApiState};
use serde_json::json;

struct DrainHost {
    origin: String,
    _handle: tokio::task::JoinHandle<()>,
}

impl DrainHost {
    async fn start(state: Arc<IndexApiState>) -> Self {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind");
        let addr = listener.local_addr().expect("addr");
        let handle = tokio::spawn(async move {
            let _ = axum::serve(listener, index_api::router(state)).await;
        });
        Self {
            origin: format!("http://{addr}"),
            _handle: handle,
        }
    }

    async fn drain(&self, limit: u32) -> (u16, serde_json::Value) {
        let response = client()
            .post(format!("{}/api/index/drain", self.origin))
            .header("content-type", "application/json")
            .timeout(Duration::from_secs(5))
            .body(format!(r#"{{"limit":{limit}}}"#))
            .send()
            .await
            .expect("drain");
        let status = response.status().as_u16();
        (status, json(response).await)
    }
}

fn trusted_auth(data: &std::path::Path) -> Arc<AuthState> {
    let mut env = HashMap::new();
    env.insert(
        "LATTICEAI_DATA_DIR".into(),
        data.to_string_lossy().into_owned(),
    );
    env.insert("LATTICEAI_HOST".into(), "127.0.0.1".into());
    env.insert("LATTICEAI_PORT".into(), "4825".into());
    let mut config = AuthConfig::from_map(&env, None);
    config.data_dir = data.to_path_buf();
    AuthState::with_clock(config, Clock::frozen(1_786_000_000.0))
}

#[tokio::test]
async fn drain_batches_texts_to_the_worker_embed_seam() {
    let data = tempfile::tempdir().expect("tempdir");
    let data_dir = data.path().to_string_lossy().into_owned();
    let runtime = RuntimeConfig::resolve(Some(&data_dir), None, None, Some(data.path()));
    let db = runtime.graph_db_path();
    let store = Arc::new(Store::open(&db).expect("store"));
    let graph = GraphWriter::open(Arc::clone(&store), data.path().join("blobs")).expect("graph");
    graph
        .upsert_nodes(&[lattice_core::graph_write::types::NodeSpec {
            id: "n-batch".into(),
            node_type: "Note".into(),
            title: "Batch note".into(),
            summary: "a short passage the seam should embed".into(),
            ..Default::default()
        }])
        .expect("node");
    {
        let conn = rusqlite::Connection::open(&db).expect("open");
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS vector_jobs (
               node_id TEXT PRIMARY KEY,
               status TEXT NOT NULL DEFAULT 'pending',
               attempts INTEGER NOT NULL DEFAULT 0,
               detail TEXT,
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL
             );
             INSERT INTO vector_jobs(node_id, status, created_at, updated_at)
             VALUES ('n-batch', 'pending', '2026-08-01T00:00:00', '2026-08-01T00:00:00');",
        )
        .expect("job");
    }

    let worker = FakeWorker::start().await;
    let seam = WorkerSeamClient::new(worker.origin()).expect("seam");
    let state = IndexApiState::new(trusted_auth(data.path()), Some(store), runtime)
        .with_graph(graph)
        .with_seam(seam)
        .with_worker_embed(true);
    let host = DrainHost::start(Arc::new(state)).await;

    let (status, body) = host.drain(25).await;
    assert_eq!(status, 200, "{body}");
    assert_eq!(body["claimed"], json!(1));
    assert!(
        body["indexed"].as_u64().unwrap_or(0) + body["retried"].as_u64().unwrap_or(0) >= 1,
        "{body}"
    );

    let embeds = worker.requests_to("/worker/embed");
    assert_eq!(embeds.len(), 1, "one batched embed, not one per item");
    let posted: serde_json::Value = serde_json::from_str(&embeds[0].body_text()).expect("json");
    assert_eq!(posted["kind"], json!("passage"));
    let texts = posted["texts"].as_array().expect("texts");
    assert!(
        !texts.is_empty(),
        "the drain must send the node text to the seam"
    );
    worker.shutdown();
}

#[tokio::test]
async fn a_busy_embed_seam_releases_the_claim() {
    let data = tempfile::tempdir().expect("tempdir");
    let data_dir = data.path().to_string_lossy().into_owned();
    let runtime = RuntimeConfig::resolve(Some(&data_dir), None, None, Some(data.path()));
    let db = runtime.graph_db_path();
    let store = Arc::new(Store::open(&db).expect("store"));
    let graph = GraphWriter::open(Arc::clone(&store), data.path().join("blobs")).expect("graph");
    graph
        .upsert_nodes(&[lattice_core::graph_write::types::NodeSpec {
            id: "n-busy".into(),
            node_type: "Note".into(),
            title: "Busy".into(),
            summary: "text".into(),
            ..Default::default()
        }])
        .expect("node");
    {
        let conn = rusqlite::Connection::open(&db).expect("open");
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS vector_jobs (
               node_id TEXT PRIMARY KEY,
               status TEXT NOT NULL DEFAULT 'pending',
               attempts INTEGER NOT NULL DEFAULT 0,
               detail TEXT,
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL
             );
             INSERT INTO vector_jobs(node_id, status, created_at, updated_at)
             VALUES ('n-busy', 'pending', '2026-08-01T00:00:00', '2026-08-01T00:00:00');",
        )
        .expect("job");
    }

    let worker = FakeWorker::start_with(common::Behaviour {
        embed_status: 429,
        embed_body: r#"{"detail":"rate limited"}"#.into(),
        ..common::Behaviour::default()
    })
    .await;
    let seam = WorkerSeamClient::new(worker.origin()).expect("seam");
    let state = IndexApiState::new(trusted_auth(data.path()), Some(store), runtime)
        .with_graph(graph)
        .with_seam(seam)
        .with_worker_embed(true);
    let host = DrainHost::start(Arc::new(state)).await;

    let (status, body) = host.drain(25).await;
    assert_eq!(status, 500, "{body}");
    assert!(
        body["detail"].as_str().unwrap_or("").contains("429")
            || body["detail"].as_str().unwrap_or("").contains("busy"),
        "{body}"
    );

    let conn = rusqlite::Connection::open(&db).expect("open");
    let job_status: String = conn
        .query_row(
            "SELECT status FROM vector_jobs WHERE node_id='n-busy'",
            [],
            |row| row.get(0),
        )
        .expect("status");
    assert_eq!(
        job_status, "pending",
        "a busy worker must not eat the claim"
    );
    worker.shutdown();
}
