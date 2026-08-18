//! The command sandbox validator — `latticeai.tools.commands.run_command`.
//!
//! The agent can propose any command string. Between that string and the user's
//! machine stand, in this order: a POSIX split, an executable **basename**
//! allowlist, a ban on shell operators, `find`/`rg` flag denials, a working
//! directory that must resolve inside the workspace, and an argument scan that
//! refuses absolute paths, `..`, and anything whose resolved form leaves the
//! workspace. The order is part of the contract — the goldens pin which rule
//! fires for a string that breaks several — and so are the messages.
//!
//! Nothing here spawns anything. [`validate`] returns a verdict; [`crate::tools::exec`]
//! decides separately whether the verdict is one of the few that may run.

use std::path::{Component, Path, PathBuf};

use crate::parse::pyshlex;
use crate::tools::sandbox::{resolve_soft, ToolError, Workspace};

/// Commands that are refused by name even though they exist.
pub const BLOCKED_COMMANDS: [&str; 15] = [
    "chmod",
    "chown",
    "curl",
    "dd",
    "diskutil",
    "launchctl",
    "mkfs",
    "rm",
    "rmdir",
    "rsync",
    "scp",
    "ssh",
    "su",
    "sudo",
    "wget",
];

/// The only executables `run_command` will ever consider.
pub const ALLOWED_COMMANDS: [&str; 9] = [
    "cat", "find", "git", "head", "ls", "pwd", "rg", "tail", "wc",
];

/// Read-only git verbs, for the dedicated `git_*` tools. `run_command` itself
/// refuses `git` outright and points at those tools.
pub const ALLOWED_GIT_SUBCOMMANDS: [&str; 4] = ["diff", "log", "show", "status"];

/// Substrings that mean a shell would do something the sandbox did not sanction.
/// Scanned over the **raw** string, before tokens, so quoting cannot hide them.
pub const SHELL_OPERATORS: [&str; 8] = ["|", "&&", "||", ";", ">", "<", "$(", "`"];

/// `find(1)` flags that execute or delete.
pub const BLOCKED_FIND_FLAGS: [&str; 8] = [
    "-delete", "-exec", "-execdir", "-fls", "-fprint", "-fprintf", "-ok", "-okdir",
];

/// `rg` flags that run a preprocessor.
pub const BLOCKED_RG_FLAGS: [&str; 2] = ["--pre", "--pre-glob"];

/// A command that passed every rule: what to run, where, and with which args.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Validated {
    /// The original string, echoed back the way Python echoes it.
    pub command: String,
    /// The executable **basename** — never a path.
    pub executable: String,
    pub args: Vec<String>,
    /// Absolute, inside the workspace.
    pub workdir: PathBuf,
}

/// `PurePosixPath(value).name`: the last component, ignoring `.` and empty
/// segments, with `..` counting as an ordinary name.
fn py_basename(value: &str) -> &str {
    let mut last = "";
    for segment in value.split('/') {
        if segment.is_empty() || segment == "." {
            continue;
        }
        last = segment;
    }
    last
}

/// `_argument_value`: for `--flag=value`, the value; otherwise the argument.
fn argument_value(argument: &str) -> &str {
    if argument.starts_with('-') {
        if let Some((_, value)) = argument.split_once('=') {
            return value;
        }
    }
    argument
}

/// `Path(value).expanduser()`, fail-closed.
///
/// Python resolves `~` from `$HOME` and `~user` from the password database,
/// raising `RuntimeError` when it cannot. Both outcomes end the same way for a
/// sandbox — an absolute path, or a crash — so an unexpandable `~` is reported
/// here as the absolute-path refusal rather than as an exception.
fn expanduser(value: &str) -> Option<PathBuf> {
    if !value.starts_with('~') {
        return Some(PathBuf::from(value));
    }
    let (prefix, rest) = match value.find('/') {
        Some(index) => (&value[..index], &value[index..]),
        None => (value, ""),
    };
    if prefix != "~" {
        return None; // ~user — refuse rather than guess a home directory.
    }
    let home = std::env::var_os("HOME")?;
    if home.is_empty() {
        return None;
    }
    let mut path = PathBuf::from(home);
    if !rest.is_empty() {
        path.push(rest.trim_start_matches('/'));
    }
    Some(path)
}

