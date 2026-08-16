//! Git, shell, network, and project script tools.

use axum::extract::State;
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use lattice_agent::{command, is_circuit_breaker};
use serde_json::{json, Value};

use crate::mcp::{detail, parse_json_object, require_admin, require_user};

use super::{relative, resolve, tool_err, tool_ok, ToolExecError, ToolsState};

pub(crate) fn run_git_status(state: &ToolsState) -> Result<Value, ToolExecError> {
    run_git(state, &["status", "--short"], ".")
}

pub(crate) async fn git_status(State(state): State<ToolsState>, headers: HeaderMap) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    match run_git_status(&state) {
        Ok(result) => tool_ok(&state.workspace, result),
        Err(error) => error.into_response(&json!({})),
    }
}

pub(crate) async fn git_diff(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let parsed = if body.is_empty() {
        json!({})
    } else {
        match parse_json_object(&body) {
            Ok(v) => v,
            Err(r) => return r,
        }
    };
    let cwd = parsed.get("cwd").and_then(Value::as_str).unwrap_or(".");
    git_run(&state, &["diff", "--"], cwd)
}

pub(crate) async fn git_log(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let parsed = if body.is_empty() {
        json!({})
    } else {
        match parse_json_object(&body) {
            Ok(v) => v,
            Err(r) => return r,
        }
    };
    let max = parsed
        .get("max_count")
        .and_then(Value::as_i64)
        .unwrap_or(5)
        .clamp(1, 20);
    let cwd = parsed.get("cwd").and_then(Value::as_str).unwrap_or(".");
    let flag = format!("--max-count={max}");
    git_run(&state, &["log", &flag, "--oneline", "--decorate"], cwd)
}

pub(crate) async fn git_show(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let parsed = if body.is_empty() {
        json!({})
    } else {
        match parse_json_object(&body) {
            Ok(v) => v,
            Err(r) => return r,
        }
    };
    let revision = parsed
        .get("revision")
        .and_then(Value::as_str)
        .unwrap_or("HEAD");
    if revision.starts_with('-')
        || revision.contains("..")
        || revision.contains(':')
        || revision.contains('/')
        || revision.contains('\\')
    {
        return tool_err("Revision is not allowed.");
    }
    let cwd = parsed.get("cwd").and_then(Value::as_str).unwrap_or(".");
    git_run(
        &state,
        &["show", "--stat", "--oneline", "--decorate", revision],
        cwd,
    )
}

fn run_git(state: &ToolsState, args: &[&str], cwd: &str) -> Result<Value, ToolExecError> {
    let workdir = state
        .workspace
        .resolve(cwd)
        .map_err(|e| ToolExecError::Message(e.message))?;
    if !workdir.exists() || !workdir.is_dir() {
        return Err(ToolExecError::Message(
            "Working directory does not exist.".into(),
        ));
    }
    let output = std::process::Command::new("git")
        .args(args)
        .current_dir(&workdir)
        .output();
    match output {
        Ok(out) => {
            let rel = if workdir == *state.workspace.root() {
                ".".into()
            } else {
                relative(&state.workspace, &workdir)
            };
            let stdout = String::from_utf8_lossy(&out.stdout);
            let stderr = String::from_utf8_lossy(&out.stderr);
            let stdout = tail(&stdout, 12_000);
            let stderr = tail(&stderr, 12_000);
            Ok(json!({
                "command": format!("git {}", args.join(" ")),
                "cwd": rel,
                "returncode": out.status.code().unwrap_or(1),
                "stdout": stdout,
                "stderr": stderr,
            }))
        }
        Err(_) => Err(ToolExecError::Message(
            "Git command timed out after 30 seconds.".into(),
        )),
    }
}

fn git_run(state: &ToolsState, args: &[&str], cwd: &str) -> Response {
    match run_git(state, args, cwd) {
        Ok(result) => tool_ok(&state.workspace, result),
        Err(error) => error.into_response(&json!({})),
    }
}

fn tail(s: &str, n: usize) -> String {
    if s.len() <= n {
        s.to_string()
    } else {
        s[s.len() - n..].to_string()
    }
}

