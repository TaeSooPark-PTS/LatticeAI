use std::collections::HashMap;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;

use axum::extract::State;
use axum::http::StatusCode;
use axum::routing::post;
use axum::{Json, Router};
use lattice_auth::{AuthConfig, AuthState};
use lattice_core::db::{RuntimeConfig, Store};
use lattice_core::embedding_contract::EmbeddingIndexContract;
use lattice_core::graph_write::GraphWriter;
use lattice_core::worker::WorkerSeamClient;
use lattice_retrieval::search_api::{self, RetrievalApiState};
use serde_json::{json, Value};
use tokio::net::TcpListener;

#[derive(Clone)]
struct MockWorker {
    calls: Arc<AtomicUsize>,
    delay: Duration,
    fail: bool,
    model_id: String,
    vector: Vec<f64>,
}

async fn embed_handler(
    State(mock): State<MockWorker>,
    Json(body): Json<Value>,
) -> (StatusCode, Json<Value>) {
    mock.calls.fetch_add(1, Ordering::SeqCst);
    assert_eq!(body.get("kind").and_then(Value::as_str), Some("query"));
    if !mock.delay.is_zero() {
        tokio::time::sleep(mock.delay).await;
    }
    if mock.fail {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"detail": "embed provider down"})),
        );
    }
    (
        StatusCode::OK,
        Json(json!({
            "provider": "huggingface",
            "model_id": mock.model_id,
            "dim": mock.vector.len(),
            "vectors": [mock.vector],
        })),
    )
}

async fn spawn_router(app: Router) -> String {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        let _ = axum::serve(listener, app).await;
    });
    format!("http://{addr}")
}

async fn spawn_mock_worker(mock: MockWorker) -> String {
    spawn_router(
        Router::new()
            .route("/worker/embed", post(embed_handler))
            .with_state(mock),
    )
    .await
}

fn build_semantic_fixture(
    dir: &std::path::Path,
    model_id: &str,
    vec_bytes: &[u8],
    dim: usize,
) -> (Arc<AuthState>, Arc<Store>, RuntimeConfig, GraphWriter) {
    let data_str = dir.to_string_lossy().into_owned();
    let mut env = HashMap::new();
    env.insert("LATTICEAI_DATA_DIR".to_string(), data_str.clone());
    let auth = AuthState::new(AuthConfig::from_map(&env, None));
    let config = RuntimeConfig::resolve(Some(&data_str), None, None, None);
    let store = Arc::new(Store::open(&config.graph_db_path()).unwrap());
    let writer = GraphWriter::open(Arc::clone(&store), dir.join("blobs")).unwrap();

    let contract = EmbeddingIndexContract::from_semantic("huggingface", model_id, dim);
    let conn = rusqlite::Connection::open(config.graph_db_path()).unwrap();
    conn.execute(
        "INSERT OR REPLACE INTO graph_meta(key, value) VALUES ('embedding_index_contract', ?1)",
        [serde_json::to_string(&contract).unwrap()],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO nodes(id, type, title, summary, metadata_json, raw_json, created_at, updated_at) \
         VALUES ('sem-1', 'Document', 'Satellite Attitude Report', 'Gyroscope drift mitigation notes', '{}', '{}', '2026-08-01T00:00:00', '2026-08-01T00:00:00')",
        [],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO nodes_v2(id, type, legacy_type, label, summary, attrs, workspace_id, created_at, updated_at) \
         VALUES ('sem-1', 'Document', 'Document', 'Satellite Attitude Report', 'Gyroscope drift mitigation notes', '{}', 'personal', '2026-08-01T00:00:00', '2026-08-01T00:00:00')",
        [],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO nodes(id, type, title, summary, metadata_json, raw_json, created_at, updated_at) \
         VALUES ('lex-1', 'Note', 'Orbital Telemetry Log', 'Routine orbital telemetry checklist', '{}', '{}', '2026-08-01T00:00:00', '2026-08-01T00:00:00')",
        [],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO nodes_v2(id, type, legacy_type, label, summary, attrs, workspace_id, created_at, updated_at) \
         VALUES ('lex-1', 'Note', 'Note', 'Orbital Telemetry Log', 'Routine orbital telemetry checklist', '{}', 'personal', '2026-08-01T00:00:00', '2026-08-01T00:00:00')",
        [],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO vector_embeddings(item_id, item_type, source_node, embedding, embedding_dim, embedding_model, text_hash, metadata_json, indexed_at) 
         VALUES ('sem-1', 'node', 'sem-1', ?1, ?2, ?3, 'h1', '{}', '2026-08-01T00:00:00')",
        rusqlite::params![vec_bytes, dim as i64, model_id],
    )
    .unwrap();
    (auth, store, config, writer)
}

