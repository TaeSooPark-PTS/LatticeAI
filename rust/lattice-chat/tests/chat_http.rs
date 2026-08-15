//! Replay `rust/fixtures/http/chat.json` and prove the with-model SSE path.
//!
//! Fixture replay covers every branch the capture could reach without a loaded
//! MLX model (WAVE2_COMMON rule 8). The token path has no fixtures: the
//! `with_model_stream_passthrough_records_then_dones` test is the proof —
//! FakeWorker SSE frames pass through, the turn chain runs natively
//! (`/worker/chat/record-turn` requested **zero** times, rows written here
//! user-then-assistant after tokens start, while `/worker/llm/stream` and
//! `/worker/embed` still are), and the stream ends with `data: [DONE]`.

use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use axum::extract::Json;
use axum::http::StatusCode;
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_auth::{AuthConfig, AuthState, Clock, OrderedMap, WorkspaceResolver};
use lattice_chat::intents::scoped_file_id;
use lattice_chat::{router, ChatConfig, ChatState, ChatWorker};
use rusqlite::Connection;
use serde_json::{json, Value};

fn fixture() -> Value {
    let path: PathBuf = [
        env!("CARGO_MANIFEST_DIR"),
        "..",
        "fixtures",
        "http",
        "chat.json",
    ]
    .iter()
    .collect();
    serde_json::from_str(&std::fs::read_to_string(&path).expect("fixture")).expect("json")
}

fn cases() -> Vec<Value> {
    fixture()["cases"].as_array().cloned().unwrap_or_default()
}

fn matches_token(expected: &Value, got: &Value) -> bool {
    match expected {
        Value::String(token) if token == "@any" => true,
        Value::String(token) if token == "@ts" => got.as_str().is_some(),
        Value::String(token) if token == "@uuid" => got.as_str().is_some(),
        Value::String(token) if token.starts_with('@') => true,
        Value::Object(exp) => {
            let Some(got_obj) = got.as_object() else {
                return false;
            };
            exp.iter().all(|(key, value)| {
                got_obj
                    .get(key)
                    .is_some_and(|got| matches_token(value, got))
            })
        }
        Value::Array(exp) => {
            let Some(got_arr) = got.as_array() else {
                return false;
            };
            exp.len() == got_arr.len()
                && exp
                    .iter()
                    .zip(got_arr.iter())
                    .all(|(expected, got)| matches_token(expected, got))
        }
        other => other == got,
    }
}

struct Personal;

impl WorkspaceResolver for Personal {
    fn resolve_read_scope(
        &self,
        requested: Option<&str>,
        _user: Option<&str>,
    ) -> Result<Option<String>, String> {
        Ok(requested
            .map(str::to_string)
            .or_else(|| Some("personal".into())))
    }

    fn resolve_write_scope(
        &self,
        requested: Option<&str>,
        user: Option<&str>,
    ) -> Result<Option<String>, String> {
        self.resolve_read_scope(requested, user)
    }
}

fn seed_users(dir: &Path) {
    let email = "owner@fixture.local";
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

/// `ConversationStore._init_db`'s table, `message_hash` included.
///
/// The column list is not decoration: the native turn writer inserts the
/// `INSERT OR IGNORE` dedup hash Python's store does, so a stub table without
/// that column would silently accept nothing. This is the shape a real store —
/// the one the fixtures were captured against — has.
fn seed_schema(dir: &Path) {
    let conn = Connection::open(dir.join("knowledge_graph.sqlite")).expect("sqlite");
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS conversation_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_hash TEXT NOT NULL UNIQUE,
            conversation_id TEXT, role TEXT NOT NULL, content TEXT NOT NULL,
            user_email TEXT, user_nickname TEXT, source TEXT,
            timestamp TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}',
            workspace_id TEXT, organization_id TEXT
         );",
    )
    .expect("schema");
}

