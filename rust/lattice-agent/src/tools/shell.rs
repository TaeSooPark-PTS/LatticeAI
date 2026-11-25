use std::time::Duration;

use serde_json::{json, Map, Value};

use crate::tools::args;
use crate::tools::sandbox::{ToolError, Workspace, MAX_COMMAND_OUTPUT, MAX_COMMAND_SECONDS};

pub const MAX_BUILD_SECONDS: u64 = 180;
pub const MAX_DEPLOY_SECONDS: u64 = 300;

pub const BUILD_SCRIPT_NAMES: [&str; 4] = ["build", "compile", "test", "typecheck"];

pub const DEPLOY_SCRIPT_NAMES: [&str; 11] = [
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

pub async fn run_command(
    workspace: &Workspace,
    arguments: &Map<String, Value>,
) -> Result<Value, ToolError> {
    let command = args::required_str(arguments, "command")?;
    let cwd = args::truthy_str(arguments, "cwd", ".")?;
    let validated = crate::tools::command::validate(workspace, &command, Some(&cwd))?;
    let execution = crate::tools::exec::execute(
        workspace,
        &validated,
        Duration::from_secs(MAX_COMMAND_SECONDS),
    )
    .await?;
    Ok(json!({
        "command": execution.command,
        "cwd": execution.cwd,
        "returncode": execution.returncode,
        "stdout": execution.stdout,
        "stderr": execution.stderr,
    }))
}

pub async fn build_project(
    workspace: &Workspace,
    arguments: &Map<String, Value>,
) -> Result<Value, ToolError> {
    let script = args::optional_str(arguments, "script", "build")?;
    run_script(
        workspace,
        arguments,
        &script,
        &BUILD_SCRIPT_NAMES,
        MAX_BUILD_SECONDS,
    )
    .await
}

pub async fn deploy_project(
    workspace: &Workspace,
    arguments: &Map<String, Value>,
) -> Result<Value, ToolError> {
    let script = args::optional_str(arguments, "script", "deploy")?;
    run_script(
        workspace,
        arguments,
        &script,
        &DEPLOY_SCRIPT_NAMES,
        MAX_DEPLOY_SECONDS,
    )
    .await
}

async fn run_script(
    workspace: &Workspace,
    arguments: &Map<String, Value>,
    script: &str,
    allowed: &[&str],
    timeout: u64,
) -> Result<Value, ToolError> {
    if !allowed.contains(&script) {
        return Err(ToolError::tool(format!(
            "Script is not allowed here: {script}"
        )));
    }
    let cwd = args::truthy_str(arguments, "cwd", ".")?;
    let workdir = workspace.resolve(&cwd)?;
    if !workdir.is_dir() {
        return Err(ToolError::tool("Working directory does not exist."));
    }
    let body = package_script(workspace, &workdir, script)?;

    let mut command = tokio::process::Command::new("npm");
    command
        .arg("run")
        .arg(script)
        .current_dir(&workdir)
        .env_clear()
        .kill_on_drop(true);
    for key in [
        "PATH", "HOME", "USER", "TMPDIR", "TEMP", "TMP", "NODE_ENV", "LANG", "LC_ALL",
    ] {
        if let Ok(val) = std::env::var(key) {
            command.env(key, val);
        }
    }
    let output = match tokio::time::timeout(Duration::from_secs(timeout), command.output()).await {
        Ok(Ok(output)) => output,
        Ok(Err(error)) => {
            return Err(ToolError::tool(format!("npm could not start: {error}")));
        }
        Err(_) => {
            return Err(ToolError::tool(format!(
                "npm run {script} timed out after {timeout} seconds."
            )))
        }
    };
    Ok(json!({
        "command": format!("npm run {script}"),
        "cwd": workspace.relative(&workdir),
        "script_body": body,
        "returncode": output.status.code().unwrap_or(-1),
        "stdout": tail(&String::from_utf8_lossy(&output.stdout)),
        "stderr": tail(&String::from_utf8_lossy(&output.stderr)),
    }))
}

fn package_script(
    workspace: &Workspace,
    workdir: &std::path::Path,
    script: &str,
) -> Result<String, ToolError> {
    let manifest = workdir.join("package.json");
    let scripts = if manifest.exists() {
        let resolved = workspace.resolve(&workspace.relative(&manifest))?;
        let raw_bytes = crate::tools::safe_io::safe_read_bytes(
            workspace,
            &resolved,
            crate::tools::sandbox::MAX_FILE_BYTES,
        )?;
        let raw = String::from_utf8(raw_bytes)
            .map_err(|error| ToolError::tool(format!("Could not parse package.json: {error}")))?;
        let parsed: Value = serde_json::from_str(&raw)
            .map_err(|error| ToolError::tool(format!("Could not parse package.json: {error}")))?;
        parsed
            .get("scripts")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default()
    } else {
        Map::new()
    };
    match scripts.get(script) {
        Some(body) => Ok(crate::parse::pystr::py_str(body)),
        None => Err(ToolError::tool(format!(
            "package.json does not define a '{script}' script."
        ))),
    }
}

fn tail(text: &str) -> String {
    let length = text.chars().count();
    if length <= MAX_COMMAND_OUTPUT {
        return text.to_string();
    }
    text.chars().skip(length - MAX_COMMAND_OUTPUT).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn workspace() -> (tempfile::TempDir, Workspace) {
        let dir = tempfile::tempdir().expect("tempdir");
        let workspace = Workspace::new(dir.path().join("agent_workspace")).expect("workspace");
        (dir, workspace)
    }

    fn args(value: Value) -> Map<String, Value> {
        value.as_object().expect("object").clone()
    }

    #[tokio::test]
    async fn a_read_only_command_runs_and_returns_pythons_five_keys() {
        let (_dir, workspace) = workspace();
        std::fs::write(workspace.root().join("a.txt"), "alpha\n").expect("seed");
        let result = run_command(&workspace, &args(json!({"command": "ls"})))
            .await
            .expect("ls");
        let mut keys: Vec<&String> = result.as_object().expect("object").keys().collect();
        keys.sort();
        assert_eq!(keys, ["command", "cwd", "returncode", "stderr", "stdout"]);
        assert_eq!(result["command"], "ls");
        assert_eq!(result["cwd"], ".");
        assert_eq!(result["returncode"], 0);
        assert!(
            result["stdout"].as_str().expect("stdout").contains("a.txt"),
            "{result}"
        );
    }

    #[tokio::test]
    async fn the_validator_refusals_are_the_command_modules() {
        let (_dir, workspace) = workspace();
        for (command, message) in [
            ("rm -rf /", "Command is not allowed: rm"),
            (
                "git status",
                "Use the read-only git_status, git_diff, git_log, or git_show tools.",
            ),
            ("ls | wc", "Shell operators are not allowed."),
            (
                "cat ../outside",
                "Path traversal in command arguments is not allowed: ../outside",
            ),
            ("", "Command is empty."),
        ] {
            assert_eq!(
                run_command(&workspace, &args(json!({"command": command})))
                    .await
                    .expect_err(command)
                    .message,
                message
            );
        }
        assert_eq!(
            run_command(&workspace, &Map::new())
                .await
                .expect_err("missing")
                .message,
            "'command'"
        );
    }

    #[tokio::test]
    async fn a_missing_working_directory_is_refused_before_the_spawn() {
        let (_dir, workspace) = workspace();
        assert_eq!(
            run_command(
                &workspace,
                &args(json!({"command": "ls", "cwd": "nope/deeper"}))
            )
            .await
            .expect_err("no cwd")
            .message,
            "Working directory does not exist."
        );
    }

    #[tokio::test]
    async fn the_script_allowlists_are_checked_first() {
        let (_dir, workspace) = workspace();
        assert_eq!(
            build_project(&workspace, &args(json!({"script": "deploy"})))
                .await
                .expect_err("not a build script")
                .message,
            "Script is not allowed here: deploy"
        );
        assert_eq!(
            deploy_project(&workspace, &args(json!({"script": "build"})))
                .await
                .expect_err("not a deploy script")
                .message,
            "Script is not allowed here: build"
        );
    }

    #[tokio::test]
    async fn a_workspace_without_the_script_says_so_rather_than_running_npm() {
        let (_dir, workspace) = workspace();
        assert_eq!(
            build_project(&workspace, &Map::new())
                .await
                .expect_err("no package.json")
                .message,
            "package.json does not define a 'build' script."
        );
        std::fs::write(
            workspace.root().join("package.json"),
            r#"{"scripts": {"test": "vitest run"}}"#,
        )
        .expect("manifest");
        assert_eq!(
            build_project(&workspace, &args(json!({"script": "build"})))
                .await
                .expect_err("absent script")
                .message,
            "package.json does not define a 'build' script."
        );
        assert_eq!(
            package_script(&workspace, workspace.root(), "test").expect("body"),
            "vitest run"
        );
    }

    #[test]
    fn a_broken_manifest_is_a_named_parse_failure() {
        let (_dir, workspace) = workspace();
        std::fs::write(workspace.root().join("package.json"), "{not json").expect("manifest");
        let error = package_script(&workspace, workspace.root(), "build").expect_err("broken");
        assert!(
            error.message.starts_with("Could not parse package.json: "),
            "{}",
            error.message
        );
    }

    #[test]
    fn the_script_allowlists_are_the_python_sets() {
        assert_eq!(BUILD_SCRIPT_NAMES.len(), 4);
        assert_eq!(DEPLOY_SCRIPT_NAMES.len(), 11);
        for (list, name) in [
            (BUILD_SCRIPT_NAMES.to_vec(), "build"),
            (DEPLOY_SCRIPT_NAMES.to_vec(), "deploy"),
        ] {
            let mut sorted = list.clone();
            sorted.sort_unstable();
            assert_eq!(list, sorted, "{name} list must stay sorted");
        }
        assert_eq!(MAX_BUILD_SECONDS, 180);
        assert_eq!(MAX_DEPLOY_SECONDS, 300);
    }

    #[test]
    fn the_output_cap_keeps_the_tail() {
        let long = "x".repeat(MAX_COMMAND_OUTPUT + 10);
        assert_eq!(tail(&long).chars().count(), MAX_COMMAND_OUTPUT);
        assert_eq!(tail("short"), "short");
        let korean = "한".repeat(MAX_COMMAND_OUTPUT + 5);
        assert_eq!(tail(&korean).chars().count(), MAX_COMMAND_OUTPUT);
    }
}
