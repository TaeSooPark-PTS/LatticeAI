//! `POST /chat` writes a file a model actually wrote (v11.9.0).
//!
//! The fixtures cannot cover this: `rust/fixtures/http/chat.json` was captured
//! with **no model loaded**, so every branch that needs one is a gap the capture
//! names. This suite is the proof instead — a scripted worker that answers
//! `POST /worker/llm/stream` with replies chosen to be the ones a small local
//! model really produces:
//!
//! * a clean document (what a good reply looks like);
//! * prose wrapped around a fenced document (what a 2B usually does);
//! * a document that stops mid-tag (what a token budget does);
//! * "I'm sorry, I can't do that" (what a refusal does).
//!
//! Each one has a required outcome, and three of the four are about *honesty*
//! rather than success: what the artifact claims, what the badge says, and the
//! one case where the right answer is to write nothing at all.

use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use axum::extract::{Json, Path as AxumPath};
use axum::http::StatusCode;
use axum::response::Response as AxumResponse;
use axum::routing::{get, post};
use axum::Router;
use base64::Engine;
use lattice_auth::{AuthConfig, AuthState, Clock, OrderedMap, WorkspaceResolver};
use lattice_chat::{router, ChatConfig, ChatState, ChatWorker};
use serde_json::{json, Value};

/// A reply the scripted worker hands out, in order.
#[derive(Clone)]
struct Script {
    replies: Arc<Mutex<Vec<String>>>,
    seen: Arc<Mutex<Vec<Value>>>,
    renders: Arc<Mutex<Vec<Value>>>,
    /// Bytes `POST /worker/render/{kind}` answers with; `None` ⇒ 503, the shape
    /// a worker without python-docx installed really answers.
    document: Arc<Mutex<Option<Vec<u8>>>>,
    /// Held before every completion, so a stream's first frame can be checked
    /// while the model is still "thinking".
    delay: Duration,
}

impl Script {
    fn new(replies: &[&str]) -> Self {
        Self {
            replies: Arc::new(Mutex::new(
                replies.iter().rev().map(|text| text.to_string()).collect(),
            )),
            seen: Arc::new(Mutex::new(Vec::new())),
            renders: Arc::new(Mutex::new(Vec::new())),
            document: Arc::new(Mutex::new(Some(
                b"PK\x03\x04 a real enough document".to_vec(),
            ))),
            delay: Duration::from_millis(0),
        }
    }

    fn slow(mut self, delay: Duration) -> Self {
        self.delay = delay;
        self
    }

    fn without_a_document_builder(self) -> Self {
        *self.document.lock().unwrap() = None;
        self
    }

    /// The next scripted reply; an exhausted script answers with nothing, which
    /// is what a worker with no model loaded does.
    fn next(&self) -> String {
        self.replies.lock().unwrap().pop().unwrap_or_default()
    }

    fn prompts(&self) -> Vec<Value> {
        self.seen.lock().unwrap().clone()
    }

    fn asked(&self) -> usize {
        self.seen.lock().unwrap().len()
    }

    fn rendered(&self) -> Vec<Value> {
        self.renders.lock().unwrap().clone()
    }
}

/// What the real seam answers with: `base64.b64encode(payload)`.
fn encode(bytes: &[u8]) -> String {
    base64::engine::general_purpose::STANDARD.encode(bytes)
}