/// Which seam paths the worker was actually asked for.
///
/// WP-W3a's proof obligation runs both ways: `/worker/chat/record-turn` must be
/// requested **zero** times (the chain is native now) while `/worker/llm/stream`
/// and `/worker/embed` must still be requested (token generation and the
/// embedding provider did not move).
#[derive(Clone, Default)]
struct SeamLog {
    paths: Arc<Mutex<Vec<String>>>,
}

impl SeamLog {
    fn hit(&self, path: &str) {
        self.paths.lock().unwrap().push(path.to_string());
    }

    fn count(&self, path: &str) -> usize {
        self.paths
            .lock()
            .unwrap()
            .iter()
            .filter(|seen| *seen == path)
            .count()
    }
}

async fn spawn_fake_worker(
    loaded: Vec<String>,
    current: Option<String>,
    seams: SeamLog,
    stream_frames: Vec<String>,
) -> String {
    let models = json!({"loaded": loaded, "current": current});
    let app = Router::new()
        .route(
            "/models",
            get(move || {
                let models = models.clone();
                async move { axum::Json(models) }
            }),
        )
        .route(
            // Still mounted, so "nobody called it" is an observation rather than
            // a 404 that would have looked the same from the client side.
            "/worker/chat/record-turn",
            post({
                let seams = seams.clone();
                move |Json(_body): Json<Value>| {
                    let seams = seams.clone();
                    async move {
                        seams.hit("/worker/chat/record-turn");
                        axum::Json(json!({"stored": true, "item": null, "ingested": null}))
                    }
                }
            }),
        )
        .route(
            // W2 §1. The stand-in provider is the deterministic hash embedder,
            // which is what the shipped worker resolves to by default — so the
            // reply here is the reply the real seam gives an untouched install.
            "/worker/embed",
            post({
                let seams = seams.clone();
                move |Json(body): Json<Value>| {
                    let seams = seams.clone();
                    async move {
                        seams.hit("/worker/embed");
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
                            "provider": "hash",
                            "model_id": model.model_id(),
                            "kind": body["kind"].as_str().unwrap_or("passage"),
                        }))
                    }
                }
            }),
        )
        .route(
            "/worker/extract",
            post({
                let seams = seams.clone();
                move |Json(_body): Json<Value>| {
                    let seams = seams.clone();
                    async move {
                        seams.hit("/worker/extract");
                        axum::Json(json!({
                            "concepts": [],
                            "triples": [],
                            "semantic": [],
                        }))
                    }
                }
            }),
        )
        .route(
            // A tripwire, not a seam. v11.6.0 took this route off the worker,
            // and until v11.7.0 chat still posted every generated file to it —
            // invisibly, because a fake worker like this one answered. It is
            // still mounted **and still answers 200** so that "nobody asked" is
            // an observation rather than a 404 that would look the same.
            "/knowledge-graph/ingest",
            post({
                let seams = seams.clone();
                move |Json(body): Json<Value>| {
                    let seams = seams.clone();
                    async move {
                        seams.hit("/knowledge-graph/ingest");
                        let text = body.get("text").and_then(Value::as_str).unwrap_or("");
                        let workspace = body.get("workspace_id").and_then(Value::as_str);
                        axum::Json(json!({
                            "status": "ok",
                            "node_id": scoped_file_id(text, workspace),
                            "chunk_count": 0,
                            "duplicate": false,
                        }))
                    }
                }
            }),
        )
        .route(
            "/worker/graph/mutate",
            post(|Json(body): Json<Value>| async move {
                axum::Json(json!({"op": body.get("op"), "result": {"status": "ok"}}))
            }),
        )
        .route(
            "/worker/llm/stream",
            post({
                let stream_frames = stream_frames.clone();
                let seams = seams.clone();
                move |_body: axum::body::Bytes| {
                    let stream_frames = stream_frames.clone();
                    let seams = seams.clone();
                    async move {
                        seams.hit("/worker/llm/stream");
                        let mut body = String::new();
                        for frame in &stream_frames {
                            body.push_str(frame);
                        }
                        if !body.contains("[DONE]") {
                            body.push_str("data: [DONE]\n\n");
                        }
                        Response::builder()
                            .status(StatusCode::OK)
                            .header("content-type", "text/event-stream")
                            .body(axum::body::Body::from(body))
                            .unwrap()
                    }
                }
            }),
        );

    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind worker");
    let addr = listener.local_addr().expect("addr");
    tokio::spawn(async move {
        let _ = axum::serve(listener, app.into_make_service()).await;
    });
    format!("http://{addr}")
}

