use std::collections::VecDeque;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use axum::routing::post;
use axum::{Json, Router};
use serde_json::{json, Value};

use super::{LoopDeps, RunRequest, Runtime};
use crate::kernel::state::AgentRunContext;
use crate::kernel::trace::LoopTrace;
use crate::seam::worker::WorkerClient;
use crate::tools::sandbox::Workspace;
use crate::tools::{NativeCall, NativeTools, ToolConfig, ToolFuture, ToolHost};

pub(crate) fn workspace() -> (tempfile::TempDir, Workspace) {
    let dir = tempfile::tempdir().expect("tempdir");
    let workspace = Workspace::new(dir.path().join("agent_workspace")).expect("workspace");
    (dir, workspace)
}

pub(crate) fn runtime(workspace: Workspace) -> Runtime {
    Runtime::new(LoopDeps::new(
        WorkerClient::new("http://127.0.0.1:1"),
        workspace,
    ))
}

#[derive(Debug, Default)]
pub(crate) struct FakeWorker {
    pub completions: Mutex<VecDeque<String>>,
    pub tool_bodies: Mutex<std::collections::BTreeMap<String, Value>>,
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
        {
            let bodies = self.tool_bodies.lock().expect("lock");
            let path = body["args"]["path"].as_str().unwrap_or_default();
            if let Some(canned) = bodies.get(&format!("{tool}:{path}")) {
                return canned.clone();
            }
            if let Some(canned) = bodies.get(&tool) {
                return canned.clone();
            }
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

#[derive(Debug)]
pub(crate) struct RecordingHost {
    inner: NativeTools,
    log: Arc<FakeWorker>,
}

impl ToolHost for RecordingHost {
    fn handles(&self, tool: &str) -> bool {
        self.inner.handles(tool)
    }

    fn execute<'a>(&'a self, call: NativeCall<'a>) -> ToolFuture<'a> {
        let mut body = json!({"tool": call.tool, "args": Value::Object(call.args.clone())});
        if let Some(workspace_id) = &call.scope.workspace_id {
            body["workspace_id"] = json!(workspace_id);
        }
        self.log.record("tool", &body);
        self.inner.execute(call)
    }
}

pub(crate) struct Harness {
    pub runtime: Runtime,
    pub request: RunRequest,
    pub worker: Arc<FakeWorker>,
    pub root: PathBuf,
    pub data_dir: PathBuf,
    _dir: tempfile::TempDir,
}

impl Harness {
    pub fn staged_items(&self) -> Vec<Value> {
        let path = self.data_dir.join("workspace_os.json");
        let Ok(text) = std::fs::read_to_string(path) else {
            return Vec::new();
        };
        serde_json::from_str::<Value>(&text)
            .ok()
            .and_then(|document| document.get("review_items").cloned())
            .and_then(|rows| rows.as_array().cloned())
            .unwrap_or_default()
    }
}

impl Harness {
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
        );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind");
    let origin = format!("http://{}", listener.local_addr().expect("addr"));
    tokio::spawn(async move {
        let _ = axum::serve(listener, app).await;
    });

    let root = workspace.root().to_path_buf();
    let data_dir = dir.path().join("data");
    let mut deps = LoopDeps::new(WorkerClient::new(&origin), workspace.clone());
    deps.proposals = Arc::new(crate::kernel::proposals::JsonProposalStore::new(&data_dir));
    deps.native = Arc::new(RecordingHost {
        inner: NativeTools::new(
            workspace,
            ToolConfig {
                brain_dir: dir.path().join("brain"),
                role: "owner".into(),
                ..ToolConfig::default()
            },
            WorkerClient::new(&origin),
        ),
        log: Arc::clone(&fake),
    });
    deps.tool_names = vec!["read_file".into(), "write_file".into()];
    deps.policies.tools.insert(
        "write_file".into(),
        crate::kernel::policy::ToolPolicy {
            risk: "write".into(),
            ..crate::kernel::policy::ToolPolicy::default()
        },
    );
    deps.policies.tools.insert(
        "read_file".into(),
        crate::kernel::policy::ToolPolicy::read_only(),
    );
    Harness {
        runtime: Runtime::new(deps),
        request: RunRequest {
            message: "make a note".into(),
            ..RunRequest::default()
        },
        worker: fake,
        root,
        data_dir,
        _dir: dir,
    }
}
