"""`run_command` is the agent's shell, and every rule here is a containment boundary.

The agent can propose any command string. What stands between that and the
user's machine is: an allowlist, a ban on shell operators, a ban on absolute or
traversing paths, a resolved-symlink check against the workspace root, a fixed
PATH, a scrubbed environment, and a timeout. Each of those is one line of code
and each is load-bearing, so each gets an assertion.

The tests point AGENT_ROOT at a temp directory so nothing here can touch the
real workspace even if a guard regresses.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import latticeai.tools as tools
from latticeai.tools import MAX_COMMAND_OUTPUT, ToolError
from latticeai.tools.commands import (
    _argument_value,
    _validate_command_paths,
    git_diff,
    git_log,
    git_show,
    git_status,
    run_command,
)


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """A throwaway AGENT_ROOT with one ordinary file in it."""
    root = tmp_path / "agent_workspace"
    root.mkdir()
    (root / "notes.txt").write_text("hello\n", encoding="utf-8")
    monkeypatch.setattr(tools, "AGENT_ROOT", root)
    mod = sys.modules.get("latticeai.tools.commands")
    if mod is not None and hasattr(mod, "AGENT_ROOT"):
        monkeypatch.setattr(mod, "AGENT_ROOT", root, raising=False)
    return root


# ── the allowlist ─────────────────────────────────────────────────────────
def test_an_empty_command_is_refused(workspace):
    with pytest.raises(ToolError, match="empty"):
        run_command("   ")


def test_a_command_outside_the_allowlist_is_refused(workspace):
    with pytest.raises(ToolError, match="not allowed"):
        run_command("curl https://example.com")


def test_an_executable_given_by_path_is_refused(workspace):
    """Allowlisting by basename is worthless if /tmp/evil/ls is accepted."""
    with pytest.raises(ToolError, match="Executable paths"):
        run_command("/bin/ls")


def test_git_is_routed_to_the_read_only_tools_rather_than_run(workspace):
    with pytest.raises(ToolError, match="read-only git"):
        run_command("git push origin main")


@pytest.mark.parametrize(
    "command",
    [
        "ls | tee /tmp/out",
        "ls && rm -rf .",
        "ls || true",
        "ls ; rm notes.txt",
        "ls > out.txt",
        "ls < in.txt",
        "ls $(whoami)",
        "ls `whoami`",
    ],
)
def test_shell_operators_are_refused(workspace, command):
    """Without this, the allowlist only gates the first word of the line."""
    with pytest.raises(ToolError, match="Shell operators"):
        run_command(command)


@pytest.mark.parametrize("flag", ["-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprintf"])
def test_find_flags_that_execute_or_delete_are_refused(workspace, flag):
    with pytest.raises(ToolError, match="find flags"):
        run_command(f"find . -name '*.txt' {flag}")


@pytest.mark.parametrize("flag", ["--pre", "--pre-glob"])
def test_ripgrep_preprocessor_flags_are_refused(workspace, flag):
    """`rg --pre` runs an arbitrary program per file — an execution path."""
    with pytest.raises(ToolError, match="rg flags"):
        run_command(f"rg pattern {flag}=/bin/sh")


# ── path containment ──────────────────────────────────────────────────────
def test_absolute_path_arguments_are_refused(workspace):
    with pytest.raises(ToolError, match="Absolute paths"):
        run_command("cat /etc/passwd")


def test_traversal_in_arguments_is_refused(workspace):
    with pytest.raises(ToolError, match="traversal"):
        run_command("cat ../../etc/passwd")


def test_a_symlink_pointing_outside_the_workspace_is_refused(workspace, tmp_path):
    """The check resolves symlinks; a link is the obvious way around a string test."""
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (workspace / "link.txt").symlink_to(outside)

    with pytest.raises(ToolError, match="escapes the agent workspace"):
        _validate_command_paths(["cat", "link.txt"], workspace)


def test_an_ordinary_relative_file_inside_the_workspace_is_allowed(workspace):
    _validate_command_paths(["cat", "notes.txt"], workspace)  # must not raise


def test_non_path_arguments_are_not_treated_as_paths(workspace):
    """Search patterns and counts must not trip the path guard."""
    _validate_command_paths(["rg", "TODO", "-n", "--max-count=5"], workspace)


def test_dev_null_is_permitted_as_the_one_absolute_exception(workspace):
    _validate_command_paths(["cat", "/dev/null"], workspace)


def test_argument_value_splits_only_on_the_first_equals():
    assert _argument_value("--path=a=b") == "a=b"
    assert _argument_value("--flag") == "--flag"
    assert _argument_value("plain") == "plain"


def test_a_missing_working_directory_is_refused(workspace):
    with pytest.raises(ToolError, match="Working directory"):
        run_command("ls", cwd="no-such-dir")


# ── the execution environment ─────────────────────────────────────────────
def test_an_allowed_command_runs_and_reports_its_output(workspace):
    result = run_command("ls")
    assert result["returncode"] == 0
    assert "notes.txt" in result["stdout"]
    assert result["cwd"] == "."


def test_the_child_process_gets_a_scrubbed_home_and_path(workspace, monkeypatch):
    """A leaked HOME or PATH would hand the agent the real user's environment."""
    monkeypatch.setenv("SECRET_TOKEN", "must-not-propagate")
    seen: dict = {}

    import subprocess as real_subprocess

    from latticeai.tools import commands as commands_module

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return real_subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(commands_module.subprocess, "run", fake_run)
    run_command("ls")

    env = seen["env"]
    assert env["HOME"] == str(workspace), "HOME must point at the sandbox"
    assert "SECRET_TOKEN" not in env, "the parent environment must not be inherited"
    assert "/usr/bin" in env["PATH"]
    assert seen["timeout"], "a command with no timeout can hang the server"


def test_a_timeout_is_reported_as_a_tool_error(workspace, monkeypatch):
    import subprocess as real_subprocess

    from latticeai.tools import commands as commands_module

    def fake_run(argv, **kwargs):
        raise real_subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(commands_module.subprocess, "run", fake_run)
    with pytest.raises(ToolError, match="timed out"):
        run_command("ls")


def test_output_is_truncated_so_one_command_cannot_flood_the_response(workspace, monkeypatch):
    import subprocess as real_subprocess

    from latticeai.tools import commands as commands_module

    def fake_run(argv, **kwargs):
        return real_subprocess.CompletedProcess(argv, 0, "x" * (MAX_COMMAND_OUTPUT * 3), "")

    monkeypatch.setattr(commands_module.subprocess, "run", fake_run)
    result = run_command("ls")
    assert len(result["stdout"]) == MAX_COMMAND_OUTPUT


def test_a_nonzero_exit_is_reported_rather_than_raised(workspace):
    """A failing command is a result the agent should see, not an exception."""
    result = run_command("ls no-such-file")
    assert result["returncode"] != 0
    assert result["stderr"]


# ── the read-only git surface ─────────────────────────────────────────────
def test_git_helpers_report_cleanly_outside_a_repository(workspace):
    """These run against user directories; "not a repo" must not be a crash."""
    for call in (git_status, git_log, git_diff, git_show):
        result = call()
        assert isinstance(result, dict)
        assert "returncode" in result or "error" in result or "stderr" in result
