mod common;

use std::collections::VecDeque;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use common::loop_golden;
use lattice_agent::kernel::agentloop::{LoopDeps, RunRequest, Runtime};
use lattice_agent::kernel::policy::PolicyTable;
use lattice_agent::kernel::state::{AgentRunContext, AgentState};
use lattice_agent::seam::worker::WorkerClient;
use lattice_agent::tools::sandbox::Workspace;
use lattice_agent::tools::{
    NativeCall, NativeTools, ToolConfig, ToolFuture, ToolHost, MUTATING_TOOLS, RENDER_TOOLS,
};
use serde_json::{json, Value};

#[derive(Debug, Default)]
struct CountingWorker {
    completions: Mutex<VecDeque<String>>,
    tool_calls: Mutex<Vec<Value>>,
    render_calls: Mutex<Vec<Value>>,
}

impl CountingWorker {
    fn tool_call_count(&self) -> usize {
        self.tool_calls.lock().expect("lock").len()
    }

    fn render_kinds(&self) -> Vec<String> {
        self.render_calls
            .lock()
            .expect("lock")
            .iter()
            .map(|call| call["kind"].as_str().unwrap_or_default().to_string())
            .collect()
    }
}

struct Server {
    origin: String,
    worker: Arc<CountingWorker>,
}

