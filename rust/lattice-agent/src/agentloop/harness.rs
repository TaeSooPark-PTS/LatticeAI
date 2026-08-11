//! The seam, faked, for the loop's own tests.
//!
//! A scripted reasoner and a tool handler that **really writes**, so the two
//! disk reads the loop owns — the pre-write snapshot and the fail-closed
//! existence check — see the workspace a real worker would have left behind.
//! A fake that only returned JSON would let a snapshot test pass while
//! snapshotting nothing.

use std::collections::VecDeque;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use axum::routing::post;
use axum::{Json, Router};
use serde_json::{json, Value};

use super::{LoopDeps, RunRequest, Runtime};
use crate::sandbox::Workspace;
use crate::state::AgentRunContext;
use crate::trace::LoopTrace;
use crate::worker::WorkerClient;

pub(crate) fn workspace() -> (tempfile::TempDir, Workspace) {
    let dir = tempfile::tempdir().expect("tempdir");
    let workspace = Workspace::new(dir.path().join("agent_workspace")).expect("workspace");
    (dir, workspace)
}

/// A runtime with no worker to reach: for the pure helpers around the loop.
pub(crate) fn runtime(workspace: Workspace) -> Runtime {
    Runtime::new(LoopDeps::new(
        WorkerClient::new("http://127.0.0.1:1"),
        workspace,
    ))
}

/// The seam, faked: a scripted reasoner and a tool handler that really
/// writes, so the loop's own disk reads (snapshot, overwrite guard) see the
/// same workspace the "worker" changed.
#[derive(Default)]
pub(crate) struct FakeWorker {
    pub completions: Mutex<VecDeque<String>>,
    /// Tool name → the exact body `/agent/tool` answers with.
    pub tool_bodies: Mutex<std::collections::BTreeMap<String, Value>>,
    pub proposal: Mutex<Option<Value>>,
    pub calls: Mutex<Vec<Value>>,
    pub root: Mutex<PathBuf>,
}

impl FakeWorker {
    fn record(&self, kind: &str, body: &Value) {
        self.calls
            .lock()
            .expect("lock")
            .push(json!({"seam": kind, "body": body.clone()}));
    }

    fn dispatch(&self, body: &Value) -> Value {
        let tool = body["tool"].as_str().unwrap_or_default().to_string();
        if let Some(canned) = self.tool_bodies.lock().expect("lock").get(&tool) {
            return canned.clone();
        }
        if matches!(tool.as_str(), "write_file" | "edit_file") {
            let path = body["args"]["path"].as_str().unwrap_or("out.txt");
            let content = body["args"]["content"].as_str().unwrap_or("");
            let target = self.root.lock().expect("lock").join(path);
            if let Some(parent) = target.parent() {
                let _ = std::fs::create_dir_all(parent);
            }
            let _ = std::fs::write(&target, content);
            return json!({"result": {"path": path, "bytes": content.len()}});
        }
        json!({"result": {"ok": true, "tool": tool}})
    }
}

pub(crate) struct Harness {
    pub runtime: Runtime,
    pub request: RunRequest,
    pub worker: Arc<FakeWorker>,
    pub root: PathBuf,
    _dir: tempfile::TempDir,
}

impl Harness {
    /// A context with a pinned trace clock, so events are comparable.
    pub fn context(&self) -> AgentRunContext {
        AgentRunContext {
            trace: LoopTrace::pinned("2026-08-11T00:00:00+00:00"),
            ..AgentRunContext::default()
        }
    }

    pub fn runtime_llm_calls(&self, ctx: &AgentRunContext) -> usize {
        ctx.trace
            .events
            .iter()
            .filter(|event| event["kind"] == json!("llm_call"))
            .count()
    }

    /// Every `/agent/tool` body the loop sent, in order.
    pub fn tool_calls(&self) -> Vec<Value> {
        self.worker
            .calls
            .lock()
            .expect("lock")
            .iter()
            .filter(|call| call["seam"] == json!("tool"))
            .map(|call| call["body"].clone())
            .collect()
    }
}

/// Start a fake worker and point a runtime at it.
pub(crate) async fn harness(completions: &[&str]) -> Harness {
    let (dir, workspace) = workspace();
    let fake = Arc::new(FakeWorker {
        completions: Mutex::new(completions.iter().map(|text| (*text).to_string()).collect()),
        root: Mutex::new(workspace.root().to_path_buf()),
        ..FakeWorker::default()
    });
    let state = Arc::clone(&fake);
    let app = Router::new()
        .route(
            "/agent/llm",
            post({
                let state = Arc::clone(&state);
                move |Json(body): Json<Value>| {
                    let state = Arc::clone(&state);
                    async move {
                        state.record("llm", &body);
                        let text = state
                            .completions
                            .lock()
                            .expect("lock")
                            .pop_front()
                            .unwrap_or_default();
                        Json(json!({"text": text}))
                    }
                }
            }),
        )
        .route(
            "/agent/tool",
            post({
                let state = Arc::clone(&state);
                move |Json(body): Json<Value>| {
                    let state = Arc::clone(&state);
                    async move {
                        state.record("tool", &body);
                        Json(state.dispatch(&body))
                    }
                }
            }),
        )
        .route(
            "/agent/change-proposal",
            post({
                let state = Arc::clone(&state);
                move |Json(body): Json<Value>| {
                    let state = Arc::clone(&state);
                    async move {
                        state.record("proposal", &body);
                        let verdict = state.proposal.lock().expect("lock").clone();
                        Json(verdict.unwrap_or_else(|| json!({"decision": "none"})))
                    }
                }
            }),
        );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind");
    let origin = format!("http://{}", listener.local_addr().expect("addr"));
    tokio::spawn(async move {
        let _ = axum::serve(listener, app).await;
    });

    let root = workspace.root().to_path_buf();
    let mut deps = LoopDeps::new(WorkerClient::new(&origin), workspace);
    deps.tool_names = vec!["read_file".into(), "write_file".into()];
    deps.policies.tools.insert(
        "write_file".into(),
        crate::policy::ToolPolicy {
            risk: "write".into(),
            ..crate::policy::ToolPolicy::default()
        },
    );
    deps.policies
        .tools
        .insert("read_file".into(), crate::policy::ToolPolicy::read_only());
    Harness {
        runtime: Runtime::new(deps),
        request: RunRequest {
            message: "make a note".into(),
            ..RunRequest::default()
        },
        worker: fake,
        root,
        _dir: dir,
    }
}
