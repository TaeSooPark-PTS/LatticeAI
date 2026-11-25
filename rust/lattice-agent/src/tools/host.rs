use std::future::Future;
use std::path::PathBuf;
use std::pin::Pin;
use std::sync::Arc;

use serde_json::{Map, Value};

use super::{authorize, desktop, files, local, render, scaffold, shell, vault, web};
use crate::kernel::policy::ToolPolicy;
use crate::seam::worker::{ToolOutcome, WorkerClient};
use crate::tools::sandbox::{ToolError, Workspace};

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

pub const RENDER_TOOLS: [&str; 4] = ["create_docx", "create_pdf", "create_pptx", "create_xlsx"];

pub const WEB_AND_INSPECT_TOOLS: [&str; 3] = ["computer_ui_inspect", "web_fetch", "web_search"];

pub fn is_native(tool: &str) -> bool {
    if crate::kernel::policy::in_set(&desktop::POINTER_TOOLS, tool) {
        return false;
    }
    crate::kernel::policy::in_set(&MUTATING_TOOLS, tool)
        || crate::kernel::policy::in_set(&RENDER_TOOLS, tool)
        || crate::kernel::policy::in_set(&WEB_AND_INSPECT_TOOLS, tool)
}

#[derive(Debug, Clone, Default)]
pub struct CallScope {
    pub user_email: Option<String>,
    pub workspace_id: Option<String>,
}

#[derive(Debug, Clone, Copy)]
pub struct NativeCall<'a> {
    pub tool: &'a str,
    pub args: &'a Map<String, Value>,
    pub policy: &'a ToolPolicy,
    pub scope: &'a CallScope,
}

pub type ToolFuture<'a> = Pin<Box<dyn Future<Output = ToolOutcome> + Send + 'a>>;

pub trait ToolHost: std::fmt::Debug + Send + Sync {
    fn handles(&self, tool: &str) -> bool;
    fn execute<'a>(&'a self, call: NativeCall<'a>) -> ToolFuture<'a>;
}

pub trait HookSink: std::fmt::Debug + Send + Sync {
    fn pre_tool(&self, tool: &str, arg_keys: &[String], scope: &CallScope) -> Result<(), String>;
    fn post_tool(&self, tool: &str, status: &str, detail: &str, scope: &CallScope);
}

#[derive(Debug, Clone)]
pub struct ToolConfig {
    pub brain_dir: PathBuf,
    pub role: String,
    pub blocked_write_prefixes: Vec<String>,
}

impl Default for ToolConfig {
    fn default() -> Self {
        Self {
            brain_dir: vault::default_brain_dir(),
            role: "user".into(),
            blocked_write_prefixes: crate::kernel::policy::default_blocked_write_prefixes(),
        }
    }
}

impl ToolConfig {
    pub fn from_env() -> Self {
        Self::default()
    }

    pub fn with_role(mut self, role: Option<String>) -> Self {
        if let Some(role) = role {
            self.role = role;
        }
        self
    }

    pub fn with_blocked_write_prefixes(mut self, prefixes: Vec<String>) -> Self {
        self.blocked_write_prefixes = prefixes;
        self
    }
}

#[derive(Debug, Clone)]
pub struct NativeTools {
    workspace: Workspace,
    config: ToolConfig,
    worker: WorkerClient,
    hooks: Option<Arc<dyn HookSink>>,
}

impl NativeTools {
    pub fn new(workspace: Workspace, config: ToolConfig, worker: WorkerClient) -> Self {
        Self {
            workspace,
            config,
            worker,
            hooks: None,
        }
    }

    pub fn with_hooks(mut self, hooks: Arc<dyn HookSink>) -> Self {
        self.hooks = Some(hooks);
        self
    }

    pub fn workspace(&self) -> &Workspace {
        &self.workspace
    }

    pub fn config(&self) -> &ToolConfig {
        &self.config
    }

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

