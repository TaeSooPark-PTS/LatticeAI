//! The production [`HookSink`]: user hooks fire for native tools.
//!
//! v11.6.0 ported the `pre_tool` → execute → `post_tool` lifecycle into
//! `lattice-agent` ([`lattice_agent::tools::HookSink`]) and left it unwired,
//! because the registry that owns `hooks.json` lives here and this crate is
//! *above* that one in the dependency graph (`lattice-platform` →
//! `lattice-agent`, never the other way). This module is the adapter that
//! closes it: a [`HooksStore`] on one side, the loop's trait on the other, and
//! the host handing one to the other at construction.
//!
//! ## What it mirrors (`lattice_brain.runtime.hooks`)
//!
//! * `dispatch_tool` — `pre_tool` fires with `{tool, args_keys, source}` for
//!   the event `tool.<name>`, and a blocking hook is Python's `PermissionError`,
//!   which the seam answers as `{"error": str(exc)}`. `post_tool` fires on
//!   **both** outcomes, carrying `detail` only on the error path.
//! * `run_hooks` — every *enabled* hook of the kind, in registry order, one
//!   recorded run each, and a `pre_*` block short-circuits the rest
//!   (fail-closed gate semantics).
//! * `_run_command` — the user hook's `command` is `shlex.split` (CPython's
//!   lexer, [`lattice_agent::pyshlex`]), run as a subprocess with the context
//!   JSON on stdin and in `LATTICE_HOOK_CONTEXT`, under a **replaced**
//!   environment of eleven variables. Exit 0 is `ok`; a non-zero exit or a
//!   timeout *blocks* a `pre_*` hook and is merely recorded for any other kind.
//! * `_record_run` — the same thirteen-key record `POST /api/hooks/run` writes,
//!   into the same `hooks_runs.json`, so `GET /api/hooks/runs` shows a hook that
//!   fired for a native tool exactly as it shows one fired from the UI.
//!
//! ## What it deliberately does not do
//!
//! Platform built-ins (`managed: "platform"`, `source: "builtin"`) are **not**
//! run here. In Python each bound its owning subsystem's behaviour, and all
//! three that touch tools are decisions this process has already made before a
//! tool is dispatched: `builtin:tool-permission-gate` blocks only on a `deny`
//! policy, which no entry in the governance table carries; the sensitive-data
//! guard classifies and never blocks; the `brain-event-triggers` `post_tool`
//! runner fires only for `tool.kg_ingest.*`, which no registered handler
//! produces. Recording them as "advisory" runs would put a line in the run log
//! for work that did not happen. The permission kernel, the role check and the
//! sandbox are where those three answers actually come from, and they run
//! whether or not a hook is registered.

use std::io::{Read, Write};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use lattice_agent::tools::{CallScope, HookSink};
use serde_json::{json, Value};

use super::HooksStore;
use crate::governance::review_queue::now_iso;

/// `HooksRegistry(command_timeout=20.0)`.
const COMMAND_TIMEOUT: Duration = Duration::from_secs(20);

/// `str(exc)[:500]` — every `detail` Python records is capped here.
const DETAIL_CHARS: usize = 500;

/// `proc.stdout[:4000]`.
const OUTPUT_CHARS: usize = 4000;

/// The environment a command hook inherits — `_run_command`'s allowlist.
///
/// Provider keys, database credentials, session secrets and arbitrary
/// `PYTHON*`/`NODE*` injection variables from the server process must never
/// reach a child. What is left is the minimum for executable lookup,
/// home-relative tools, locale handling, temporary files and Windows startup.
const ALLOWED_ENV: [&str; 11] = [
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "TMP",
    "TEMP",
    "SYSTEMROOT",
    "WINDIR",
    "PATHEXT",
];

/// The lifecycle, over the registry that owns `hooks.json`.
#[derive(Clone)]
pub struct NativeHookSink {
    hooks: HooksStore,
    /// `dispatch_tool(source=…)` — `"agent"` for the loop.
    source: String,
    /// `HooksRegistry(command_timeout=…)`, a parameter there and here.
    timeout: Duration,
}

