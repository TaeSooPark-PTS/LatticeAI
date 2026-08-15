//! The native tool set — every mutating handler, executed here (v11.6.0, W4).
//!
//! v11.5.1 left one hole in "the kernel decides, the worker acts": the acting.
//! Every write the loop chose still travelled back over `POST /agent/tool` to a
//! Python handler, so a run that never left Rust for its *decisions* left Rust
//! for all of its *effects*. This module closes it. The eighteen mutating
//! handlers of `latticeai.tools` run here, and the four document creators split
//! (the builder computes in the worker, the file is written here), which leaves
//! `/agent/tool` carrying only the twenty-five compute-only handlers.
//!
//! Three rules hold for every tool below:
//!
//! * **the same result** — key for key and message for message with the Python
//!   handler, because the transcript, the critic and the artifact list all read
//!   those keys. Even the refusals are ported literally, including the fact
//!   that a missing required argument is Python's `KeyError` repr (`'path'`);
//! * **the same containment** — every workspace path resolves through
//!   [`crate::sandbox::Workspace::resolve`], never a raw string, so the tools,
//!   the loop's pre-write snapshot and the overwrite guard share one rule;
//! * **the same authorization** — [`authorize::check_role`], the one check the
//!   Python seam ran that the loop's permission kernel does not, ported with
//!   the role taken as input.
//!
//! ## Hook semantics (`lattice_brain.runtime.hooks.dispatch_tool`)
//!
//! The Python seam wrapped every dispatch in `pre_tool` → execute → `post_tool`.
//! Three platform hooks bind runners to those points and **none of them changes
//! what happens to these tools**:
//! `builtin:tool-permission-gate` blocks only when the governance policy says
//! `deny`, which no entry in `TOOL_GOVERNANCE` does (its risks are read / write
//! / exec / destructive), so it is a log line over a decision the kernel has
//! already made; `builtin:sensitive-data-guard` classifies and never blocks;
//! and the `brain-event-triggers` `post_tool` runner fires only for
//! `tool.kg_ingest.*` events, which no registered handler produces. Those three
//! are therefore **retired** for native tools, and saying so is the honest
//! description of what was already happening.
//!
//! What is *not* retired is a **user-registered** hook: those carry a shell
//! command, run as a subprocess, and a `pre_tool` one can block. That is a real
//! feature over a registry (`hooks.json`) this crate does not own, so the
//! lifecycle survives as a port — [`HookSink`] — that the gateway wires.
//!
//! Since v11.7.0 the gateway does wire it: `lattice_platform::hooks::
//! NativeHookSink` is the production implementation (the registry is above this
//! crate in the dependency graph, so the adapter lives with the registry), it
//! arrives through `LoopConfig::with_hooks`, and both construction sites —
//! [`crate::runbody::RunBody::to_deps_with_hooks`] and
//! [`crate::agentloop::LoopDeps::with_hook_sink`] — carry it. A user
//! `pre_tool` hook can now block a native write, and every fire is recorded in
//! `hooks_runs.json` where `GET /api/hooks/runs` shows it.

pub mod args;
pub mod authorize;
pub mod desktop;
pub mod files;
pub mod local;
pub mod render;
pub mod scaffold;
pub mod shell;
pub mod vault;

use std::future::Future;
use std::path::PathBuf;
use std::pin::Pin;
use std::sync::Arc;

use serde_json::{Map, Value};

use crate::policy::ToolPolicy;
use crate::sandbox::{ToolError, Workspace};
use crate::worker::{ToolOutcome, WorkerClient};

/// The eighteen mutating handlers, sorted.
///
/// This is grok's DROP verdict table (`scratchpad/grok_keepset_v2.md` §4.4)
/// made executable: a handler is here when it writes a file, writes the graph,
/// actuates the OS or executes something.
pub const MUTATING_TOOLS: [&str; 18] = [
    "build_project",
    "computer_click",
    "computer_drag",
    "computer_key",
    "computer_move",
    "computer_open_app",
    "computer_open_url",
    "computer_scroll",
    "computer_type",
    "create_web_project",
    "deploy_project",
    "edit_file",
    "knowledge_save",
    "local_write",
    "obsidian_save",
    "run_command",
    "todo_write",
    "write_file",
];

