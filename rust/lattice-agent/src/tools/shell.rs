//! `run_command` / `build_project` / `deploy_project` — the exec tools.
//!
//! `run_command` is already ported twice over: [`crate::command::validate`] is
//! the validator and [`crate::exec::execute`] is the spawn, both pinned by the
//! kernel goldens. All this module adds is the handler's result shape — the
//! five keys Python returns, and no more, because the transcript records them
//! verbatim and the critic reads them.
//!
//! `build_project` / `deploy_project` are the npm-script runners. They are
//! deliberately **not** routed through the `run_command` sandbox: Python spawns
//! `npm run <script>` with the inherited environment (npm needs its own PATH,
//! node, and the user's registry configuration), gated instead by an allowlist
//! of script names and by the script having to exist in `package.json`.

use std::time::Duration;

use serde_json::{json, Map, Value};

use crate::sandbox::{ToolError, Workspace, MAX_COMMAND_OUTPUT, MAX_COMMAND_SECONDS};
use crate::tools::args;

/// `MAX_BUILD_SECONDS`.
pub const MAX_BUILD_SECONDS: u64 = 180;
/// `MAX_DEPLOY_SECONDS`.
pub const MAX_DEPLOY_SECONDS: u64 = 300;

/// `BUILD_SCRIPT_NAMES`, sorted.
pub const BUILD_SCRIPT_NAMES: [&str; 4] = ["build", "compile", "test", "typecheck"];

/// `DEPLOY_SCRIPT_NAMES`, sorted.
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

/// `run_command(command, cwd=None)`.
pub async fn run_command(
    workspace: &Workspace,
    arguments: &Map<String, Value>,
) -> Result<Value, ToolError> {
    let command = args::required_str(arguments, "command")?;
    let cwd = args::truthy_str(arguments, "cwd", ".")?;
    let validated = crate::command::validate(workspace, &command, Some(&cwd))?;
    let execution = crate::exec::execute(
        workspace,
        &validated,
        Duration::from_secs(MAX_COMMAND_SECONDS),
    )
    .await?;
    // Python returns exactly these five keys. `Execution` also carries the two
    // truncation flags, which are a native addition and stay out of the tool
    // result so the transcript keeps the shape the critic was trained on.
    Ok(json!({
        "command": execution.command,
        "cwd": execution.cwd,
        "returncode": execution.returncode,
        "stdout": execution.stdout,
        "stderr": execution.stderr,
    }))
}

/// `build_project(cwd=None, script="build")`.
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

/// `deploy_project(cwd=None, script="deploy")`.
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

/// `_run_script`: allowlist → working directory → `package.json` → `npm run`.
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
    let body = package_script(&workdir, script)?;

    let mut command = tokio::process::Command::new("npm");
    command
        .arg("run")
        .arg(script)
        .current_dir(&workdir)
        .kill_on_drop(true);
    let output = match tokio::time::timeout(Duration::from_secs(timeout), command.output()).await {
        Ok(Ok(output)) => output,
        Ok(Err(error)) => {
            // Python raises FileNotFoundError, which the seam answers as a 500.
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

/// `_load_package_scripts` plus the "is it defined?" check.
fn package_script(workdir: &std::path::Path, script: &str) -> Result<String, ToolError> {
    let manifest = workdir.join("package.json");
    let scripts = if manifest.exists() {
        let raw = std::fs::read_to_string(&manifest)
            .map_err(|error| ToolError::tool(format!("Could not parse package.json: {error}")))?;
        let parsed: Value = serde_json::from_str(&raw)
            .map_err(|error| ToolError::tool(format!("Could not parse package.json: {error}")))?;
        // `data.get("scripts") or {}`, and a non-dict is an empty table.
        parsed
            .get("scripts")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default()
    } else {
        // No manifest is an empty table, not an error — the "does it define
        // this script?" refusal below is the one Python raises.
        Map::new()
    };
    match scripts.get(script) {
        // `{str(k): str(v)}` — a non-string body stringifies rather than fails.
        Some(body) => Ok(crate::pystr::py_str(body)),
        None => Err(ToolError::tool(format!(
            "package.json does not define a '{script}' script."
        ))),
    }
}

/// `output[-MAX_COMMAND_OUTPUT:]` — the tail, counted in characters.
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
        // The five Python keys and no others — `Execution`'s two truncation
        // flags are a native addition that must not reach the transcript.
        //
        // Compared as a *set*: `lattice-retrieval` enables
        // `serde_json/preserve_order`, and cargo unifies features across a
        // workspace build, so `Map`'s iteration order is insertion order when
        // this crate is built beside it and sorted when it is built alone. Which
        // keys exist is the contract; their order in the map is not.
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
            package_script(workspace.root(), "test").expect("body"),
            "vitest run"
        );
    }

    #[test]
    fn a_broken_manifest_is_a_named_parse_failure() {
        let (_dir, workspace) = workspace();
        std::fs::write(workspace.root().join("package.json"), "{not json").expect("manifest");
        let error = package_script(workspace.root(), "build").expect_err("broken");
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