impl std::fmt::Debug for NativeHookSink {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("NativeHookSink")
            .field("source", &self.source)
            .finish()
    }
}

impl NativeHookSink {
    /// A sink over `hooks`, attributing every run to `source`.
    pub fn new(hooks: HooksStore, source: impl Into<String>) -> Self {
        Self {
            hooks,
            source: source.into(),
            timeout: COMMAND_TIMEOUT,
        }
    }

    /// A shorter ceiling than the twenty seconds Python defaults to.
    pub fn with_timeout(mut self, timeout: Duration) -> Self {
        self.timeout = timeout;
        self
    }

    /// `run_hooks(kind, context)` — returns the block reason, when one blocks.
    fn dispatch(
        &self,
        kind: &str,
        tool: &str,
        payload: Value,
        scope: &CallScope,
    ) -> Option<String> {
        let event = format!("tool.{tool}");
        let context = json!({
            "kind": kind,
            "event": event,
            "payload": payload,
            "metadata": {},
            "user_email": scope.user_email,
            "workspace_id": scope.workspace_id,
            "blocked": false,
            "block_reason": "",
            "notes": [],
        });
        let context_json = context.to_string();
        for hook in self.hooks.enabled_of_kind(kind) {
            // User-registered hooks only — see the module header.
            if hook.get("source").and_then(Value::as_str) != Some("user") {
                continue;
            }
            let hook_id = hook.get("id").and_then(Value::as_str).unwrap_or("");
            let started = Instant::now();
            let outcome = run_one(&hook, kind, hook_id, &context_json, self.timeout);
            let duration_ms = started.elapsed().as_millis().min(i64::MAX as u128) as i64;
            self.hooks.record_run(json!({
                "hook_id": hook_id,
                "name": hook.get("name").cloned().unwrap_or_else(|| json!(hook_id)),
                "kind": hook.get("kind").cloned().unwrap_or_else(|| json!(kind)),
                "status": outcome.status,
                "detail": outcome.detail,
                "output": outcome.output,
                "duration_ms": duration_ms,
                "blocked": outcome.blocked,
                "source": hook.get("source").cloned().unwrap_or_else(|| json!("user")),
                "binding": hook.get("binding").cloned().unwrap_or_else(|| json!("advisory")),
                "started_at": now_iso(),
                "target_event": event,
                "target_kind": kind,
            }));
            if outcome.blocked {
                // `context.block(res.detail or f"{hook['id']} blocked {event}")`.
                return Some(if outcome.detail.is_empty() {
                    format!("{hook_id} blocked {event}")
                } else {
                    outcome.detail
                });
            }
        }
        None
    }
}

impl HookSink for NativeHookSink {
    fn pre_tool(&self, tool: &str, arg_keys: &[String], scope: &CallScope) -> Result<(), String> {
        let payload = json!({
            "tool": tool,
            "args_keys": arg_keys,
            "source": self.source,
        });
        match self.dispatch("pre_tool", tool, payload, scope) {
            Some(reason) => Err(reason),
            None => Ok(()),
        }
    }

    fn post_tool(&self, tool: &str, status: &str, detail: &str, scope: &CallScope) {
        let mut payload = json!({
            "tool": tool,
            "status": status,
            "source": self.source,
        });
        if status == "error" {
            payload["detail"] = json!(detail);
        }
        // A `post_tool` hook cannot block: `_run_command` only gates `pre_*`.
        let _ = self.dispatch("post_tool", tool, payload, scope);
    }
}

/// `HookResult`'s four decided fields.
struct Outcome {
    status: &'static str,
    detail: String,
    output: String,
    blocked: bool,
}

impl Outcome {
    fn advisory() -> Self {
        Self {
            status: "advisory",
            detail: String::new(),
            output: String::new(),
            blocked: false,
        }
    }

    fn error(detail: String) -> Self {
        Self {
            status: "error",
            detail: truncate(&detail, DETAIL_CHARS),
            output: String::new(),
            blocked: false,
        }
    }
}

