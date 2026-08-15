//! Binary upload extraction + per-chunk supplied vectors (F-ING).

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
use lattice_ingest::local_files_api::{self, LocalFilesState};
use serde_json::{json, Value};

fn seed_users(dir: &Path) {
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
        dir.join("users.json"),
        lattice_auth::pyjson::dumps_indent2(&users).expect("users"),
    )
    .expect("write users");
}

#[derive(Clone, Default)]
struct SeamLog {
    inner: Arc<Mutex<Vec<(String, Value)>>>,
}

impl SeamLog {
    fn push(&self, path: &str, body: Value) {
        self.inner
            .lock()
            .expect("log")
            .push((path.to_string(), body));
    }

    fn paths(&self) -> Vec<String> {
        self.inner
            .lock()
            .expect("log")
            .iter()
            .map(|(path, _)| path.clone())
            .collect()
    }

    fn bodies(&self, path: &str) -> Vec<Value> {
        self.inner
            .lock()
            .expect("log")
            .iter()
            .filter(|(seen, _)| seen == path)
            .map(|(_, body)| body.clone())
            .collect()
    }
}

struct WorkerSpec {
    parse_ok: bool,
    parsed_text: String,
    embed_ok: bool,
}

impl Default for WorkerSpec {
    fn default() -> Self {
        Self {
            parse_ok: true,
            parsed_text: "Extracted PDF body about Lattice.".into(),
            embed_ok: true,
        }
    }
}

