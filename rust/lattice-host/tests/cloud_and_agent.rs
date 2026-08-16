//! Host wiring for the cloud sinks (G1) and the agent loop (N1).
//!
//! The scripted worker never dials a real model API. `/agent/llm` returns
//! canned JSON actions; file writes run natively in the agent workspace.

mod common;

use std::path::PathBuf;
use std::sync::Arc;

use axum::extract::Json;
use axum::routing::{get, post};
use axum::Router;
use common::{client, FakeWorker, FixedProvider, TestGateway};
use lattice_core::db::RuntimeConfig;
use lattice_host::gateway::onedoor::OneDoorState;
use lattice_host::gateway::posture::Posture;
use lattice_host::gateway::GatewayState;
use serde_json::{json, Value};

struct ScriptedLlm {
    completions: std::sync::Mutex<std::collections::VecDeque<String>>,
}

fn plan_with(steps: Value) -> String {
    json!({"action": "plan", "goal": "the goal", "steps": steps, "estimated_steps": 1}).to_string()
}

const WRITE: &str =
    r#"{"action": "write_file", "args": {"path": "note.md", "content": "hello from the loop\n"}}"#;
const FINAL: &str = r#"{"action": "final", "message": "완료했습니다."}"#;
const PASS: &str = r#"{"action": "verdict", "verdict": "PASS", "next_state": "DONE",
                       "reason": "ok", "corrections": []}"#;

