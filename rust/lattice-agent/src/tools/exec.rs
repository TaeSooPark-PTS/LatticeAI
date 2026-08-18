//! Native execution of validated, read-only commands.
//!
//! This is the narrowest door in the crate, and it is narrow on purpose:
//!
//! * only a [`crate::tools::command::Validated`] command gets here at all;
//! * only the eight read-only executables in [`NATIVE_EXECUTABLES`] are run —
//!   `git`, `build_project` and `deploy_project` are validated and then handed
//!   back as a verdict, never spawned;
//! * the child gets a **replaced** environment (four variables), a fixed
//!   `PATH`, the workspace as `HOME` and as its working directory;
//! * the binary is resolved on that fixed `PATH`, so a workspace file called
//!   `ls` cannot become the `ls` that runs;
//! * output is capped at [`MAX_COMMAND_OUTPUT`] characters, keeping the **tail**
//!   exactly as Python's `stdout[-N:]` does;
//! * the wall clock is capped, and a child that outlives it is killed rather
//!   than detached.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde::Serialize;
use tokio::process::Command;

use crate::tools::command::Validated;
use crate::tools::sandbox::{ToolError, Workspace, MAX_COMMAND_OUTPUT, MAX_COMMAND_SECONDS};

/// The only `PATH` a sandboxed child ever sees.
pub const SAFE_EXECUTABLE_PATH: &str =
    "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin";

/// The read-only subset this crate will actually run: `ALLOWED_COMMANDS` minus
/// `git`, which `run_command` refuses in favour of the dedicated git tools.
pub const NATIVE_EXECUTABLES: [&str; 8] = ["cat", "find", "head", "ls", "pwd", "rg", "tail", "wc"];

/// Whether a validated command is one the host may run itself.
pub fn is_natively_executable(executable: &str) -> bool {
    NATIVE_EXECUTABLES.binary_search(&executable).is_ok()
}

/// The result of a command that ran.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Execution {
    /// The command string, echoed as Python echoes it.
    pub command: String,
    /// Workspace-relative working directory, `"."` for the root.
    pub cwd: String,
    pub returncode: i32,
    pub stdout: String,
    pub stderr: String,
    /// Whether the cap bit — Python drops the head silently, so saying so is
    /// the only way a caller can tell a short answer from a cut one.
    pub stdout_truncated: bool,
    pub stderr_truncated: bool,
}

/// The child environment: a replacement, not an addition.
///
/// `LANG`/`LC_ALL` are inherited when set because Python inherits them (they
/// decide how the child renders text); everything else the parent holds —
/// tokens, keys, `SSH_AUTH_SOCK` — is dropped.
pub fn sandbox_env(
    root: &Path,
    lang: Option<String>,
    lc_all: Option<String>,
) -> BTreeMap<String, String> {
    BTreeMap::from([
        ("HOME".to_string(), root.display().to_string()),
        ("LANG".to_string(), lang.unwrap_or_else(|| "C.UTF-8".into())),
        (
            "LC_ALL".to_string(),
            lc_all.unwrap_or_else(|| "C.UTF-8".into()),
        ),
        ("PATH".to_string(), SAFE_EXECUTABLE_PATH.to_string()),
    ])
}

/// `shutil.which(executable, path=SAFE_EXECUTABLE_PATH)`.
pub fn which(executable: &str) -> Option<PathBuf> {
    for directory in SAFE_EXECUTABLE_PATH.split(':') {
        let candidate = Path::new(directory).join(executable);
        let Ok(metadata) = std::fs::metadata(&candidate) else {
            continue;
        };
        if metadata.is_dir() {
            continue;
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            if metadata.permissions().mode() & 0o111 == 0 {
                continue;
            }
        }
        return Some(candidate);
    }
    None
}

/// Keep the last `MAX_COMMAND_OUTPUT` **characters**, like Python's `[-N:]`.
fn truncate_tail(text: &str) -> (String, bool) {
    let length = text.chars().count();
    if length <= MAX_COMMAND_OUTPUT {
        return (text.to_string(), false);
    }
    (
        text.chars().skip(length - MAX_COMMAND_OUTPUT).collect(),
        true,
    )
}

/// The exact child [`execute`] will spawn: program, arguments, directory and a
/// replaced environment.
///
/// Public so a test can assert what gets spawned rather than assert a
/// re-implementation of it. Refuses — without spawning — any executable outside
/// [`NATIVE_EXECUTABLES`], so a future widening of the command allowlist cannot
/// quietly become a new execution path.
pub fn child_command(workspace: &Workspace, validated: &Validated) -> Result<Command, ToolError> {
    if !is_natively_executable(&validated.executable) {
        return Err(ToolError::tool(format!(
            "Command is validated but not executed natively: {}",
            validated.executable
        )));
    }
    let Some(program) = which(&validated.executable) else {
        return Err(ToolError::tool(format!(
            "Allowed command is not installed: {}",
            validated.executable
        )));
    };
    let mut command = Command::new(program);
    command
        .args(&validated.args)
        .current_dir(&validated.workdir)
        .env_clear()
        .envs(sandbox_env(
            workspace.root(),
            std::env::var("LANG").ok(),
            std::env::var("LC_ALL").ok(),
        ))
        // A child that outlives its timeout is killed, not orphaned.
        .kill_on_drop(true);
    Ok(command)
}