async fn spawn_worker(spec: WorkerSpec, log: SeamLog) -> String {
    let parse_ok = spec.parse_ok;
    let parsed_text = spec.parsed_text;
    let embed_ok = spec.embed_ok;
    let parse_log = log.clone();
    let extract_log = log.clone();
    let embed_log = log;
    let app = Router::new()
        .route(
            "/worker/parse",
            post(move |Json(body): Json<Value>| {
                let parse_log = parse_log.clone();
                let parsed_text = parsed_text.clone();
                async move {
                    parse_log.push("/worker/parse", body);
                    if parse_ok {
                        axum::Json(json!({
                            "filename": "report.pdf",
                            "ext": ".pdf",
                            "pages": 1,
                            "chars": parsed_text.chars().count(),
                            "preview": parsed_text,
                            "content": parsed_text,
                        }))
                        .into_response()
                    } else {
                        (
                            axum::http::StatusCode::BAD_REQUEST,
                            axum::Json(json!({"detail": "parse failed"})),
                        )
                            .into_response()
                    }
                }
            }),
        )
        .route(
            "/worker/extract",
            post(move |Json(body): Json<Value>| {
                let extract_log = extract_log.clone();
                async move {
                    extract_log.push("/worker/extract", body);
                    axum::Json(json!({
                        "concepts": [{"text": "Lattice", "node_type": "Concept"}],
                        "triples": [],
                        "semantic": [],
                    }))
                }
            }),
        )
        .route(
            "/worker/embed",
            post(move |Json(body): Json<Value>| {
                let embed_log = embed_log.clone();
                async move {
                    embed_log.push("/worker/embed", body.clone());
                    if embed_ok {
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
                        .into_response()
                    } else {
                        (
                            axum::http::StatusCode::SERVICE_UNAVAILABLE,
                            axum::Json(json!({"detail": "embedder down"})),
                        )
                            .into_response()
                    }
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

async fn boot(data: &Path, worker: &str) -> (String, String, std::path::PathBuf) {
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
    let db = data.join("knowledge_graph.sqlite");
    let store = Arc::new(Store::open(&db).expect("store"));
    let graph =
        GraphWriter::open(Arc::clone(&store), data.join("knowledge_graph_blobs")).expect("graph");
    let runtime =
        RuntimeConfig::resolve(Some(data.to_str().unwrap()), None, Some(worker), Some(data));
    let seam = WorkerSeamClient::new(worker).expect("seam");
    let state = LocalFilesState::new(auth, Some(store), runtime)
        .with_graph(graph)
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
    (format!("http://{addr}"), token, db)
}

async fn post_bytes(
    origin: &str,
    token: &str,
    body: Vec<u8>,
    filename: &str,
    content_type: &str,
) -> (u16, Value) {
    let client = reqwest::Client::builder()
        .no_proxy()
        .build()
        .expect("client");
    let response = client
        .post(format!("{origin}/upload/document"))
        .header("cookie", format!("session_token={token}"))
        .header("content-type", content_type)
        .header("x-filename", filename)
        .body(body)
        .send()
        .await
        .expect("upload");
    let status = response.status().as_u16();
    let text = response.text().await.expect("text");
    let value = serde_json::from_str(&text).unwrap_or(json!({"raw": text}));
    (status, value)
}

#[tokio::test]
async fn a_utf8_markdown_upload_skips_the_parse_seam() {
    let data = tempfile::tempdir().expect("data");
    let log = SeamLog::default();
    let worker = spawn_worker(WorkerSpec::default(), log.clone()).await;
    let (origin, token, db) = boot(data.path(), &worker).await;
    let body = b"# Lattice handbook\n\nFixture upload.\n".to_vec();
    let (status, payload) = post_bytes(&origin, &token, body, "handbook.md", "text/markdown").await;
    assert_eq!(status, 200, "{payload}");
    assert_eq!(payload["type"], "Document");
    assert!(payload["chunk_count"].as_u64().unwrap_or(0) >= 1);
    assert!(!log.paths().iter().any(|path| path == "/worker/parse"));
    assert!(log.paths().iter().any(|path| path == "/worker/extract"));
    assert!(log.paths().iter().any(|path| path == "/worker/embed"));
    let conn = rusqlite::Connection::open(&db).expect("db");
    let chunks: i64 = conn
        .query_row("SELECT COUNT(*) FROM chunks", [], |row| row.get(0))
        .expect("chunks");
    assert!(chunks >= 1);
    let supplied: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM vector_embeddings WHERE item_type='chunk'",
            [],
            |row| row.get(0),
        )
        .expect("chunk vectors");
    assert!(supplied >= 1, "chunk rows must carry a supplied vector");
}

#[tokio::test]
async fn a_binary_pdf_goes_through_worker_parse_then_the_enrichment_chain() {
    let data = tempfile::tempdir().expect("data");
    let log = SeamLog::default();
    let worker = spawn_worker(WorkerSpec::default(), log.clone()).await;
    let (origin, token, db) = boot(data.path(), &worker).await;
    let pdf = std::fs::read(
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("fixtures")
            .join("ingest")
            .join("tiny.pdf"),
    )
    .expect("rust/fixtures/ingest/tiny.pdf");
    let (status, payload) = post_bytes(
        &origin,
        &token,
        pdf.to_vec(),
        "report.pdf",
        "application/pdf",
    )
    .await;
    assert_eq!(status, 200, "{payload}");
    assert_eq!(payload["type"], "Document");
    let parses = log.bodies("/worker/parse");
    assert_eq!(parses.len(), 1, "binary uploads must call /worker/parse");
    assert_eq!(parses[0]["filename"], "report.pdf");
    assert!(parses[0]["content_b64"].as_str().unwrap_or("").len() > 8);
    let extracts = log.bodies("/worker/extract");
    assert_eq!(extracts.len(), 1);
    let extract_text = extracts[0]["text"].as_str().unwrap_or("");
    assert!(
        extract_text.contains("Extracted PDF body about Lattice."),
        "{extract_text}"
    );
    let conn = rusqlite::Connection::open(&db).expect("db");
    let concepts: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM nodes WHERE type='Concept'",
            [],
            |row| row.get(0),
        )
        .expect("concepts");
    assert_eq!(concepts, 1, "parse text must feed the extract seam");
    let chunk_vectors: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM vector_embeddings WHERE item_type='chunk'",
            [],
            |row| row.get(0),
        )
        .expect("chunk vectors");
    assert!(chunk_vectors >= 1);
}

#[tokio::test]
async fn parse_seam_failure_still_records_the_document() {
    let data = tempfile::tempdir().expect("data");
    let log = SeamLog::default();
    let worker = spawn_worker(
        WorkerSpec {
            parse_ok: false,
            ..WorkerSpec::default()
        },
        log.clone(),
    )
    .await;
    let (origin, token, db) = boot(data.path(), &worker).await;
    let pdf = b"%PDF-1.1\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n%%EOF\n";
    let (status, payload) = post_bytes(
        &origin,
        &token,
        pdf.to_vec(),
        "broken.pdf",
        "application/pdf",
    )
    .await;
    assert_eq!(status, 200, "{payload}");
    assert_eq!(payload["type"], "Document");
    assert!(!log.paths().iter().any(|path| path == "/worker/extract"));
    let conn = rusqlite::Connection::open(&db).expect("db");
    let docs: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM nodes WHERE type='Document'",
            [],
            |row| row.get(0),
        )
        .expect("docs");
    assert_eq!(docs, 1);
}

#[tokio::test]
async fn embed_seam_failure_still_records_the_document() {
    let data = tempfile::tempdir().expect("data");
    let log = SeamLog::default();
    let worker = spawn_worker(
        WorkerSpec {
            embed_ok: false,
            ..WorkerSpec::default()
        },
        log,
    )
    .await;
    let (origin, token, db) = boot(data.path(), &worker).await;
    let (status, payload) = post_bytes(
        &origin,
        &token,
        b"plain note about Lattice\n".to_vec(),
        "note.txt",
        "text/plain",
    )
    .await;
    assert_eq!(status, 200, "{payload}");
    assert_eq!(payload["type"], "Document");
    let conn = rusqlite::Connection::open(&db).expect("db");
    let docs: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM nodes WHERE type='Document'",
            [],
            |row| row.get(0),
        )
        .expect("docs");
    assert_eq!(docs, 1);
}

#[tokio::test]
async fn multipart_unwraps_the_spa_file_field() {
    let data = tempfile::tempdir().expect("data");
    let log = SeamLog::default();
    let worker = spawn_worker(WorkerSpec::default(), log.clone()).await;
    let (origin, token, _) = boot(data.path(), &worker).await;
    let body = b"--xyz\r\nContent-Disposition: form-data; name=\"file\"; filename=\"report.pdf\"\r\nContent-Type: application/pdf\r\n\r\n%PDF-1.1\nfixture\n%%EOF\r\n--xyz--\r\n";
    let client = reqwest::Client::builder()
        .no_proxy()
        .build()
        .expect("client");
    let response = client
        .post(format!("{origin}/upload/document"))
        .header("cookie", format!("session_token={token}"))
        .header("content-type", "multipart/form-data; boundary=xyz")
        .body(body.as_slice())
        .send()
        .await
        .expect("upload");
    assert_eq!(response.status().as_u16(), 200);
    let parses = log.bodies("/worker/parse");
    assert_eq!(parses.len(), 1);
    assert_eq!(parses[0]["filename"], "report.pdf");
}