fn encode_f32_le(vals: &[f32]) -> Vec<u8> {
    let mut out = Vec::with_capacity(vals.len() * 4);
    for v in vals {
        out.extend_from_slice(&v.to_le_bytes());
    }
    out
}

async fn post_json(
    client: &reqwest::Client,
    url: String,
    payload: &Value,
) -> (reqwest::StatusCode, Value) {
    let resp = client
        .post(url)
        .header("content-type", "application/json")
        .body(payload.to_string())
        .send()
        .await
        .unwrap();
    let status = resp.status();
    let text = resp.text().await.unwrap();
    let parsed = serde_json::from_str(&text).unwrap_or(Value::Null);
    (status, parsed)
}

#[tokio::test]
async fn semantic_store_embeds_queries_via_worker_seam_for_vector_and_hybrid() {
    let dir = tempfile::tempdir().unwrap();
    let model_id = "huggingface:google/embeddinggemma-2:4";
    let vec_bytes = encode_f32_le(&[1.0, 0.0, 0.0, 0.0]);
    let (auth, store, config, writer) = build_semantic_fixture(dir.path(), model_id, &vec_bytes, 4);

    let calls = Arc::new(AtomicUsize::new(0));
    let worker_origin = spawn_mock_worker(MockWorker {
        calls: Arc::clone(&calls),
        delay: Duration::ZERO,
        fail: false,
        model_id: model_id.to_string(),
        vector: vec![1.0, 0.0, 0.0, 0.0],
    })
    .await;

    let seam = WorkerSeamClient::new(&worker_origin).unwrap();
    let state = Arc::new(
        RetrievalApiState::new(auth, Some(store), config)
            .with_graph(writer)
            .with_seam(seam),
    );
    let api_origin = spawn_router(search_api::router(state)).await;
    let client = reqwest::Client::builder().no_proxy().build().unwrap();

    let (status, body) = post_json(
        &client,
        format!("{api_origin}/api/search/vector"),
        &json!({"query": "spacecraft orientation failure", "limit": 5}),
    )
    .await;
    assert_eq!(status, reqwest::StatusCode::OK);
    assert_eq!(body["provider_status"], "semantic_provider_match");
    assert_eq!(body["matches"][0]["id"], "sem-1");
    assert_eq!(calls.load(Ordering::SeqCst), 1);

    let (status2, body2) = post_json(
        &client,
        format!("{api_origin}/api/search/hybrid"),
        &json!({"query": "spacecraft orientation failure", "limit": 5}),
    )
    .await;
    assert_eq!(status2, reqwest::StatusCode::OK);
    let ids: Vec<&str> = body2["matches"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|r| r.get("id").and_then(Value::as_str))
        .collect();
    assert!(
        ids.contains(&"sem-1"),
        "expected semantic match in hybrid results: {body2}"
    );
    assert_eq!(calls.load(Ordering::SeqCst), 2);
}

