use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::Path;
use std::sync::{Arc, Mutex};

use axum::extract::Json;
use axum::response::IntoResponse;
use axum::routing::post;
use axum::Router;
use lattice_auth::{AuthConfig, AuthState, Clock, OrderedMap};
use lattice_core::db::{RuntimeConfig, Store};
use lattice_core::graph_write::GraphWriter;
use lattice_core::worker::WorkerSeamClient;
use lattice_core::IngestStatus;
use lattice_ingest::browser_api::{self, BrowserState};
use lattice_ingest::local_files_api::{self, LocalFilesState};
use lattice_ingest::{NoteIngestor, NoteSubmission};
use serde_json::{json, Value};

#[derive(Clone)]
struct MockWorkerConfig {
    parse_ok: bool,
    extract_ok: bool,
    embed_ok: bool,
    provider: String,
    model_id: String,
    dim: usize,
    wrong_vector_len: Option<usize>,
}

impl Default for MockWorkerConfig {
    fn default() -> Self {
        Self {
            parse_ok: true,
            extract_ok: true,
            embed_ok: true,
            provider: "huggingface".into(),
            model_id: "huggingface:google/embeddinggemma-2:768".into(),
            dim: 4,
            wrong_vector_len: None,
        }
    }
}

async fn spawn_worker(cfg: Arc<Mutex<MockWorkerConfig>>) -> String {
    let parse_cfg = Arc::clone(&cfg);
    let extract_cfg = Arc::clone(&cfg);
    let embed_cfg = Arc::clone(&cfg);

    let app = Router::new()
        .route(
            "/worker/parse",
            post(move |Json(body): Json<Value>| {
                let cfg = Arc::clone(&parse_cfg);
                async move {
                    let ok = cfg.lock().unwrap().parse_ok;
                    if ok {
                        let name = body["filename"].as_str().unwrap_or("file.pdf");
                        axum::Json(json!({
                            "filename": name,
                            "content": "Parsed PDF document content for semantic contract test.",
                            "format": "pdf"
                        }))
                        .into_response()
                    } else {
                        (
                            axum::http::StatusCode::SERVICE_UNAVAILABLE,
                            axum::Json(json!({"detail": "parser unavailable"})),
                        )
                            .into_response()
                    }
                }
            }),
        )
        .route(
            "/worker/extract",
            post(move |_body: Json<Value>| {
                let cfg = Arc::clone(&extract_cfg);
                async move {
                    let ok = cfg.lock().unwrap().extract_ok;
                    if ok {
                        axum::Json(json!({
                            "concepts": [{"name": "Lattice", "type": "Topic"}],
                            "triples": [],
                            "semantic": []
                        }))
                        .into_response()
                    } else {
                        (
                            axum::http::StatusCode::SERVICE_UNAVAILABLE,
                            axum::Json(json!({"detail": "extractor unavailable"})),
                        )
                            .into_response()
                    }
                }
            }),
        )
        .route(
            "/worker/embed",
            post(move |Json(body): Json<Value>| {
                let cfg = Arc::clone(&embed_cfg);
                async move {
                    let snapshot = cfg.lock().unwrap().clone();
                    if !snapshot.embed_ok {
                        return (
                            axum::http::StatusCode::SERVICE_UNAVAILABLE,
                            axum::Json(json!({"detail": "embedder unavailable"})),
                        )
                            .into_response();
                    }
                    let texts = body["texts"].as_array().cloned().unwrap_or_default();
                    let vec_len = snapshot.wrong_vector_len.unwrap_or(snapshot.dim);
                    let vectors: Vec<Vec<f64>> = texts
                        .iter()
                        .enumerate()
                        .map(|(idx, _)| {
                            (0..vec_len)
                                .map(|j| ((idx + 1) as f64 * 0.1) + (j as f64 * 0.05))
                                .collect()
                        })
                        .collect();
                    axum::Json(json!({
                        "vectors": vectors,
                        "dim": snapshot.dim,
                        "provider": snapshot.provider,
                        "model_id": snapshot.model_id,
                        "contract": {
                            "provider": snapshot.provider,
                            "model_id": snapshot.model_id,
                            "model_revision": "rev-1",
                            "dimension": snapshot.dim,
                            "normalization": "l2",
                            "passage_mode": "embeddinggemma_passage",
                            "query_mode": "embeddinggemma_query",
                            "index_version": 1,
                            "grade": "semantic",
                            "semantic": true
                        }
                    }))
                    .into_response()
                }
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

fn seed_auth(data: &Path) -> (Arc<AuthState>, String) {
    let email = "owner@lattice.test";
    let mut owner = OrderedMap::new();
    owner.insert("password", json!("x"));
    owner.insert("name", json!("owner"));
    owner.insert("nickname", json!("owner"));
    owner.insert("role", json!("admin"));
    owner.insert("disabled", json!(false));
    owner.insert("id", json!(lattice_auth::stable_user_id(email)));
    owner.insert("email", json!(email));
    let mut users = OrderedMap::new();
    users.insert(email, serde_json::to_value(owner).unwrap());
    std::fs::write(
        data.join("users.json"),
        lattice_auth::pyjson::dumps_indent2(&users).expect("users"),
    )
    .expect("write users");

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
    let token = auth.sessions().create("user:owner", Some(email));
    (auth, token)
}

async fn boot_upload(data: &Path, worker: &str) -> (String, String, Arc<Store>, GraphWriter) {
    let (auth, token) = seed_auth(data);
    let db = data.join("knowledge_graph.sqlite");
    let store = Arc::new(Store::open(&db).expect("store"));
    let graph =
        GraphWriter::open(Arc::clone(&store), data.join("knowledge_graph_blobs")).expect("graph");
    let runtime =
        RuntimeConfig::resolve(Some(data.to_str().unwrap()), None, Some(worker), Some(data));
    let seam = WorkerSeamClient::new(worker).expect("seam");
    let state = LocalFilesState::new(auth, Some(Arc::clone(&store)), runtime)
        .with_graph(graph.clone())
        .with_seam(seam);
    let app = local_files_api::router(Arc::new(state));
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
    (format!("http://{addr}"), token, store, graph)
}

async fn post_upload(
    origin: &str,
    token: &str,
    body: &[u8],
    filename: &str,
    content_type: &str,
) -> (u16, Value) {
    let client = reqwest::Client::builder().no_proxy().build().unwrap();
    let response = client
        .post(format!("{origin}/upload/document"))
        .header("cookie", format!("session_token={token}"))
        .header("content-type", content_type)
        .header("x-filename", filename)
        .body(body.to_vec())
        .send()
        .await
        .expect("upload");
    let status = response.status().as_u16();
    let text = response.text().await.expect("text");
    let payload: Value = serde_json::from_str(&text).expect("json");
    (status, payload)
}

#[tokio::test]
async fn upload_preserves_semantic_provider_contract_and_reports_degraded_and_reindex_statuses() {
    let data = tempfile::tempdir().expect("data");
    let cfg = Arc::new(Mutex::new(MockWorkerConfig::default()));
    let worker = spawn_worker(Arc::clone(&cfg)).await;
    let (origin, token, store, graph) = boot_upload(data.path(), &worker).await;

    let (status, payload) = post_upload(
        &origin,
        &token,
        b"# Semantic Upload\n\nEmbeddingGemma 2 vector contract verification.\n",
        "semantic.md",
        "text/markdown",
    )
    .await;
    assert_eq!(status, 200, "{payload}");
    assert_eq!(payload["ingest_status"], "complete");
    assert_eq!(payload["embedding_mode"], "semantic");
    assert_eq!(payload["semantic"], true);
    assert_eq!(payload["degraded_reasons"], json!([]));
    assert_eq!(
        payload["embedding_contract"]["model_id"],
        "huggingface:google/embeddinggemma-2:768"
    );

    let contract = graph
        .current_index_contract()
        .expect("contract")
        .expect("persisted contract");
    assert_eq!(contract.model_id, "huggingface:google/embeddinggemma-2:768");
    assert_eq!(contract.dimension, 4);

    store
        .with_read_conn(|conn| {
            let semantic_rows: i64 = conn
                .query_row(
                    "SELECT COUNT(*) FROM vector_embeddings \
                     WHERE embedding_model='huggingface:google/embeddinggemma-2:768' \
                       AND embedding_dim=4",
                    [],
                    |row| row.get(0),
                )
                .unwrap();
            assert!(
                semantic_rows >= 2,
                "both document and chunk rows must carry semantic provider vectors"
            );
            Ok(())
        })
        .unwrap();

    cfg.lock().unwrap().embed_ok = false;
    let (status, payload) = post_upload(
        &origin,
        &token,
        b"# Fallback Note\n\nSaved while embed seam is down.\n",
        "fallback.md",
        "text/markdown",
    )
    .await;
    assert_eq!(status, 200, "{payload}");
    assert_eq!(payload["ingest_status"], "partial");
    assert_eq!(payload["embedding_mode"], "hash_fallback");
    assert_eq!(payload["semantic"], false);
    assert!(payload["degraded_reasons"]
        .as_array()
        .unwrap()
        .iter()
        .any(|v| v == "embed_seam_unavailable"));

    {
        let mut guard = cfg.lock().unwrap();
        guard.embed_ok = true;
        guard.wrong_vector_len = Some(2);
    }
    let (status, payload) = post_upload(
        &origin,
        &token,
        b"# Bad Dim Note\n\nCorrupted vector payload.\n",
        "baddim.md",
        "text/markdown",
    )
    .await;
    assert_eq!(status, 200, "{payload}");
    assert_eq!(payload["ingest_status"], "partial");
    assert_eq!(payload["semantic"], false);
    assert!(payload["degraded_reasons"]
        .as_array()
        .unwrap()
        .iter()
        .any(|v| v
            .as_str()
            .unwrap_or("")
            .starts_with("embed_contract_invalid:")));

    {
        let mut guard = cfg.lock().unwrap();
        guard.wrong_vector_len = None;
        guard.parse_ok = false;
    }
    let (status, payload) = post_upload(
        &origin,
        &token,
        b"%PDF-1.1\n1 0 obj<<>>endobj\n%%EOF\n",
        "broken.pdf",
        "application/pdf",
    )
    .await;
    assert_eq!(status, 200, "{payload}");
    assert_eq!(payload["ingest_status"], "partial");
    assert!(payload["degraded_reasons"]
        .as_array()
        .unwrap()
        .iter()
        .any(|v| v == "parse_seam_unavailable"));

    {
        let mut guard = cfg.lock().unwrap();
        guard.parse_ok = true;
        guard.provider = "mlx".into();
        guard.model_id = "mlx:mlx-community/all-MiniLM-L6-v2".into();
        guard.dim = 3;
    }
    let (status, payload) = post_upload(
        &origin,
        &token,
        b"# Switched Provider\n\nTriggers reindex_required signal.\n",
        "switched.md",
        "text/markdown",
    )
    .await;
    assert_eq!(status, 200, "{payload}");
    assert_eq!(payload["ingest_status"], "reindex_required");
    assert!(payload["degraded_reasons"]
        .as_array()
        .unwrap()
        .iter()
        .any(|v| v == "embedding_contract_reindex_required"));
}

#[tokio::test]
async fn browser_tab_and_note_ingestor_preserve_semantic_vectors_and_status() {
    let data = tempfile::tempdir().expect("data");
    let cfg = Arc::new(Mutex::new(MockWorkerConfig::default()));
    let worker = spawn_worker(Arc::clone(&cfg)).await;
    let (auth, token) = seed_auth(data.path());

    let db = data.path().join("knowledge_graph.sqlite");
    let store = Arc::new(Store::open(&db).expect("store"));
    let graph = GraphWriter::open(
        Arc::clone(&store),
        data.path().join("knowledge_graph_blobs"),
    )
    .expect("graph");
    let seam = WorkerSeamClient::new(&worker).expect("seam");
    let runtime = RuntimeConfig::resolve(
        Some(data.path().to_str().unwrap()),
        None,
        Some(&worker),
        Some(data.path()),
    );

    let bstate = BrowserState::new(auth, runtime)
        .with_graph(graph.clone())
        .with_seam(seam.clone());
    let bapp = browser_api::router(Arc::new(bstate));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind");
    let addr: SocketAddr = listener.local_addr().expect("addr");
    tokio::spawn(async move {
        let _ = axum::serve(
            listener,
            bapp.into_make_service_with_connect_info::<SocketAddr>(),
        )
        .await;
    });

    let client = reqwest::Client::builder().no_proxy().build().unwrap();
    let payload = json!({
        "url": "https://example.com/spec",
        "title": "Semantic Spec",
        "text": "Browser tab body captured with real semantic provider embeddings."
    });
    let res = client
        .post(format!("http://{addr}/api/browser/ingest-current-tab"))
        .header("cookie", format!("session_token={token}"))
        .header("content-type", "application/json")
        .body(serde_json::to_vec(&payload).unwrap())
        .send()
        .await
        .expect("browser tab");
    assert_eq!(res.status().as_u16(), 200);
    let body_text = res.text().await.expect("text");
    let body: Value = serde_json::from_str(&body_text).expect("json");
    assert_eq!(body["status"], "ok");
    assert_eq!(body["ingest_status"], "complete");
    assert_eq!(body["embedding_mode"], "semantic");
    assert_eq!(body["semantic"], true);

    let ingestor = NoteIngestor::new(graph.clone()).with_seam(seam);
    let note = NoteSubmission {
        title: "Watched Note.md".into(),
        content: "# Title\n\nBody paragraph for watched note ingestion.".into(),
        source: Some("file://watched.md".into()),
        metadata: Default::default(),
    };
    let receipt = ingestor
        .ingest_note(&note, Some("owner@lattice.test"), Some("personal"))
        .await
        .expect("note ingest");
    assert_eq!(receipt.ingest_status, IngestStatus::Complete);
    assert_eq!(receipt.embedding_mode, "semantic");
    assert!(receipt.semantic);
    assert!(receipt.indexed);

    let offline_ingestor = NoteIngestor::new(graph);
    let offline_note = NoteSubmission {
        title: "Offline Note.md".into(),
        content: "# Offline\n\nBody saved without worker seam.".into(),
        source: Some("file://offline.md".into()),
        metadata: Default::default(),
    };
    let offline_receipt = offline_ingestor
        .ingest_note(&offline_note, Some("owner@lattice.test"), Some("personal"))
        .await
        .expect("offline ingest");
    assert_eq!(offline_receipt.ingest_status, IngestStatus::Partial);
    assert_eq!(offline_receipt.embedding_mode, "hash_fallback");
    assert!(!offline_receipt.semantic);
    assert!(offline_receipt.indexed);
}
