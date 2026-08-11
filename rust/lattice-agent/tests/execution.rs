//! Native execution: the same commands Python ran, with the same answers, plus
//! the containment properties a golden cannot express.
//!
//! The goldens pin *what* a read-only command returns. The rest of this file
//! pins what the sandbox does around it: a symlink escape is actually attempted
//! against a file that really exists outside the workspace and really is
//! refused; the timeout really kills a child that would otherwise hang forever;
//! and the child really sees a replaced environment rather than the parent's.

mod common;

use std::collections::BTreeMap;
use std::time::{Duration, Instant};

use common::{assert_no_failures, cases, read_golden, with_root};
use lattice_agent::command::{validate, Validated};
use lattice_agent::exec::{
    child_command, execute, is_natively_executable, sandbox_env, which, NATIVE_EXECUTABLES,
    SAFE_EXECUTABLE_PATH,
};
use lattice_agent::sandbox::{Workspace, MAX_COMMAND_OUTPUT};
use serde_json::json;

fn workspace(dir: &tempfile::TempDir) -> Workspace {
    common::build_tree(dir.path())
}

#[tokio::test]
async fn every_execution_matches_its_python_golden() {
    let dir = tempfile::tempdir().expect("tempdir");
    let workspace = workspace(&dir);
    let golden = read_golden("execution.json");
    let rows = cases(&golden, "cases");
    let mut failures = Vec::new();

    for case in &rows {
        let key = case["key"].as_str().expect("key");
        let command = case["command"].as_str().expect("command");
        let validated = match validate(&workspace, command, case["cwd"].as_str()) {
            Ok(validated) => validated,
            Err(err) => {
                failures.push(format!("  {key}: python ran it, rust refused it ({err})"));
                continue;
            }
        };
        let result = match execute(&workspace, &validated, Duration::from_secs(30)).await {
            Ok(result) => result,
            Err(err) => {
                failures.push(format!("  {key}: execution failed ({err})"));
                continue;
            }
        };
        let expected_stdout = with_root(case["stdout"].as_str().expect("stdout"), &workspace);
        if result.stdout != expected_stdout {
            failures.push(format!(
                "  {key}: stdout differs (python {} chars, rust {} chars)",
                expected_stdout.chars().count(),
                result.stdout.chars().count()
            ));
        }
        if json!(result.returncode) != case["returncode"] {
            failures.push(format!(
                "  {key}: returncode python={} rust={}",
                case["returncode"], result.returncode
            ));
        }
        if json!(result.cwd) != case["result_cwd"] {
            failures.push(format!(
                "  {key}: cwd python={} rust={}",
                case["result_cwd"], result.cwd
            ));
        }
        // stderr is pinned only where it is identical on macOS and Linux.
        if let Some(stderr) = case.get("stderr").and_then(|value| value.as_str()) {
            if result.stderr != stderr {
                failures.push(format!(
                    "  {key}: stderr python={stderr:?} rust={:?}",
                    result.stderr
                ));
            }
        }
    }
    assert_no_failures(rows.len(), failures, "executions");
}

#[tokio::test]
async fn the_output_cap_keeps_the_tail_and_says_so() {
    let dir = tempfile::tempdir().expect("tempdir");
    let workspace = workspace(&dir);
    let validated = validate(&workspace, "cat big.txt", None).expect("valid");
    let result = execute(&workspace, &validated, Duration::from_secs(30))
        .await
        .expect("cat");
    assert_eq!(result.stdout.chars().count(), MAX_COMMAND_OUTPUT);
    assert!(
        result.stdout_truncated,
        "the caller must be told it was cut"
    );
    assert!(
        result.stdout.ends_with("0002999\n"),
        "the tail is what survives"
    );
    assert!(
        !result.stdout.starts_with("0000000\n"),
        "the head is what is dropped"
    );
}

#[tokio::test]
async fn a_symlink_escape_is_attempted_and_refused() {
    let dir = tempfile::tempdir().expect("tempdir");
    let workspace = workspace(&dir);
    // The escape target really exists and really is readable — otherwise this
    // test would pass against a workspace with nothing to steal.
    let secret = dir.path().join("outside_secret.txt");
    assert_eq!(
        std::fs::read_to_string(&secret).expect("the secret must exist"),
        "top secret\n"
    );
    assert!(workspace.root().join("escape_link").is_symlink());

    let err = validate(&workspace, "cat escape_link", None).expect_err("must refuse");
    assert_eq!(err.message, "Path escapes the agent workspace: escape_link");
    // And the file-tool sandbox refuses the same link by the same rule.
    assert_eq!(
        workspace
            .resolve("escape_link")
            .expect_err("must refuse")
            .message,
        "Path escapes the agent workspace."
    );
    // A link that stays inside is not collateral damage.
    let inside = validate(&workspace, "cat inside_link", None).expect("inside link is fine");
    let result = execute(&workspace, &inside, Duration::from_secs(30))
        .await
        .expect("run");
    assert_eq!(result.stdout, "alpha\nbeta\ngamma\n");
}

