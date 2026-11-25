#![allow(dead_code)]

use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use lattice_agent::tools::sandbox::Workspace;
use serde_json::Value;

pub fn fixtures() -> PathBuf {
    [env!("CARGO_MANIFEST_DIR"), "..", "fixtures", "agent"]
        .iter()
        .collect()
}

pub fn golden_path(name: &str) -> PathBuf {
    fixtures().join("golden").join(name)
}

pub fn read_golden(name: &str) -> Value {
    let path = golden_path(name);
    let raw = std::fs::read_to_string(&path).unwrap_or_else(|err| {
        panic!(
            "missing golden {} ({err}) — these files are frozen and committed; \
             see rust/fixtures/agent/FROZEN.md",
            path.display()
        )
    });
    serde_json::from_str(&raw).expect("goldens must be valid JSON")
}

pub fn manifest() -> &'static Value {
    static MANIFEST: OnceLock<Value> = OnceLock::new();
    MANIFEST.get_or_init(|| read_golden("manifest.json"))
}

pub fn cases(golden: &Value, key: &str) -> Vec<Value> {
    golden[key]
        .as_array()
        .unwrap_or_else(|| panic!("golden has no {key} array"))
        .clone()
}

pub fn build_tree(dir: &Path) -> Workspace {
    let root = dir.join("agent_workspace");
    std::fs::create_dir_all(&root).expect("root");
    for node in manifest()["tree"].as_array().expect("tree") {
        let kind = node["kind"].as_str().expect("kind");
        let relative = node["path"].as_str().expect("path");
        let target = root.join(relative);
        match kind {
            "outside" => std::fs::write(dir.join(relative), content(node)).expect("outside file"),
            "dir" => std::fs::create_dir_all(&target).expect("dir"),
            "file" => std::fs::write(&target, content(node)).expect("file"),
            "lines" => {
                let count = node["count"].as_u64().expect("count");
                let body: String = (0..count).map(|index| format!("{index:07}\n")).collect();
                std::fs::write(&target, body).expect("lines");
            }
            "symlink" => {
                let link_target = node["target"].as_str().expect("target");
                #[cfg(unix)]
                std::os::unix::fs::symlink(link_target, &target).expect("symlink");
                #[cfg(not(unix))]
                panic!("the fixture tree needs symlinks: {link_target}");
            }
            other => panic!("unknown tree node kind {other}"),
        }
    }
    Workspace::new(&root).expect("workspace")
}

fn content(node: &Value) -> String {
    node["content"].as_str().unwrap_or_default().to_string()
}

pub fn with_root(text: &str, workspace: &Workspace) -> String {
    text.replace("<AGENT_ROOT>", &workspace.root().display().to_string())
}

pub fn assert_no_failures(checked: usize, failures: Vec<String>, what: &str) {
    assert!(
        failures.is_empty(),
        "{} of {checked} {what} mismatched:\n{}",
        failures.len(),
        failures
            .iter()
            .take(25)
            .cloned()
            .collect::<Vec<_>>()
            .join("\n")
    );
    assert!(checked > 0, "no {what} were checked at all");
}

use std::collections::BTreeSet;
use std::sync::{Arc, Mutex};

use lattice_agent::kernel::agentloop::{LoopDeps, Prompts, RunRequest, Runtime};
use lattice_agent::kernel::policy::PolicyTable;
use lattice_agent::kernel::proposals::{JsonProposalStore, NewReviewItem, ProposalStore};
use lattice_agent::seam::worker::WorkerClient;
use serde_json::json;

pub fn loop_golden(name: &str) -> Value {
    let path: PathBuf = [
        env!("CARGO_MANIFEST_DIR"),
        "..",
        "fixtures",
        "agent_loop",
        "golden",
        name,
    ]
    .iter()
    .collect();
    let raw = std::fs::read_to_string(&path).unwrap_or_else(|err| {
        panic!(
            "missing golden {} ({err}) — run scripts/generate_agent_loop_fixtures.py",
            path.display()
        )
    });
    serde_json::from_str(&raw).expect("goldens must be valid JSON")
}