/// The four document creators, sorted: compute in the worker, write here.
pub const RENDER_TOOLS: [&str; 4] = ["create_docx", "create_pdf", "create_pptx", "create_xlsx"];

/// Whether the loop should execute `tool` itself instead of calling the worker.
///
/// [`MUTATING_TOOLS`] minus [`desktop::POINTER_TOOLS`], plus [`RENDER_TOOLS`] —
/// **twelve plus four**. The six pointer tools are a deliberate exception
/// (v11.6.0 gateway integration §4b): mouse and keyboard actuation is
/// `pyautogui`, a *capability of the worker's interpreter*, not a file write
/// this crate can perform. Native versions could only reproduce the refusal,
/// and reproducing a refusal costs the one install that pip-installed
/// `pyautogui` into the worker venv its working pointer control. So the loop
/// keeps sending them to `POST /agent/tool`, where the real handler lives, and
/// the `/agent/tool` whitelist is the twenty-five compute handlers plus these
/// six.
pub fn is_native(tool: &str) -> bool {
    if crate::in_set(&desktop::POINTER_TOOLS, tool) {
        return false;
    }
    crate::in_set(&MUTATING_TOOLS, tool) || crate::in_set(&RENDER_TOOLS, tool)
}

/// Who the run belongs to — the seam's `user_email` / `workspace_id` fields.
///
/// The knowledge tools read their scope from the **arguments** (the loop
/// overwrites them there before policy evaluation), so this carries the same
/// values for the hook lifecycle rather than a second authority on scope.
#[derive(Debug, Clone, Default)]
pub struct CallScope {
    pub user_email: Option<String>,
    pub workspace_id: Option<String>,
}

/// One native dispatch: what to run, with what, under which policy, for whom.
#[derive(Debug, Clone, Copy)]
pub struct NativeCall<'a> {
    pub tool: &'a str,
    pub args: &'a Map<String, Value>,
    /// The **registry** policy (`PolicyTable::get`), not the argument-rewritten
    /// one: `check_role` reads `policy_for(tool, {})`, so a blocked-prefix write
    /// must not become an admin-only tool on its way to the role check.
    pub policy: &'a ToolPolicy,
    pub scope: &'a CallScope,
}

/// A boxed dispatch future. The crate carries no async-trait dependency, and
/// one `Pin<Box<…>>` per tool call is noise next to the syscalls it wraps.
pub type ToolFuture<'a> = Pin<Box<dyn Future<Output = ToolOutcome> + Send + 'a>>;

/// The port the loop dispatches through.
///
/// A trait rather than a struct for exactly one reason: the trajectory suite
/// wraps the real implementation to record what was dispatched, and a fake that
/// did not really write would let the loop's own disk reads pass over an empty
/// workspace.
pub trait ToolHost: std::fmt::Debug + Send + Sync {
    /// Whether this host executes `tool` itself.
    fn handles(&self, tool: &str) -> bool;
    /// Run one tool. Every failure is an outcome, never a panic.
    fn execute<'a>(&'a self, call: NativeCall<'a>) -> ToolFuture<'a>;
}

/// The `pre_tool` / `post_tool` lifecycle, for a host that owns `hooks.json`.
///
/// `pre_tool` returning `Err(reason)` is Python's `PermissionError`, which the
/// seam answers as `{"error": str(exc)}` — so the reason lands on the step.
pub trait HookSink: std::fmt::Debug + Send + Sync {
    /// The payload Python sends is `{tool, args_keys, source}`; the keys are
    /// passed rather than the values, as `dispatch_tool` does.
    fn pre_tool(&self, tool: &str, arg_keys: &[String], scope: &CallScope) -> Result<(), String>;
    /// Fired for both outcomes — Python fires `post_tool` on the error path too,
    /// before re-raising.
    fn post_tool(&self, tool: &str, status: &str, detail: &str, scope: &CallScope);
}

/// What the native tools need that the workspace does not carry.
#[derive(Debug, Clone)]
pub struct ToolConfig {
    /// `BRAIN_DIR` — the knowledge garden's root.
    pub brain_dir: PathBuf,
    /// The caller's role, for [`authorize::check_role`]. Python's own default
    /// (`_default_get_user_role`) is `user`, and so is this one: an unwired
    /// host is exactly as restrictive as an unconfigured `ToolDispatchService`.
    pub role: String,
    /// `LOCAL_WRITE_BLOCKED_PREFIXES`, as data — the same list the policy table
    /// carries, so there is one copy of the denylist in a run.
    pub blocked_write_prefixes: Vec<String>,
}

