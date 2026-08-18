//! Setup wizard install-item execution (SSE frames, no fake "complete").
//!
//! brew/pip/uv stay manual unless the request names the item in
//! `execute`. The command that then runs is re-derived here from the
//! item id and a closed verb — never the client-supplied string.

use std::collections::HashSet;
use std::process::{Command, Stdio};
use std::sync::Arc;
use std::time::Duration;

use serde_json::{json, Map, Value};

use super::SetupState;

/// How long a consented brew/pip/uv invocation may run.
const EXECUTE_TIMEOUT: Duration = Duration::from_secs(60);

/// Captured stdout/stderr from a consented invocation.
#[derive(Debug, Clone)]
pub struct RunnerOutput {
    pub stdout: String,
    pub stderr: String,
    pub code: i32,
}

/// Test seam for consented brew/pip/uv execution.
pub type InstallRunner =
    Arc<dyn Fn(&AllowlistedCommand) -> Result<RunnerOutput, String> + Send + Sync>;

/// A brew/pip/uv invocation the plan itself produced.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AllowlistedCommand {
    pub program: String,
    pub args: Vec<String>,
}

impl AllowlistedCommand {
    pub fn display(&self) -> String {
        let mut parts = vec![self.program.as_str()];
        parts.extend(self.args.iter().map(String::as_str));
        parts.join(" ")
    }
}

