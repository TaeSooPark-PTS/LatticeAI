//! The native chat-turn chain end to end (WP-W3a).
//!
//! `tests/chat_http.rs` proves the chain runs from `POST /chat` and that
//! `/worker/chat/record-turn` is never requested. This file proves the four
//! properties that make retiring that seam safe:
//!
//! 1. what the store holds is the **redacted** text, and an assistant's turn is
//!    brand-normalised on top of it;
//! 2. a turn written here reads back through the **existing** native history
//!    routes (`GET /history`, `GET /history/conversations/{id}`) — the same
//!    `lattice-retrieval` lanes that read Python-written rows;
//! 3. with no graph the turn still stores, and says so with `ingested: null` —
//!    the seam's own semantics;
//! 4. when `/worker/embed` reports an embedder this process cannot reproduce,
//!    the vector is left as backlog (`indexing_status: "pending"`) instead of
//!    being written under the wrong model id, which is the failure W2 §4 names.

use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::Path;
use std::sync::Arc;

use axum::extract::Json;
use axum::routing::post;
use axum::Router;
use lattice_auth::{AuthConfig, AuthState, Clock, OrderedMap};
use lattice_chat::intents::HistoryMeta;
use lattice_chat::turn::write_chat_turn;
use lattice_chat::{router, ChatConfig, ChatState, ChatWorker};
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

fn auth_for(dir: &Path) -> Arc<AuthState> {
    let mut env = HashMap::new();
    env.insert("LATTICEAI_REQUIRE_AUTH".into(), "1".into());
    env.insert(
        "LATTICEAI_DATA_DIR".into(),
        dir.to_string_lossy().into_owned(),
    );
    let mut config = AuthConfig::from_map(&env, None);
    config.data_dir = dir.to_path_buf();
    AuthState::with_clock(config, Clock::frozen(1_786_000_000.0))
}

fn meta<'a>(conversation: &'a str) -> HistoryMeta<'a> {
    HistoryMeta {
        email: Some("owner@fixture.local"),
        nickname: Some("owner"),
        source: Some("web"),
        conversation_id: Some(conversation),
        workspace_id: None,
    }
}