#[tokio::test]
async fn the_timeout_kills_a_command_that_would_hang_forever() {
    let dir = tempfile::tempdir().expect("tempdir");
    let workspace = workspace(&dir);
    // A FIFO with no writer blocks `cat` in open(2) — indefinitely, and without
    // spinning, which is exactly the shape a timeout has to survive.
    let fifo = workspace.root().join("blocking_fifo");
    let made = std::process::Command::new("mkfifo")
        .arg(&fifo)
        .status()
        .map(|status| status.success())
        .unwrap_or(false);
    if !made {
        eprintln!("mkfifo unavailable — skipping the blocking half of the timeout test");
        return;
    }

    let validated = validate(&workspace, "cat blocking_fifo", None).expect("valid");
    let started = Instant::now();
    let err = execute(&workspace, &validated, Duration::from_secs(1))
        .await
        .expect_err("must time out");
    let elapsed = started.elapsed();
    assert_eq!(err.message, "Command timed out after 1 seconds.");
    assert!(
        elapsed >= Duration::from_secs(1) && elapsed < Duration::from_secs(15),
        "the timeout fired at {elapsed:?}, which is not the timeout"
    );
}

#[tokio::test]
async fn the_child_environment_is_replaced_not_extended() {
    let dir = tempfile::tempdir().expect("tempdir");
    let workspace = workspace(&dir);
    let validated = validate(&workspace, "ls notes", None).expect("valid");
    let command = child_command(&workspace, &validated).expect("child");
    let standard = command.as_std();

    let env: BTreeMap<String, String> = standard
        .get_envs()
        .map(|(key, value)| {
            (
                key.to_string_lossy().into_owned(),
                value
                    .expect("the sandbox never unsets, it replaces")
                    .to_string_lossy()
                    .into_owned(),
            )
        })
        .collect();
    assert_eq!(
        env.keys().collect::<Vec<_>>(),
        ["HOME", "LANG", "LC_ALL", "PATH"],
        "the child sees four variables and no more"
    );
    assert_eq!(env["HOME"], workspace.root().display().to_string());
    assert_eq!(env["PATH"], SAFE_EXECUTABLE_PATH);
    assert_eq!(
        standard.get_current_dir(),
        Some(workspace.root()),
        "the child starts inside the workspace"
    );
    assert!(
        standard.get_program().to_string_lossy().ends_with("/ls"),
        "the program is the resolved binary, not the bare name"
    );

    // And a real child really observes it: `env` prints what it was given.
    let Some(env_binary) = which("env") else {
        eprintln!("env(1) not on the safe PATH — skipping the observed-environment half");
        return;
    };
    let output = std::process::Command::new(env_binary)
        .env_clear()
        .envs(sandbox_env(
            workspace.root(),
            Some("C.UTF-8".into()),
            Some("C.UTF-8".into()),
        ))
        .current_dir(workspace.root())
        .output()
        .expect("env runs");
    let mut observed: Vec<String> = String::from_utf8_lossy(&output.stdout)
        .lines()
        .map(str::to_string)
        .collect();
    observed.sort();
    assert_eq!(
        observed,
        vec![
            format!("HOME={}", workspace.root().display()),
            "LANG=C.UTF-8".to_string(),
            "LC_ALL=C.UTF-8".to_string(),
            format!("PATH={SAFE_EXECUTABLE_PATH}"),
        ],
        "the parent's environment leaked into the child"
    );
}

#[tokio::test]
async fn only_the_read_only_set_is_ever_spawned() {
    let dir = tempfile::tempdir().expect("tempdir");
    let workspace = workspace(&dir);
    for name in ["git", "npm", "rm", "bash"] {
        assert!(!is_natively_executable(name), "{name}");
        let validated = Validated {
            command: format!("{name} --version"),
            executable: name.to_string(),
            args: vec!["--version".to_string()],
            workdir: workspace.root().to_path_buf(),
        };
        let err = execute(&workspace, &validated, Duration::from_secs(5))
            .await
            .expect_err("must refuse");
        assert_eq!(
            err.message,
            format!("Command is validated but not executed natively: {name}")
        );
    }
    for name in NATIVE_EXECUTABLES {
        assert!(is_natively_executable(name), "{name}");
    }
}

#[tokio::test]
async fn a_missing_binary_is_reported_instead_of_pretending_to_run() {
    let dir = tempfile::tempdir().expect("tempdir");
    let workspace = workspace(&dir);
    if which("rg").is_some() {
        // rg is installed here, so the honest branch is the running one.
        let validated = validate(&workspace, "rg alpha notes", None).expect("valid");
        let result = execute(&workspace, &validated, Duration::from_secs(30))
            .await
            .expect("rg runs");
        assert!(result.stdout.contains("alpha"));
        return;
    }
    let validated = validate(&workspace, "rg alpha notes", None).expect("valid");
    let err = execute(&workspace, &validated, Duration::from_secs(30))
        .await
        .expect_err("rg is not installed");
    assert_eq!(err.message, "Allowed command is not installed: rg");
}