/// `_run_one(hook, context)` — a command runs, anything else is advisory.
fn run_one(
    hook: &Value,
    kind: &str,
    hook_id: &str,
    context_json: &str,
    timeout: Duration,
) -> Outcome {
    let command = hook
        .get("command")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    if command.is_empty() {
        // No bound runner and no command → listed and ordered only.
        return Outcome::advisory();
    }
    run_command(&command, kind, hook_id, context_json, timeout)
}

/// `_run_command(hook, context)`.
fn run_command(
    command: &str,
    kind: &str,
    hook_id: &str,
    context_json: &str,
    timeout: Duration,
) -> Outcome {
    let argv = match lattice_agent::pyshlex::split(command) {
        Ok(argv) => argv,
        Err(error) => return Outcome::error(format!("invalid command: {}", error.message())),
    };
    let Some((program, arguments)) = argv.split_first() else {
        return Outcome {
            status: "skipped",
            detail: "empty command".into(),
            output: String::new(),
            blocked: false,
        };
    };
    // A `pre_*` hook gates the pending action; every other kind only reports.
    let is_gate = kind.starts_with("pre_");

    let mut child = match Command::new(program)
        .args(arguments)
        .env_clear()
        .envs(
            std::env::vars().filter(|(key, _)| ALLOWED_ENV.contains(&key.to_uppercase().as_str())),
        )
        .env("LATTICE_HOOK_KIND", kind)
        .env("LATTICE_HOOK_EVENT", hook_event(context_json, kind))
        .env("LATTICE_HOOK_ID", hook_id)
        .env("LATTICE_HOOK_CONTEXT", context_json)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
    {
        Ok(child) => child,
        Err(error) => return Outcome::error(error.to_string()),
    };

    // stdin and the two pipes are drained on their own threads: a child that
    // writes more than a pipe buffer before reading its input would otherwise
    // deadlock against a parent that is waiting for it to exit.
    if let Some(mut stdin) = child.stdin.take() {
        let payload = context_json.to_string();
        std::thread::spawn(move || {
            let _ = stdin.write_all(payload.as_bytes());
        });
    }
    let stdout = child.stdout.take().map(drain);
    let stderr = child.stderr.take().map(drain);

    let deadline = Instant::now() + timeout;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break Some(status),
            Err(error) => return Outcome::error(error.to_string()),
            Ok(None) => {
                if Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    break None;
                }
                std::thread::sleep(Duration::from_millis(5));
            }
        }
    };
    let joined = |handle: Option<std::thread::JoinHandle<String>>| {
        handle
            .and_then(|handle| handle.join().ok())
            .unwrap_or_default()
    };
    let out = joined(stdout);
    let err = joined(stderr);

    let Some(status) = status else {
        return Outcome {
            status: if is_gate { "blocked" } else { "error" },
            detail: format!("timed out after {}s", timeout.as_secs()),
            output: String::new(),
            blocked: is_gate,
        };
    };
    let output = truncate(&out, OUTPUT_CHARS);
    if status.success() {
        return Outcome {
            status: "ok",
            detail: String::new(),
            output,
            blocked: false,
        };
    }
    let code = status.code().unwrap_or(-1);
    let detail = if !err.trim().is_empty() {
        err
    } else if !out.trim().is_empty() {
        out
    } else {
        format!("exit code {code}")
    };
    Outcome {
        status: if is_gate { "blocked" } else { "error" },
        detail: truncate(detail.trim(), DETAIL_CHARS),
        output,
        blocked: is_gate,
    }
}

/// Read a pipe to the end on its own thread, decoded the way `text=True` does.
fn drain<R: Read + Send + 'static>(mut pipe: R) -> std::thread::JoinHandle<String> {
    std::thread::spawn(move || {
        let mut buffer = Vec::new();
        let _ = pipe.read_to_end(&mut buffer);
        String::from_utf8_lossy(&buffer).into_owned()
    })
}