/// `_validate_command_paths`: traversal and symlink escapes hidden in arguments.
fn validate_command_paths(
    workspace: &Workspace,
    args: &[String],
    workdir: &Path,
) -> Result<(), ToolError> {
    for argument in args {
        let value = argument_value(argument);
        // Flags without a value, the empty string, and the one device every
        // read-only command is allowed to name are not paths.
        if value.is_empty()
            || value == "/dev/null"
            || (argument.starts_with('-') && !argument.contains('='))
        {
            continue;
        }
        let Some(expanded) = expanduser(value) else {
            return Err(ToolError::tool(format!(
                "Absolute paths in command arguments are not allowed: {value}"
            )));
        };
        if expanded.is_absolute() {
            return Err(ToolError::tool(format!(
                "Absolute paths in command arguments are not allowed: {value}"
            )));
        }
        if expanded
            .components()
            .any(|component| component == Component::ParentDir)
        {
            return Err(ToolError::tool(format!(
                "Path traversal in command arguments is not allowed: {value}"
            )));
        }

        let candidate = workdir.join(&expanded);
        // Anything that exists (a dangling symlink included) and anything
        // shaped like a path is resolved and must land inside the workspace.
        // Everything else can be a search pattern, a count or an expression,
        // and is not treated as a path at all.
        let exists = std::fs::symlink_metadata(&candidate).is_ok();
        let path_shaped = exists || value.contains('/') || value.contains('\\');
        if path_shaped && !workspace.contains(&resolve_soft(&candidate)) {
            return Err(ToolError::tool(format!(
                "Path escapes the agent workspace: {value}"
            )));
        }
    }
    Ok(())
}