pub(crate) struct InstallOutcome {
    pub(crate) status: String,
    pub(crate) frames: Vec<Vec<(&'static str, Value)>>,
}

pub(crate) async fn execute_install_item(
    state: &SetupState,
    item: &Value,
    execute: &HashSet<String>,
) -> InstallOutcome {
    let item_id = item.get("id").and_then(Value::as_str).unwrap_or("unknown");
    let name = item.get("name").and_then(Value::as_str).unwrap_or(item_id);
    let action = item.get("action").and_then(Value::as_object);
    let atype = action
        .and_then(|a| a.get("type"))
        .and_then(Value::as_str)
        .unwrap_or("");
    let classified = classify_install_action(atype, action, item);
    match classified {
        InstallKind::Ready => InstallOutcome {
            status: "skipped".into(),
            frames: vec![vec![
                ("id", json!(item_id)),
                ("status", json!("skipped")),
                ("msg", json!(format!("{name} — 이미 준비됨"))),
            ]],
        },
        InstallKind::Auth { url } => InstallOutcome {
            status: "auth".into(),
            frames: {
                (state.opener)(&url);
                vec![
                    vec![
                        ("id", json!(item_id)),
                        ("status", json!("auth")),
                        ("msg", json!("브라우저에서 인증 페이지를 엽니다...")),
                        ("auth_url", json!(url)),
                    ],
                    vec![
                        ("id", json!(item_id)),
                        ("status", json!("waiting")),
                        ("msg", json!("브라우저에서 인증 완료 후 계속하세요")),
                    ],
                ]
            },
        },
        InstallKind::Manual { command } => {
            if execute.contains(item_id) {
                match derived_allowlisted(atype, action, item_id) {
                    Some(cmd) => run_consented(state, item_id, name, cmd).await,
                    None => refused_item(item_id, name),
                }
            } else {
                InstallOutcome {
                    status: "manual".into(),
                    frames: vec![vec![
                        ("id", json!(item_id)),
                        ("status", json!("manual")),
                        ("msg", json!(command.clone())),
                        ("command", json!(command)),
                    ]],
                }
            }
        }
        InstallKind::PrepareModel { model, engine } => {
            prepare_model_item(state, item_id, name, &model, &engine).await
        }
        InstallKind::Unknown { detail } => {
            if execute.contains(item_id) {
                return refused_item(item_id, name);
            }
            InstallOutcome {
                status: "error".into(),
                frames: vec![
                    vec![
                        ("id", json!(item_id)),
                        ("status", json!("starting")),
                        ("msg", json!(format!("{name} 준비 중..."))),
                    ],
                    vec![
                        ("id", json!(item_id)),
                        ("status", json!("error")),
                        ("msg", json!(detail)),
                    ],
                ],
            }
        }
    }
}

pub(crate) fn refused_unknown_id(item_id: &str) -> InstallOutcome {
    refused_item(item_id, item_id)
}

fn refused_item(item_id: &str, name: &str) -> InstallOutcome {
    InstallOutcome {
        status: "failed".into(),
        frames: vec![vec![
            ("id", json!(item_id)),
            ("status", json!("failed")),
            (
                "msg",
                json!(format!(
                    "{name} — refused: not an allowlisted brew/pip/uv item"
                )),
            ),
        ]],
    }
}

async fn run_consented(
    state: &SetupState,
    item_id: &str,
    name: &str,
    cmd: AllowlistedCommand,
) -> InstallOutcome {
    let mut frames = vec![vec![
        ("id", json!(item_id)),
        ("status", json!("starting")),
        ("msg", json!(format!("{name} 실행 중: {}", cmd.display()))),
        ("command", json!(cmd.display())),
    ]];
    let output = if let Some(runner) = state.runner.as_ref() {
        runner(&cmd)
    } else {
        let spawned = cmd.clone();
        match tokio::task::spawn_blocking(move || spawn_allowlisted(&spawned)).await {
            Ok(result) => result,
            Err(err) => Err(format!("install task failed: {err}")),
        }
    };
    match output {
        Ok(result) if result.code == 0 => {
            for line in result.stdout.lines() {
                if line.is_empty() {
                    continue;
                }
                frames.push(vec![
                    ("id", json!(item_id)),
                    ("status", json!("progress")),
                    ("msg", json!(line)),
                ]);
            }
            frames.push(vec![
                ("id", json!(item_id)),
                ("status", json!("done")),
                ("msg", json!(format!("{name} 완료"))),
            ]);
            InstallOutcome {
                status: "done".into(),
                frames,
            }
        }
        Ok(result) => {
            let tail = stderr_tail(&result.stderr);
            frames.push(vec![
                ("id", json!(item_id)),
                ("status", json!("failed")),
                ("msg", json!(format!("{name} 실패 (exit {})", result.code))),
                ("stderr", json!(tail)),
            ]);
            InstallOutcome {
                status: "failed".into(),
                frames,
            }
        }
        Err(err) => {
            frames.push(vec![
                ("id", json!(item_id)),
                ("status", json!("failed")),
                ("msg", json!(format!("{name} 실패: {err}"))),
            ]);
            InstallOutcome {
                status: "failed".into(),
                frames,
            }
        }
    }
}

fn stderr_tail(stderr: &str) -> String {
    const LIMIT: usize = 2_000;
    if stderr.len() <= LIMIT {
        stderr.to_string()
    } else {
        stderr[stderr.len() - LIMIT..].to_string()
    }
}

enum InstallKind {
    Ready,
    Auth { url: String },
    Manual { command: String },
    PrepareModel { model: String, engine: String },
    Unknown { detail: String },
}

fn classify_install_action(
    atype: &str,
    action: Option<&Map<String, Value>>,
    item: &Value,
) -> InstallKind {
    let model = action
        .and_then(|a| a.get("model").or_else(|| a.get("model_id")))
        .or_else(|| item.get("model_id"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let engine = action
        .and_then(|a| a.get("engine"))
        .and_then(Value::as_str)
        .unwrap_or("local_mlx")
        .to_string();
    let command = action
        .and_then(|a| a.get("command").or_else(|| a.get("cmd")))
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    match atype {
        "" | "ready" | "noop" => {
            if !model.is_empty() {
                InstallKind::PrepareModel { model, engine }
            } else {
                InstallKind::Ready
            }
        }
        "auth" | "url" => InstallKind::Auth {
            url: action
                .and_then(|a| a.get("url"))
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string(),
        },
        "brew" | "pip" | "uv" | "apt" | "dnf" | "pacman" | "host" | "install_package" => {
            let fallback = if command.is_empty() {
                match atype {
                    "brew" => format!(
                        "brew install {}",
                        item.get("id").and_then(Value::as_str).unwrap_or("")
                    ),
                    "pip" => format!(
                        "pip3 install {}",
                        item.get("id").and_then(Value::as_str).unwrap_or("")
                    ),
                    "uv" => format!(
                        "uv pip install {}",
                        item.get("id").and_then(Value::as_str).unwrap_or("")
                    ),
                    "apt" => format!(
                        "sudo apt-get install {}",
                        item.get("id").and_then(Value::as_str).unwrap_or("")
                    ),
                    other => format!(
                        "{other} install {}",
                        item.get("id").and_then(Value::as_str).unwrap_or("")
                    ),
                }
            } else {
                command
            };
            InstallKind::Manual { command: fallback }
        }
        "download" | "prepare_model" | "prepare-model" | "model" | "pull" => {
            if model.is_empty() {
                InstallKind::Unknown {
                    detail: "prepare-model action is missing a model id".into(),
                }
            } else {
                InstallKind::PrepareModel { model, engine }
            }
        }
        other => InstallKind::Unknown {
            detail: format!("알 수 없는 액션: {other}"),
        },
    }
}

/// Re-derive the command the plan would have generated for this item.
/// Client `command` / `cmd` strings are ignored.
fn derived_allowlisted(
    atype: &str,
    action: Option<&Map<String, Value>>,
    item_id: &str,
) -> Option<AllowlistedCommand> {
    let verb = action
        .and_then(|a| a.get("verb"))
        .and_then(Value::as_str)
        .unwrap_or("install");
    let cmd = match (atype, verb) {
        ("brew", "version") => AllowlistedCommand {
            program: "brew".into(),
            args: vec!["--version".into()],
        },
        ("pip", "version") => AllowlistedCommand {
            program: "python3".into(),
            args: vec!["-m".into(), "pip".into(), "--version".into()],
        },
        ("uv", "version") => AllowlistedCommand {
            program: "uv".into(),
            args: vec!["--version".into()],
        },
        ("brew", "install") if is_safe_package(item_id) => AllowlistedCommand {
            program: "brew".into(),
            args: vec!["install".into(), item_id.to_string()],
        },
        ("pip", "install") if is_safe_package(item_id) => AllowlistedCommand {
            program: "python3".into(),
            args: vec![
                "-m".into(),
                "pip".into(),
                "install".into(),
                item_id.to_string(),
            ],
        },
        ("uv", "install") if is_safe_package(item_id) => AllowlistedCommand {
            program: "uv".into(),
            args: vec!["pip".into(), "install".into(), item_id.to_string()],
        },
        _ => return None,
    };
    is_allowlisted(&cmd).then_some(cmd)
}

fn is_safe_package(id: &str) -> bool {
    let mut chars = id.chars();
    let Some(first) = chars.next() else {
        return false;
    };
    first.is_ascii_alphanumeric()
        && chars.all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '+' | '-' | '@'))
        && !id.contains("..")
}

/// Strict allowlist: brew/pip/pip3/uv with install or --version only.
pub fn is_allowlisted(cmd: &AllowlistedCommand) -> bool {
    let program = cmd.program.as_str();
    let args: Vec<&str> = cmd.args.iter().map(String::as_str).collect();
    match (program, args.as_slice()) {
        ("brew" | "pip" | "pip3" | "uv", ["--version"]) => true,
        ("brew", ["install", pkg]) => is_safe_package(pkg),
        ("pip" | "pip3", ["install", pkg]) => is_safe_package(pkg),
        ("uv", ["pip", "install", pkg]) => is_safe_package(pkg),
        ("python3", ["-m", "pip", "--version"]) => true,
        ("python3", ["-m", "pip", "install", pkg]) => is_safe_package(pkg),
        _ => false,
    }
}

fn spawn_allowlisted(cmd: &AllowlistedCommand) -> Result<RunnerOutput, String> {
    if !is_allowlisted(cmd) {
        return Err("command is not an allowlisted brew/pip/uv invocation".into());
    }
    let mut child = Command::new(&cmd.program)
        .args(&cmd.args)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|err| format!("failed to start {}: {err}", cmd.program))?;
    let start = std::time::Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) if start.elapsed() >= EXECUTE_TIMEOUT => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(format!("timed out after {}s", EXECUTE_TIMEOUT.as_secs()));
            }
            Ok(None) => std::thread::sleep(Duration::from_millis(20)),
            Err(err) => return Err(err.to_string()),
        }
    }
    let output = child.wait_with_output().map_err(|err| err.to_string())?;
    Ok(RunnerOutput {
        stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
        code: output.status.code().unwrap_or(1),
    })
}

