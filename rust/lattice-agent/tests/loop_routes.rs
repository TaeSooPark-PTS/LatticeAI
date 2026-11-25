mod common;

use common::start_replay_worker;
use lattice_agent::kernel::runs::{AgentRunStore, PausedRun};
use lattice_agent::kernel::state::AgentRunContext;
use lattice_agent::surface::looproutes::{loop_router, LoopConfig};
use serde_json::{json, Value};
use std::path::PathBuf;

struct Harness {
    origin: String,
    runs_dir: PathBuf,
    worker: std::sync::Arc<common::ReplayWorker>,
    client: reqwest::Client,
}

impl Harness {
    async fn post(&self, path: &str, body: Value) -> reqwest::Response {
        self.client
            .post(format!("{}{path}", self.origin))
            .header("content-type", "application/json")
            .body(serde_json::to_vec(&body).expect("body"))
            .send()
            .await
            .expect("request")
    }

    async fn get(&self, path: &str) -> Value {
        let response = self
            .client
            .get(format!("{}{path}", self.origin))
            .send()
            .await
            .expect("request");
        json_of(response).await
    }

    fn store(&self) -> AgentRunStore {
        AgentRunStore::new(self.runs_dir.clone())
    }
}

async fn json_of(response: reqwest::Response) -> Value {
    let bytes = response.bytes().await.expect("body");
    serde_json::from_slice(&bytes).unwrap_or(Value::Null)
}

fn scratch(name: &str) -> PathBuf {
    let dir = PathBuf::from(env!("CARGO_TARGET_TMPDIR"))
        .join("loop_routes")
        .join(name);
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).expect("scratch");
    dir
}

async fn harness(name: &str, completions: &[&str]) -> Harness {
    let dir = scratch(name);
    let workspace = lattice_agent::tools::sandbox::Workspace::new(dir.join("agent_workspace"))
        .expect("workspace");
    let server = start_replay_worker(workspace.root()).await;
    server.worker.push_completions(
        &completions
            .iter()
            .map(|text| (*text).to_string())
            .collect::<Vec<_>>(),
    );
    let runs_dir = dir.join("rust_agent_runs");
    let router = loop_router(
        workspace,
        LoopConfig {
            worker_origin: server.origin.clone(),
            runs_dir: runs_dir.clone(),
            client: None,
            proposals: Some(std::sync::Arc::new(
                lattice_agent::kernel::proposals::JsonProposalStore::new(dir.join("data")),
            )),
            hooks: None,
            external: None,
        },
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind");
    let origin = format!("http://{}", listener.local_addr().expect("addr"));
    tokio::spawn(async move {
        let _ = axum::serve(listener, router).await;
    });
    Harness {
        origin,
        runs_dir,
        worker: server.worker,
        client: reqwest::Client::new(),
    }
}

fn plan_with(steps: Value) -> String {
    json!({"action": "plan", "goal": "the goal", "steps": steps, "estimated_steps": 1}).to_string()
}

const WRITE: &str = r#"{"action": "write_file", "args": {"path": "note.md", "content": "hi\n"}}"#;
const FINAL: &str = r#"{"action": "final", "message": "완료했습니다."}"#;
const PASS: &str = r#"{"action": "verdict", "verdict": "PASS", "next_state": "DONE",
                       "reason": "ok", "corrections": []}"#;

fn run_body() -> Value {
    json!({
        "message": "make a note",
        "user_email": "owner@example.com",
        "permission_mode": "trusted",
        "policies": {"tools": {"write_file": {"risk": "write", "sandbox": "workspace"}}},
    })
}

#[tokio::test]
async fn a_clean_run_answers_the_terminal_payload_python_answers() {
    let harness = harness(
        "clean",
        &[
            &plan_with(json!([{"action": "write_file", "args": {"path": "note.md"}}])),
            WRITE,
            FINAL,
            PASS,
        ],
    )
    .await;
    harness.worker.load_tool_calls(&[
        json!({"tool": "write_file", "result": {"path": "note.md", "bytes": 3}}),
    ]);

    let response = harness.post("/rust/agent/run", run_body()).await;
    assert_eq!(response.status(), 200);
    let payload = json_of(response).await;
    assert_eq!(payload["status"], "ok");
    assert_eq!(payload["final_state"], "DONE");
    assert_eq!(payload["response"], "완료했습니다.");
    assert_eq!(
        payload["state_history"],
        json!(["PLANNING", "EXECUTING", "VERIFYING", "DONE"])
    );
    assert_eq!(payload["created_files"][0]["filename"], "note.md");
    assert_eq!(payload["artifacts"][0]["previewable"], true);
    assert_eq!(payload["loop"]["llm_calls"], 4);
    assert!(payload["workspace"]
        .as_str()
        .expect("workspace")
        .ends_with("agent_workspace"));
    assert!(payload["steps"].as_array().expect("steps").len() >= 4);
    assert!(payload["audit"].is_array(), "{payload}");
}