    async fn dispatch(&self, call: NativeCall<'_>) -> Result<Value, ToolError> {
        match call.tool {
            "write_file" => self.blocking(call.args, files::write_file).await,
            "edit_file" => self.blocking(call.args, files::edit_file).await,
            "todo_write" => self.blocking(call.args, files::todo_write).await,
            "create_web_project" => self.blocking(call.args, scaffold::create_web_project).await,
            "knowledge_save" => {
                self.in_vault_scoped(call.args, call.scope, vault::knowledge_save)
                    .await
            }
            "obsidian_save" => {
                self.in_vault_scoped(call.args, call.scope, vault::obsidian_save)
                    .await
            }
            "local_write" => {
                let prefixes = self.config.blocked_write_prefixes.clone();
                let args = call.args.clone();
                let workspace = self.workspace.clone();
                blocking(move || {
                    if workspace.is_confined() {
                        local::local_write_confined(&workspace, &prefixes, &args)
                    } else {
                        local::local_write(&prefixes, &args)
                    }
                })
                .await
            }
            "run_command" => shell::run_command(&self.workspace, call.args).await,
            "build_project" => shell::build_project(&self.workspace, call.args).await,
            "deploy_project" => shell::deploy_project(&self.workspace, call.args).await,
            "computer_open_app" => desktop::computer_open_app(call.args).await,
            "computer_open_url" => desktop::computer_open_url(call.args).await,
            "computer_ui_inspect" => desktop::computer_ui_inspect(call.args).await,
            "web_fetch" => web::web_fetch(call.args).await,
            "web_search" => web::web_search(call.args).await,
            pointer if crate::kernel::policy::in_set(&desktop::POINTER_TOOLS, pointer) => {
                desktop::pointer_tool(pointer, call.args)
            }
            creator if render::is_creator(creator) => {
                render::create_document(&self.worker, &self.workspace, creator, call.args).await
            }
            other => Err(ToolError::tool(format!("Unknown tool: {other}"))),
        }
    }

    async fn blocking<F>(&self, args: &Map<String, Value>, tool: F) -> Result<Value, ToolError>
    where
        F: FnOnce(&Workspace, &Map<String, Value>) -> Result<Value, ToolError> + Send + 'static,
    {
        let workspace = self.workspace.clone();
        let args = args.clone();
        blocking(move || tool(&workspace, &args)).await
    }

    async fn in_vault_scoped<F>(
        &self,
        args: &Map<String, Value>,
        scope: &CallScope,
        tool: F,
    ) -> Result<Value, ToolError>
    where
        F: FnOnce(&std::path::Path, &Map<String, Value>) -> Result<Value, ToolError>
            + Send
            + 'static,
    {
        let brain_dir = self.config.brain_dir.clone();
        let mut args = args.clone();
        if let Some(ref user) = scope.user_email {
            if !user.trim().is_empty() {
                args.insert("user_email".into(), Value::String(user.clone()));
            }
        }
        if let Some(ref ws) = scope.workspace_id {
            if !ws.trim().is_empty() {
                args.insert("workspace_id".into(), Value::String(ws.clone()));
            }
        }
        blocking(move || tool(&brain_dir, &args)).await
    }
}

async fn pre_tool(
    hooks: &Arc<dyn HookSink>,
    tool: &str,
    keys: Vec<String>,
    scope: &CallScope,
) -> Result<(), String> {
    let (hooks, tool, scope) = (Arc::clone(hooks), tool.to_string(), scope.clone());
    match tokio::task::spawn_blocking(move || hooks.pre_tool(&tool, &keys, &scope)).await {
        Ok(verdict) => verdict,
        Err(_) => Ok(()),
    }
}

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
            if crate::kernel::policy::in_set(&desktop::POINTER_TOOLS, mutating) {
                continue;
            }
            assert!(is_native(mutating), "{mutating}");
        }
    }

    #[test]
    fn the_six_pointer_tools_go_to_the_worker() {
        assert_eq!(desktop::POINTER_TOOLS.len(), 6);
        for pointer in desktop::POINTER_TOOLS {
            assert!(
                crate::kernel::policy::in_set(&MUTATING_TOOLS, pointer),
                "{pointer} is still a mutating handler in Python"
            );
            assert!(
                !is_native(pointer),
                "{pointer} actuates through pyautogui; a native refusal would \
                 only take the capability away from an install that has it"
            );
        }
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

    #[test]
    fn web_and_inspect_tools_are_sorted_and_native() {
        assert_eq!(WEB_AND_INSPECT_TOOLS.len(), 3);
        let mut sorted = WEB_AND_INSPECT_TOOLS.to_vec();
        sorted.sort_unstable();
        assert_eq!(WEB_AND_INSPECT_TOOLS.to_vec(), sorted);
        for tool in WEB_AND_INSPECT_TOOLS {
            assert!(is_native(tool), "{tool} must be dispatched natively");
        }
    }
}