pub(crate) async fn run_command(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_admin(&state.auth, &headers) {
        return r;
    }
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    let command = parsed.get("command").and_then(Value::as_str).unwrap_or("");
    let cwd = parsed.get("cwd").and_then(Value::as_str).unwrap_or(".");
    let policy = lattice_agent::policy::ToolPolicy {
        risk: "exec".into(),
        destructive: false,
        shell: true,
        network: false,
        auto_approve: false,
        sandbox: "workspace".into(),
        rollback: "none".into(),
        capability: None,
        scope: None,
    };
    if let Some(reason) = is_circuit_breaker("run_command", &policy, &{
        let mut m = serde_json::Map::new();
        m.insert("command".into(), json!(command));
        m
    }) {
        return detail(
            StatusCode::FORBIDDEN,
            &format!("'run_command' 차단: {reason}"),
        );
    }
    match command::validate(&state.workspace, command, Some(cwd)) {
        Ok(validated) => {
            let output = std::process::Command::new(&validated.executable)
                .args(&validated.args)
                .current_dir(&validated.workdir)
                .output();
            match output {
                Ok(out) => {
                    let rel = if cwd.is_empty() { "." } else { cwd };
                    tool_ok(
                        &state.workspace,
                        json!({
                            "command": command,
                            "cwd": rel,
                            "returncode": out.status.code().unwrap_or(1),
                            "stdout": String::from_utf8_lossy(&out.stdout),
                            "stderr": String::from_utf8_lossy(&out.stderr),
                        }),
                    )
                }
                Err(err) => tool_err(&err.to_string()),
            }
        }
        Err(err) => detail(
            StatusCode::FORBIDDEN,
            &format!("'run_command' 차단: {}", err.message),
        ),
    }
}

pub(crate) async fn network_status(
    State(state): State<ToolsState>,
    headers: HeaderMap,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    tool_ok(
        &state.workspace,
        json!({"hostname": hostname(), "note": "sampled locally"}),
    )
}

fn hostname() -> String {
    std::process::Command::new("hostname")
        .output()
        .ok()
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .map(|s| s.trim().to_string())
        .unwrap_or_default()
}

pub(crate) async fn build_project(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_admin(&state.auth, &headers) {
        return r;
    }
    let parsed = if body.is_empty() {
        json!({})
    } else {
        match parse_json_object(&body) {
            Ok(v) => v,
            Err(r) => return r,
        }
    };
    let cwd = parsed.get("cwd").and_then(Value::as_str).unwrap_or(".");
    let script = parsed
        .get("script")
        .and_then(Value::as_str)
        .unwrap_or("build");
    run_npm_script(&state, cwd, script, &BUILD_SCRIPT_NAMES, MAX_BUILD_SECONDS).await
}

pub(crate) async fn deploy_project(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_admin(&state.auth, &headers) {
        return r;
    }
    let parsed = if body.is_empty() {
        json!({})
    } else {
        match parse_json_object(&body) {
            Ok(v) => v,
            Err(r) => return r,
        }
    };
    let cwd = parsed.get("cwd").and_then(Value::as_str).unwrap_or(".");
    let script = parsed
        .get("script")
        .and_then(Value::as_str)
        .unwrap_or("deploy");
    run_npm_script(
        &state,
        cwd,
        script,
        &DEPLOY_SCRIPT_NAMES,
        MAX_DEPLOY_SECONDS,
    )
    .await
}

/// `BUILD_SCRIPT_NAMES`, sorted. Matches the agent-side allowlist.
const BUILD_SCRIPT_NAMES: [&str; 4] = ["build", "compile", "test", "typecheck"];
/// `DEPLOY_SCRIPT_NAMES`, sorted. Matches the agent-side allowlist.
const DEPLOY_SCRIPT_NAMES: [&str; 11] = [
    "build:exe",
    "build:installer",
    "build:pkg",
    "deploy",
    "dist",
    "make",
    "package",
    "package:mac",
    "package:win",
    "preview",
    "release",
];
const MAX_BUILD_SECONDS: u64 = 180;
const MAX_DEPLOY_SECONDS: u64 = 300;

