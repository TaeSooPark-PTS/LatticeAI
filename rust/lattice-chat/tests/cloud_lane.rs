//! Hybrid cloud lane: mock OpenAI-compatible HTTP + the status contract.
//!
//! No real provider is contacted. The adapter is pointed at a loopback axum
//! server that speaks Chat Completions SSE.

use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::Path;
use std::sync::Arc;

use axum::http::StatusCode;
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_auth::{AuthConfig, AuthState, Clock};
use lattice_chat::{router, ChatConfig, ChatState, CloudProvider, OpenAiCompatibleAdapter};
use serde_json::{json, Value};

fn seed_users(dir: &Path) {
    let email = "owner@fixture.local";
    let mut owner = lattice_auth::OrderedMap::new();
    owner.insert("password", json!("x"));
    owner.insert("name", json!("owner"));
    owner.insert("role", json!("admin"));
    owner.insert("disabled", json!(false));
    owner.insert("id", json!(lattice_auth::stable_user_id(email)));
    owner.insert("email", json!(email));
    let mut users = lattice_auth::OrderedMap::new();
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

async fn serve_chat(state: ChatState, auth: &AuthState) -> (String, String) {
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

fn client() -> reqwest::Client {
    reqwest::Client::builder()
        .no_proxy()
        .build()
        .expect("client")
}

async fn spawn_mock_openai(status: u16, pieces: &[&str]) -> String {
    let frames: Vec<String> = pieces
        .iter()
        .map(|piece| {
            format!(
                "data: {}\n\n",
                json!({"choices":[{"delta":{"content": piece}}]})
            )
        })
        .collect();
    let app = Router::new()
        .route(
            "/models",
            get(|| async { axum::Json(json!({"data": [{"id": "mock-model"}]})) }),
        )
        .route(
            "/chat/completions",
            post(move || {
                let frames = frames.clone();
                let status = status;
                async move {
                    if status != 200 {
                        return Response::builder()
                            .status(StatusCode::from_u16(status).unwrap())
                            .body(axum::body::Body::from("provider down"))
                            .unwrap();
                    }
                    let mut body = frames.join("");
                    body.push_str("data: [DONE]\n\n");
                    Response::builder()
                        .status(StatusCode::OK)
                        .header("content-type", "text/event-stream")
                        .body(axum::body::Body::from(body))
                        .unwrap()
                }
            }),
        );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind");
    let addr = listener.local_addr().expect("addr");
    tokio::spawn(async move {
        let _ = axum::serve(listener, app).await;
    });
    format!("http://{addr}")
}

fn empty_models_worker() -> Router {
    Router::new().route(
        "/models",
        get(|| async { axum::Json(json!({"loaded": [], "current": null})) }),
    )
}

async fn spawn_worker(app: Router) -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind");
    let addr = listener.local_addr().expect("addr");
    tokio::spawn(async move {
        let _ = axum::serve(listener, app).await;
    });
    format!("http://{addr}")
}

async fn install(
    mock_origin: &str,
    cloud_allowed: bool,
) -> (tempfile::TempDir, ChatState, Arc<AuthState>) {
    let data = tempfile::tempdir().expect("data");
    seed_users(data.path());
    if cloud_allowed {
        std::fs::write(
            data.path().join("network_boundary.json"),
            json!({"default": "cloud_allowed"}).to_string(),
        )
        .unwrap();
    }
    let auth = auth_for(data.path());
    let worker_origin = spawn_worker(empty_models_worker()).await;
    let worker = lattice_chat::ChatWorker::new(&worker_origin).expect("worker");
    let adapter = OpenAiCompatibleAdapter::from_parts("test-key", mock_origin, "mock-model");
    let provider = CloudProvider::api_key(adapter, "openai_compatible");
    let graph_db = data.path().join("knowledge_graph.sqlite");
    let store = Arc::new(lattice_core::db::Store::open(&graph_db).expect("store"));
    let graph = lattice_core::graph_write::GraphWriter::open(
        store,
        data.path().join("knowledge_graph_blobs"),
    )
    .expect("graph");
    let state = ChatState::new(
        Arc::clone(&auth),
        ChatConfig {
            data_dir: data.path().to_path_buf(),
            graph_db: Some(graph_db),
            agent_root: data.path().join("agent"),
            ..ChatConfig::default()
        },
    )
    .with_worker(worker)
    .with_graph(graph)
    .with_cloud_provider(provider);
    (data, state, auth)
}

fn sse_frames(body: &str) -> Vec<Value> {
    body.split("\n\n")
        .filter_map(|block| {
            let line = block
                .lines()
                .find(|line| line.starts_with("data: "))
                .map(|line| &line[6..])?;
            if line == "[DONE]" {
                return None;
            }
            serde_json::from_str(line).ok()
        })
        .collect()
}

#[tokio::test]
async fn a_cloud_turn_streams_tokens_and_names_the_provider() {
    let mock = spawn_mock_openai(200, &["서", "울"]).await;
    let (_data, state, auth) = install(&mock, true).await;
    let (origin, token) = serve_chat(state, &auth).await;
    let response = client()
        .post(format!("{origin}/chat"))
        .header("cookie", format!("session_token={token}"))
        .header("content-type", "application/json")
        .body(json!({"message":"수도는?","stream":true}).to_string())
        .send()
        .await
        .expect("request");
    assert_eq!(response.status(), 200);
    let body = response.text().await.expect("body");
    let frames = sse_frames(&body);
    assert_eq!(frames[0]["type"], "hybrid_context");
    assert_eq!(frames[0]["reason"], "no_local_model");
    let tokens: String = frames
        .iter()
        .filter(|frame| frame["type"] == "token")
        .filter_map(|frame| frame["chunk"].as_str())
        .collect();
    assert_eq!(tokens, "서울");
    let done = frames
        .iter()
        .find(|frame| frame["type"] == "hybrid_done")
        .expect("hybrid_done");
    assert_eq!(done["provider"], "openai_compatible");
    assert_eq!(done["model"], "mock-model");
    assert_eq!(done["answer"], "서울");
}

#[tokio::test]
async fn stream_false_is_a_json_body() {
    let mock = spawn_mock_openai(200, &["ok"]).await;
    let (_data, state, auth) = install(&mock, true).await;
    let (origin, token) = serve_chat(state, &auth).await;
    let response = client()
        .post(format!("{origin}/chat"))
        .header("cookie", format!("session_token={token}"))
        .header("content-type", "application/json")
        .body(json!({"message":"hi","stream":false}).to_string())
        .send()
        .await
        .expect("request");
    assert_eq!(response.status(), 200);
    let body: Value = serde_json::from_str(&response.text().await.unwrap()).unwrap();
    assert_eq!(body["response"], "ok");
    assert_eq!(body["provider"], "openai_compatible");
    assert_eq!(body["reason"], "no_local_model");
}

#[tokio::test]
async fn a_provider_500_is_an_honest_error_frame() {
    let mock = spawn_mock_openai(500, &[]).await;
    let (_data, state, auth) = install(&mock, true).await;
    let (origin, token) = serve_chat(state, &auth).await;
    let response = client()
        .post(format!("{origin}/chat"))
        .header("cookie", format!("session_token={token}"))
        .header("content-type", "application/json")
        .body(json!({"message":"hi","stream":true}).to_string())
        .send()
        .await
        .expect("request");
    let body = response.text().await.unwrap();
    let frames = sse_frames(&body);
    let error = frames
        .iter()
        .find(|frame| frame["type"] == "error")
        .expect("error frame");
    assert!(error["detail"].as_str().unwrap().contains("500"), "{error}");
}

#[tokio::test]
async fn a_fat_turn_is_refused_by_the_token_guard() {
    let mock = spawn_mock_openai(200, &["no"]).await;
    let (_data, state, auth) = install(&mock, true).await;
    let (origin, token) = serve_chat(state, &auth).await;
    let fat = "가".repeat(20_000);
    let response = client()
        .post(format!("{origin}/chat"))
        .header("cookie", format!("session_token={token}"))
        .header("content-type", "application/json")
        .body(json!({"message":fat,"stream":true}).to_string())
        .send()
        .await
        .expect("request");
    let body = response.text().await.unwrap();
    let frames = sse_frames(&body);
    let error = frames
        .iter()
        .find(|frame| frame["type"] == "error")
        .expect("error");
    assert!(
        error["detail"]
            .as_str()
            .unwrap()
            .contains("cloud token guard"),
        "{error}"
    );
}

#[tokio::test]
async fn local_only_override_never_enters_the_hybrid_lane() {
    let mock = spawn_mock_openai(200, &["cloud"]).await;
    let (_data, state, auth) = install(&mock, true).await;
    let (origin, token) = serve_chat(state, &auth).await;
    let response = client()
        .post(format!("{origin}/chat"))
        .header("cookie", format!("session_token={token}"))
        .header("content-type", "application/json")
        .body(json!({"message":"hi","stream":false,"network_mode":"local_only"}).to_string())
        .send()
        .await
        .expect("request");
    let body: Value = serde_json::from_str(&response.text().await.unwrap()).unwrap();
    assert!(
        body.get("provider").is_none(),
        "a local_only override must not produce a hybrid JSON body: {body}"
    );
}

#[tokio::test]
async fn cloud_status_matches_the_injected_provider() {
    let mock = spawn_mock_openai(200, &[]).await;
    let (_data, state, auth) = install(&mock, true).await;
    let (origin, token) = serve_chat(state, &auth).await;
    let response = client()
        .get(format!("{origin}/api/cloud/status"))
        .header("cookie", format!("session_token={token}"))
        .send()
        .await
        .expect("request");
    assert_eq!(response.status(), 200);
    let body: Value = serde_json::from_str(&response.text().await.unwrap()).unwrap();
    assert_eq!(body["configured"], true);
    assert_eq!(body["mode"], "api_key");
    assert_eq!(body["provider"], "openai_compatible");
    assert_eq!(body["model"], "mock-model");
    assert_eq!(body["verified"], true);
}

#[tokio::test]
async fn an_unreachable_api_key_fail_closes_status() {
    let adapter =
        OpenAiCompatibleAdapter::from_parts("sk-test", "http://127.0.0.1:1", "mock-model");
    let provider = CloudProvider::api_key(adapter, "openai_compatible");
    let status = provider.status().await.to_value();
    assert_eq!(status["configured"], false);
    assert_eq!(status["verified"], false);
    assert!(
        status["detail"]
            .as_str()
            .unwrap_or_default()
            .contains("unreachable")
            || status["detail"]
                .as_str()
                .unwrap_or_default()
                .contains("answered"),
        "{status}"
    );
}

#[tokio::test]
async fn an_unconfigured_status_is_none() {
    let data = tempfile::tempdir().expect("data");
    seed_users(data.path());
    std::fs::write(
        data.path().join("cloud_provider.json"),
        json!({"mode":"none"}).to_string(),
    )
    .unwrap();
    let auth = auth_for(data.path());
    let state = ChatState::new(
        Arc::clone(&auth),
        ChatConfig {
            data_dir: data.path().to_path_buf(),
            ..ChatConfig::default()
        },
    );
    let (origin, token) = serve_chat(state, &auth).await;
    let body: Value = serde_json::from_str(
        &client()
            .get(format!("{origin}/api/cloud/status"))
            .header("cookie", format!("session_token={token}"))
            .send()
            .await
            .unwrap()
            .text()
            .await
            .unwrap(),
    )
    .unwrap();
    assert_eq!(body["configured"], false);
    assert_eq!(body["mode"], "none");
}

#[tokio::test]
async fn disk_configured_api_key_e2e_verifies_bearer_and_strips_local_only_nodes() {
    use axum::extract::State as AxumState;
    use axum::http::{HeaderMap, StatusCode};
    use axum::routing::{get, post};
    use std::sync::Mutex;

    #[derive(Clone, Default)]
    struct Recorded {
        auth_headers: Arc<Mutex<Vec<String>>>,
        outbound_bodies: Arc<Mutex<Vec<Value>>>,
    }

    let rec = Recorded::default();
    let app =
        axum::Router::new()
            .route(
                "/v1/models",
                get(
                    |AxumState(r): AxumState<Recorded>, headers: HeaderMap| async move {
                        let auth = headers
                            .get("authorization")
                            .and_then(|v| v.to_str().ok())
                            .unwrap_or("")
                            .to_string();
                        r.auth_headers.lock().unwrap().push(auth.clone());
                        if auth != "Bearer sk-e2e-valid" {
                            return (
                                StatusCode::UNAUTHORIZED,
                                json!({"error":"bad key"}).to_string(),
                            );
                        }
                        (
                            StatusCode::OK,
                            json!({"data":[{"id":"gpt-4o-mini"}]}).to_string(),
                        )
                    },
                ),
            )
            .route(
                "/v1/chat/completions",
                post(
                    |AxumState(r): AxumState<Recorded>,
                     headers: HeaderMap,
                     body: axum::body::Bytes| async move {
                        let auth = headers
                            .get("authorization")
                            .and_then(|v| v.to_str().ok())
                            .unwrap_or("")
                            .to_string();
                        r.auth_headers.lock().unwrap().push(auth.clone());
                        if auth != "Bearer sk-e2e-valid" {
                            return (
                                StatusCode::UNAUTHORIZED,
                                [("content-type", "application/json")],
                                json!({"error":"unauthorized"}).to_string(),
                            );
                        }
                        let parsed: Value = serde_json::from_slice(&body).unwrap_or(Value::Null);
                        r.outbound_bodies.lock().unwrap().push(parsed);
                        let sse = format!(
                            "data: {}\n\ndata: [DONE]\n\n",
                            json!({"choices":[{"delta":{"content":"검증완료"}}]})
                        );
                        (StatusCode::OK, [("content-type", "text/event-stream")], sse)
                    },
                ),
            )
            .with_state(rec.clone());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let mock_base = format!("http://{}/v1", listener.local_addr().unwrap());
    tokio::spawn(async move {
        let _ = axum::serve(listener, app).await;
    });

    let data = tempfile::tempdir().expect("data");
    seed_users(data.path());
    std::fs::write(
        data.path().join("cloud_provider.json"),
        json!({
            "mode": "api_key",
            "provider": "openai",
            "api_key": "sk-e2e-valid",
            "base_url": mock_base,
            "model": "gpt-4o-mini"
        })
        .to_string(),
    )
    .unwrap();
    std::fs::write(
        data.path().join("network_boundary.json"),
        json!({"default": "cloud_allowed"}).to_string(),
    )
    .unwrap();

    let graph_db = data.path().join("knowledge_graph.sqlite");
    let store = Arc::new(lattice_core::db::Store::open(&graph_db).expect("store"));
    let graph = lattice_core::graph_write::GraphWriter::open(
        store,
        data.path().join("knowledge_graph_blobs"),
    )
    .expect("graph");
    // Seed one shareable node and one sensitive local_only node.
    let _ = graph
        .upsert_nodes(&[
            lattice_core::graph_write::types::NodeSpec {
                id: "doc:public".into(),
                node_type: "Note".into(),
                title: "Public roadmap alpha".into(),
                summary: "alpha architecture notes".into(),
                metadata: json!({"workspace_id": "default"})
                    .as_object()
                    .cloned()
                    .unwrap(),
                raw: serde_json::Map::new(),
                owner: None,
                workspace_id: Some("default".into()),
                visibility: None,
            },
            lattice_core::graph_write::types::NodeSpec {
                id: "doc:secret".into(),
                node_type: "Note".into(),
                title: "Secret payroll alpha".into(),
                summary: "TOP_SECRET_PAYROLL_9911".into(),
                metadata: json!({
                    "workspace_id": "default",
                    "local_only": true,
                    "sensitivity": "local_only"
                })
                .as_object()
                .cloned()
                .unwrap(),
                raw: serde_json::Map::new(),
                owner: None,
                workspace_id: Some("default".into()),
                visibility: None,
            },
        ])
        .expect("seed nodes");

    let worker_origin = spawn_worker(empty_models_worker()).await;
    let worker = lattice_chat::ChatWorker::new(&worker_origin).expect("worker");
    let auth = auth_for(data.path());
    let state = ChatState::new(
        Arc::clone(&auth),
        ChatConfig {
            data_dir: data.path().to_path_buf(),
            graph_db: Some(graph_db),
            agent_root: data.path().join("agent"),
            ..ChatConfig::default()
        },
    )
    .with_worker(worker)
    .with_graph(graph);
    let (origin, token) = serve_chat(state, &auth).await;

    // 1. Live /api/cloud/status probes GET /v1/models with Bearer key.
    let status_body: Value = serde_json::from_str(
        &client()
            .get(format!("{origin}/api/cloud/status"))
            .header("cookie", format!("session_token={token}"))
            .send()
            .await
            .unwrap()
            .text()
            .await
            .unwrap(),
    )
    .unwrap();
    assert_eq!(status_body["configured"], true);
    assert_eq!(status_body["verified"], true);
    assert_eq!(status_body["mode"], "api_key");

    // 2. End-to-end /chat completion streams via api_key and never leaks local_only nodes.
    let chat_body: Value = serde_json::from_str(
        &client()
            .post(format!("{origin}/chat"))
            .header("cookie", format!("session_token={token}"))
            .header("content-type", "application/json")
            .body(json!({"message": "alpha 요약해줘", "stream": false}).to_string())
            .send()
            .await
            .unwrap()
            .text()
            .await
            .unwrap(),
    )
    .unwrap();
    assert_eq!(chat_body["response"], "검증완료");
    let bodies = rec.outbound_bodies.lock().unwrap().clone();
    assert_eq!(bodies.len(), 1);
    let serialized = bodies[0].to_string();
    assert!(
        !serialized.contains("TOP_SECRET_PAYROLL_9911") && !serialized.contains("Secret payroll"),
        "local_only node leaked to api_key cloud payload: {serialized}"
    );
    assert!(rec
        .auth_headers
        .lock()
        .unwrap()
        .iter()
        .all(|h| h == "Bearer sk-e2e-valid"));
}