struct Install {
    origin: String,
    token: String,
    auth: Arc<AuthState>,
    seams: SeamLog,
    graph_db: PathBuf,
    _data: tempfile::TempDir,
    _agent: tempfile::TempDir,
    _handle: tokio::task::JoinHandle<()>,
    _worker: String,
}

impl Install {
    async fn start(loaded: Vec<String>, current: Option<String>, frames: Vec<String>) -> Self {
        let data = tempfile::tempdir().expect("data");
        let agent = tempfile::tempdir().expect("agent");
        seed_users(data.path());
        seed_schema(data.path());

        let mut env = HashMap::new();
        env.insert("LATTICEAI_REQUIRE_AUTH".into(), "1".into());
        env.insert("LATTICEAI_HOST".into(), "127.0.0.1".into());
        env.insert("LATTICEAI_PORT".into(), "4825".into());
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

        let seams = SeamLog::default();
        let worker_origin = spawn_fake_worker(loaded, current, seams.clone(), frames).await;
        let worker = ChatWorker::new(&worker_origin).expect("worker");
        let graph_db = data.path().join("knowledge_graph.sqlite");
        let chat_config = ChatConfig {
            data_dir: data.path().to_path_buf(),
            graph_db: Some(graph_db.clone()),
            agent_root: agent.path().to_path_buf(),
            ..ChatConfig::default()
        };
        // WP-W1's write engine, built the way the integrator builds it. This
        // replay had no writer bound until v11.7.0, which is why the stranded
        // `POST /knowledge-graph/ingest` looked healthy: the fake worker was
        // the only thing answering, and it always said "ok".
        let store = Arc::new(lattice_core::db::Store::open(&graph_db).expect("store"));
        let graph = lattice_core::graph_write::GraphWriter::open(
            store,
            data.path().join("knowledge_graph_blobs"),
        )
        .expect("graph writer");
        let state = ChatState::new(Arc::clone(&auth), chat_config)
            .with_worker(worker)
            .with_graph(graph)
            .with_workspace(Arc::new(Personal));
        let app = router(state);
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind");
        let addr: SocketAddr = listener.local_addr().expect("addr");
        let handle = tokio::spawn(async move {
            let _ = axum::serve(
                listener,
                app.into_make_service_with_connect_info::<SocketAddr>(),
            )
            .await;
        });
        Self {
            origin: format!("http://{addr}"),
            token,
            auth,
            seams,
            graph_db,
            _data: data,
            _agent: agent,
            _handle: handle,
            _worker: worker_origin,
        }
    }