#[tokio::test]
async fn a_run_that_needs_approval_pauses_without_executing_anything() {
    let harness = harness(
        "pause",
        &[&plan_with(
            json!([{"action": "run_command", "args": {"command": "ls"}, "description": "list"}]),
        )],
    )
    .await;
    let mut body = run_body();
    body["permission_mode"] = json!("strict");
    body["policies"]["tools"]["run_command"] = json!({"risk": "exec", "shell": true});

    let payload = json_of(harness.post("/rust/agent/run", body).await).await;
    assert_eq!(payload["status"], "awaiting_approval");
    assert_eq!(payload["final_state"], "WAITING_APPROVAL");
    assert_eq!(payload["non_auto_steps"], json!(["run_command"]));
    assert_eq!(payload["approval"]["plan_summary"], "the goal\n1. list");
    let token = payload["approval"]["token"].as_str().expect("token");
    assert_eq!(token.len(), 43, "32 random bytes, base64url, unpadded");
    assert!(payload["approval"]["expires_at"]
        .as_str()
        .expect("expires_at")
        .ends_with("+00:00"));
    let run_id = payload["run_id"].as_str().expect("run_id").to_string();
    assert_eq!(run_id.len(), 22);
    assert_eq!(harness.worker.observed_calls().len(), 0, "nothing ran");

    let pending = harness.get("/rust/agent/approvals").await;
    assert_eq!(pending["pending"][0]["run_id"], run_id.as_str());
    assert_eq!(pending["pending"][0]["goal"], "the goal");
    let scoped = harness
        .get("/rust/agent/approvals?user=someone@else.com")
        .await;
    assert_eq!(scoped["pending"], json!([]), "listing is per user");

    let refused = harness
        .post(
            "/rust/agent/resume",
            json!({"run_id": run_id, "approval_token": "wrong",
                   "user_email": "owner@example.com"}),
        )
        .await;
    assert_eq!(refused.status(), 403);
    assert!(harness.store().load(&run_id).is_some(), "still pending");

    let stolen = harness
        .post(
            "/rust/agent/resume",
            json!({"run_id": run_id, "approval_token": token,
                   "user_email": "someone@else.com"}),
        )
        .await;
    assert_eq!(stolen.status(), 403);

    let cancelled = json_of(
        harness
            .post(
                "/rust/agent/resume",
                json!({"run_id": run_id, "approval_token": token, "approve": false,
                       "user_email": "owner@example.com"}),
            )
            .await,
    )
    .await;
    assert_eq!(cancelled["status"], "cancelled");
    assert_eq!(cancelled["run_id"], run_id.as_str());
    assert!(
        harness.store().load(&run_id).is_none(),
        "consumed either way"
    );

    let gone = harness
        .post(
            "/rust/agent/resume",
            json!({"run_id": run_id, "approval_token": token,
                   "user_email": "owner@example.com"}),
        )
        .await;
    assert_eq!(gone.status(), 404);
}

#[tokio::test]
async fn an_approved_resume_carries_the_paused_plan_into_execution() {
    let harness = harness(
        "resume",
        &[
            &plan_with(
                json!([{"action": "run_command", "args": {"command": "ls"}, "description": "list"}]),
            ),
            FINAL,
            PASS,
        ],
    )
    .await;
    let mut body = run_body();
    body["permission_mode"] = json!("strict");
    body["policies"]["tools"]["run_command"] = json!({"risk": "exec", "shell": true});
    let paused = json_of(harness.post("/rust/agent/run", body).await).await;
    let run_id = paused["run_id"].as_str().expect("run_id").to_string();
    let token = paused["approval"]["token"]
        .as_str()
        .expect("token")
        .to_string();

    let finished = json_of(
        harness
            .post(
                "/rust/agent/resume",
                json!({"run_id": run_id, "approval_token": token,
                       "user_email": "owner@example.com",
                       "modified_plan": {"goal": "edited", "steps": [
                           {"action": "run_command", "args": {"command": "ls"}}]}}),
            )
            .await,
    )
    .await;
    assert_eq!(
        finished["final_state"], "NEEDS_REVIEW",
        "a final with no tool ran"
    );
    let steps = finished["steps"].as_array().expect("steps");
    assert!(steps.iter().any(|step| step["edited_plan"] == json!(true)));
    assert!(steps
        .iter()
        .any(|step| step["decision"] == json!("human_approved")));
    assert_eq!(
        finished["state_history"],
        json!([
            "PLANNING",
            "WAITING_APPROVAL",
            "EXECUTING",
            "VERIFYING",
            "NEEDS_REVIEW"
        ])
    );
}