async fn spawn_worker(script: Script) -> String {
    let models = json!({"loaded": ["demo"], "current": "demo"});
    let app = Router::new()
        .route(
            "/models",
            get(move || {
                let models = models.clone();
                async move { axum::Json(models) }
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
                    "vectors": vectors, "dim": model.dim(),
                    "provider": "hash", "model_id": model.model_id(),
                }))
            }),
        )
        .route(
            "/worker/extract",
            post(|Json(_body): Json<Value>| async move {
                axum::Json(json!({"concepts": [], "triples": [], "semantic": []}))
            }),
        )
        .route(
            "/worker/render/:kind",
            post({
                let script = script.clone();
                move |AxumPath(kind): AxumPath<String>, Json(body): Json<Value>| {
                    let script = script.clone();
                    async move {
                        let mut record = body.clone();
                        if let Some(object) = record.as_object_mut() {
                            object.insert("kind".into(), json!(kind));
                        }
                        script.renders.lock().unwrap().push(record);
                        let built = script.document.lock().unwrap().clone();
                        match built {
                            Some(bytes) => (
                                StatusCode::OK,
                                axum::Json(json!({
                                    "content_b64": encode(&bytes),
                                    "bytes": bytes.len(),
                                })),
                            ),
                            None => (
                                StatusCode::SERVICE_UNAVAILABLE,
                                axum::Json(json!({
                                    "detail": format!("this worker cannot render '{kind}'"),
                                })),
                            ),
                        }
                    }
                }
            }),
        )
        .route(
            "/worker/llm/stream",
            post({
                let script = script.clone();
                move |Json(body): Json<Value>| {
                    let script = script.clone();
                    async move {
                        script.seen.lock().unwrap().push(body);
                        if !script.delay.is_zero() {
                            tokio::time::sleep(script.delay).await;
                        }
                        let reply = script.next();
                        let mut frames = String::new();
                        if !reply.is_empty() {
                            frames.push_str(&format!("data: {}\n\n", json!({"text": reply})));
                        }
                        frames.push_str("data: [DONE]\n\n");
                        AxumResponse::builder()
                            .status(StatusCode::OK)
                            .header("content-type", "text/event-stream")
                            .body(axum::body::Body::from(frames))
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
    script: Script,
    agent_root: PathBuf,
    _data: tempfile::TempDir,
    _agent: tempfile::TempDir,
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

impl Install {
    async fn start(script: Script) -> Self {
        let data = tempfile::tempdir().expect("data");
        let agent = tempfile::tempdir().expect("agent");
        seed_users(data.path());

        let mut env = HashMap::new();
        env.insert("LATTICEAI_REQUIRE_AUTH".into(), "1".into());
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

        let worker_origin = spawn_worker(script.clone()).await;
        let graph_db = data.path().join("knowledge_graph.sqlite");
        let store = Arc::new(lattice_core::db::Store::open(&graph_db).expect("store"));
        let graph = lattice_core::graph_write::GraphWriter::open(
            store,
            data.path().join("knowledge_graph_blobs"),
        )
        .expect("graph writer");
        let state = ChatState::new(
            auth,
            ChatConfig {
                data_dir: data.path().to_path_buf(),
                graph_db: Some(graph_db),
                agent_root: agent.path().to_path_buf(),
                ..ChatConfig::default()
            },
        )
        .with_worker(ChatWorker::new(&worker_origin).expect("worker"))
        .with_graph(graph)
        .with_workspace(Arc::new(Personal));
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
        Self {
            origin: format!("http://{addr}"),
            token,
            script,
            agent_root: agent.path().to_path_buf(),
            _data: data,
            _agent: agent,
        }
    }

    fn client(&self) -> reqwest::Client {
        reqwest::Client::builder()
            .no_proxy()
            .timeout(Duration::from_secs(30))
            .build()
            .expect("client")
    }

    async fn chat(&self, body: Value) -> (u16, Value) {
        let response = self.send(body).await;
        let status = response.status().as_u16();
        let text = response.text().await.expect("text");
        (
            status,
            serde_json::from_str(&text).unwrap_or(Value::String(text)),
        )
    }

    async fn send(&self, body: Value) -> reqwest::Response {
        self.client()
            .post(format!("{}/chat", self.origin))
            .header("cookie", format!("session_token={}", self.token))
            .header("content-type", "application/json")
            .header("X-Lattice-Language", "ko")
            .body(serde_json::to_string(&body).expect("body"))
            .send()
            .await
            .expect("chat")
    }

    fn read(&self, relative: &str) -> String {
        std::fs::read_to_string(self.agent_root.join(relative)).expect("the file that was written")
    }

    fn exists(&self, relative: &str) -> bool {
        self.agent_root.join(relative).exists()
    }
}

const CLEAN_PAGE: &str = "<!doctype html>\n<html lang=\"ko\">\n<head><meta charset=\"utf-8\"><title>할 일</title></head>\n<body><h1>할 일</h1></body>\n</html>";
const TRUNCATED_PAGE: &str = "<!doctype html>\n<html lang=\"ko\">\n<head><meta charset=\"utf-8\"><title>할 일</title></head>\n<body><h1>할 일";
const REFUSAL: &str = "죄송하지만 파일을 만들 수 없습니다. 대신 방법을 알려드릴게요.";

fn ask(message: &str) -> Value {
    json!({"message": message, "conversation_id": "conv-1", "stream": false})
}

#[tokio::test]
async fn a_plain_html_request_writes_the_page_the_model_wrote() {
    let install = Install::start(Script::new(&[CLEAN_PAGE])).await;
    let (status, body) = install.chat(ask("HTML 파일 만들어줘")).await;

    assert_eq!(status, 200, "{body}");
    assert_eq!(body["created_files"][0]["path"], "generated_page.html");
    assert_eq!(
        install.read("generated_page.html"),
        CLEAN_PAGE,
        "content that validates is written byte for byte"
    );
    let artifact = &body["artifacts"][0];
    assert_eq!(artifact["valid"], true);
    assert_eq!(artifact["repaired"], false);
    assert_eq!(artifact["previewable"], true);
    assert_eq!(artifact["bytes"], CLEAN_PAGE.len());
    assert_eq!(body["generation"]["repaired"], false);
    assert_eq!(body["generation"]["attempts"][0]["outcome"], "clean");
    assert_eq!(body["action_route"], "chat_file_generation");
    assert_eq!(body["final_state"], "DONE");
    assert_eq!(body["brain_ingest"]["status"], "ok");
    assert!(
        body["response"]
            .as_str()
            .unwrap_or_default()
            .contains("generated_page.html 파일을 만들었습니다"),
        "{body}"
    );

    // One ask, in document mode, carrying the HTML anchor as the system prompt.
    assert_eq!(install.script.asked(), 1, "a clean reply is not retried");
    let prompt = &install.script.prompts()[0];
    assert_eq!(prompt["mode"], "document");
    assert_eq!(prompt["model_id"], "demo");
    let system = prompt["context"].as_str().unwrap_or_default();
    assert!(system.contains(lattice_chat::filegen::prompts::SYSTEM));
    assert!(system.contains(lattice_chat::filegen::prompts::HTML));
    assert!(prompt["message"]
        .as_str()
        .unwrap_or_default()
        .contains("HTML 파일 만들어줘"));
    assert!(
        prompt["max_tokens"].as_i64().unwrap_or_default()
            >= lattice_chat::filegen::author::MIN_TOKENS,
        "a document needs more room than a chat turn's 2048"
    );
}

#[tokio::test]
async fn a_2b_reply_wrapped_in_prose_and_fences_is_unwrapped_not_repaired() {
    let messy = format!(
        "네! 요청하신 페이지입니다:\n\n```html\n{CLEAN_PAGE}\n```\n\n더 필요하면 말씀해 주세요."
    );
    let install = Install::start(Script::new(&[&messy])).await;
    let (status, body) = install.chat(ask("HTML 파일 만들어줘")).await;

    assert_eq!(status, 200, "{body}");
    assert_eq!(
        install.read("generated_page.html"),
        CLEAN_PAGE,
        "the model's own document, with the chat framing removed"
    );
    assert_eq!(body["artifacts"][0]["valid"], true);
    assert_eq!(
        body["artifacts"][0]["repaired"], false,
        "unwrapping is not repairing: nothing was invented, so the badge stays off"
    );
    assert_eq!(body["generation"]["repaired"], false);
    assert_eq!(body["generation"]["attempts"][0]["outcome"], "sanitized");
    assert_eq!(
        install.script.asked(),
        1,
        "a rescued document is the model's work; asking again would waste a minute"
    );
}

#[tokio::test]
async fn a_truncated_page_is_repaired_and_the_badge_says_so() {
    // Both attempts stop mid-tag — the shape a small model produces when the
    // token budget runs out, twice.
    let install = Install::start(Script::new(&[TRUNCATED_PAGE, TRUNCATED_PAGE])).await;
    let (status, body) = install.chat(ask("HTML 파일 만들어줘")).await;

    assert_eq!(status, 200, "{body}");
    let written = install.read("generated_page.html");
    assert!(written.contains("</html>"), "repair finished the document");
    assert!(written.contains("할 일"), "the model's own text survived");
    assert_eq!(body["artifacts"][0]["repaired"], true);
    assert_eq!(
        body["artifacts"][0]["valid"], true,
        "a repaired document is structurally valid — that is what repair is for"
    );
    // The badge the SPA renders (`agentPayloadFiles` reads `generation.repaired`).
    assert_eq!(body["generation"]["repaired"], true);
    let attempts = body["generation"]["attempts"].as_array().expect("attempts");
    assert_eq!(attempts.len(), 2, "one retry, and only one");
    assert_eq!(attempts[0]["outcome"], "repaired");
    assert_eq!(attempts[0]["path"], "generated_page.html");

    // The retry names what was wrong with the first answer.
    assert_eq!(install.script.asked(), 2);
    let retry = install.script.prompts()[1]["context"]
        .as_str()
        .unwrap_or_default()
        .to_string();
    assert!(retry.contains("truncated"), "{retry}");
    assert!(retry.contains("Your previous answer could not be used"));
}

#[tokio::test]
async fn a_refusal_is_retried_once_and_then_refused_honestly() {
    let install = Install::start(Script::new(&[REFUSAL, REFUSAL])).await;
    let (status, body) = install.chat(ask("HTML 파일 만들어줘")).await;

    assert_eq!(status, 400, "{body}");
    assert_eq!(body["detail"], "파일 내용을 만들지 못했습니다.");
    assert!(
        !install.exists("generated_page.html"),
        "a refusal must never become a scaffold on disk — `repair_file_content` \
         would happily have written one"
    );
    let attempts = body["attempts"].as_array().expect("the honest trail");
    assert_eq!(attempts.len(), 2);
    assert_eq!(attempts[0]["outcome"], "unusable");
    assert_eq!(install.script.asked(), 2, "asked twice, then stopped");
}

#[tokio::test]
async fn the_retry_is_what_gets_written_when_the_first_answer_was_not_a_file() {
    let install = Install::start(Script::new(&[REFUSAL, CLEAN_PAGE])).await;
    let (status, body) = install.chat(ask("HTML 파일 만들어줘")).await;

    assert_eq!(status, 200, "{body}");
    assert_eq!(install.read("generated_page.html"), CLEAN_PAGE);
    assert_eq!(body["generation"]["repaired"], false);
    let attempts = body["generation"]["attempts"].as_array().expect("attempts");
    assert_eq!(attempts[0]["outcome"], "unusable");
    assert_eq!(attempts[1]["outcome"], "clean");
}

#[tokio::test]
async fn a_multi_file_project_writes_three_and_names_what_it_deferred() {
    // The React manifest declares five files; this surface writes three.
    let package = "{\n  \"name\": \"todo-app\",\n  \"private\": true\n}";
    let entry = "import { createRoot } from \"react-dom/client\";\nimport App from \"./App.jsx\";\ncreateRoot(document.getElementById(\"root\")).render(<App />);";
    let install = Install::start(Script::new(&[package, CLEAN_PAGE, entry])).await;
    let (status, body) = install.chat(ask("React 로 todo 웹페이지 만들어줘")).await;

    assert_eq!(status, 200, "{body}");
    let created: Vec<&str> = body["created_files"]
        .as_array()
        .expect("files")
        .iter()
        .filter_map(|file| file["path"].as_str())
        .collect();
    assert_eq!(created, ["package.json", "index.html", "src/main.jsx"]);
    assert_eq!(install.read("package.json"), package);
    assert_eq!(install.read("src/main.jsx"), entry);
    assert_eq!(
        install.script.asked(),
        3,
        "three files, three asks — never in parallel, never a fourth"
    );
    let answer = body["response"].as_str().unwrap_or_default();
    assert!(
        answer.contains("src/App.jsx"),
        "the deferred files are named: {answer}"
    );
    assert!(answer.contains("src/App.css"), "{answer}");
    assert!(answer.contains("이어서 요청해 주세요"), "{answer}");
    assert_eq!(body["final_state"], "DONE", "nothing failed, only deferred");
    assert_eq!(body["brain_ingest"].as_array().map(Vec::len), Some(3));
    assert_eq!(body["brain_ingest"][1]["path"], "index.html");
}

#[tokio::test]
async fn a_project_file_the_model_refuses_is_reported_not_hidden() {
    let install = Install::start(Script::new(&[
        CLEAN_PAGE, // index.html
        "body { color: rebeccapurple; }",
        REFUSAL, // app.js — twice, because the refusal is retried
        REFUSAL,
    ]))
    .await;
    let (status, body) = install
        .chat(ask("html css js 로 간단한 웹페이지 만들어줘"))
        .await;

    assert_eq!(status, 200, "{body}");
    assert!(install.exists("index.html") && install.exists("style.css"));
    assert!(!install.exists("app.js"), "nothing was invented for it");
    assert_eq!(
        body["final_state"], "NEEDS_REVIEW",
        "a missing file the user asked for must not render as a clean success"
    );
    assert!(body["response"]
        .as_str()
        .unwrap_or_default()
        .contains("app.js"));
}

#[tokio::test]
async fn the_inline_path_writes_the_users_bytes_and_reports_their_verdict() {
    let install = Install::start(Script::new(&[])).await;
    let (status, body) = install
        .chat(ask("fixture-note.md 파일 만들어줘. 내용: Hello Lattice"))
        .await;

    assert_eq!(status, 200, "{body}");
    assert_eq!(install.read("fixture-note.md"), "Hello Lattice");
    assert_eq!(body["artifacts"][0]["valid"], true);
    assert_eq!(body["artifacts"][0]["repaired"], false);
    assert_eq!(body["action_route"], "direct_write_file");
    assert!(
        body.get("generation").is_none(),
        "nothing was generated, so the reply claims no generation"
    );
    assert_eq!(install.script.asked(), 0, "the model was never consulted");

    // …and content the validator would refuse is still the user's to write.
    let (status, body) = install
        .chat(ask("page.html 파일 만들어줘. 내용: 나중에 채울게요"))
        .await;
    assert_eq!(status, 200, "{body}");
    assert_eq!(install.read("page.html"), "나중에 채울게요");
    assert_eq!(
        body["artifacts"][0]["valid"], false,
        "the artifact reports the verdict instead of asserting `true`"
    );
    assert_eq!(body["artifacts"][0]["repaired"], false);
}

#[tokio::test]
async fn a_docx_is_a_real_render_and_never_prose_under_a_docx_name() {
    let install = Install::start(Script::new(&[])).await;
    let (status, body) = install
        .chat(ask(
            "fixture-report.docx 파일 만들어줘. 내용: 리트리벌 가중치 정리",
        ))
        .await;

    assert_eq!(status, 200, "{body}");
    let bytes = std::fs::read(install.agent_root.join("fixture-report.docx")).expect("document");
    assert_eq!(
        bytes, b"PK\x03\x04 a real enough document",
        "the bytes are the builder's, not the prose"
    );
    assert_ne!(
        bytes,
        "리트리벌 가중치 정리".as_bytes(),
        "v11.7.0 wrote exactly this and called it a Word document"
    );
    let rendered = install.script.rendered();
    assert_eq!(rendered.len(), 1);
    assert_eq!(rendered[0]["kind"], "docx");
    assert_eq!(rendered[0]["body"], "리트리벌 가중치 정리");
    assert_eq!(
        rendered[0]["title"], "",
        "a heading nobody asked for is content this surface invented"
    );
    assert_eq!(body["created_files"][0]["action"], "create_docx");
    assert_eq!(body["artifacts"][0]["previewable"], false);
    assert_eq!(body["artifacts"][0]["valid"], true);
    assert_eq!(body["artifacts"][0]["bytes"], bytes.len());
    // The Brain still remembers the document's words, not its bytes.
    assert_eq!(body["brain_ingest"]["status"], "ok");
}

#[tokio::test]
async fn a_generated_pdf_is_typeset_from_the_model_authored_text() {
    let install = Install::start(Script::new(&["첫 문단.\n\n둘째 문단."])).await;
    let (status, body) = install.chat(ask("report.pdf 파일 만들어줘")).await;

    assert_eq!(status, 200, "{body}");
    let rendered = install.script.rendered();
    assert_eq!(rendered[0]["kind"], "pdf");
    assert_eq!(rendered[0]["body"], "첫 문단.\n\n둘째 문단.");
    assert_eq!(body["created_files"][0]["action"], "create_pdf");
    assert_eq!(install.script.asked(), 1);
    let system = install.script.prompts()[0]["context"]
        .as_str()
        .unwrap_or_default()
        .to_string();
    assert!(
        system.contains(lattice_chat::filegen::prompts::DOCUMENT),
        "{system}"
    );
}

#[tokio::test]
async fn a_worker_that_cannot_build_documents_refuses_instead_of_faking_one() {
    let install = Install::start(Script::new(&[]).without_a_document_builder()).await;
    let (status, body) = install
        .chat(ask("report.docx 파일 만들어줘. 내용: 분기 요약"))
        .await;

    assert_eq!(status, 400, "{body}");
    assert_eq!(body["error"], "document_render_failed");
    assert!(
        body["detail"]
            .as_str()
            .unwrap_or_default()
            .contains("cannot render"),
        "the worker's own reason reaches the user: {body}"
    );
    assert!(
        !install.exists("report.docx"),
        "a failed render writes nothing at all"
    );
}

#[tokio::test]
async fn a_spreadsheet_names_the_agent_rather_than_guessing_at_rows() {
    let install = Install::start(Script::new(&[])).await;
    let (status, body) = install
        .chat(ask("sales.xlsx 파일 만들어줘. 내용: 1월 100, 2월 200"))
        .await;

    assert_eq!(status, 400, "{body}");
    assert_eq!(body["error"], "office_format_unsupported");
    assert_eq!(body["action"], "use_agent");
    let detail = body["detail"].as_str().unwrap_or_default();
    assert!(
        detail.contains("sales.xlsx") && detail.contains("에이전트"),
        "{detail}"
    );
    assert!(!install.exists("sales.xlsx"));
    assert!(
        install.script.rendered().is_empty(),
        "nothing was even attempted"
    );

    // …and asking for it as a stream is still a real 400, not a 200 whose body
    // holds an error frame: the format is refused from the filename, before
    // there is any stream to put a frame in.
    let response = install
        .send(json!({"message": "deck.pptx 파일 만들어줘", "stream": true}))
        .await;
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    assert!(!response
        .headers()
        .get("content-type")
        .and_then(|value| value.to_str().ok())
        .unwrap_or_default()
        .starts_with("text/event-stream"));
    let body: Value =
        serde_json::from_str(&response.text().await.expect("text")).expect("a JSON refusal");
    assert_eq!(body["error"], "office_format_unsupported");
}

#[tokio::test]
async fn a_streaming_generation_announces_the_file_before_the_model_answers() {
    let install =
        Install::start(Script::new(&[CLEAN_PAGE]).slow(Duration::from_millis(1_500))).await;
    let mut response = install
        .send(json!({"message": "HTML 파일 만들어줘", "stream": true}))
        .await;
    assert_eq!(response.status(), StatusCode::OK);
    assert!(response
        .headers()
        .get("content-type")
        .and_then(|value| value.to_str().ok())
        .unwrap_or_default()
        .starts_with("text/event-stream"));
    assert_eq!(response.headers()["x-model"], "demo");

    // The progress frame is live, not a summary written after the work: it
    // arrives while the scripted model is still holding its answer.
    let first = tokio::time::timeout(Duration::from_millis(1_000), response.chunk())
        .await
        .expect("the progress frame must not wait for the model")
        .expect("chunk")
        .expect("some bytes");
    let first = String::from_utf8(first.to_vec()).expect("utf8");
    assert_eq!(
        first,
        "data: {\"type\":\"file_generation\",\"path\":\"generated_page.html\",\"status\":\"generating\",\"chunk\":\"\"}\n\n"
    );

    let rest = response.text().await.expect("body");
    assert!(rest.contains("\"status\":\"written\""), "{rest}");
    assert!(rest.contains("\"action_route\":\"chat_file_generation\""));
    assert!(rest.contains("\"repaired\":false"));
    assert!(rest.trim_end().ends_with("data: [DONE]"), "{rest}");
    assert_eq!(install.read("generated_page.html"), CLEAN_PAGE);

    // Every frame in the whole stream carries a `chunk`, progress frames
    // included. The VS Code extension's reader is `chunks.push(parsed.chunk)`
    // and its caller does `accumulated += chunk`, so a frame without the key
    // would print the literal "undefined" into that editor's answer.
    let stream = format!("{first}{rest}");
    let frames: Vec<&str> = stream
        .split("\n\n")
        .filter_map(|frame| frame.trim().strip_prefix("data: "))
        .filter(|payload| *payload != "[DONE]")
        .collect();
    assert_eq!(
        frames.len(),
        4,
        "progress ×2, then the payload's two: {stream}"
    );
    for payload in frames {
        let parsed: Value = serde_json::from_str(payload).expect("every frame is JSON");
        assert!(
            parsed.get("chunk").and_then(Value::as_str).is_some(),
            "frame without a chunk key: {payload}"
        );
    }
}

#[tokio::test]
async fn a_streaming_refusal_is_still_a_stream_that_ends_with_the_sentinel() {
    let install = Install::start(Script::new(&[REFUSAL, REFUSAL])).await;
    let response = install
        .send(json!({"message": "HTML 파일 만들어줘", "stream": true}))
        .await;
    assert_eq!(
        response.status(),
        StatusCode::OK,
        "the stream was already committed when the model refused"
    );
    let body = response.text().await.expect("body");
    assert!(body.contains("\"status\":\"generating\""));
    assert!(body.contains("\"status\":\"failed\""));
    assert!(body.contains("\"error\":\"file_generation_failed\""));
    assert!(body.trim_end().ends_with("data: [DONE]"), "{body}");
    assert!(!install.exists("generated_page.html"));
}

#[tokio::test]
async fn the_never_overwrite_rule_still_holds_for_a_generated_file() {
    let install = Install::start(Script::new(&[CLEAN_PAGE, CLEAN_PAGE])).await;
    let (status, _) = install.chat(ask("HTML 파일 만들어줘")).await;
    assert_eq!(status, 200);
    let (status, body) = install.chat(ask("HTML 파일 만들어줘")).await;
    assert_eq!(status, 200, "{body}");
    assert_eq!(body["created_files"][0]["path"], "generated_page_2.html");
    assert!(body["response"]
        .as_str()
        .unwrap_or_default()
        .contains("새 이름으로 저장했습니다"));
    assert!(install.exists("generated_page.html") && install.exists("generated_page_2.html"));
}