async fn run_npm_script(
    state: &ToolsState,
    cwd: &str,
    script: &str,
    allowed: &[&str],
    timeout: u64,
) -> Response {
    if !allowed.contains(&script) {
        return tool_err(&format!("Script is not allowed here: {script}"));
    }
    let workdir = match resolve(&state.workspace, cwd) {
        Ok(p) => p,
        Err(r) => return r,
    };
    if !workdir.is_dir() {
        return tool_err("Working directory does not exist.");
    }
    let body = match package_script(&workdir, script) {
        Ok(body) => body,
        Err(message) => return tool_err(&message),
    };
    let mut command = tokio::process::Command::new("npm");
    command
        .arg("run")
        .arg(script)
        .current_dir(&workdir)
        .kill_on_drop(true);
    let output =
        match tokio::time::timeout(std::time::Duration::from_secs(timeout), command.output()).await
        {
            Ok(Ok(output)) => output,
            Ok(Err(error)) => return tool_err(&format!("npm could not start: {error}")),
            Err(_) => {
                return tool_err(&format!(
                    "npm run {script} timed out after {timeout} seconds."
                ))
            }
        };
    let rel = if workdir == *state.workspace.root() {
        ".".to_string()
    } else {
        relative(&state.workspace, &workdir)
    };
    tool_ok(
        &state.workspace,
        json!({
            "command": format!("npm run {script}"),
            "cwd": rel,
            "script_body": body,
            "returncode": output.status.code().unwrap_or(1),
            "stdout": tail(&String::from_utf8_lossy(&output.stdout), 12_000),
            "stderr": tail(&String::from_utf8_lossy(&output.stderr), 12_000),
        }),
    )
}

fn package_script(workdir: &std::path::Path, script: &str) -> Result<String, String> {
    let manifest = workdir.join("package.json");
    if !manifest.exists() {
        return Err(format!("package.json does not define a '{script}' script."));
    }
    let raw = std::fs::read_to_string(&manifest)
        .map_err(|_| format!("package.json does not define a '{script}' script."))?;
    let parsed: Value = serde_json::from_str(&raw)
        .map_err(|_| format!("package.json does not define a '{script}' script."))?;
    match parsed
        .get("scripts")
        .and_then(Value::as_object)
        .and_then(|scripts| scripts.get(script))
    {
        Some(body) => Ok(match body {
            Value::String(text) => text.clone(),
            other => other.to_string(),
        }),
        None => Err(format!("package.json does not define a '{script}' script.")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use lattice_agent::sandbox::Workspace;

    #[test]
    fn a_missing_script_is_the_only_missing_error() {
        let dir = tempfile::tempdir().expect("tmp");
        let workspace = Workspace::new(dir.path()).expect("ws");
        assert_eq!(
            package_script(workspace.root(), "build").unwrap_err(),
            "package.json does not define a 'build' script."
        );
        std::fs::write(
            workspace.root().join("package.json"),
            r#"{"scripts": {"test": "true"}}"#,
        )
        .expect("manifest");
        assert_eq!(
            package_script(workspace.root(), "build").unwrap_err(),
            "package.json does not define a 'build' script."
        );
        assert_eq!(package_script(workspace.root(), "test").unwrap(), "true");
    }

    #[tokio::test]
    async fn a_defined_script_runs_through_the_workspace_sandbox() {
        let dir = tempfile::tempdir().expect("tmp");
        let workspace = Workspace::new(dir.path().join("ws")).expect("ws");
        std::fs::write(
            workspace.root().join("package.json"),
            r#"{"scripts": {"build": "node -e \"process.exit(0)\""}}"#,
        )
        .expect("manifest");
        let auth = {
            let mut env = std::collections::HashMap::new();
            env.insert("LATTICEAI_REQUIRE_AUTH".into(), "0".into());
            env.insert(
                "LATTICEAI_DATA_DIR".into(),
                dir.path().to_string_lossy().into_owned(),
            );
            lattice_auth::AuthState::new(lattice_auth::AuthConfig::from_map(&env, None))
        };
        let state = ToolsState::new(auth, workspace, dir.path());
        let response =
            run_npm_script(&state, ".", "build", &BUILD_SCRIPT_NAMES, MAX_BUILD_SECONDS).await;
        assert_eq!(response.status(), StatusCode::OK, "defined script must run");
    }
}