#[tokio::test]
async fn an_expired_approval_is_a_410_with_a_replan_hint() {
    let harness = harness("expired", &[]).await;
    let store = harness.store();
    let ctx = AgentRunContext::new();
    assert!(store.save(
        &PausedRun {
            run_id: "expired-run-id",
            user: "owner@example.com",
            language_hint: "Korean",
            token: "the-token",
            expires_epoch: lattice_agent::kernel::trace::epoch_now() - 60.0,
            expires_at: "2026-08-11T00:00:00+00:00",
            legacy_context: false,
            request: json!({"message": "make a note"}),
        },
        &ctx,
    ));

    let response = harness
        .post(
            "/rust/agent/resume",
            json!({"run_id": "expired-run-id", "approval_token": "the-token",
                   "user_email": "owner@example.com"}),
        )
        .await;
    assert_eq!(response.status(), 410);
    let payload = json_of(response).await;
    assert_eq!(payload["detail"]["error"], "approval_expired");
    assert_eq!(payload["detail"]["replan"]["message"], "make a note");
    assert!(
        harness.store().load("expired-run-id").is_none(),
        "swept on the way out"
    );
    assert_eq!(
        harness.get("/rust/agent/approvals").await["pending"],
        json!([])
    );
}

#[tokio::test]
async fn a_streaming_run_emits_named_step_frames_then_the_terminal_payload() {
    let harness = harness(
        "stream",
        &[
            &plan_with(json!([{"action": "write_file", "args": {"path": "note.md"}}])),
            WRITE,
            FINAL,
            PASS,
        ],
    )
    .await;
    harness.worker.load_tool_calls(&[
        json!({"tool": "write_file", "result": {"path": "note.md", "bytes": 3}}),
    ]);
    let mut body = run_body();
    body["stream"] = json!(true);

    let response = harness.post("/rust/agent/run", body).await;
    assert_eq!(response.status(), 200);
    assert_eq!(
        response
            .headers()
            .get("content-type")
            .and_then(|value| value.to_str().ok()),
        Some("text/event-stream")
    );
    let text = response.text().await.expect("stream body");

    let step_frames: Vec<&str> = text
        .split("\n\n")
        .filter(|frame| frame.starts_with("event: agent_step"))
        .collect();
    assert!(
        step_frames.len() >= 4,
        "one frame per observed step: {text}"
    );
    assert!(
        step_frames[0].contains("\"phase\":\"plan\""),
        "{}",
        step_frames[0]
    );
    assert!(
        text.contains("\"event\":\"tool\""),
        "the tool step is streamed"
    );
    assert!(text.contains("\"phase\":\"terminal\""));
    assert!(
        text.ends_with("data: [DONE]\n\n"),
        "the historical terminator"
    );

    let trailer: Value = text
        .split("\n\n")
        .filter_map(|frame| frame.strip_prefix("data: "))
        .filter(|frame| frame.starts_with('{'))
        .last()
        .and_then(|frame| serde_json::from_str(frame).ok())
        .expect("a terminal data frame");
    assert_eq!(trailer["chunk"], "");
    assert_eq!(trailer["agent"]["final_state"], "DONE");
    assert_eq!(trailer["agent"]["status"], "ok");
}

#[tokio::test]
async fn a_run_without_a_message_or_a_reachable_worker_is_refused_honestly() {
    let harness = harness("refusals", &[]).await;
    for body in [json!({"message": "   "}), json!({"message": ""})] {
        let response = harness.post("/rust/agent/run", body).await;
        assert_eq!(response.status(), 400);
    }
    let malformed = harness
        .post("/rust/agent/run", json!({"max_steps": "many"}))
        .await;
    assert_eq!(malformed.status(), 400);

    let response = harness.post("/rust/agent/resume", json!({})).await;
    assert_eq!(response.status(), 404);
}

#[tokio::test]
async fn an_unreachable_worker_ends_the_run_as_a_bad_gateway() {
    let dir = scratch("unreachable");
    let workspace = lattice_agent::tools::sandbox::Workspace::new(dir.join("agent_workspace"))
        .expect("workspace");
    let router = loop_router(
        workspace,
        LoopConfig {
            worker_origin: "http://127.0.0.1:1".into(),
            runs_dir: dir.join("rust_agent_runs"),
            client: None,
            proposals: Some(std::sync::Arc::new(
                lattice_agent::kernel::proposals::JsonProposalStore::new(dir.join("data")),
            )),
            hooks: None,
            external: None,
        },
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind");
    let origin = format!("http://{}", listener.local_addr().expect("addr"));
    tokio::spawn(async move {
        let _ = axum::serve(listener, router).await;
    });
    let response = reqwest::Client::new()
        .post(format!("{origin}/rust/agent/run"))
        .header("content-type", "application/json")
        .body(r#"{"message": "make a note"}"#)
        .send()
        .await
        .expect("request");
    assert_eq!(response.status(), 502);
    let payload = json_of(response).await;
    assert!(payload["detail"]
        .as_str()
        .expect("detail")
        .contains("unreachable"));
}
