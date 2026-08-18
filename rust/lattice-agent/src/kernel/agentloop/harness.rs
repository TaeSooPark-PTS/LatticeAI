//! The seam, faked, for the loop's own tests.
//!
//! A scripted reasoner and a tool handler that **really writes**, so the two
//! disk reads the loop owns — the pre-write snapshot and the fail-closed
//! existence check — see the workspace a real worker would have left behind.
//! A fake that only returned JSON would let a snapshot test pass while
//! snapshotting nothing.
//!
//! Since W4 the mutating tools are native, so "really writes" is no longer a
//! fake at all: [`RecordingHost`] wraps the **real** [`NativeTools`] and records
//! what it executed into the same ordered log the fake worker uses. A test
//! therefore reads one list of dispatches whichever side ran them, and the
//! knowledge tools are pointed at a temporary brain directory so no test can
//! write into the developer's actual vault.

use std::collections::VecDeque;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use axum::routing::post;
use axum::{Json, Router};
use serde_json::{json, Value};

use super::{LoopDeps, RunRequest, Runtime};
use crate::kernel::state::AgentRunContext;
use crate::kernel::trace::LoopTrace;
use crate::surface::worker::WorkerClient;
use crate::tools::sandbox::Workspace;
use crate::tools::{NativeCall, NativeTools, ToolConfig, ToolFuture, ToolHost};

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
#[derive(Debug, Default)]
pub(crate) struct FakeWorker {
    pub completions: Mutex<VecDeque<String>>,
    /// Tool name → the exact body `/agent/tool` answers with.
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
            // A tool that answers differently per path is keyed `tool:path` —
            // which is how a test replays "the stated directory was not there,
            // the tool's own default was". The plain tool key still answers
            // every other call, so nothing that does not use the long key
            // changes.
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

/// The real native tool set, logging each dispatch beside the worker's.
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
    /// The scratch data directory the proposal store writes into. A test that
    /// stages must never reach `$HOME`, and reading the staged item back is
    /// how "it was staged" is proved rather than assumed.
    pub data_dir: PathBuf,
    _dir: tempfile::TempDir,
}

impl Harness {
    /// The review items the loop staged, in order.
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
    // Never `JsonProposalStore::from_env()` here: that would stage into the
    // developer's own `$HOME/.ltcai/workspace_os.json`.
    deps.proposals = Arc::new(crate::kernel::proposals::JsonProposalStore::new(&data_dir));
    // The real tools, with the vault pointed at the test's own directory and
    // the owner role, so the loop's gates are what decides — not `check_role`.
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