#[tokio::test]
async fn semantic_store_degrades_gracefully_to_lexical_when_worker_embed_fails() {
    let dir = tempfile::tempdir().unwrap();
    let model_id = "huggingface:google/embeddinggemma-2:4";
    let vec_bytes = encode_f32_le(&[1.0, 0.0, 0.0, 0.0]);
    let (auth, store, config, writer) = build_semantic_fixture(dir.path(), model_id, &vec_bytes, 4);

    let calls = Arc::new(AtomicUsize::new(0));
    let worker_origin = spawn_mock_worker(MockWorker {
        calls: Arc::clone(&calls),
        delay: Duration::ZERO,
        fail: true,
        model_id: model_id.to_string(),
        vector: vec![1.0, 0.0, 0.0, 0.0],
    })
    .await;

    let seam = WorkerSeamClient::new(&worker_origin).unwrap();
    let state = Arc::new(
        RetrievalApiState::new(auth, Some(store), config)
            .with_graph(writer)
            .with_seam(seam),
    );
    let api_origin = spawn_router(search_api::router(state)).await;
    let client = reqwest::Client::builder().no_proxy().build().unwrap();

    let (status, body) = post_json(
        &client,
        format!("{api_origin}/api/search/hybrid"),
        &json!({"query": "Orbital Telemetry", "limit": 5}),
    )
    .await;
    assert_eq!(status, reqwest::StatusCode::OK);
    let ids: Vec<&str> = body["matches"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|r| r.get("id").and_then(Value::as_str))
        .collect();
    assert!(
        ids.contains(&"lex-1"),
        "lexical fallback must still return lex-1: {body}"
    );
    assert_eq!(calls.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn hash_store_never_calls_worker_embed_seam() {
    let dir = tempfile::tempdir().unwrap();
    let data_str = dir.path().to_string_lossy().into_owned();
    let mut env = HashMap::new();
    env.insert("LATTICEAI_DATA_DIR".to_string(), data_str.clone());
    let auth = AuthState::new(AuthConfig::from_map(&env, None));
    let config = RuntimeConfig::resolve(Some(&data_str), None, None, None);
    let store = Arc::new(Store::open(&config.graph_db_path()).unwrap());
    let writer = GraphWriter::open(Arc::clone(&store), dir.path().join("blobs")).unwrap();

    let calls = Arc::new(AtomicUsize::new(0));
    let worker_origin = spawn_mock_worker(MockWorker {
        calls: Arc::clone(&calls),
        delay: Duration::ZERO,
        fail: false,
        model_id: "lattice-local-hash-v1".to_string(),
        vector: vec![0.0; 384],
    })
    .await;

    let seam = WorkerSeamClient::new(&worker_origin).unwrap();
    let state = Arc::new(
        RetrievalApiState::new(auth, Some(store), config)
            .with_graph(writer)
            .with_seam(seam),
    );
    let api_origin = spawn_router(search_api::router(state)).await;
    let client = reqwest::Client::builder().no_proxy().build().unwrap();

    let (status, _body) = post_json(
        &client,
        format!("{api_origin}/api/search/hybrid"),
        &json!({"query": "anything", "limit": 5}),
    )
    .await;
    assert_eq!(status, reqwest::StatusCode::OK);
    assert_eq!(calls.load(Ordering::SeqCst), 0);
}

#[tokio::test]
async fn a_worker_serving_another_model_degrades_instead_of_scoring_across_spaces() {
    let dir = tempfile::tempdir().unwrap();
    let model_id = "huggingface:google/embeddinggemma-2:4";
    let vec_bytes = encode_f32_le(&[1.0, 0.0, 0.0, 0.0]);
    let (auth, store, config, writer) = build_semantic_fixture(dir.path(), model_id, &vec_bytes, 4);

    let calls = Arc::new(AtomicUsize::new(0));
    let worker_origin = spawn_mock_worker(MockWorker {
        calls: Arc::clone(&calls),
        delay: Duration::ZERO,
        fail: false,
        model_id: "lattice-local-hash-v1:4".to_string(),
        vector: vec![1.0, 0.0, 0.0, 0.0],
    })
    .await;

    let seam = WorkerSeamClient::new(&worker_origin).unwrap();
    let state = Arc::new(
        RetrievalApiState::new(auth, Some(store), config)
            .with_graph(writer)
            .with_seam(seam),
    );
    let api_origin = spawn_router(search_api::router(state)).await;
    let client = reqwest::Client::builder().no_proxy().build().unwrap();

    let (status, body) = post_json(
        &client,
        format!("{api_origin}/api/search/vector"),
        &json!({"query": "spacecraft orientation failure", "limit": 5}),
    )
    .await;
    assert_eq!(status, reqwest::StatusCode::OK);
    assert_eq!(body["provider_status"], "degraded");
    let reason = body["degraded_reason"].as_str().unwrap();
    assert!(
        reason.starts_with("query_embed_contract_mismatch"),
        "{reason}"
    );
    assert_eq!(calls.load(Ordering::SeqCst), 1);
}

#[derive(Clone)]
struct RerankWorker {
    rerank_calls: Arc<AtomicUsize>,
    favourite: &'static str,
}

async fn rerank_handler(
    State(worker): State<RerankWorker>,
    Json(body): Json<Value>,
) -> (StatusCode, Json<Value>) {
    worker.rerank_calls.fetch_add(1, Ordering::SeqCst);
    let scores: Vec<f64> = body["documents"]
        .as_array()
        .unwrap()
        .iter()
        .map(|doc| {
            if doc.as_str().unwrap_or("").contains(worker.favourite) {
                0.99
            } else {
                0.01
            }
        })
        .collect();
    (
        StatusCode::OK,
        Json(json!({"scores": scores, "model_id": "cross-encoder:BAAI/bge-reranker-v2-m3"})),
    )
}

#[tokio::test]
async fn hybrid_search_promotes_the_cross_encoder_favourite_and_reports_it() {
    let dir = tempfile::tempdir().unwrap();
    let model_id = "huggingface:google/embeddinggemma-2:4";
    let vec_bytes = encode_f32_le(&[1.0, 0.0, 0.0, 0.0]);
    let (auth, store, config, writer) = build_semantic_fixture(dir.path(), model_id, &vec_bytes, 4);

    let embed_calls = Arc::new(AtomicUsize::new(0));
    let rerank_calls = Arc::new(AtomicUsize::new(0));
    let embed = Router::new()
        .route("/worker/embed", post(embed_handler))
        .with_state(MockWorker {
            calls: Arc::clone(&embed_calls),
            delay: Duration::ZERO,
            fail: false,
            model_id: model_id.to_string(),
            vector: vec![1.0, 0.0, 0.0, 0.0],
        });
    let rerank = Router::new()
        .route("/worker/rerank", post(rerank_handler))
        .with_state(RerankWorker {
            rerank_calls: Arc::clone(&rerank_calls),
            favourite: "Orbital Telemetry",
        });
    let worker_origin = spawn_router(embed.merge(rerank)).await;

    let seam = WorkerSeamClient::new(&worker_origin).unwrap();
    let state = Arc::new(
        RetrievalApiState::new(auth, Some(store), config)
            .with_graph(writer)
            .with_seam(seam),
    );
    let api_origin = spawn_router(search_api::router(state)).await;
    let client = reqwest::Client::builder().no_proxy().build().unwrap();

    for mode in ["", "graphrag"] {
        let (status, body) = post_json(
            &client,
            format!("{api_origin}/api/search/hybrid"),
            &json!({"query": "satellite telemetry report", "limit": 5, "mode": mode}),
        )
        .await;
        assert_eq!(status, reqwest::StatusCode::OK, "{body}");
        let matches = body["matches"].as_array().unwrap();
        if matches.len() >= 2 {
            assert_eq!(body["rerank"]["applied"], json!(true), "{mode}: {body}");
            assert_eq!(body["rerank"]["strategy"], json!("cross_encoder"));
            assert_eq!(matches[0]["id"], json!("lex-1"), "{mode}: {body}");
            assert_eq!(matches[0]["rerank_promoted"], json!(true));
        } else {
            assert_eq!(body["rerank"]["applied"], json!(false), "{mode}: {body}");
        }
    }
    assert!(rerank_calls.load(Ordering::SeqCst) >= 1);
}

#[tokio::test]
async fn sort_by_newest_reorders_after_the_cross_encoder_and_refuses_unknown_modes() {
    let dir = tempfile::tempdir().unwrap();
    let model_id = "huggingface:google/embeddinggemma-2:4";
    let vec_bytes = encode_f32_le(&[1.0, 0.0, 0.0, 0.0]);
    let (auth, store, config, writer) = build_semantic_fixture(dir.path(), model_id, &vec_bytes, 4);
    let embed = Router::new()
        .route("/worker/embed", post(embed_handler))
        .with_state(MockWorker {
            calls: Arc::new(AtomicUsize::new(0)),
            delay: Duration::ZERO,
            fail: false,
            model_id: model_id.to_string(),
            vector: vec![1.0, 0.0, 0.0, 0.0],
        });
    let rerank = Router::new()
        .route("/worker/rerank", post(rerank_handler))
        .with_state(RerankWorker {
            rerank_calls: Arc::new(AtomicUsize::new(0)),
            favourite: "Orbital Telemetry",
        });
    let worker_origin = spawn_router(embed.merge(rerank)).await;
    let state = Arc::new(
        RetrievalApiState::new(auth, Some(store), config)
            .with_graph(writer)
            .with_seam(WorkerSeamClient::new(&worker_origin).unwrap()),
    );
    let api_origin = spawn_router(search_api::router(state)).await;
    let client = reqwest::Client::builder().no_proxy().build().unwrap();

    for mode in ["", "graphrag"] {
        let (status, body) = post_json(
            &client,
            format!("{api_origin}/api/search/hybrid"),
            &json!({"query": "satellite telemetry report", "limit": 5, "mode": mode, "sort_by": "newest"}),
        )
        .await;
        assert_eq!(status, reqwest::StatusCode::OK, "{body}");
        assert_eq!(body["sort_by"], json!("newest"), "{mode}: {body}");
        let pipeline = body["temporal"]["pipeline"].as_array().unwrap();
        let rerank_at = pipeline.iter().position(|s| s == "rerank").unwrap();
        let sort_at = pipeline.iter().position(|s| s == "sort").unwrap();
        assert!(rerank_at < sort_at);
        assert_eq!(body["temporal"]["sort"]["applied"], json!("newest"));
        assert!(body["rerank"].is_object(), "{mode}: {body}");
        if body["rerank"]["applied"] == json!(true) {
            let promoted: Vec<&Value> = body["matches"]
                .as_array()
                .unwrap()
                .iter()
                .filter(|m| m["rerank_promoted"] == json!(true))
                .collect();
            assert!(!promoted.is_empty(), "the gate keeps promoted hits: {body}");
        }
    }

    let (status, body) = post_json(
        &client,
        format!("{api_origin}/api/search/hybrid"),
        &json!({"query": "satellite", "sort_by": "oldest"}),
    )
    .await;
    assert_eq!(status, reqwest::StatusCode::UNPROCESSABLE_ENTITY, "{body}");
    assert_eq!(body["detail"][0]["loc"], json!(["body", "sort_by"]));
    let refused = client
        .get(format!(
            "{api_origin}/api/search/vector?q=satellite&sort_by=oldest"
        ))
        .send()
        .await
        .unwrap();
    assert_eq!(refused.status(), reqwest::StatusCode::UNPROCESSABLE_ENTITY);
    for path in ["keyword", "vector"] {
        let (status, body) = post_json(
            &client,
            format!("{api_origin}/api/search/{path}"),
            &json!({"query": "satellite telemetry", "sort_by": "relevance", "include_historical": true}),
        )
        .await;
        assert_eq!(status, reqwest::StatusCode::OK, "{path}: {body}");
        assert_eq!(
            body["temporal"]["include_historical"],
            json!(true),
            "{path}: {body}"
        );
    }
}