impl Default for ToolConfig {
    fn default() -> Self {
        Self {
            brain_dir: vault::default_brain_dir(),
            role: "user".into(),
            blocked_write_prefixes: crate::policy::default_blocked_write_prefixes(),
        }
    }
}

impl ToolConfig {
    /// The environment's brain directory, with Python's defaults.
    pub fn from_env() -> Self {
        Self::default()
    }

    /// The caller's role, resolved by the gateway from the session.
    pub fn with_role(mut self, role: Option<String>) -> Self {
        if let Some(role) = role {
            self.role = role;
        }
        self
    }

    /// The denylist from the run's policy table.
    pub fn with_blocked_write_prefixes(mut self, prefixes: Vec<String>) -> Self {
        self.blocked_write_prefixes = prefixes;
        self
    }
}

/// The production [`ToolHost`].
#[derive(Debug, Clone)]
pub struct NativeTools {
    workspace: Workspace,
    config: ToolConfig,
    worker: WorkerClient,
    hooks: Option<Arc<dyn HookSink>>,
}

impl NativeTools {
    /// A host over one workspace, one config and one worker (for the four
    /// render calls; nothing else here talks to the worker).
    pub fn new(workspace: Workspace, config: ToolConfig, worker: WorkerClient) -> Self {
        Self {
            workspace,
            config,
            worker,
            hooks: None,
        }
    }

    /// Wire the `pre_tool` / `post_tool` lifecycle.
    pub fn with_hooks(mut self, hooks: Arc<dyn HookSink>) -> Self {
        self.hooks = Some(hooks);
        self
    }

    /// The workspace these tools write into.
    pub fn workspace(&self) -> &Workspace {
        &self.workspace
    }

    /// The config in force.
    pub fn config(&self) -> &ToolConfig {
        &self.config
    }

    /// Role check → `pre_tool` → the tool → `post_tool`, in Python's order.
    async fn run(&self, call: NativeCall<'_>) -> Result<Value, ToolError> {
        authorize::check_role(call.tool, call.policy, &self.config.role)?;
        if let Some(hooks) = &self.hooks {
            let keys: Vec<String> = call.args.keys().cloned().collect();
            if let Err(reason) = pre_tool(hooks, call.tool, keys, call.scope).await {
                let reason = if reason.is_empty() {
                    format!("Tool '{}' blocked by a pre_tool hook.", call.tool)
                } else {
                    reason
                };
                return Err(ToolError::tool(reason));
            }
        }
        let outcome = self.dispatch(call).await;
        if let Some(hooks) = &self.hooks {
            let (status, detail) = match &outcome {
                Ok(_) => ("ok", String::new()),
                Err(error) => ("error", error.message.clone()),
            };
            post_tool(hooks, call.tool, status, detail, call.scope).await;
        }
        outcome
    }

    /// The table itself.
    async fn dispatch(&self, call: NativeCall<'_>) -> Result<Value, ToolError> {
        match call.tool {
            "write_file" => self.blocking(call.args, files::write_file).await,
            "edit_file" => self.blocking(call.args, files::edit_file).await,
            "todo_write" => self.blocking(call.args, files::todo_write).await,
            "create_web_project" => self.blocking(call.args, scaffold::create_web_project).await,
            "knowledge_save" => self.in_vault(call.args, vault::knowledge_save).await,
            "obsidian_save" => self.in_vault(call.args, vault::obsidian_save).await,
            "local_write" => {
                let prefixes = self.config.blocked_write_prefixes.clone();
                let args = call.args.clone();
                blocking(move || local::local_write(&prefixes, &args)).await
            }
            "run_command" => shell::run_command(&self.workspace, call.args).await,
            "build_project" => shell::build_project(&self.workspace, call.args).await,
            "deploy_project" => shell::deploy_project(&self.workspace, call.args).await,
            "computer_open_app" => desktop::computer_open_app(call.args).await,
            "computer_open_url" => desktop::computer_open_url(call.args).await,
            // Unreachable through the loop since §4b — `handles` says no, so
            // the six pointer tools go to the worker. Kept for a direct caller
            // (and for the harness), where answering Python's own message is
            // still the right answer.
            pointer if crate::in_set(&desktop::POINTER_TOOLS, pointer) => {
                desktop::pointer_tool(pointer, call.args)
            }
            creator if render::is_creator(creator) => {
                render::create_document(&self.worker, &self.workspace, creator, call.args).await
            }
            // Unreachable through the loop, which asks `handles` first. A direct
            // caller gets `ToolRegistry.execute`'s own answer.
            other => Err(ToolError::tool(format!("Unknown tool: {other}"))),
        }
    }

