use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::{Arc, Mutex};

use axum::extract::Json;
use axum::routing::{get, post};
use axum::Router;
use lattice_auth::{AuthConfig, AuthState, Clock, OrderedMap};
use lattice_chat::{
    router, ChatConfig, ChatState, ChatWorker, CloudProvider, OpenAiCompatibleAdapter,
};
use lattice_core::graph_write::types::{ChunkPiece, IngestContentRequest};
use lattice_core::graph_write::GraphWriter;
use serde_json::{json, Map, Value};

type Captured = Arc<Mutex<Vec<Value>>>;

async fn listen(app: Router) -> String {
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
    format!("http://{addr}")
}

fn answer_frames(frame: Value) -> ([(&'static str, &'static str); 1], String) {
    (
        [("content-type", "text/event-stream")],
        format!("data: {frame}\n\ndata: [DONE]\n\n"),
    )
}

fn recording(path: &str, seen: Captured, frame: Value) -> Router {
    Router::new().route(
        path,
        post(move |Json(body): Json<Value>| {
            seen.lock().unwrap().push(body);
            let reply = answer_frames(frame.clone());
            async move { reply }
        }),
    )
}

struct Rig {
    origin: String,
    token: String,
    local: Captured,
    cloud: Captured,
    graph: GraphWriter,
    _data: tempfile::TempDir,
}

impl Rig {
    async fn start(model: Option<&str>, cloud_allowed: bool) -> Self {
        let data = tempfile::tempdir().expect("data");
        let email = "owner@fixture.local";
        let mut users = OrderedMap::new();
        users.insert(
            email,
            json!({
                "password": "x", "name": "owner", "role": "admin", "disabled": false,
                "id": lattice_auth::stable_user_id(email), "email": email,
            }),
        );
        std::fs::write(
            data.path().join("users.json"),
            lattice_auth::pyjson::dumps_indent2(&users).expect("users"),
        )
        .expect("write users");
        if cloud_allowed {
            std::fs::write(
                data.path().join("network_boundary.json"),
                json!({"default": "cloud_allowed"}).to_string(),
            )
            .expect("boundary");
        }
        let mut env = HashMap::new();
        env.insert("LATTICEAI_REQUIRE_AUTH".into(), "1".into());
        env.insert(
            "LATTICEAI_DATA_DIR".into(),
            data.path().to_string_lossy().into_owned(),
        );
        let mut config = AuthConfig::from_map(&env, None);
        config.data_dir = data.path().to_path_buf();
        let auth = AuthState::with_clock(config, Clock::frozen(1_786_000_000.0));
        let token = auth.sessions().create("user:owner", Some(email));

        let local = Captured::default();
        let models = json!({
            "loaded": model.map(|id| vec![id]).unwrap_or_default(),
            "current": model,
        });
        let worker_origin = listen(
            recording("/worker/llm/stream", local.clone(), json!({"text": "ok"})).route(
                "/models",
                get(move || {
                    let models = models.clone();
                    async move { axum::Json(models) }
                }),
            ),
        )
        .await;
        let cloud = Captured::default();
        let cloud_origin = listen(
            recording(
                "/chat/completions",
                cloud.clone(),
                json!({"choices": [{"delta": {"content": "cloud"}}]}),
            )
            .route(
                "/models",
                get(|| async { axum::Json(json!({"data": [{"id": "mock-model"}]})) }),
            ),
        )
        .await;

        let graph_db = data.path().join("knowledge_graph.sqlite");
        let store = Arc::new(lattice_core::db::Store::open(&graph_db).expect("store"));
        let blobs = data.path().join("blobs");
        let graph = GraphWriter::open(store.clone(), blobs.clone()).expect("graph");
        let mut state = ChatState::new(
            Arc::clone(&auth),
            ChatConfig {
                data_dir: data.path().to_path_buf(),
                graph_db: Some(graph_db),
                agent_root: data.path().join("agent"),
                ..ChatConfig::default()
            },
        )
        .with_worker(ChatWorker::new(&worker_origin).expect("worker"))
        .with_graph(GraphWriter::open(store, blobs).expect("graph"));
        if cloud_allowed {
            let adapter =
                OpenAiCompatibleAdapter::from_parts("test-key", &cloud_origin, "mock-model");
            state = state.with_cloud_provider(CloudProvider::api_key(adapter, "openai_compatible"));
        }
        Self {
            origin: listen(router(state)).await,
            token,
            local,
            cloud,
            graph,
            _data: data,
        }
    }