    async fn replay(&self, case: &Value) {
        let name = case["name"].as_str().unwrap_or("?");
        if name == "chat_get_redirect" || case["family"].as_str() == Some("chat_agent_http") {
            return;
        }
        if name == "rate_limited_429" {
            // Fill the chat bucket so this request is the 429. Retry-After
            // depends on when the window opened during replay, so the
            // fixture's "1s" is not pinned — only the 429 + prefix.
            while self
                .auth
                .enforce_rate_limit("owner@fixture.local", "chat")
                .is_ok()
            {}
        }
        let method = case["method"].as_str().expect("method");
        let path = case["path"].as_str().expect("path");
        let mut url = format!("{}{}", self.origin, path);
        if let Some(query) = case["query"].as_object() {
            if !query.is_empty() {
                let parts: Vec<String> = query
                    .iter()
                    .map(|(key, value)| {
                        let raw = match value {
                            Value::String(text) => text.clone(),
                            other => other.to_string().trim_matches('"').to_string(),
                        };
                        format!("{key}={raw}")
                    })
                    .collect();
                url.push('?');
                url.push_str(&parts.join("&"));
            }
        }
        let client = reqwest::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .timeout(Duration::from_secs(20))
            .build()
            .expect("client");
        let mut builder = client.request(
            reqwest::Method::from_bytes(method.as_bytes()).expect("method"),
            &url,
        );
        if let Some(headers) = case["request_headers"].as_object() {
            for (header, value) in headers {
                let text = value
                    .as_str()
                    .unwrap_or_default()
                    .replace("@session", &self.token);
                builder = builder.header(header.as_str(), text);
            }
        }
        if let Some(body) = case.get("request_body") {
            if !body.is_null() {
                builder = builder
                    .header("content-type", "application/json")
                    .body(serde_json::to_string(body).expect("body"));
            }
        }
        let response = builder.send().await.expect(name);
        let status = response.status().as_u16();
        let content_type = response
            .headers()
            .get("content-type")
            .and_then(|value| value.to_str().ok())
            .unwrap_or("")
            .to_string();
        let body = response.text().await.expect("text");
        assert_eq!(
            status,
            case["status"].as_u64().unwrap() as u16,
            "{name} status"
        );
        if let Some(expected_ct) = case["response_headers"]
            .as_object()
            .and_then(|headers| headers.get("content-type"))
            .and_then(Value::as_str)
        {
            assert!(
                content_type.starts_with(expected_ct) || content_type.contains("application/json"),
                "{name} content-type {content_type} vs {expected_ct}"
            );
        }
        if let Some(expected) = case.get("response_body") {
            if expected.is_null() {
                return;
            }
            let got: Value = serde_json::from_str(&body).unwrap_or(Value::String(body.clone()));
            if name == "rate_limited_429" {
                let detail = got.get("detail").and_then(Value::as_str).unwrap_or("");
                assert!(
                    detail.starts_with("Rate limit exceeded for chat"),
                    "{name} detail: {detail}"
                );
                return;
            }
            assert!(
                matches_token(expected, &got),
                "{name} body mismatch\nexpected: {}\ngot:      {}",
                serde_json::to_string_pretty(expected).unwrap_or_default(),
                serde_json::to_string_pretty(&got).unwrap_or_default()
            );
        }
    }
}

#[tokio::test]
async fn replay_chat_and_history_fixtures() {
    let install = Install::start(Vec::new(), None, Vec::new()).await;
    for case in cases() {
        install.replay(&case).await;
    }

    // The two `direct_write_file` fixtures pin `brain_ingest.node_id` as
    // `file:<scoped hash of the content>`; the replay above already proved the
    // reply matches. This proves *where the answer came from*: the Brain in
    // this process, not a worker that would 404 in production.
    assert_eq!(
        install.seams.count("/knowledge-graph/ingest"),
        0,
        "generated-file ingest is native — a request here means chat is posting \
         into a route the worker stopped serving in v11.6.0"
    );
    let conn = Connection::open(&install.graph_db).expect("db");
    let mut statement = conn
        .prepare("SELECT id, title FROM nodes WHERE type = 'Document' ORDER BY id")
        .expect("select");
    let documents: Vec<(String, String)> = statement
        .query_map([], |row| Ok((row.get(0)?, row.get(1)?)))
        .expect("rows")
        .filter_map(Result::ok)
        .collect();
    let ids: Vec<&str> = documents.iter().map(|(id, _)| id.as_str()).collect();
    assert!(
        ids.contains(&"file:fce1110ff985bba9cad1f594")
            && ids.contains(&"file:9f033e8b2619a947d73cfadd"),
        "both fixture-generated files are Document nodes in the Brain: {documents:?}"
    );
    let titles: Vec<&str> = documents.iter().map(|(_, title)| title.as_str()).collect();
    assert!(titles.contains(&"fixture-note.md"), "{documents:?}");
    // The file's text, not just its name, is what the node carries — the
    // native upload door's `extracted["content"]` contract.
    let summary: String = conn
        .query_row(
            "SELECT summary FROM nodes WHERE id = 'file:fce1110ff985bba9cad1f594'",
            [],
            |row| row.get(0),
        )
        .expect("summary");
    assert_eq!(summary, "Hello Lattice");
    let provenance: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM ingestion_provenance WHERE source_type = 'file'",
            [],
            |row| row.get(0),
        )
        .expect("provenance");
    assert_eq!(
        provenance, 2,
        "every generated file records where it came from"
    );
}