    /// Run a workspace tool off the reactor — Python's `asyncio.to_thread`.
    async fn blocking<F>(&self, args: &Map<String, Value>, tool: F) -> Result<Value, ToolError>
    where
        F: FnOnce(&Workspace, &Map<String, Value>) -> Result<Value, ToolError> + Send + 'static,
    {
        let workspace = self.workspace.clone();
        let args = args.clone();
        blocking(move || tool(&workspace, &args)).await
    }

    /// The same, for the two tools rooted in the knowledge garden.
    async fn in_vault<F>(&self, args: &Map<String, Value>, tool: F) -> Result<Value, ToolError>
    where
        F: FnOnce(&std::path::Path, &Map<String, Value>) -> Result<Value, ToolError>
            + Send
            + 'static,
    {
        let brain_dir = self.config.brain_dir.clone();
        let args = args.clone();
        blocking(move || tool(&brain_dir, &args)).await
    }
}

/// `pre_tool`, off the reactor.
///
/// A user hook is a **subprocess** with a twenty-second ceiling, and the trait
/// is synchronous because Python's is. Running it on the event loop would stall
/// every other request behind somebody's shell script (the v10.9.0 lesson), so
/// the sink is called from a blocking thread like every other syscall here.
async fn pre_tool(
    hooks: &Arc<dyn HookSink>,
    tool: &str,
    keys: Vec<String>,
    scope: &CallScope,
) -> Result<(), String> {
    let (hooks, tool, scope) = (Arc::clone(hooks), tool.to_string(), scope.clone());
    match tokio::task::spawn_blocking(move || hooks.pre_tool(&tool, &keys, &scope)).await {
        Ok(verdict) => verdict,
        // "A misbehaving hook never breaks the dispatch" (`_run_one`): a sink
        // that panicked did not block, and the tool runs.
        Err(_) => Ok(()),
    }
}

/// `post_tool`, off the reactor, awaited so the run is recorded before the
/// result is returned — Python fires it before it hands the value back.
async fn post_tool(
    hooks: &Arc<dyn HookSink>,
    tool: &str,
    status: &str,
    detail: String,
    scope: &CallScope,
) {
    let (hooks, tool, status, scope) = (
        Arc::clone(hooks),
        tool.to_string(),
        status.to_string(),
        scope.clone(),
    );
    let _ = tokio::task::spawn_blocking(move || {
        hooks.post_tool(&tool, &status, &detail, &scope);
    })
    .await;
}

/// `asyncio.to_thread`: file tools open files, and this server has one event
/// loop for every user (the v10.9.0 lesson, in Rust).
async fn blocking<F>(tool: F) -> Result<Value, ToolError>
where
    F: FnOnce() -> Result<Value, ToolError> + Send + 'static,
{
    match tokio::task::spawn_blocking(tool).await {
        Ok(result) => result,
        Err(join) => Err(ToolError::tool(format!("native tool task failed: {join}"))),
    }
}

impl ToolHost for NativeTools {
    fn handles(&self, tool: &str) -> bool {
        is_native(tool)
    }