pub fn loop_normalize(value: &Value, root: &str) -> Value {
    const DECODER_PREFIX: &str = "Agent did not return valid JSON: ";
    match value {
        Value::String(text) => {
            let replaced = text.replace(root, "<AGENT_ROOT>");
            if replaced.starts_with(DECODER_PREFIX) {
                Value::String(format!("{DECODER_PREFIX}<decoder-detail>"))
            } else {
                Value::String(replaced)
            }
        }
        Value::Object(map) => Value::Object(
            map.iter()
                .filter(|(key, _)| key.as_str() != "at" && key.as_str() != "stderr")
                .map(|(key, item)| (key.clone(), loop_normalize(item, root)))
                .collect(),
        ),
        Value::Array(items) => Value::Array(
            items
                .iter()
                .map(|item| loop_normalize(item, root))
                .collect(),
        ),
        other => other.clone(),
    }
}

#[derive(Debug, Default)]
pub struct ReplayWorker {
    completions: Mutex<Vec<String>>,
    tool_calls: Mutex<Vec<Value>>,
    observed: Mutex<Vec<Value>>,
    prompts: Mutex<Vec<String>>,
    root: Mutex<PathBuf>,
}

impl ReplayWorker {
    pub fn observed_prompts(&self) -> Vec<String> {
        self.prompts.lock().expect("lock").clone()
    }

    fn record_prompt(&self, body: &Value) {
        self.prompts
            .lock()
            .expect("lock")
            .push(body["context"].as_str().unwrap_or_default().to_string());
    }

    fn next_completion(&self) -> String {
        let mut queue = self.completions.lock().expect("lock");
        if queue.is_empty() {
            String::new()
        } else {
            queue.remove(0)
        }
    }

    fn dispatch(&self, body: &Value) -> Value {
        let tool = body["tool"].as_str().unwrap_or_default().to_string();
        let args = body["args"].clone();
        self.observed
            .lock()
            .expect("lock")
            .push(json!({"tool": tool, "args": args.clone()}));
        let mut queue = self.tool_calls.lock().expect("lock");
        if queue.is_empty() {
            return json!({"error": format!("no recorded tool call left for '{tool}'")});
        }
        let recorded = queue.remove(0);
        if recorded["tool"] != json!(tool) {
            return json!({
                "error": format!("recorded call is {} but the loop asked for {tool}",
                                 recorded["tool"])
            });
        }
        if let Some(result) = recorded.get("result") {
            if matches!(tool.as_str(), "write_file" | "edit_file") {
                let root = self.root.lock().expect("lock").clone();
                let target = root.join(args["path"].as_str().unwrap_or("out.txt"));
                if let Some(parent) = target.parent() {
                    let _ = std::fs::create_dir_all(parent);
                }
                let _ = std::fs::write(&target, args["content"].as_str().unwrap_or(""));
            }
            return json!({"result": result});
        }
        json!({"error": recorded.get("error").cloned().unwrap_or(Value::Null)})
    }

    pub fn observed_calls(&self) -> Vec<Value> {
        self.observed.lock().expect("lock").clone()
    }

    pub fn observe(&self, tool: &str, args: &Value) {
        self.observed
            .lock()
            .expect("lock")
            .push(json!({"tool": tool, "args": args.clone()}));
    }

    pub fn has_front_tool_call(&self, tool: &str) -> bool {
        self.tool_calls
            .lock()
            .expect("lock")
            .first()
            .is_some_and(|entry| entry.get("tool").and_then(Value::as_str) == Some(tool))
    }

    pub fn load_tool_calls(&self, recorded: &[Value]) {
        *self.tool_calls.lock().expect("lock") = recorded.to_vec();
    }

    pub fn push_completions(&self, texts: &[String]) {
        self.completions
            .lock()
            .expect("lock")
            .extend(texts.iter().cloned());
    }

    pub fn remaining_completions(&self) -> usize {
        self.completions.lock().expect("lock").len()
    }
}

pub struct ReplayServer {
    pub worker: Arc<ReplayWorker>,
    pub origin: String,
}