async fn start_worker() -> Server {
    let worker = Arc::new(CountingWorker::default());
    let app = axum::Router::new()
        .route(
            "/agent/llm",
            axum::routing::post({
                let state = Arc::clone(&worker);
                move |_body: axum::Json<Value>| {
                    let state = Arc::clone(&state);
                    async move {
                        let text = state
                            .completions
                            .lock()
                            .expect("lock")
                            .pop_front()
                            .unwrap_or_default();
                        axum::Json(json!({"text": text}))
                    }
                }
            }),
        )
        .route(
            "/agent/tool",
            axum::routing::post({
                let state = Arc::clone(&worker);
                move |axum::Json(body): axum::Json<Value>| {
                    let state = Arc::clone(&state);
                    async move {
                        state.tool_calls.lock().expect("lock").push(body);
                        axum::Json(json!({"result": {"ok": true}}))
                    }
                }
            }),
        )
        .route(
            "/worker/render/{kind}",
            axum::routing::post({
                let state = Arc::clone(&worker);
                move |axum::extract::Path(kind): axum::extract::Path<String>,
                      axum::Json(body): axum::Json<Value>| {
                    let state = Arc::clone(&state);
                    async move {
                        state
                            .render_calls
                            .lock()
                            .expect("lock")
                            .push(json!({"kind": kind, "body": body}));
                        axum::Json(json!({"content_b64": "UkVOREVSRUQ="}))
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
    Server { origin, worker }
}

#[derive(Debug)]
struct CountingHost {
    inner: NativeTools,
    calls: Arc<Mutex<Vec<String>>>,
}

impl ToolHost for CountingHost {
    fn handles(&self, tool: &str) -> bool {
        self.inner.handles(tool)
    }

    fn execute<'a>(&'a self, call: NativeCall<'a>) -> ToolFuture<'a> {
        self.calls.lock().expect("lock").push(call.tool.to_string());
        self.inner.execute(call)
    }
}

fn scratch(name: &str) -> (PathBuf, Workspace) {
    let dir = PathBuf::from(env!("CARGO_TARGET_TMPDIR"))
        .join("native_tools")
        .join(name);
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).expect("scratch");
    let workspace = Workspace::new(dir.join("agent_workspace")).expect("workspace");
    (dir, workspace)
}

fn policies() -> PolicyTable {
    serde_json::from_value(loop_golden("policies.json")).expect("policy table")
}

fn deps(
    server: &Server,
    dir: &std::path::Path,
    workspace: &Workspace,
    role: &str,
) -> (LoopDeps, Arc<Mutex<Vec<String>>>) {
    let calls: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
    let native = Arc::new(CountingHost {
        inner: NativeTools::new(
            workspace.clone(),
            ToolConfig {
                brain_dir: dir.join("brain"),
                role: role.to_string(),
                ..ToolConfig::default()
            },
            WorkerClient::new(&server.origin),
        ),
        calls: Arc::clone(&calls),
    });
    let table = policies();
    let tool_names: Vec<String> = table.tools.keys().cloned().collect();
    let deps = LoopDeps {
        policies: table,
        tool_names,
        native,
        proposals: Arc::new(lattice_agent::kernel::proposals::JsonProposalStore::new(
            dir.join("data"),
        )),
        ..LoopDeps::new(WorkerClient::new(&server.origin), workspace.clone())
    };
    (deps, calls)
}

fn request() -> RunRequest {
    RunRequest {
        message: "do the thing".into(),
        user_email: Some("owner@example.com".into()),
        workspace_id: Some("personal".into()),
        permission_mode: Some("bypass".into()),
        max_steps: 4,
        ..RunRequest::default()
    }
}

const FINAL: &str = r#"{"action": "final", "message": "done"}"#;

fn script(tool: &str, args: &Value) -> Vec<String> {
    vec![
        json!({"thoughts": "t", "action": tool, "args": args}).to_string(),
        FINAL.to_string(),
    ]
}

fn cases(dir: &std::path::Path) -> Vec<(&'static str, Value)> {
    let local = dir.join("home/local.txt").display().to_string();
    vec![
        ("write_file", json!({"path": "note.md", "content": "hello"})),
        (
            "edit_file",
            json!({"path": "seed.md", "old_string": "one", "new_string": "1"}),
        ),
        ("todo_write", json!({"todos": [{"content": "a"}]})),
        ("create_web_project", json!({"path": "site"})),
        ("local_write", json!({"path": local, "content": "x"})),
        ("knowledge_save", json!({"content": "a note"})),
        ("obsidian_save", json!({"content": "a note"})),
        ("run_command", json!({"command": "pwd"})),
        ("build_project", json!({})),
        ("deploy_project", json!({})),
        ("computer_open_app", json!({"app": "   "})),
        ("computer_open_url", json!({"url": "  "})),
        ("create_docx", json!({"title": "t", "body": "b"})),
        ("create_xlsx", json!({"rows": [[1, 2]]})),
        ("create_pptx", json!({"title": "t", "slides": []})),
        ("create_pdf", json!({"title": "t", "body": "b"})),
    ]
}

#[tokio::test]
async fn no_native_tool_reaches_the_worker_and_every_one_of_them_runs() {
    let server = start_worker().await;
    let mut covered: Vec<&str> = Vec::new();

    for (tool, args) in cases(&PathBuf::from(env!("CARGO_TARGET_TMPDIR"))) {
        let (dir, workspace) = scratch(tool);
        let args = match tool {
            "local_write" => json!({"path": dir.join("home/local.txt").display().to_string(),
                                    "content": "x"}),
            _ => args,
        };
        std::fs::write(workspace.root().join("seed.md"), "one\ntwo\n").expect("seed");

        let (deps, dispatched) = deps(&server, &dir, &workspace, "owner");
        let mut runtime = Runtime::new(deps);
        *server.worker.completions.lock().expect("lock") = script(tool, &args).into();
        let mut ctx = AgentRunContext::new();
        ctx.state = AgentState::Executing;
        let req = request();
        runtime.execute(&mut ctx, &req).await.expect("execute");

        assert_eq!(
            server.worker.tool_call_count(),
            0,
            "{tool} reached POST /agent/tool"
        );
        assert_eq!(
            dispatched.lock().expect("lock").clone(),
            vec![tool.to_string()],
            "{tool} was not dispatched natively exactly once"
        );
        let step = &ctx.transcript[0];
        assert_eq!(step["action"], tool);
        assert!(
            step.get("result").is_some() || step.get("error").is_some(),
            "{tool}: {step}"
        );
        covered.push(tool);
    }

    let mut expected: Vec<&str> = MUTATING_TOOLS
        .iter()
        .chain(RENDER_TOOLS.iter())
        .filter(|tool| lattice_agent::tools::is_native(tool))
        .copied()
        .collect();
    expected.sort_unstable();
    covered.sort_unstable();
    assert_eq!(covered, expected, "every native tool must be swept");
    assert_eq!(expected.len(), 16, "twelve mutating + four render");
    assert_eq!(
        server.worker.render_kinds(),
        vec!["docx", "xlsx", "pptx", "pdf"],
        "the document creators are the only ones that call the worker at all"
    );
}

#[tokio::test]
async fn every_pointer_tool_still_goes_to_the_worker() {
    let server = start_worker().await;
    for (tool, args) in [
        ("computer_click", json!({"x": 10, "y": 20})),
        ("computer_type", json!({"text": "hi"})),
        ("computer_key", json!({"key": "return"})),
        ("computer_scroll", json!({"x": 1, "y": 2})),
        ("computer_move", json!({"x": 1, "y": 2})),
        ("computer_drag", json!({"x1": 1, "y1": 2, "x2": 3, "y2": 4})),
    ] {
        let before = server.worker.tool_call_count();
        let (dir, workspace) = scratch(tool);
        let (deps, dispatched) = deps(&server, &dir, &workspace, "owner");
        let mut runtime = Runtime::new(deps);
        *server.worker.completions.lock().expect("lock") = script(tool, &args).into();
        let mut ctx = AgentRunContext::new();
        ctx.state = AgentState::Executing;
        let req = request();
        runtime.execute(&mut ctx, &req).await.expect("execute");

        assert_eq!(
            server.worker.tool_call_count(),
            before + 1,
            "{tool} must reach POST /agent/tool — pyautogui lives there"
        );
        assert!(
            dispatched.lock().expect("lock").is_empty(),
            "{tool} must not be dispatched natively"
        );
        assert_eq!(ctx.transcript[0]["action"], tool);
    }
}

#[tokio::test]
async fn the_writes_actually_land_where_python_put_them() {
    let server = start_worker().await;
    let (dir, workspace) = scratch("effects");
    let (write_deps, _) = deps(&server, &dir, &workspace, "owner");
    let mut runtime = Runtime::new(write_deps);
    *server.worker.completions.lock().expect("lock") = script(
        "write_file",
        &json!({"path": "notes/a.md", "content": "hello"}),
    )
    .into();
    let mut ctx = AgentRunContext::new();
    ctx.state = AgentState::Executing;
    runtime
        .execute(&mut ctx, &request())
        .await
        .expect("execute");
    assert_eq!(
        ctx.transcript[0]["result"],
        json!({"path": "notes/a.md", "bytes": 5})
    );
    assert_eq!(
        std::fs::read_to_string(workspace.root().join("notes/a.md")).expect("file"),
        "hello"
    );

    let (creator_deps, _) = deps(&server, &dir, &workspace, "owner");
    let mut runtime = Runtime::new(creator_deps);
    *server.worker.completions.lock().expect("lock") =
        script("create_docx", &json!({"title": "t", "filename": "report"})).into();
    let mut ctx = AgentRunContext::new();
    ctx.state = AgentState::Executing;
    runtime
        .execute(&mut ctx, &request())
        .await
        .expect("execute");
    assert_eq!(
        ctx.transcript[0]["result"],
        json!({"path": "generated_documents/report.docx", "bytes": 8})
    );
    assert_eq!(
        std::fs::read(workspace.root().join("generated_documents/report.docx")).expect("file"),
        b"RENDERED"
    );
}

#[tokio::test]
async fn the_default_role_refuses_the_admin_only_tools_without_calling_anyone() {
    let server = start_worker().await;
    let (dir, workspace) = scratch("role");
    let (deps, dispatched) = deps(&server, &dir, &workspace, "user");
    let mut runtime = Runtime::new(deps);
    *server.worker.completions.lock().expect("lock") =
        script("run_command", &json!({"command": "pwd"})).into();
    let mut ctx = AgentRunContext::new();
    ctx.state = AgentState::Executing;
    runtime
        .execute(&mut ctx, &request())
        .await
        .expect("execute");

    assert_eq!(
        ctx.transcript[0]["error"],
        "'run_command' 툴은 관리자 전용입니다."
    );
    assert_eq!(server.worker.tool_call_count(), 0);
    assert_eq!(
        dispatched.lock().expect("lock").len(),
        1,
        "it was dispatched"
    );
}

#[tokio::test]
async fn a_scoped_knowledge_write_uses_the_servers_scope_not_the_models() {
    let server = start_worker().await;
    let (dir, workspace) = scratch("scope");
    let (deps, _) = deps(&server, &dir, &workspace, "owner");
    let mut runtime = Runtime::new(deps);
    *server.worker.completions.lock().expect("lock") = script(
        "knowledge_save",
        &json!({"content": "secret", "workspace_id": "someone-elses",
                "user_email": "attacker@example.com"}),
    )
    .into();
    let mut ctx = AgentRunContext::new();
    ctx.state = AgentState::Executing;
    runtime
        .execute(&mut ctx, &request())
        .await
        .expect("execute");

    let path = ctx.transcript[0]["result"]["path"]
        .as_str()
        .expect("path")
        .to_string();
    assert!(
        path.starts_with(&dir.join("brain").display().to_string()),
        "{path}"
    );
    let scopes = dir.join("brain/.lattice-scopes");
    let partitions = std::fs::read_dir(&scopes)
        .expect("scopes")
        .filter_map(Result::ok)
        .count();
    assert_eq!(partitions, 1, "one workspace partition, the run's");
}

#[tokio::test]
async fn a_blocked_gate_never_reaches_the_native_tools() {
    let server = start_worker().await;
    let (dir, workspace) = scratch("blocked");
    let (deps, dispatched) = deps(&server, &dir, &workspace, "owner");
    let mut runtime = Runtime::new(deps);
    *server.worker.completions.lock().expect("lock") = script(
        "write_file",
        &json!({"path": "/etc/hosts", "content": "127.0.0.1 evil"}),
    )
    .into();
    let mut ctx = AgentRunContext::new();
    ctx.state = AgentState::Executing;
    runtime
        .execute(&mut ctx, &request())
        .await
        .expect("execute");

    assert!(ctx.transcript[0]["error"]
        .as_str()
        .expect("error")
        .starts_with("BLOCKED: "));
    assert!(
        dispatched.lock().expect("lock").is_empty(),
        "the tool set was never asked"
    );
    assert_eq!(server.worker.tool_call_count(), 0);
}

#[tokio::test]
async fn a_compute_tool_still_goes_to_the_worker() {
    let server = start_worker().await;
    let (dir, workspace) = scratch("compute");
    let (deps, dispatched) = deps(&server, &dir, &workspace, "owner");
    let mut runtime = Runtime::new(deps);
    *server.worker.completions.lock().expect("lock") =
        script("read_file", &json!({"path": "seed.md"})).into();
    let mut ctx = AgentRunContext::new();
    ctx.state = AgentState::Executing;
    runtime
        .execute(&mut ctx, &request())
        .await
        .expect("execute");

    assert_eq!(server.worker.tool_call_count(), 1);
    assert_eq!(
        server.worker.tool_calls.lock().expect("lock")[0]["tool"],
        "read_file"
    );
    assert!(dispatched.lock().expect("lock").is_empty());
}