    fn execute<'a>(&'a self, call: NativeCall<'a>) -> ToolFuture<'a> {
        Box::pin(async move {
            match self.run(call).await {
                Ok(result) => ToolOutcome::Result(result),
                // The seam answers `{"error": str(exc)}` with a 200 for a tool
                // that refused, and the loop records the message on the step.
                Err(error) => ToolOutcome::Error(error.message),
            }
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::sync::Mutex;

    fn host() -> (tempfile::TempDir, NativeTools) {
        let dir = tempfile::tempdir().expect("tempdir");
        let workspace = Workspace::new(dir.path().join("agent_workspace")).expect("workspace");
        let config = ToolConfig {
            brain_dir: dir.path().join("brain"),
            role: "owner".into(),
            ..ToolConfig::default()
        };
        let host = NativeTools::new(workspace, config, WorkerClient::new("http://127.0.0.1:1"));
        (dir, host)
    }

    fn call<'a>(
        tool: &'a str,
        args: &'a Map<String, Value>,
        policy: &'a ToolPolicy,
        scope: &'a CallScope,
    ) -> NativeCall<'a> {
        NativeCall {
            tool,
            args,
            policy,
            scope,
        }
    }

    fn args(value: Value) -> Map<String, Value> {
        value.as_object().expect("object").clone()
    }

    #[test]
    fn the_native_set_is_the_verdict_table_and_nothing_else() {
        assert_eq!(MUTATING_TOOLS.len(), 18);
        assert_eq!(RENDER_TOOLS.len(), 4);
        for table in [MUTATING_TOOLS.to_vec(), RENDER_TOOLS.to_vec()] {
            let mut sorted = table.clone();
            sorted.sort_unstable();
            assert_eq!(table, sorted, "the tables are binary-searched");
        }
        for compute in [
            "read_file",
            "list_dir",
            "grep",
            "todo_read",
            "local_read",
            "read_document",
            "knowledge_search",
            "obsidian_tree",
            "git_status",
            "computer_screenshot",
            "computer_status",
            "vision_analyze",
            "network_status",
            "clear_history",
        ] {
            assert!(!is_native(compute), "{compute} stays a worker call");
        }
        for mutating in MUTATING_TOOLS.iter().chain(RENDER_TOOLS.iter()) {
            if crate::in_set(&desktop::POINTER_TOOLS, mutating) {
                continue;
            }
            assert!(is_native(mutating), "{mutating}");
        }
    }

    /// §4b: actuation is a worker capability, so the loop must not claim it.
    #[test]
    fn the_six_pointer_tools_go_to_the_worker() {
        assert_eq!(desktop::POINTER_TOOLS.len(), 6);
        for pointer in desktop::POINTER_TOOLS {
            assert!(
                crate::in_set(&MUTATING_TOOLS, pointer),
                "{pointer} is still a mutating handler in Python"
            );
            assert!(
                !is_native(pointer),
                "{pointer} actuates through pyautogui; a native refusal would \
                 only take the capability away from an install that has it"
            );
        }
        // The two openers next to them stay native: `open`/`xdg-open`/`start`
        // is a subprocess, not an optional Python dependency.
        assert!(is_native("computer_open_app"));
        assert!(is_native("computer_open_url"));
        assert_eq!(
            MUTATING_TOOLS.iter().filter(|tool| is_native(tool)).count(),
            12,
            "twelve of the eighteen mutating handlers run here"
        );
    }

    #[tokio::test]
    async fn a_write_runs_natively_and_reports_the_handler_result() {
        let (_dir, host) = host();
        let policy = ToolPolicy::default();
        let scope = CallScope::default();
        let arguments = args(json!({"path": "note.md", "content": "hello"}));
        let outcome = host
            .execute(call("write_file", &arguments, &policy, &scope))
            .await;
        assert_eq!(
            outcome,
            ToolOutcome::Result(json!({"path": "note.md", "bytes": 5}))
        );
        assert_eq!(
            std::fs::read_to_string(host.workspace().root().join("note.md")).expect("file"),
            "hello"
        );
    }

    #[tokio::test]
    async fn a_refusal_is_an_error_outcome_with_the_python_message() {
        let (_dir, host) = host();
        let policy = ToolPolicy::default();
        let scope = CallScope::default();
        let arguments = args(json!({"path": "../escape.md", "content": "x"}));
        assert_eq!(
            host.execute(call("write_file", &arguments, &policy, &scope))
                .await,
            ToolOutcome::Error("Path escapes the agent workspace.".into())
        );
    }

    #[tokio::test]
    async fn the_role_check_runs_before_the_tool_does() {
        let (dir, host) = host();
        let restricted = NativeTools::new(
            host.workspace().clone(),
            ToolConfig {
                role: "user".into(),
                ..host.config().clone()
            },
            WorkerClient::new("http://127.0.0.1:1"),
        );
        let exec = ToolPolicy {
            risk: "exec".into(),
            ..ToolPolicy::default()
        };
        let scope = CallScope::default();
        let arguments = args(json!({"command": "ls"}));
        assert_eq!(
            restricted
                .execute(call("run_command", &arguments, &exec, &scope))
                .await,
            ToolOutcome::Error("'run_command' 툴은 관리자 전용입니다.".into())
        );
        drop(dir);
    }

    #[derive(Debug, Default)]
    struct RecordingHooks {
        events: Mutex<Vec<String>>,
        block: Mutex<Option<String>>,
    }

    impl HookSink for RecordingHooks {
        fn pre_tool(
            &self,
            tool: &str,
            arg_keys: &[String],
            _scope: &CallScope,
        ) -> Result<(), String> {
            // Sorted for the assertion's sake, not the hook's: `Map`'s iteration
            // order is insertion order when `lattice-retrieval` pulls
            // `serde_json/preserve_order` into a workspace build and sorted when
            // this crate is built alone. Python sends the argument *names*, and
            // that is what a hook reads.
            let mut arg_keys = arg_keys.to_vec();
            arg_keys.sort();
            self.events
                .lock()
                .expect("lock")
                .push(format!("pre:{tool}:{}", arg_keys.join(",")));
            match self.block.lock().expect("lock").clone() {
                Some(reason) => Err(reason),
                None => Ok(()),
            }
        }

        fn post_tool(&self, tool: &str, status: &str, detail: &str, _scope: &CallScope) {
            self.events
                .lock()
                .expect("lock")
                .push(format!("post:{tool}:{status}:{detail}"));
        }
    }

    #[tokio::test]
    async fn a_wired_hook_sink_sees_both_ends_and_can_block() {
        let (_dir, host) = host();
        let hooks = Arc::new(RecordingHooks::default());
        let host = host.clone().with_hooks(hooks.clone());
        let policy = ToolPolicy::default();
        let scope = CallScope::default();

        let ok = args(json!({"path": "a.md", "content": "x"}));
        host.execute(call("write_file", &ok, &policy, &scope)).await;
        let bad = args(json!({"path": "../out", "content": "x"}));
        host.execute(call("write_file", &bad, &policy, &scope))
            .await;
        assert_eq!(
            hooks.events.lock().expect("lock").clone(),
            vec![
                "pre:write_file:content,path".to_string(),
                "post:write_file:ok:".to_string(),
                "pre:write_file:content,path".to_string(),
                "post:write_file:error:Path escapes the agent workspace.".to_string(),
            ],
            "post_tool fires on the error path too"
        );

        *hooks.block.lock().expect("lock") = Some("blocked by policy".into());
        assert_eq!(
            host.execute(call("write_file", &ok, &policy, &scope)).await,
            ToolOutcome::Error("blocked by policy".into())
        );
        assert!(
            !host.workspace().root().join("blocked.md").exists(),
            "a blocked pre_tool hook stops the write"
        );
    }

    #[tokio::test]
    async fn an_unknown_tool_never_pretends_to_have_run() {
        let (_dir, host) = host();
        let policy = ToolPolicy::default();
        let scope = CallScope::default();
        assert!(!host.handles("read_file"));
        assert_eq!(
            host.execute(call("read_file", &Map::new(), &policy, &scope))
                .await,
            ToolOutcome::Error("Unknown tool: read_file".into())
        );
    }

    #[test]
    fn the_config_defaults_are_pythons_defaults() {
        let config = ToolConfig::from_env();
        assert_eq!(config.role, "user");
        assert_eq!(config.blocked_write_prefixes.len(), 8);
        assert!(config.brain_dir.ends_with(".ltcai-brain") || config.brain_dir.is_absolute());
        let configured = ToolConfig::default()
            .with_role(Some("owner".into()))
            .with_blocked_write_prefixes(vec!["/srv/".into()]);
        assert_eq!(configured.role, "owner");
        assert_eq!(configured.blocked_write_prefixes, vec!["/srv/".to_string()]);
        assert_eq!(
            ToolConfig::default().with_role(None).role,
            "user",
            "an absent role keeps the safe default"
        );
    }
}