/// `context.event`, read back off the JSON the child is handed.
fn hook_event(context_json: &str, fallback: &str) -> String {
    serde_json::from_str::<Value>(context_json)
        .ok()
        .and_then(|context| {
            context
                .get("event")
                .and_then(Value::as_str)
                .map(str::to_string)
        })
        .unwrap_or_else(|| fallback.to_string())
}

/// `text[:limit]` — characters, as every Python slice in the original.
fn truncate(text: &str, limit: usize) -> String {
    match text.char_indices().nth(limit) {
        Some((offset, _)) => text[..offset].to_string(),
        None => text.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use lattice_agent::policy::ToolPolicy;
    use lattice_agent::sandbox::Workspace;
    use lattice_agent::tools::{NativeCall, NativeTools, ToolConfig, ToolHost};
    use lattice_agent::worker::{ToolOutcome, WorkerClient};
    use std::sync::Arc;

    /// A registry, a workspace and a host wired to the sink — the production
    /// shape, over a scratch directory (never the developer's `~/.ltcai`).
    struct Wired {
        _dir: tempfile::TempDir,
        hooks: HooksStore,
        tools: NativeTools,
        workspace: Workspace,
    }

    fn wire() -> Wired {
        let dir = tempfile::tempdir().expect("tempdir");
        let hooks = HooksStore::open(dir.path());
        let workspace = Workspace::new(dir.path().join("agent_workspace")).expect("workspace");
        let sink =
            NativeHookSink::new(hooks.clone(), "agent").with_timeout(Duration::from_millis(400));
        let tools = NativeTools::new(
            workspace.clone(),
            ToolConfig {
                brain_dir: dir.path().join("brain"),
                role: "owner".into(),
                ..ToolConfig::default()
            },
            WorkerClient::new("http://127.0.0.1:1"),
        )
        .with_hooks(Arc::new(sink));
        Wired {
            _dir: dir,
            hooks,
            tools,
            workspace,
        }
    }

    async fn write(wired: &Wired, path: &str, content: &str) -> ToolOutcome {
        let args = serde_json::json!({"path": path, "content": content});
        let args = args.as_object().expect("object").clone();
        let policy = ToolPolicy::default();
        let scope = CallScope {
            user_email: Some("owner@example.com".into()),
            workspace_id: Some("ws-1".into()),
        };
        wired
            .tools
            .execute(NativeCall {
                tool: "write_file",
                args: &args,
                policy: &policy,
                scope: &scope,
            })
            .await
    }

    fn runs(wired: &Wired) -> Vec<Value> {
        wired
            .hooks
            .recent_runs(50, None)
            .get("runs")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
    }

    fn register(wired: &Wired, name: &str, kind: &str, command: &str) {
        wired
            .hooks
            .register(name, kind, "", command, None, true)
            .expect("register");
    }

    #[tokio::test]
    async fn an_advisory_user_hook_records_a_run_for_a_native_tool() {
        let wired = wire();
        register(&wired, "Watcher", "pre_tool", "");
        assert!(matches!(
            write(&wired, "note.md", "hello").await,
            ToolOutcome::Result(_)
        ));

        let runs = runs(&wired);
        // Newest first: the seeded `brain-event-triggers` post_tool hook, then
        // the registered pre_tool one. No built-in rows: this build does not
        // run them for a native tool and does not claim it did.
        assert_eq!(runs.len(), 2, "{runs:#?}");
        assert_eq!(
            runs[0]["hook_id"],
            json!(crate::governance::hooks::BRAIN_EVENT_TRIGGERS)
        );
        assert_eq!(runs[0]["target_kind"], json!("post_tool"));
        assert_eq!(runs[1]["hook_id"], json!("user:watcher"));
        for run in &runs {
            assert_eq!(run["status"], json!("advisory"));
            assert_eq!(run["blocked"], json!(false));
            assert_eq!(run["source"], json!("user"));
            assert_eq!(run["target_event"], json!("tool.write_file"));
            assert!(run["started_at"].as_str().is_some_and(|at| at.len() >= 19));
        }
        assert!(
            !runs.iter().any(|run| run["hook_id"]
                .as_str()
                .is_some_and(|id| id.starts_with("builtin:"))),
            "platform built-ins are retired for native tools"
        );
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn a_failing_pre_tool_command_blocks_the_write_and_says_why() {
        let wired = wire();
        register(
            &wired,
            "Gate",
            "pre_tool",
            "/bin/sh -c 'echo refused by policy >&2; exit 3'",
        );
        assert_eq!(
            write(&wired, "blocked.md", "hello").await,
            ToolOutcome::Error("refused by policy".into()),
            "the hook's stderr is the refusal the transcript records"
        );
        assert!(
            !wired.workspace.root().join("blocked.md").exists(),
            "a blocked pre_tool hook stops the write"
        );

        let runs = runs(&wired);
        // One row: the gate blocked, so nothing after it ran — not the rest of
        // the `pre_tool` chain and not `post_tool`.
        assert_eq!(runs.len(), 1, "{runs:#?}");
        assert_eq!(runs[0]["status"], json!("blocked"));
        assert_eq!(runs[0]["blocked"], json!(true));
        assert_eq!(runs[0]["detail"], json!("refused by policy"));
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn a_successful_command_hook_keeps_its_output_and_lets_the_write_through() {
        let wired = wire();
        register(&wired, "Logger", "pre_tool", "/bin/sh -c 'echo seen'");
        register(&wired, "After", "post_tool", "/bin/sh -c 'cat >/dev/null'");
        assert!(matches!(
            write(&wired, "note.md", "hello").await,
            ToolOutcome::Result(_)
        ));
        assert_eq!(
            std::fs::read_to_string(wired.workspace.root().join("note.md")).expect("file"),
            "hello"
        );
        let runs = runs(&wired);
        assert_eq!(runs.len(), 3, "{runs:#?}");
        let logger = runs
            .iter()
            .find(|run| run["hook_id"] == json!("user:logger"))
            .expect("logger");
        assert_eq!(logger["status"], json!("ok"));
        assert_eq!(logger["output"], json!("seen\n"));
        assert_eq!(logger["target_kind"], json!("pre_tool"));
        let after = runs
            .iter()
            .find(|run| run["hook_id"] == json!("user:after"))
            .expect("after");
        assert_eq!(after["status"], json!("ok"));
        assert_eq!(after["target_kind"], json!("post_tool"));
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn a_post_tool_failure_is_recorded_and_never_blocks() {
        let wired = wire();
        register(&wired, "Noisy", "post_tool", "/bin/sh -c 'exit 9'");
        assert!(matches!(
            write(&wired, "note.md", "hello").await,
            ToolOutcome::Result(_)
        ));
        assert!(wired.workspace.root().join("note.md").exists());
        let noisy = runs(&wired)
            .into_iter()
            .find(|run| run["hook_id"] == json!("user:noisy"))
            .expect("noisy");
        assert_eq!(noisy["status"], json!("error"), "only `pre_*` gates");
        assert_eq!(noisy["blocked"], json!(false));
        assert_eq!(noisy["detail"], json!("exit code 9"));
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn a_hook_that_hangs_fails_closed_for_a_gate() {
        let wired = wire();
        register(&wired, "Slow", "pre_tool", "/bin/sh -c 'sleep 30'");
        assert_eq!(
            write(&wired, "slow.md", "hello").await,
            ToolOutcome::Error("timed out after 0s".into()),
            "a gate that cannot answer refuses"
        );
        assert!(!wired.workspace.root().join("slow.md").exists());
        assert_eq!(runs(&wired)[0]["blocked"], json!(true));
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn the_child_sees_the_context_and_not_the_servers_secrets() {
        let wired = wire();
        std::env::set_var("LATTICEAI_SECRET_TOKEN", "super-secret");
        let script = wired._dir.path().join("probe.sh");
        std::fs::write(
            &script,
            "#!/bin/sh\ncat\necho \"env=${LATTICEAI_SECRET_TOKEN:-absent}\"\n\
echo \"kind=$LATTICE_HOOK_KIND event=$LATTICE_HOOK_EVENT id=$LATTICE_HOOK_ID\"\n",
        )
        .expect("script");
        std::fs::set_permissions(
            &script,
            <std::fs::Permissions as std::os::unix::fs::PermissionsExt>::from_mode(0o755),
        )
        .expect("chmod");
        register(&wired, "Probe", "pre_tool", &script.display().to_string());
        assert!(matches!(
            write(&wired, "note.md", "hello").await,
            ToolOutcome::Result(_)
        ));
        let output = runs(&wired)
            .into_iter()
            .find(|run| run["hook_id"] == json!("user:probe"))
            .expect("probe")["output"]
            .as_str()
            .unwrap_or_default()
            .to_string();
        std::env::remove_var("LATTICEAI_SECRET_TOKEN");
        // The payload Python sends: the argument *names*, never their values.
        assert!(output.contains("\"tool\":\"write_file\""), "{output}");
        assert!(
            output.contains("\"args_keys\":[\"path\",\"content\"]")
                || output.contains("\"args_keys\":[\"content\",\"path\"]"),
            "{output}"
        );
        assert!(
            !output.contains("hello"),
            "the content is not handed to a hook: {output}"
        );
        assert!(
            output.contains("\"user_email\":\"owner@example.com\""),
            "{output}"
        );
        assert!(output.contains("\"workspace_id\":\"ws-1\""), "{output}");
        assert!(
            output.contains("env=absent"),
            "a hook never inherits a server secret: {output}"
        );
        assert!(
            output.contains("kind=pre_tool event=tool.write_file id=user:probe"),
            "{output}"
        );
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn a_missing_or_unparseable_command_is_an_error_rather_than_a_block() {
        let wired = wire();
        register(&wired, "Quoted", "pre_tool", "/bin/sh -c 'unterminated");
        register(&wired, "Absent", "pre_tool", "/nonexistent/hook-binary");
        assert!(
            matches!(
                write(&wired, "note.md", "hello").await,
                ToolOutcome::Result(_)
            ),
            "a broken hook never blocks the write"
        );
        let runs = runs(&wired);
        let quoted = runs
            .iter()
            .find(|run| run["hook_id"] == json!("user:quoted"))
            .expect("quoted");
        assert_eq!(quoted["status"], json!("error"));
        assert_eq!(
            quoted["detail"],
            json!("invalid command: No closing quotation")
        );
        let absent = runs
            .iter()
            .find(|run| run["hook_id"] == json!("user:absent"))
            .expect("absent");
        assert_eq!(absent["status"], json!("error"));
        assert_eq!(absent["blocked"], json!(false));
    }

    #[tokio::test]
    async fn a_disabled_hook_does_not_fire() {
        let wired = wire();
        wired
            .hooks
            .register("Off", "pre_tool", "", "", None, false)
            .expect("register");
        assert!(matches!(
            write(&wired, "note.md", "hello").await,
            ToolOutcome::Result(_)
        ));
        assert!(
            !runs(&wired)
                .iter()
                .any(|run| run["hook_id"] == json!("user:off")),
            "the registry's enabled flag is what decides"
        );
    }

    #[tokio::test]
    async fn the_run_log_is_bounded_the_way_pythons_deque_is() {
        let wired = wire();
        register(&wired, "Watcher", "pre_tool", "");
        for index in 0..60 {
            write(&wired, &format!("note-{index}.md"), "hello").await;
        }
        let runs = runs(&wired);
        assert_eq!(runs.len(), 50, "the query limit still applies");
        let total = wired
            .hooks
            .recent_runs(500, None)
            .get("total")
            .and_then(Value::as_i64)
            .unwrap_or_default();
        assert_eq!(
            total,
            crate::governance::hooks::store::RUN_LOG_LIMIT as i64,
            "120 fires, a hundred kept — `hooks_runs.json` cannot grow forever"
        );
    }
}