#[tokio::test]
async fn with_model_stream_passthrough_records_then_dones() {
    // No fixture covers the loaded-model token path (capture note in chat.json).
    // This FakeWorker proves frame passthrough, that the turn chain is native
    // (zero `/worker/chat/record-turn` requests, rows written here instead, user
    // then assistant), that the two seams that did **not** move are still used,
    // and that the stream ends with `[DONE]`.
    let frames = vec![
        "data: {\"text\":\"Hel\"}\n\n".to_string(),
        "data: {\"text\":\"lo\"}\n\n".to_string(),
        "data: [DONE]\n\n".to_string(),
    ];
    let data = tempfile::tempdir().expect("data");
    let agent = tempfile::tempdir().expect("agent");
    seed_users(data.path());
    seed_schema(data.path());
    let mut env = HashMap::new();
    env.insert("LATTICEAI_REQUIRE_AUTH".into(), "1".into());
    env.insert("LATTICEAI_HOST".into(), "127.0.0.1".into());
    env.insert("LATTICEAI_PORT".into(), "4825".into());
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
    let seams = SeamLog::default();
    let worker_origin = spawn_fake_worker(
        vec!["demo".into()],
        Some("demo".into()),
        seams.clone(),
        frames,
    )
    .await;
    let worker = ChatWorker::new(&worker_origin).expect("worker");
    let chat_config = ChatConfig {
        data_dir: data.path().to_path_buf(),
        graph_db: Some(data.path().join("knowledge_graph.sqlite")),
        agent_root: agent.path().to_path_buf(),
        ..ChatConfig::default()
    };
    // WP-W1's write engine, built the way the integrator builds it: one store,
    // one writer, bootstrapped before a route serves.
    let store = Arc::new(
        lattice_core::db::Store::open(&data.path().join("knowledge_graph.sqlite")).expect("store"),
    );
    let graph = lattice_core::graph_write::GraphWriter::open(
        store,
        data.path().join("knowledge_graph_blobs"),
    )
    .expect("graph writer");
    let state = ChatState::new(auth, chat_config)
        .with_worker(worker)
        .with_graph(graph)
        .with_workspace(Arc::new(Personal));
    let app = router(state);
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind");
    let addr = listener.local_addr().expect("addr");
    tokio::spawn(async move {
        let _ = axum::serve(
            listener,
            app.into_make_service_with_connect_info::<SocketAddr>(),
        )
        .await;
    });
    let client = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(20))
        .build()
        .expect("client");
    let response = client
        .post(format!("http://{addr}/chat"))
        .header("cookie", format!("session_token={token}"))
        .header("content-type", "application/json")
        .body(r#"{"message":"hi","stream":true,"model":"demo"}"#)
        .send()
        .await
        .expect("chat");
    assert_eq!(response.status(), StatusCode::OK);
    assert!(response
        .headers()
        .get("content-type")
        .and_then(|value| value.to_str().ok())
        .unwrap_or("")
        .starts_with("text/event-stream"));
    let body = response.text().await.expect("body");
    assert!(body.contains("\"chunk\":\"Hel\""), "{body}");
    assert!(body.contains("\"chunk\":\"lo\""), "{body}");
    assert!(
        body.trim_end().ends_with("data: [DONE]"),
        "stream must end with the sentinel: {body}"
    );
    // The seam is retired: nobody asked the worker to record the turn.
    assert_eq!(
        seams.count("/worker/chat/record-turn"),
        0,
        "the history chain is native — chat must not call the record-turn seam"
    );
    // …and the two calls that did not move were still made.
    assert_eq!(
        seams.count("/worker/llm/stream"),
        1,
        "tokens still come from the worker"
    );
    assert!(
        seams.count("/worker/embed") >= 1,
        "the embedding provider is still the worker's (W2 §1)"
    );
    assert_eq!(
        seams.count("/worker/extract"),
        2,
        "extract is called once per ingested turn (user + assistant)"
    );

    // The rows landed here instead, in order, with the store's own columns.
    struct StoredRow {
        role: String,
        content: String,
        user_email: String,
        user_nickname: String,
        source: String,
        workspace_id: String,
        metadata_json: String,
        hash_length: i64,
    }
    let conn = Connection::open(data.path().join("knowledge_graph.sqlite")).expect("db");
    let mut statement = conn
        .prepare(
            "SELECT role, content, user_email, user_nickname, source, workspace_id,
                    metadata_json, length(message_hash)
             FROM conversation_messages ORDER BY id",
        )
        .expect("select");
    let rows: Vec<StoredRow> = statement
        .query_map([], |row| {
            Ok(StoredRow {
                role: row.get(0)?,
                content: row.get(1)?,
                user_email: row.get(2)?,
                user_nickname: row.get(3)?,
                source: row.get(4)?,
                workspace_id: row.get(5)?,
                metadata_json: row.get(6)?,
                hash_length: row.get(7)?,
            })
        })
        .expect("rows")
        .filter_map(Result::ok)
        .collect();
    assert_eq!(
        rows.iter().map(|row| row.role.as_str()).collect::<Vec<_>>(),
        ["user", "assistant"],
        "user then assistant, after the tokens"
    );
    assert_eq!(rows[0].content, "hi");
    assert_eq!(rows[1].content, "Hello");
    assert_eq!(rows[0].user_email, "owner@fixture.local");
    assert_eq!(rows[0].user_nickname, "owner");
    assert_eq!(rows[0].source, "web");
    assert_eq!(rows[0].workspace_id, "personal");
    assert_eq!(rows[0].metadata_json, "{}");
    assert_eq!(
        rows[0].hash_length, 64,
        "message_hash is a full sha256 hex digest"
    );

    // The Brain grew: `ingest_message` wrote the Message/AIResponse pair and the
    // Chat node that holds them, and the vector sync filed them under the model
    // `/worker/embed` reported.
    let messages: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM nodes WHERE type IN ('Message', 'AIResponse')",
            [],
            |row| row.get(0),
        )
        .expect("nodes");
    assert_eq!(messages, 2, "one node per turn");
    let vectors: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM vector_embeddings WHERE item_type='node'",
            [],
            |row| row.get(0),
        )
        .expect("vectors");
    assert!(vectors >= 2, "the turn's vectors were written natively");
    let provenance: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM ingestion_provenance WHERE source_type='chat_message'",
            [],
            |row| row.get(0),
        )
        .expect("provenance");
    assert_eq!(provenance, 2, "every ingest records where it came from");

    // And the audit row R2's helper writes is on R2's file.
    let audit: Value = serde_json::from_str(
        &std::fs::read_to_string(data.path().join("audit_log.json")).expect("audit log"),
    )
    .expect("audit json");
    let chat_rows: Vec<&Value> = audit
        .as_array()
        .expect("array")
        .iter()
        .filter(|row| row["event_type"] == "chat_message")
        .collect();
    assert_eq!(chat_rows.len(), 2);
    assert_eq!(chat_rows[0]["role"], "user");
    assert_eq!(chat_rows[0]["content_chars"], 2);
    assert_eq!(chat_rows[0]["sensitivity"], "none");
    assert!(
        chat_rows[0]["event_id"]
            .as_str()
            .unwrap_or_default()
            .starts_with("audit-"),
        "the event id is R2's, not a second format"
    );
}

#[test]
fn crate_exports_the_router_factory() {
    assert!(lattice_chat::crate_ready());
    assert!(lattice_chat::MOUNTED
        .iter()
        .any(|(m, p)| *m == "POST" && *p == "/chat"));
}