/// A worker whose `/worker/embed` answers under `model_id`, with `dim` floats.
async fn spawn_embed_worker(model_id: &'static str, dim: usize) -> String {
    let app = Router::new()
        .route(
            "/worker/embed",
            post(move |Json(body): Json<Value>| async move {
                let texts = body["texts"].as_array().cloned().unwrap_or_default();
                let vectors: Vec<Vec<f64>> = texts.iter().map(|_| vec![0.0; dim]).collect();
                axum::Json(json!({
                    "vectors": vectors, "dim": dim, "provider": "network",
                    "model_id": model_id, "kind": "passage",
                }))
            }),
        )
        .route(
            "/worker/extract",
            post(|Json(_body): Json<Value>| async move {
                axum::Json(json!({
                    "concepts": [{"text": "Lattice", "node_type": "Concept"}],
                    "triples": [],
                    "semantic": [],
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

/// Serve the chat router and return `(origin, session token)`.
async fn serve(state: ChatState, auth: &Arc<AuthState>) -> (String, String) {
    let token = auth
        .sessions()
        .create("user:owner", Some("owner@fixture.local"));
    let app = router(state);
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
    (format!("http://{addr}"), token)
}

async fn get_json(origin: &str, path: &str, token: &str) -> Value {
    let client = reqwest::Client::builder()
        .no_proxy()
        .build()
        .expect("client");
    let body = client
        .get(format!("{origin}{path}"))
        .header("cookie", format!("session_token={token}"))
        .send()
        .await
        .expect("request")
        .text()
        .await
        .expect("text");
    serde_json::from_str(&body).unwrap_or(Value::Null)
}

#[tokio::test]
async fn a_redacted_turn_reads_back_through_the_native_history_routes() {
    let data = tempfile::tempdir().expect("data");
    seed_users(data.path());
    let auth = auth_for(data.path());
    let config = ChatConfig {
        data_dir: data.path().to_path_buf(),
        // The read lanes need the path; the *writer* is deliberately unbound, so
        // the store half must stand on its own and the database must be created
        // on first write the way `ConversationStore.__init__` creates it.
        graph_db: Some(data.path().join("knowledge_graph.sqlite")),
        agent_root: data.path().join("agent"),
        ..ChatConfig::default()
    };
    assert!(
        !data.path().join("knowledge_graph.sqlite").exists(),
        "the database must not exist before the first turn"
    );
    let state = ChatState::new(Arc::clone(&auth), config);

    let user = write_chat_turn(
        &state,
        "user",
        "내 키는 api_key: abcdefgh12345678 야",
        &meta("conv-1"),
    )
    .await;
    let assistant = write_chat_turn(
        &state,
        "assistant",
        "Connect AI 가 답변합니다",
        &meta("conv-1"),
    )
    .await;

    assert!(user.stored, "the store must accept the row");
    assert_eq!(
        user.content(),
        Some("내 키는 api_key=[REDACTED_SECRET] 야"),
        "the stored text is the redacted one, never the original"
    );
    assert_eq!(
        assistant.content(),
        Some("Lattice AI 가 답변합니다"),
        "an assistant turn is brand-normalised after redaction"
    );
    assert_eq!(
        user.ingested, None,
        "no graph writer bound ⇒ ingested: null, the seam's own graph-off answer"
    );

    // Read back through the routes the SPA and the extension actually call.
    let (origin, token) = serve(state, &auth).await;
    let history = get_json(&origin, "/history", &token).await;
    let items = history.as_array().expect("history array");
    assert_eq!(items.len(), 2);
    assert_eq!(items[0]["role"], "user");
    assert_eq!(items[0]["content"], "내 키는 api_key=[REDACTED_SECRET] 야");
    assert_eq!(items[0]["user_email"], "owner@fixture.local");
    assert_eq!(items[0]["user_nickname"], "owner");
    assert_eq!(items[0]["source"], "web");
    assert_eq!(items[0]["conversation_id"], "conv-1");
    assert_eq!(items[1]["role"], "assistant");
    assert_eq!(items[1]["content"], "Lattice AI 가 답변합니다");
    // The store's declared column order, as `/rust/history` has always answered.
    let keys: Vec<&String> = items[0].as_object().expect("item").keys().collect();
    assert_eq!(keys[0], "role");
    assert_eq!(keys[1], "content");
    assert_eq!(keys[2], "timestamp");

    let conversation = get_json(&origin, "/history/conversations/conv-1", &token).await;
    assert_eq!(conversation["id"], "conv-1");
    assert_eq!(
        conversation["messages"].as_array().expect("messages").len(),
        2
    );
    let grouped = get_json(&origin, "/history/conversations", &token).await;
    assert_eq!(grouped.as_array().expect("groups").len(), 1);
    let found = get_json(&origin, "/history/search?q=Lattice", &token).await;
    assert_eq!(found["query"], "Lattice");
    assert_eq!(found["results"].as_array().expect("results").len(), 1);
}

#[tokio::test]
async fn the_same_turn_twice_is_one_row() {
    let data = tempfile::tempdir().expect("data");
    seed_users(data.path());
    let auth = auth_for(data.path());
    let state = ChatState::new(
        auth,
        ChatConfig {
            data_dir: data.path().to_path_buf(),
            agent_root: data.path().join("agent"),
            ..ChatConfig::default()
        },
    );
    // `message_hash` is UNIQUE and the insert is `INSERT OR IGNORE`, so a retry
    // of an identical turn is a no-op rather than a duplicate — the property the
    // dedup column exists for.
    let item = write_chat_turn(&state, "user", "같은 말", &meta("conv-dup")).await;
    let timestamp = item.item.as_ref().expect("item")["timestamp"].clone();
    let repeat = json!({
        "role": "user", "content": "같은 말", "timestamp": timestamp,
        "user_email": "owner@fixture.local", "conversation_id": "conv-dup",
        "source": "web",
    });
    let conn = rusqlite::Connection::open(state.conversation_db()).expect("db");
    conn.execute(
        "INSERT OR IGNORE INTO conversation_messages
           (message_hash, conversation_id, role, content, user_email, user_nickname,
            source, timestamp, metadata_json, workspace_id, organization_id)
         VALUES (?1, 'conv-dup', 'user', '같은 말', 'owner@fixture.local', 'owner',
                 'web', ?2, '{}', NULL, NULL)",
        rusqlite::params![
            lattice_chat::turn::message_hash(&repeat),
            repeat["timestamp"].as_str().unwrap_or_default(),
        ],
    )
    .expect("insert");
    let count: i64 = conn
        .query_row("SELECT COUNT(*) FROM conversation_messages", [], |row| {
            row.get(0)
        })
        .expect("count");
    assert_eq!(
        count, 1,
        "the hash a Python row would carry is the one we write"
    );
}

#[tokio::test]
async fn an_embedder_this_process_cannot_reproduce_leaves_the_vector_as_backlog() {
    let data = tempfile::tempdir().expect("data");
    seed_users(data.path());
    let auth = auth_for(data.path());
    let db = data.path().join("knowledge_graph.sqlite");
    let store = Arc::new(lattice_core::db::Store::open(&db).expect("store"));
    let graph = lattice_core::graph_write::GraphWriter::open(
        store,
        data.path().join("knowledge_graph_blobs"),
    )
    .expect("graph");
    let origin = spawn_embed_worker("openai:text-embedding-3-small", 1536).await;
    let state = ChatState::new(
        auth,
        ChatConfig {
            data_dir: data.path().to_path_buf(),
            graph_db: Some(db.clone()),
            agent_root: data.path().join("agent"),
            ..ChatConfig::default()
        },
    )
    .with_worker(ChatWorker::new(&origin).expect("worker"))
    .with_graph(graph);

    let turn = write_chat_turn(&state, "user", "임베딩 확인", &meta("conv-embed")).await;
    assert!(
        turn.stored,
        "the message is stored whatever the embedder says"
    );
    let ingested = turn
        .ingested
        .expect("the graph is on, so there is a receipt");
    assert_eq!(ingested["status"], "ok", "the graph write itself landed");
    assert_eq!(
        ingested["indexing_status"], "pending",
        "an index we cannot reproduce is backlog, not a silent wrong write"
    );
    assert!(
        ingested["detail"]
            .as_str()
            .unwrap_or_default()
            .contains("embedder mismatch"),
        "the reason is named: {}",
        ingested["detail"]
    );
    assert_eq!(ingested["source_type"], "chat_message");

    let conn = rusqlite::Connection::open(&db).expect("db");
    let nodes: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM nodes WHERE type='Message'",
            [],
            |row| row.get(0),
        )
        .expect("nodes");
    assert_eq!(nodes, 1, "the graph write itself is unaffected");
    // W5's supplied-vector door files the worker vector on the Message node.
    // Other nodes (Chat / Person) still hash inline. The receipt stays
    // `pending` because write_vectors would overwrite the provider row.
    let message_id: String = conn
        .query_row("SELECT id FROM nodes WHERE type='Message'", [], |row| {
            row.get(0)
        })
        .expect("message id");
    let model: String = conn
        .query_row(
            "SELECT embedding_model FROM vector_embeddings WHERE item_id=?",
            rusqlite::params![message_id],
            |row| row.get(0),
        )
        .expect("message vector");
    assert_eq!(
        model, "openai:text-embedding-3-small",
        "the Message node carries the worker vector, not the native hash"
    );
    let native: String = conn
        .query_row(
            "SELECT embedding_model FROM vector_embeddings ve \
             JOIN nodes n ON n.id=ve.item_id WHERE n.type='Chat'",
            [],
            |row| row.get(0),
        )
        .expect("chat vector");
    assert_eq!(
        native,
        lattice_core::embeddings::LocalEmbeddingModel::from_env().model_id(),
        "nodes the caller did not supply a vector for still hash"
    );
}

#[tokio::test]
async fn with_a_graph_but_no_worker_the_turn_still_lands() {
    let data = tempfile::tempdir().expect("data");
    seed_users(data.path());
    let auth = auth_for(data.path());
    let db = data.path().join("knowledge_graph.sqlite");
    let store = Arc::new(lattice_core::db::Store::open(&db).expect("store"));
    let graph = lattice_core::graph_write::GraphWriter::open(
        store,
        data.path().join("knowledge_graph_blobs"),
    )
    .expect("graph");
    let state = ChatState::new(
        auth,
        ChatConfig {
            data_dir: data.path().to_path_buf(),
            graph_db: Some(db.clone()),
            agent_root: data.path().join("agent"),
            ..ChatConfig::default()
        },
    )
    .with_graph(graph);

    // No worker at all: this is the case the seam could not survive — a turn
    // used to be lost when the AI worker was unreachable, because the chain
    // itself lived there.
    let turn = write_chat_turn(&state, "assistant", "connect-ai 입니다", &meta("conv-off")).await;
    assert!(turn.stored);
    assert_eq!(turn.content(), Some("Lattice AI 입니다"));
    let ingested = turn.ingested.expect("the graph is on");
    assert_eq!(ingested["indexing_status"], "pending");
    assert!(
        ingested["provenance_id"].is_string(),
        "provenance still recorded"
    );

    let conn = rusqlite::Connection::open(&db).expect("db");
    let responses: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM nodes WHERE type='AIResponse'",
            [],
            |row| row.get(0),
        )
        .expect("nodes");
    assert_eq!(responses, 1);
    // The audit row is on R2's file, in R2's format, with no sink bound.
    let audit: Value = serde_json::from_str(
        &std::fs::read_to_string(data.path().join("audit_log.json")).expect("audit log"),
    )
    .expect("json");
    let row = &audit.as_array().expect("array")[0];
    assert_eq!(row["event_type"], "chat_message");
    assert_eq!(row["role"], "assistant");
    assert_eq!(row["source"], "web");
    assert_eq!(row["workspace_id"], Value::Null);
    assert_eq!(row["sensitive_labels"], json!([]));
    assert!(row["contract"].is_object(), "the family envelope R2 stamps");
}

#[tokio::test]
async fn extract_failure_is_best_effort_the_turn_still_lands() {
    let data = tempfile::tempdir().expect("data");
    seed_users(data.path());
    let auth = auth_for(data.path());
    let db = data.path().join("knowledge_graph.sqlite");
    let store = Arc::new(lattice_core::db::Store::open(&db).expect("store"));
    let graph = lattice_core::graph_write::GraphWriter::open(
        store,
        data.path().join("knowledge_graph_blobs"),
    )
    .expect("graph");
    let app = Router::new()
        .route(
            "/worker/extract",
            post(|| async { axum::http::StatusCode::INTERNAL_SERVER_ERROR }),
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
                    "provider": "hash",
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
    let origin = format!("http://{addr}");
    let state = ChatState::new(
        auth,
        ChatConfig {
            data_dir: data.path().to_path_buf(),
            graph_db: Some(db.clone()),
            agent_root: data.path().join("agent"),
            ..ChatConfig::default()
        },
    )
    .with_worker(ChatWorker::new(&origin).expect("worker"))
    .with_graph(graph);

    let turn = write_chat_turn(
        &state,
        "user",
        "Lattice AI uses Graph RAG.",
        &meta("conv-extract"),
    )
    .await;
    assert!(turn.stored, "extract failure must not lose the turn");
    let ingested = turn
        .ingested
        .expect("ingest is best-effort: empty concepts, not a failed write");
    assert_eq!(ingested["status"], "ok");

    let conn = rusqlite::Connection::open(&db).expect("db");
    let concepts: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM nodes WHERE type='Concept'",
            [],
            |row| row.get(0),
        )
        .expect("concepts");
    assert_eq!(
        concepts, 0,
        "a failed extract leaves the concept subgraph empty"
    );
}

#[tokio::test]
async fn agreeing_embedder_supplies_chunk_vectors() {
    let data = tempfile::tempdir().expect("data");
    seed_users(data.path());
    let auth = auth_for(data.path());
    let db = data.path().join("knowledge_graph.sqlite");
    let store = Arc::new(lattice_core::db::Store::open(&db).expect("store"));
    let graph = lattice_core::graph_write::GraphWriter::open(
        store,
        data.path().join("knowledge_graph_blobs"),
    )
    .expect("graph");
    let origin = {
        let app = Router::new()
            .route(
                "/worker/extract",
                post(|Json(_body): Json<Value>| async move {
                    axum::Json(json!({
                        "concepts": [],
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
                        "provider": "hash",
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
    };
    let state = ChatState::new(
        auth,
        ChatConfig {
            data_dir: data.path().to_path_buf(),
            graph_db: Some(db.clone()),
            agent_root: data.path().join("agent"),
            ..ChatConfig::default()
        },
    )
    .with_worker(ChatWorker::new(&origin).expect("worker"))
    .with_graph(graph);

    let turn = write_chat_turn(
        &state,
        "user",
        "Lattice AI uses Graph RAG for retrieval.",
        &meta("conv-chunks"),
    )
    .await;
    assert!(turn.stored);
    let ingested = turn.ingested.expect("graph on");
    assert_eq!(ingested["status"], "ok");

    let conn = rusqlite::Connection::open(&db).expect("db");
    let chunks: i64 = conn
        .query_row("SELECT COUNT(*) FROM chunks", [], |row| row.get(0))
        .expect("chunks");
    assert!(chunks >= 1, "a chat turn must write chunk rows");
    let model: String = conn
        .query_row(
            "SELECT embedding_model FROM vector_embeddings WHERE item_type='chunk' LIMIT 1",
            [],
            |row| row.get(0),
        )
        .expect("chunk vector");
    assert_eq!(
        model,
        lattice_core::embeddings::LocalEmbeddingModel::from_env().model_id()
    );
    let messages: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM nodes WHERE type='Message'",
            [],
            |row| row.get(0),
        )
        .expect("messages");
    assert_eq!(messages, 1);
}