/// Run a validated command. `timeout` is clamped to [`MAX_COMMAND_SECONDS`].
pub async fn execute(
    workspace: &Workspace,
    validated: &Validated,
    timeout: Duration,
) -> Result<Execution, ToolError> {
    let mut command = child_command(workspace, validated)?;
    let limit = timeout.min(Duration::from_secs(MAX_COMMAND_SECONDS));

    let output = match tokio::time::timeout(limit, command.output()).await {
        Ok(Ok(output)) => output,
        Ok(Err(err)) => {
            return Err(ToolError::tool(format!(
                "Command could not start: {} ({err})",
                validated.executable
            )))
        }
        Err(_) => {
            return Err(ToolError::tool(format!(
                "Command timed out after {} seconds.",
                limit.as_secs()
            )))
        }
    };

    let (stdout, stdout_truncated) = truncate_tail(&String::from_utf8_lossy(&output.stdout));
    let (stderr, stderr_truncated) = truncate_tail(&String::from_utf8_lossy(&output.stderr));
    Ok(Execution {
        command: validated.command.clone(),
        cwd: workspace.relative(&validated.workdir),
        // A signalled child has no code; -1 keeps the field an integer and is
        // never a code a process can exit with.
        returncode: output.status.code().unwrap_or(-1),
        stdout,
        stderr,
        stdout_truncated,
        stderr_truncated,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_native_set_is_the_allowlist_minus_git() {
        let mut expected: Vec<&str> = crate::tools::command::ALLOWED_COMMANDS
            .iter()
            .filter(|name| **name != "git")
            .copied()
            .collect();
        expected.sort_unstable();
        assert_eq!(NATIVE_EXECUTABLES.to_vec(), expected);
        assert!(!is_natively_executable("git"));
        assert!(!is_natively_executable("npm"));
        assert!(is_natively_executable("cat"));
    }

    #[test]
    fn the_environment_is_four_variables_and_a_fixed_path() {
        let env = sandbox_env(Path::new("/tmp/ws"), None, Some("ko_KR.UTF-8".into()));
        assert_eq!(
            env.keys().collect::<Vec<_>>(),
            ["HOME", "LANG", "LC_ALL", "PATH"]
        );
        assert_eq!(env["HOME"], "/tmp/ws");
        assert_eq!(env["LANG"], "C.UTF-8");
        assert_eq!(env["LC_ALL"], "ko_KR.UTF-8");
        assert_eq!(env["PATH"], SAFE_EXECUTABLE_PATH);
        assert!(
            !SAFE_EXECUTABLE_PATH.contains('.')
                || !SAFE_EXECUTABLE_PATH.split(':').any(|p| p.is_empty()),
            "an empty PATH entry means the current directory"
        );
    }

    #[test]
    fn resolution_happens_on_the_fixed_path_only() {
        assert!(which("ls").is_some(), "ls must exist on the safe PATH");
        assert!(which("definitely-not-a-real-binary").is_none());
        let resolved = which("ls").expect("ls");
        assert!(
            SAFE_EXECUTABLE_PATH
                .split(':')
                .any(|dir| resolved.starts_with(dir)),
            "{resolved:?} came from outside the safe PATH"
        );
    }

    #[test]
    fn truncation_keeps_the_tail_like_pythons_negative_slice() {
        let (short, cut) = truncate_tail("hello");
        assert_eq!(short, "hello");
        assert!(!cut);
        let long: String = (0..MAX_COMMAND_OUTPUT + 10).map(|_| 'x').collect();
        let (kept, cut) = truncate_tail(&long);
        assert_eq!(kept.chars().count(), MAX_COMMAND_OUTPUT);
        assert!(cut);
        // Characters, not bytes: a multi-byte tail must not be split.
        let korean: String = (0..MAX_COMMAND_OUTPUT + 5).map(|_| '한').collect();
        let (kept, cut) = truncate_tail(&korean);
        assert_eq!(kept.chars().count(), MAX_COMMAND_OUTPUT);
        assert!(cut);
        assert!(kept.chars().all(|ch| ch == '한'));
        // The *head* is what gets dropped.
        let numbered: String = (0..MAX_COMMAND_OUTPUT + 3)
            .map(|i| ((i % 10) as u8 + b'0') as char)
            .collect();
        let (kept, _) = truncate_tail(&numbered);
        assert!(numbered.ends_with(&kept));
    }
}