    fn seed(&self, title: &str, chunks: &[&str], metadata: Value) -> String {
        self.graph
            .ingest_content(&IngestContentRequest {
                source_type: "note".into(),
                title: title.into(),
                text: chunks.join("\n"),
                chunks: chunks
                    .iter()
                    .map(|text| ChunkPiece {
                        text: (*text).into(),
                        fields: Map::new(),
                        embedding: None,
                    })
                    .collect(),
                metadata: metadata.as_object().cloned().unwrap_or_default(),
                ..Default::default()
            })
            .expect("ingest")
            .node_id
    }

    async fn post(&self, body: Value) -> String {
        let response = reqwest::Client::builder()
            .no_proxy()
            .timeout(std::time::Duration::from_secs(30))
            .build()
            .expect("client")
            .post(format!("{}/chat", self.origin))
            .header("cookie", format!("session_token={}", self.token))
            .header("content-type", "application/json")
            .body(body.to_string())
            .send()
            .await
            .expect("chat");
        assert_eq!(response.status(), 200);
        response.text().await.expect("body")
    }

    async fn json(&self, body: Value) -> Value {
        serde_json::from_str(&self.post(body).await).expect("json body")
    }

    fn local_context(&self) -> String {
        self.local
            .lock()
            .unwrap()
            .last()
            .and_then(|body| body["context"].as_str().map(str::to_string))
            .unwrap_or_default()
    }

    fn cloud_messages(&self) -> String {
        self.cloud
            .lock()
            .unwrap()
            .last()
            .map(|body| body["messages"].to_string())
            .unwrap_or_default()
    }
}

fn frames(body: &str) -> Vec<Value> {
    body.lines()
        .filter_map(|line| line.strip_prefix("data: "))
        .filter_map(|line| serde_json::from_str(line).ok())
        .collect()
}

#[tokio::test]
async fn a_local_turn_sends_labelled_evidence_and_reports_the_plan() {
    let rig = Rig::start(Some("demo"), false).await;
    rig.seed(
        "Phoenix runbook",
        &[
            "Phoenix rollback restarts the host",
            "Phoenix rollback checks the port",
            "Phoenix rollback rotates the keys",
            "Phoenix rollback archives the logs",
        ],
        json!({}),
    );
    let body = rig
        .json(json!({"message": "Phoenix rollback", "stream": false, "model": "demo"}))
        .await;
    let context = rig.local_context();
    assert!(
        context.contains(lattice_retrieval::context::EVIDENCE_RULES),
        "{context}"
    );
    assert!(context.contains("[updated: 2"), "{context}");
    assert!(context.contains("| status: active]"), "{context}");
    let assembly = &body["trace"]["context_assembly"];
    assert_eq!(assembly["query_plan"]["strategy"], "rules");
    assert_eq!(assembly["diversity"]["applied"], true);
    assert_eq!(assembly["diversity"]["per_document_cap"], 2);
    assert_eq!(assembly["self_rag"]["required"], false);
    let knowledge = assembly["sections"]
        .as_array()
        .and_then(|sections| {
            sections
                .iter()
                .find(|section| section["source"] == "knowledge")
        })
        .expect("knowledge section");
    let cited = knowledge["provenance"].as_array().map_or(0, Vec::len);
    assert!((1..=3).contains(&cited), "{knowledge}");
}

#[tokio::test]
async fn a_streamed_follow_up_is_rewritten_with_the_earlier_topic() {
    let rig = Rig::start(Some("demo"), false).await;
    rig.seed(
        "Phoenix deployment plan",
        &["Phoenix deployment rollback restarts the host"],
        json!({}),
    );
    for message in ["Phoenix deployment plan", "what about its rollback?"] {
        let body = rig
            .post(json!({
                "message": message, "stream": true, "model": "demo", "conversation_id": "c-1",
            }))
            .await;
        let trailer = frames(&body)
            .into_iter()
            .find(|frame| frame.get("trace").is_some())
            .expect("trailer");
        let plan = &trailer["trace"]["context_assembly"]["query_plan"];
        if message.starts_with("what") {
            assert_eq!(plan["follow_up"], true, "{plan}");
            assert!(
                plan["standalone"]
                    .as_str()
                    .is_some_and(|text| text.starts_with("Phoenix deployment plan")),
                "{plan}"
            );
        } else {
            assert_eq!(plan["follow_up"], false, "{plan}");
        }
    }
    assert!(rig.local_context().contains("[updated: "));
}