async fn prepare_model_item(
    state: &SetupState,
    item_id: &str,
    name: &str,
    model: &str,
    engine: &str,
) -> InstallOutcome {
    let mut frames = vec![vec![
        ("id", json!(item_id)),
        ("status", json!("starting")),
        ("msg", json!(format!("{name} 준비 중..."))),
    ]];
    let Some(worker) = state.worker.as_ref() else {
        frames.push(vec![
            ("id", json!(item_id)),
            ("status", json!("error")),
            (
                "msg",
                json!("worker is not configured; cannot run /engines/prepare-model"),
            ),
        ]);
        return InstallOutcome {
            status: "error".into(),
            frames,
        };
    };
    let body = json!({
        "model": model,
        "engine": engine,
        "allow_download": true,
    });
    match worker
        .stream_sse("/engines/prepare-model/stream", &body)
        .await
    {
        Ok(upstream) => {
            let mut response = upstream.into_response();
            let mut saw_error = false;
            while let Ok(Some(chunk)) = response.chunk().await {
                let text = String::from_utf8_lossy(&chunk);
                if text.contains("\"error\"") || text.contains("\"status\":\"error\"") {
                    saw_error = true;
                }
                frames.push(vec![
                    ("id", json!(item_id)),
                    ("status", json!("progress")),
                    ("msg", json!(text.trim())),
                ]);
            }
            if saw_error {
                frames.push(vec![
                    ("id", json!(item_id)),
                    ("status", json!("error")),
                    ("msg", json!(format!("{name} 준비 실패"))),
                ]);
                InstallOutcome {
                    status: "error".into(),
                    frames,
                }
            } else {
                frames.push(vec![
                    ("id", json!(item_id)),
                    ("status", json!("done")),
                    ("msg", json!(format!("{name} 준비 완료"))),
                ]);
                InstallOutcome {
                    status: "done".into(),
                    frames,
                }
            }
        }
        Err(err) => {
            frames.push(vec![
                ("id", json!(item_id)),
                ("status", json!("error")),
                ("msg", json!(err.to_string())),
            ]);
            InstallOutcome {
                status: "error".into(),
                frames,
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn brew_and_pip_are_manual_with_the_exact_command() {
        let item = json!({"id": "mlx", "name": "MLX"});
        match classify_install_action("brew", None, &item) {
            InstallKind::Manual { command } => assert_eq!(command, "brew install mlx"),
            _ => panic!("expected manual, got a different kind"),
        }
        let action = serde_json::Map::from_iter([("command".into(), json!("pip3 install mlx"))]);
        match classify_install_action("pip", Some(&action), &item) {
            InstallKind::Manual { command } => assert_eq!(command, "pip3 install mlx"),
            _ => panic!("expected manual pip"),
        }
    }

    #[test]
    fn prepare_model_is_recognized_and_empty_is_ready() {
        let item = json!({"id": "model", "model_id": "mlx-community/x"});
        match classify_install_action("prepare_model", None, &item) {
            InstallKind::PrepareModel { model, .. } => {
                assert_eq!(model, "mlx-community/x");
            }
            _ => panic!("expected prepare_model"),
        }
        match classify_install_action("", None, &json!({"id": "done"})) {
            InstallKind::Ready => {}
            _ => panic!("expected ready"),
        }
    }

    #[test]
    fn derived_command_ignores_the_client_string() {
        let action =
            serde_json::Map::from_iter([("command".into(), json!("rm -rf / && pip install evil"))]);
        let cmd = derived_allowlisted("pip", Some(&action), "mlx").expect("derived");
        assert_eq!(cmd.program, "python3");
        assert_eq!(cmd.args, ["-m", "pip", "install", "mlx"]);
        assert!(is_allowlisted(&cmd));
    }

    #[test]
    fn non_allowlisted_ids_do_not_derive() {
        assert!(derived_allowlisted("apt", None, "curl").is_none());
        assert!(derived_allowlisted("host", None, "anything").is_none());
        assert!(derived_allowlisted("pip", None, "../evil").is_none());
        assert!(derived_allowlisted("pip", None, "-e").is_none());
        assert!(derived_allowlisted("brew", None, "foo; rm -rf /").is_none());
    }

    #[test]
    fn version_verb_derives_a_probe_not_an_install() {
        let action = serde_json::Map::from_iter([("verb".into(), json!("version"))]);
        let cmd = derived_allowlisted("pip", Some(&action), "pip").expect("version");
        assert_eq!(cmd.display(), "python3 -m pip --version");
    }

    #[test]
    fn spawn_runs_a_harmless_pip_version() {
        let cmd = AllowlistedCommand {
            program: "python3".into(),
            args: vec!["-m".into(), "pip".into(), "--version".into()],
        };
        let output = spawn_allowlisted(&cmd).expect("python3 -m pip --version");
        assert_eq!(output.code, 0, "{}", output.stderr);
        assert!(
            output.stdout.to_ascii_lowercase().contains("pip"),
            "stdout was {:?}",
            output.stdout
        );
    }
}