async fn spawn_scripted_worker() -> (String, PathBuf) {
    let scratch = PathBuf::from(env!("CARGO_TARGET_TMPDIR"))
        .join("cloud_and_agent")
        .join("worker");
    let _ = std::fs::remove_dir_all(&scratch);
    std::fs::create_dir_all(&scratch).expect("scratch");
    let llm = Arc::new(ScriptedLlm {
        completions: std::sync::Mutex::new(
            [
                plan_with(json!([{"action": "write_file", "args": {"path": "note.md"}}])),
                WRITE.to_string(),
                FINAL.to_string(),
                PASS.to_string(),
            ]
            .into_iter()
            .collect(),
        ),
    });
    let app = Router::new()
        .route(
            "/models",
            get(|| async {
                axum::Json(json!({
                    "loaded": ["scripted"],
                    "current": "scripted",
                }))
            }),
        )
        .route(
            "/health",
            get(|| async { axum::Json(json!({"status": "ok"})) }),
        )
        .route(
            "/agent/llm",
            post({
                let llm = Arc::clone(&llm);
                move |Json(_body): Json<Value>| {
                    let llm = Arc::clone(&llm);
                    async move {
                        let text = llm
                            .completions
                            .lock()
                            .expect("lock")
                            .pop_front()
                            .unwrap_or_default();
                        axum::Json(json!({"text": text}))
                    }
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
    (format!("http://{addr}"), scratch)
}

async fn spawn_mock_openai() -> String {
    let app = Router::new().route(
        "/chat/completions",
        post(|| async {
            let body = format!(
                "data: {}\n\ndata: [DONE]\n\n",
                json!({"choices":[{"delta":{"content":"서울"}}]})
            );
            axum::response::Response::builder()
                .status(200)
                .header("content-type", "text/event-stream")
                .body(axum::body::Body::from(body))
                .unwrap()
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

async fn product_gateway(
    worker_origin: &str,
    worker_port: u16,
    name: &str,
    cloud_base: Option<&str>,
) -> (TestGateway, PathBuf) {
    let scratch = PathBuf::from(env!("CARGO_TARGET_TMPDIR"))
        .join("cloud_and_agent")
        .join(name);
    let _ = std::fs::remove_dir_all(&scratch);
    let data_dir = scratch.join("data");
    let agent_root = scratch.join("agent_workspace");
    let brain_dir = scratch.join("brain");
    for dir in [&data_dir, &agent_root, &brain_dir] {
        std::fs::create_dir_all(dir).expect("scratch");
    }
    std::env::set_var("LATTICEAI_BRAIN_DIR", &brain_dir);
    std::fs::write(
        data_dir.join("network_boundary.json"),
        json!({"default": "cloud_allowed"}).to_string(),
    )
    .ok();
    if let Some(base) = cloud_base {
        std::fs::write(
            data_dir.join("cloud_provider.json"),
            json!({
                "mode": "api_key",
                "provider": "openai_compatible",
                "model": "mock-model",
                "base_url": base,
                "api_key": "test-key",
            })
            .to_string(),
        )
        .unwrap();
    }
    let config = RuntimeConfig::resolve(
        Some(&data_dir.to_string_lossy()),
        None,
        Some(worker_origin),
        None,
    );
    let loop_config = lattice_agent::LoopConfig {
        worker_origin: worker_origin.to_string(),
        runs_dir: scratch.join("rust_agent_runs"),
        client: Some(client()),
        proposals: Some(Arc::new(lattice_agent::proposals::JsonProposalStore::new(
            scratch.join("proposals"),
        ))),
        hooks: None,
    };
    let product =
        OneDoorState::open_with_config(config, worker_origin, client(), &agent_root, loop_config)
            .expect("product");
    let state = GatewayState::new(Arc::new(FixedProvider::new(
        worker_origin.to_string(),
        worker_port,
    )))
    .expect("gateway")
    .with_db_path(data_dir.join("knowledge_graph.sqlite"))
    .with_agent_root(&agent_root)
    .with_agent_runs_dir(scratch.join("rust_agent_runs"))
    .with_pinned_posture(Posture::Open)
    .with_product(Arc::new(product));
    (TestGateway::start_with_state(state).await, scratch)
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
async fn a_cloud_turn_stages_a_review_item_and_writes_egress() {
    let mock = spawn_mock_openai().await;
    let worker = FakeWorker::start().await;
    let (gateway, scratch) =
        product_gateway(&worker.origin(), worker.port(), "g1", Some(&mock)).await;
    let response = client()
        .post(gateway.url("/chat"))
        .header("content-type", "application/json")
        .body(json!({"message":"수도는?","stream":true}).to_string())
        .send()
        .await
        .expect("chat");
    assert_eq!(response.status(), 200);
    let body = response.text().await.unwrap();
    let frames = sse_frames(&body);
    let done = frames
        .iter()
        .find(|frame| frame["type"] == "hybrid_done")
        .expect("hybrid_done");
    assert_eq!(done["provider"], "openai_compatible");
    assert_eq!(done["kg_expansion"]["status"], "queued_for_review");

    let data_dir = scratch.join("data");
    let os = std::fs::read_to_string(data_dir.join("workspace_os.json")).unwrap_or_default();
    assert!(
        os.contains("kg_cloud_expansion"),
        "review queue must hold the cloud expansion: {os}"
    );
    let audit = std::fs::read_to_string(data_dir.join("audit_log.json")).unwrap_or_default();
    assert!(
        audit.contains("cloud_egress"),
        "egress audit must be written: {audit}"
    );
    gateway.stop().await;
    worker.shutdown();
}

#[tokio::test]
async fn post_agent_writes_a_file_end_to_end() {
    let (origin, _scratch) = spawn_scripted_worker().await;
    let port = origin
        .rsplit(':')
        .next()
        .and_then(|p| p.parse().ok())
        .unwrap_or(0);
    let (gateway, scratch) = product_gateway(&origin, port, "n1", None).await;
    let response = client()
        .post(gateway.url("/agent"))
        .header("content-type", "application/json")
        .body(
            json!({
                "message": "write note.md",
                "permission_mode": "trusted",
            })
            .to_string(),
        )
        .send()
        .await
        .expect("agent");
    let status = response.status();
    let body = response.text().await.unwrap();
    assert_eq!(status, 200, "{body}");
    let payload: Value = serde_json::from_str(&body).unwrap_or(json!({}));
    assert_eq!(payload["status"], "ok", "{payload}");
    let note = scratch.join("agent_workspace").join("note.md");
    let written = std::fs::read_to_string(&note).unwrap_or_default();
    assert_eq!(written, "hello from the loop\n");
    gateway.stop().await;
}