pub async fn start_replay_worker(root: &Path) -> ReplayServer {
    let worker = Arc::new(ReplayWorker {
        root: Mutex::new(root.to_path_buf()),
        ..ReplayWorker::default()
    });
    let app = axum::Router::new()
        .route(
            "/agent/llm",
            axum::routing::post({
                let state = Arc::clone(&worker);
                move |axum::Json(body): axum::Json<Value>| {
                    let state = Arc::clone(&state);
                    async move {
                        state.record_prompt(&body);
                        axum::Json(json!({"text": state.next_completion()}))
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
                    async move { axum::Json(state.dispatch(&body)) }
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
    ReplayServer { worker, origin }
}

#[derive(Debug)]
pub struct RecordingHost {
    inner: lattice_agent::tools::NativeTools,
    worker: Arc<ReplayWorker>,
}

impl lattice_agent::tools::ToolHost for RecordingHost {
    fn handles(&self, tool: &str) -> bool {
        if matches!(tool, "web_fetch" | "web_search" | "computer_ui_inspect")
            && self.worker.has_front_tool_call(tool)
        {
            return false;
        }
        self.inner.handles(tool)
    }

    fn execute<'a>(
        &'a self,
        call: lattice_agent::tools::NativeCall<'a>,
    ) -> lattice_agent::tools::ToolFuture<'a> {
        self.worker
            .observe(call.tool, &Value::Object(call.args.clone()));
        self.inner.execute(call)
    }
}

#[derive(Debug)]
pub struct PinnedProposalStore {
    inner: JsonProposalStore,
    pinned: Mutex<Option<String>>,
    staged: Mutex<Vec<Value>>,
}

impl PinnedProposalStore {
    pub fn new(data_dir: impl Into<PathBuf>) -> Self {
        Self {
            inner: JsonProposalStore::new(data_dir),
            pinned: Mutex::new(None),
            staged: Mutex::new(Vec::new()),
        }
    }

    pub fn pin(&self, id: &str) {
        *self.pinned.lock().expect("lock") = Some(id.to_string());
    }

    pub fn staged(&self) -> Vec<Value> {
        self.staged.lock().expect("lock").clone()
    }
}

impl ProposalStore for PinnedProposalStore {
    fn create(&self, item: &NewReviewItem) -> Result<Value, String> {
        let mut stored = self.inner.create(item)?;
        self.staged.lock().expect("lock").push(stored.clone());
        if let Some(id) = self.pinned.lock().expect("lock").clone() {
            stored["id"] = json!(id);
        }
        Ok(stored)
    }
}

pub fn loop_deps(
    server: &ReplayServer,
    workspace: lattice_agent::tools::sandbox::Workspace,
) -> LoopDeps {
    let scratch = workspace
        .root()
        .parent()
        .unwrap_or(workspace.root())
        .to_path_buf();
    loop_deps_with(
        server,
        workspace,
        Arc::new(JsonProposalStore::new(scratch.join("data"))),
    )
}

pub fn loop_deps_with(
    server: &ReplayServer,
    workspace: lattice_agent::tools::sandbox::Workspace,
    proposals: Arc<dyn ProposalStore>,
) -> LoopDeps {
    let policies: PolicyTable =
        serde_json::from_value(loop_golden("policies.json")).expect("policy table");
    let tool_names: Vec<String> = policies.tools.keys().cloned().collect();
    let scratch = workspace
        .root()
        .parent()
        .unwrap_or(workspace.root())
        .join("brain");
    let native = Arc::new(RecordingHost {
        inner: lattice_agent::tools::NativeTools::new(
            workspace.clone(),
            lattice_agent::tools::ToolConfig {
                brain_dir: scratch,
                role: "owner".into(),
                ..lattice_agent::tools::ToolConfig::default()
            },
            WorkerClient::new(&server.origin),
        ),
        worker: Arc::clone(&server.worker),
    });
    LoopDeps {
        policies,
        tool_names,
        native,
        proposals,
        prompts: Prompts::default(),
        ..LoopDeps::new(WorkerClient::new(&server.origin), workspace)
    }
}

pub fn loop_request(message: &str) -> RunRequest {
    RunRequest {
        message: message.to_string(),
        user_email: Some("owner@example.com".into()),
        language_hint: "Korean".into(),
        ..RunRequest::default()
    }
}

pub fn file_create_actions() -> BTreeSet<String> {
    lattice_agent::kernel::agentloop::default_file_create_actions()
}

pub fn canonical(value: &Value) -> String {
    serde_json::to_string(&with_sorted_keys(value)).expect("canonical json")
}

fn with_sorted_keys(value: &Value) -> Value {
    match value {
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            let mut sorted = serde_json::Map::with_capacity(keys.len());
            for key in keys {
                sorted.insert(key.clone(), with_sorted_keys(&map[key]));
            }
            Value::Object(sorted)
        }
        Value::Array(items) => Value::Array(items.iter().map(with_sorted_keys).collect()),
        other => other.clone(),
    }
}

pub fn audit_of(runtime: &Runtime, root: &str) -> Value {
    loop_normalize(&Value::Array(runtime.audit.clone()), root)
}
