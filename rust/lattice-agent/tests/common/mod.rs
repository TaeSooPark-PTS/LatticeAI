//! Shared fixture plumbing for the agent parity suites.
//!
//! Both suites read the same committed goldens and both build the same
//! throwaway workspace from the manifest's `tree` spec — the point being that
//! the Rust side never invents a fixture the Python side did not describe.
#![allow(dead_code)] // each test binary uses a different half of this module.

use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use lattice_agent::sandbox::Workspace;
use serde_json::Value;

/// `rust/fixtures/agent`.
pub fn fixtures() -> PathBuf {
    [env!("CARGO_MANIFEST_DIR"), "..", "fixtures", "agent"]
        .iter()
        .collect()
}

pub fn read_golden(name: &str) -> Value {
    let path = fixtures().join("golden").join(name);
    let raw = std::fs::read_to_string(&path).unwrap_or_else(|err| {
        panic!(
            "missing golden {} ({err}) — run scripts/generate_agent_parity_fixtures.py",
            path.display()
        )
    });
    serde_json::from_str(&raw).expect("goldens must be valid JSON")
}

pub fn manifest() -> &'static Value {
    static MANIFEST: OnceLock<Value> = OnceLock::new();
    MANIFEST.get_or_init(|| read_golden("manifest.json"))
}

/// The cases of a grid file, as a slice.
pub fn cases(golden: &Value, key: &str) -> Vec<Value> {
    golden[key]
        .as_array()
        .unwrap_or_else(|| panic!("golden has no {key} array"))
        .clone()
}

/// Build the workspace the command fixtures were generated in.
///
/// The spec is the manifest's, so a tree change on the Python side reaches this
/// suite as a different tree rather than as a mysterious mismatch.
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

/// Substitute the placeholder the generator writes for the absolute root.
pub fn with_root(text: &str, workspace: &Workspace) -> String {
    text.replace("<AGENT_ROOT>", &workspace.root().display().to_string())
}

/// Report every mismatch at once: a parity failure that names one case out of a
/// thousand and hides the rest is a parity failure you fix three times.
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

// ── agent loop fixtures (v11.5.1) ────────────────────────────────────────────
//
// The loop goldens live in their own directory and carry their own
// normalisation contract, so their plumbing is kept apart from the kernel
// fixtures above rather than folded into functions that mean something else.

use std::collections::BTreeSet;
use std::sync::{Arc, Mutex};

use lattice_agent::agentloop::{LoopDeps, Prompts, RunRequest, Runtime};
use lattice_agent::policy::PolicyTable;
use lattice_agent::worker::WorkerClient;
use serde_json::json;

/// `rust/fixtures/agent_loop/golden/<name>`.
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

/// The four machine-independence rules, byte for byte with the generator's.
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

/// The AI worker, faked: it replays the recorded completions, replays the
/// recorded tool results **and applies their file effects**, and answers the
/// recorded governor verdict.
///
/// Applying the effects is what makes the comparison honest: the loop's own two
/// disk reads — the pre-write snapshot and the fail-closed existence check —
/// must see the workspace the real worker would have left behind.
#[derive(Default)]
pub struct ReplayWorker {
    completions: Mutex<Vec<String>>,
    tool_calls: Mutex<Vec<Value>>,
    observed: Mutex<Vec<Value>>,
    proposal: Mutex<Value>,
    root: Mutex<PathBuf>,
}

impl ReplayWorker {
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

    /// Every `/agent/tool` body the loop sent, in order.
    pub fn observed_calls(&self) -> Vec<Value> {
        self.observed.lock().expect("lock").clone()
    }

    /// Load the recorded tool results this worker will replay, in order.
    pub fn load_tool_calls(&self, recorded: &[Value]) {
        *self.tool_calls.lock().expect("lock") = recorded.to_vec();
    }

    /// Queue more completions (the verification grid reuses one worker).
    pub fn push_completions(&self, texts: &[String]) {
        self.completions
            .lock()
            .expect("lock")
            .extend(texts.iter().cloned());
    }

    pub fn set_proposal(&self, verdict: Value) {
        *self.proposal.lock().expect("lock") = verdict;
    }

    pub fn remaining_completions(&self) -> usize {
        self.completions.lock().expect("lock").len()
    }
}

/// A running fake worker plus the origin to reach it at.
pub struct ReplayServer {
    pub worker: Arc<ReplayWorker>,
    pub origin: String,
}

/// Start a fake worker rooted at `root`.
pub async fn start_replay_worker(root: &Path) -> ReplayServer {
    let worker = Arc::new(ReplayWorker {
        root: Mutex::new(root.to_path_buf()),
        proposal: Mutex::new(json!({"decision": "none"})),
        ..ReplayWorker::default()
    });
    let app = axum::Router::new()
        .route(
            "/agent/llm",
            axum::routing::post({
                let state = Arc::clone(&worker);
                move |_body: axum::Json<Value>| {
                    let state = Arc::clone(&state);
                    async move { axum::Json(json!({"text": state.next_completion()})) }
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
        )
        .route(
            "/agent/change-proposal",
            axum::routing::post({
                let state = Arc::clone(&worker);
                move |_body: axum::Json<Value>| {
                    let state = Arc::clone(&state);
                    async move { axum::Json(state.proposal.lock().expect("lock").clone()) }
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

/// The ports the recorded scenarios ran under: the real policy table from the
/// goldens, the Python constants for every tool set, and no prompts (the worker
/// owns those, and a scripted reasoner never reads them).
pub fn loop_deps(origin: &str, workspace: lattice_agent::sandbox::Workspace) -> LoopDeps {
    let policies: PolicyTable =
        serde_json::from_value(loop_golden("policies.json")).expect("policy table");
    let tool_names: Vec<String> = policies.tools.keys().cloned().collect();
    LoopDeps {
        policies,
        tool_names,
        prompts: Prompts::default(),
        ..LoopDeps::new(WorkerClient::new(origin), workspace)
    }
}

/// The request Python drove the trajectories with.
pub fn loop_request(message: &str) -> RunRequest {
    RunRequest {
        message: message.to_string(),
        user_email: Some("owner@example.com".into()),
        language_hint: "Korean".into(),
        ..RunRequest::default()
    }
}

/// `FILE_CREATE_ACTIONS`, for callers comparing artifact views.
pub fn file_create_actions() -> BTreeSet<String> {
    lattice_agent::agentloop::default_file_create_actions()
}

/// Canonical JSON: sorted keys, so a `bool` becoming an `int` still differs.
pub fn canonical(value: &Value) -> String {
    serde_json::to_string(value).expect("canonical json")
}

/// The audit trail a runtime accumulated, as a comparable value.
pub fn audit_of(runtime: &Runtime, root: &str) -> Value {
    loop_normalize(&Value::Array(runtime.audit.clone()), root)
}