/// Validate one command string. `Ok` means every rule passed — not that the
/// command may be executed; see [`crate::tools::exec::is_natively_executable`].
pub fn validate(
    workspace: &Workspace,
    command: &str,
    cwd: Option<&str>,
) -> Result<Validated, ToolError> {
    let parts = pyshlex::split(command).map_err(|err| ToolError::shlex(err.message()))?;
    let Some(first) = parts.first() else {
        return Err(ToolError::tool("Command is empty."));
    };

    let executable = py_basename(first);
    if first != executable {
        return Err(ToolError::tool("Executable paths are not allowed."));
    }
    if BLOCKED_COMMANDS.binary_search(&executable).is_ok()
        || ALLOWED_COMMANDS.binary_search(&executable).is_err()
    {
        return Err(ToolError::tool(format!(
            "Command is not allowed: {executable}"
        )));
    }
    if executable == "git" {
        return Err(ToolError::tool(
            "Use the read-only git_status, git_diff, git_log, or git_show tools.",
        ));
    }
    if SHELL_OPERATORS
        .iter()
        .any(|operator| command.contains(operator))
    {
        return Err(ToolError::tool("Shell operators are not allowed."));
    }

    let args: Vec<String> = parts[1..].to_vec();
    if executable == "find" {
        let blocked: Vec<&str> = args
            .iter()
            .filter(|flag| BLOCKED_FIND_FLAGS.contains(&flag.as_str()))
            .map(String::as_str)
            .collect();
        if !blocked.is_empty() {
            return Err(ToolError::tool(format!(
                "find flags are not allowed: {}",
                blocked.join(", ")
            )));
        }
    }
    if executable == "rg" {
        let blocked: Vec<&str> = args
            .iter()
            .filter(|flag| {
                BLOCKED_RG_FLAGS.iter().any(|denied| {
                    flag.as_str() == *denied || flag.starts_with(&format!("{denied}="))
                })
            })
            .map(String::as_str)
            .collect();
        if !blocked.is_empty() {
            return Err(ToolError::tool(format!(
                "rg flags are not allowed: {}",
                blocked.join(", ")
            )));
        }
    }

    let workdir = workspace.resolve(cwd.filter(|value| !value.is_empty()).unwrap_or("."))?;
    if !workdir.is_dir() {
        return Err(ToolError::tool("Working directory does not exist."));
    }
    validate_command_paths(workspace, &args, &workdir)?;

    Ok(Validated {
        command: command.to_string(),
        executable: executable.to_string(),
        args,
        workdir,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn workspace() -> (tempfile::TempDir, Workspace) {
        let dir = tempfile::tempdir().expect("tempdir");
        let root = dir.path().join("agent_workspace");
        let workspace = Workspace::new(&root).expect("workspace");
        std::fs::create_dir_all(root.join("notes")).expect("notes");
        std::fs::write(root.join("notes/a.txt"), "alpha\n").expect("file");
        std::fs::write(dir.path().join("outside_secret.txt"), "secret\n").expect("outside");
        (dir, workspace)
    }

    fn refuse(workspace: &Workspace, command: &str) -> String {
        validate(workspace, command, None)
            .expect_err(&format!("{command:?} must be refused"))
            .message
    }

    #[test]
    fn the_constant_tables_are_sorted_for_binary_search() {
        for set in [
            &BLOCKED_COMMANDS[..],
            &ALLOWED_COMMANDS[..],
            &ALLOWED_GIT_SUBCOMMANDS[..],
        ] {
            let mut sorted = set.to_vec();
            sorted.sort_unstable();
            assert_eq!(set, &sorted[..]);
        }
    }

    #[test]
    fn pathlib_basename_semantics_are_reproduced() {
        for (input, expected) in [
            ("ls", "ls"),
            ("ls/", "ls"),
            ("/bin/ls", "ls"),
            ("./ls", "ls"),
            ("..", ".."),
            (".", ""),
            ("", ""),
            ("a//b", "b"),
            ("a/.", "a"),
            ("//ls", "ls"),
            ("/", ""),
        ] {
            assert_eq!(py_basename(input), expected, "{input:?}");
        }
    }

    #[test]
    fn only_allow_listed_basenames_survive() {
        let (_dir, workspace) = workspace();
        assert!(validate(&workspace, "ls -la", None).is_ok());
        assert_eq!(
            refuse(&workspace, "echo hi"),
            "Command is not allowed: echo"
        );
        assert_eq!(
            refuse(&workspace, "rm -r notes"),
            "Command is not allowed: rm"
        );
        assert_eq!(
            refuse(&workspace, "sudo ls"),
            "Command is not allowed: sudo"
        );
        assert_eq!(
            refuse(&workspace, "/bin/ls"),
            "Executable paths are not allowed."
        );
        assert_eq!(
            refuse(&workspace, "./ls"),
            "Executable paths are not allowed."
        );
        assert_eq!(
            refuse(&workspace, "git status"),
            "Use the read-only git_status, git_diff, git_log, or git_show tools."
        );
    }

    #[test]
    fn an_empty_command_is_empty_however_it_is_spelled() {
        let (_dir, workspace) = workspace();
        assert_eq!(refuse(&workspace, ""), "Command is empty.");
        assert_eq!(refuse(&workspace, "    "), "Command is empty.");
    }

    #[test]
    fn shell_operators_are_refused_before_the_arguments_are_read() {
        let (_dir, workspace) = workspace();
        for command in [
            "cat notes/a.txt | wc -l",
            "cat notes/a.txt && ls",
            "cat notes/a.txt; ls",
            "cat > out.txt",
            "wc -l < notes/a.txt",
            "cat $(ls)",
            "cat `ls`",
            "ls || ls",
        ] {
            assert_eq!(
                refuse(&workspace, command),
                "Shell operators are not allowed.",
                "{command}"
            );
        }
    }

    #[test]
    fn find_and_rg_lose_the_flags_that_execute() {
        let (_dir, workspace) = workspace();
        assert_eq!(
            refuse(&workspace, "find . -delete"),
            "find flags are not allowed: -delete"
        );
        assert_eq!(
            refuse(&workspace, "find . -name x -exec cat {} +"),
            "find flags are not allowed: -exec"
        );
        assert_eq!(
            refuse(&workspace, "rg --pre cat foo"),
            "rg flags are not allowed: --pre"
        );
        assert_eq!(
            refuse(&workspace, "rg --pre-glob=*.py foo"),
            "rg flags are not allowed: --pre-glob=*.py"
        );
        // The rule is exact-or-`=`; a flag that merely starts with the same
        // letters is a different flag.
        assert!(validate(&workspace, "rg --pretty foo", None).is_ok());
        assert!(validate(&workspace, "find . -name -execute", None).is_ok());
    }

    #[test]
    fn arguments_may_not_be_absolute_or_traverse() {
        let (_dir, workspace) = workspace();
        assert_eq!(
            refuse(&workspace, "cat /etc/passwd"),
            "Absolute paths in command arguments are not allowed: /etc/passwd"
        );
        assert_eq!(
            refuse(&workspace, "cat ../outside_secret.txt"),
            "Path traversal in command arguments is not allowed: ../outside_secret.txt"
        );
        assert_eq!(
            refuse(&workspace, "cat notes/../../outside_secret.txt"),
            "Path traversal in command arguments is not allowed: notes/../../outside_secret.txt"
        );
        // `/dev/null` is the one absolute path the read tools may name.
        assert!(validate(&workspace, "cat /dev/null", None).is_ok());
    }

    #[test]
    fn a_tilde_argument_is_treated_as_the_absolute_path_it_becomes() {
        let (_dir, workspace) = workspace();
        assert_eq!(
            refuse(&workspace, "cat ~"),
            "Absolute paths in command arguments are not allowed: ~"
        );
        assert_eq!(
            refuse(&workspace, "cat ~/secret"),
            "Absolute paths in command arguments are not allowed: ~/secret"
        );
        assert_eq!(
            refuse(&workspace, "cat ~nobody/secret"),
            "Absolute paths in command arguments are not allowed: ~nobody/secret"
        );
    }

    #[test]
    fn key_value_flags_are_scanned_by_their_value() {
        let (_dir, workspace) = workspace();
        assert!(validate(&workspace, "ls --color=auto", None).is_ok());
        assert!(validate(&workspace, "ls --color=notes/a.txt", None).is_ok());
        assert_eq!(
            refuse(&workspace, "ls --color=/etc"),
            "Absolute paths in command arguments are not allowed: /etc"
        );
        assert_eq!(
            refuse(&workspace, "ls --color=../x"),
            "Path traversal in command arguments is not allowed: ../x"
        );
        // A bare flag is not a path, whatever it looks like.
        assert!(validate(&workspace, "cat -", None).is_ok());
        assert!(validate(&workspace, "ls -la", None).is_ok());
    }

    #[cfg(unix)]
    #[test]
    fn a_symlink_out_of_the_workspace_is_refused_by_its_resolved_target() {
        let (_dir, workspace) = workspace();
        std::os::unix::fs::symlink(
            "../outside_secret.txt",
            workspace.root().join("escape_link"),
        )
        .expect("symlink");
        std::os::unix::fs::symlink("notes/a.txt", workspace.root().join("inside_link"))
            .expect("symlink");
        assert_eq!(
            refuse(&workspace, "cat escape_link"),
            "Path escapes the agent workspace: escape_link"
        );
        assert!(validate(&workspace, "cat inside_link", None).is_ok());
    }

    #[test]
    fn a_value_that_is_not_a_path_is_not_checked_as_one() {
        let (_dir, workspace) = workspace();
        // Nonexistent and not path-shaped: a search pattern, left alone.
        assert!(validate(&workspace, "cat missing.txt", None).is_ok());
        assert!(validate(&workspace, "rg TODO", None).is_ok());
    }

    #[test]
    fn the_working_directory_must_be_a_real_directory_inside_the_workspace() {
        let (_dir, workspace) = workspace();
        assert!(validate(&workspace, "ls", Some("notes")).is_ok());
        for (cwd, message) in [
            ("../", "Path escapes the agent workspace."),
            ("/etc", "Path escapes the agent workspace."),
            ("nope", "Working directory does not exist."),
            ("notes/a.txt", "Working directory does not exist."),
        ] {
            let err = validate(&workspace, "ls", Some(cwd)).expect_err(cwd);
            assert_eq!(err.message, message, "{cwd}");
        }
    }

    #[test]
    fn a_split_failure_is_reported_as_shlexs_own_error() {
        let (_dir, workspace) = workspace();
        let err = validate(&workspace, "cat 'unterminated", None).expect_err("must refuse");
        assert_eq!(err.kind, crate::tools::sandbox::ErrorKind::Shlex);
        assert_eq!(err.message, "No closing quotation");
    }

    #[test]
    fn a_validated_command_carries_the_basename_and_the_rest() {
        let (_dir, workspace) = workspace();
        let validated = validate(&workspace, "head -n 2 notes/a.txt", None).expect("valid");
        assert_eq!(validated.executable, "head");
        assert_eq!(validated.args, ["-n", "2", "notes/a.txt"]);
        assert_eq!(validated.workdir, workspace.root());
        assert_eq!(validated.command, "head -n 2 notes/a.txt");
    }
}