#[tokio::test]
async fn an_unsupported_records_question_abstains_both_ways_and_stays_abstaining() {
    let rig = Rig::start(Some("demo"), false).await;
    rig.seed("Garden", &["Tomatoes need water twice a week"], json!({}));
    let question = "What do my notes say about the Zanzibar tariff quota?";
    for stream in [false, true, false] {
        let body = rig
            .post(json!({
                "message": question, "stream": stream, "model": "demo", "conversation_id": "c-2",
            }))
            .await;
        let verdict = if stream {
            frames(&body)
                .into_iter()
                .find(|frame| frame.get("abstention").is_some())
                .unwrap_or_else(|| panic!("{body}"))
        } else {
            serde_json::from_str(&body).expect("json")
        };
        assert_eq!(verdict["abstention"]["abstained"], true, "{verdict}");
        assert_eq!(verdict["grounding"]["status"], "no_context", "{verdict}");
    }
    assert!(rig.local.lock().unwrap().is_empty());
}

#[tokio::test]
async fn the_hybrid_lane_labels_evidence_and_instructs_the_provider() {
    let rig = Rig::start(None, true).await;
    rig.seed(
        "Phoenix runbook",
        &["Phoenix rollback restarts the host"],
        json!({}),
    );
    for stream in [false, true] {
        let body = rig
            .post(json!({"message": "Phoenix rollback", "stream": stream}))
            .await;
        assert!(body.contains("cloud"), "{body}");
        let sent = rig.cloud_messages();
        assert!(sent.contains("Evidence rules:"), "{sent}");
        assert!(sent.contains("[updated: 2"), "{sent}");
        assert!(sent.contains("| status: active]"), "{sent}");
    }
}

#[tokio::test]
async fn local_only_evidence_is_never_sent_and_the_refusal_says_so() {
    let rig = Rig::start(None, true).await;
    rig.seed(
        "Zanzibar tariff",
        &["Zanzibar tariff quota is forty crates"],
        json!({"private": true, "local_only": true}),
    );
    for stream in [false, true] {
        let body = rig
            .post(json!({
                "message": "What do my notes say about the Zanzibar tariff quota?",
                "stream": stream,
            }))
            .await;
        assert!(!rig.cloud_messages().contains("forty crates"), "{body}");
        assert!(body.contains("evidence_not_shareable"), "{body}");
    }
}

#[tokio::test]
async fn unrelated_shareable_notes_do_not_satisfy_the_gate_for_the_provider() {
    let rig = Rig::start(None, true).await;
    rig.seed(
        "Zanzibar tariff",
        &["Zanzibar tariff quota is forty crates"],
        json!({"private": true, "local_only": true}),
    );
    rig.seed("Garden", &["Tomatoes need water twice a week"], json!({}));
    for stream in [false, true] {
        let body = rig
            .post(json!({
                "message": "What do my notes say about the Zanzibar tariff quota?",
                "stream": stream,
            }))
            .await;
        assert!(rig.cloud.lock().unwrap().is_empty(), "{body}");
        assert!(body.contains("evidence_not_shareable"), "{body}");
    }
}

#[tokio::test]
async fn a_records_question_abstains_when_retrieval_itself_fails() {
    let rig = Rig::start(Some("demo"), false).await;
    rig.seed("Garden", &["Tomatoes need water twice a week"], json!({}));
    rusqlite::Connection::open(rig._data.path().join("knowledge_graph.sqlite"))
        .expect("open")
        .execute_batch(
            "PRAGMA foreign_keys=OFF; ALTER TABLE nodes RENAME TO nodes_gone; \
             ALTER TABLE nodes_v2 RENAME TO nodes_v2_gone; \
             ALTER TABLE chunks RENAME TO chunks_gone;",
        )
        .expect("rename");
    let body = rig
        .json(json!({
            "message": "What do my notes say about tomatoes?", "stream": false, "model": "demo",
        }))
        .await;
    assert_eq!(body["abstention"]["abstained"], true, "{body}");
    assert!(rig.local.lock().unwrap().is_empty());
}

#[tokio::test]
async fn chunks_of_a_private_document_never_reach_the_provider() {
    for flag in [
        "shareable",
        "private",
        "local_only",
        "do_not_share",
        "sensitive",
    ] {
        let rig = Rig::start(None, true).await;
        let mut metadata = Map::new();
        metadata.insert(flag.into(), json!(true));
        rig.seed(
            "Zanzibar tariff",
            &["Zanzibar tariff quota is forty crates"],
            Value::Object(metadata),
        );
        rig.post(json!({"message": "Zanzibar tariff quota", "stream": false}))
            .await;
        let sent = rig.cloud_messages();
        assert!(!sent.is_empty(), "{flag}");
        assert!(!sent.contains("[Source]"), "{flag}: {sent}");
        assert_eq!(
            sent.contains("forty crates"),
            flag == "shareable",
            "{flag}: {sent}"
        );
    }
}
