"""wp13 coverage — ``latticeai.tools.commands`` beyond the containment guards.

``tests/unit/test_command_sandbox.py`` and ``test_command_tool_guards.py``
already pin the allowlist, the shell-operator ban and the path/symlink checks.
What is left is everything downstream of those: the "allowed but not
installed" case, the npm script runner (allowlist, package.json parsing,
timeout) and the read-only git surface (subcommand allowlist, remote refusal,
argument shaping).

Nothing here starts a real process. ``subprocess.run`` is replaced per test so
the assertions can be about the argv, cwd and timeout the tool *chose*, which
is the part that carries the containment guarantee.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import latticeai.tools as tools
from latticeai.tools import (
    MAX_BUILD_SECONDS,
    MAX_COMMAND_OUTPUT,
    MAX_COMMAND_SECONDS,
    MAX_DEPLOY_SECONDS,
    ToolError,
)
from latticeai.tools import commands as commands_module
from latticeai.tools.commands import (
    _load_package_scripts,
    _run_git,
    build_project,
    deploy_project,
    git_diff,
    git_log,
    git_show,
    git_status,
    run_command,
)


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "agent_workspace"
    root.mkdir()
    monkeypatch.setattr(tools, "AGENT_ROOT", root)
    return root


def _record_run(monkeypatch, *, stdout: str = "", stderr: str = "", code: int = 0) -> List[Dict[str, Any]]:
    """Replace ``subprocess.run`` and capture every invocation."""
    calls: List[Dict[str, Any]] = []

    def fake_run(argv, **kwargs):
        calls.append({"argv": list(argv), **kwargs})
        return subprocess.CompletedProcess(argv, code, stdout, stderr)

    monkeypatch.setattr(commands_module.subprocess, "run", fake_run)
    return calls


def _raise_timeout(monkeypatch) -> None:
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 1))

    monkeypatch.setattr(commands_module.subprocess, "run", fake_run)


def _package_json(workdir: Path, payload: Any) -> None:
    workdir.joinpath("package.json").write_text(
        payload if isinstance(payload, str) else json.dumps(payload),
        encoding="utf-8",
    )


# ── run_command: the allowed-but-absent case ─────────────────────────────────


def test_an_allowed_command_that_is_not_installed_is_a_tool_error(workspace, monkeypatch) -> None:
    """The allowlist is not a promise the binary exists on this machine."""
    monkeypatch.setattr(commands_module.shutil, "which", lambda name, path=None: None)

    with pytest.raises(ToolError, match="not installed: rg"):
        run_command("rg needle")


def test_the_resolved_executable_is_looked_up_on_the_fixed_path(workspace, monkeypatch) -> None:
    seen: Dict[str, Any] = {}

    def fake_which(name, path=None):
        seen["name"] = name
        seen["path"] = path
        return "/usr/bin/" + name

    monkeypatch.setattr(commands_module.shutil, "which", fake_which)
    calls = _record_run(monkeypatch, stdout="ok\n")

    result = run_command("ls -la")

    assert seen["name"] == "ls"
    assert "/usr/bin" in seen["path"] and "PATH" not in seen["path"]
    assert calls[0]["argv"] == ["/usr/bin/ls", "-la"]
    assert calls[0]["timeout"] == MAX_COMMAND_SECONDS
    assert result["stdout"] == "ok\n"


# ── _load_package_scripts ────────────────────────────────────────────────────


def test_a_directory_without_a_package_json_has_no_scripts(workspace) -> None:
    assert _load_package_scripts(workspace) == {}


def test_unparsable_package_json_is_reported_rather_than_ignored(workspace) -> None:
    _package_json(workspace, "{not json")

    with pytest.raises(ToolError, match="Could not parse package.json"):
        _load_package_scripts(workspace)


def test_a_non_object_scripts_field_yields_no_scripts(workspace) -> None:
    _package_json(workspace, {"scripts": "npm run everything"})

    assert _load_package_scripts(workspace) == {}


def test_scripts_are_normalised_to_strings(workspace) -> None:
    _package_json(workspace, {"scripts": {"build": "vite build", "test": 1}})

    assert _load_package_scripts(workspace) == {"build": "vite build", "test": "1"}


# ── build_project / deploy_project ───────────────────────────────────────────


def test_build_refuses_a_script_outside_the_build_allowlist(workspace) -> None:
    _package_json(workspace, {"scripts": {"deploy": "vercel --prod"}})

    with pytest.raises(ToolError, match="not allowed here: deploy"):
        build_project(script="deploy")


def test_deploy_refuses_a_script_outside_the_deploy_allowlist(workspace) -> None:
    with pytest.raises(ToolError, match="not allowed here: lint"):
        deploy_project(script="lint")


def test_a_missing_working_directory_is_refused_before_npm_runs(workspace, monkeypatch) -> None:
    calls = _record_run(monkeypatch)

    with pytest.raises(ToolError, match="Working directory does not exist"):
        build_project(cwd="no-such-project", script="build")

    assert calls == []


def test_a_script_the_project_does_not_define_is_refused(workspace, monkeypatch) -> None:
    _package_json(workspace, {"scripts": {"test": "vitest"}})
    calls = _record_run(monkeypatch)

    with pytest.raises(ToolError, match="does not define a 'build' script"):
        build_project(script="build")

    assert calls == []


def test_build_runs_npm_in_the_project_directory_and_reports_the_script_body(
    workspace, monkeypatch
) -> None:
    project = workspace / "site"
    project.mkdir()
    _package_json(project, {"scripts": {"build": "vite build"}})
    calls = _record_run(monkeypatch, stdout="built\n", stderr="warn\n", code=0)

    result = build_project(cwd="site", script="build")

    assert calls[0]["argv"] == ["npm", "run", "build"]
    assert calls[0]["cwd"] == project
    assert calls[0]["timeout"] == MAX_BUILD_SECONDS
    assert result["command"] == "npm run build"
    assert result["cwd"] == "site"
    assert result["script_body"] == "vite build"
    assert result["returncode"] == 0
    assert result["stdout"] == "built\n"
    assert result["stderr"] == "warn\n"


def test_deploy_uses_the_longer_deploy_timeout_and_reports_the_workspace_root(
    workspace, monkeypatch
) -> None:
    _package_json(workspace, {"scripts": {"deploy": "vercel --prod"}})
    calls = _record_run(monkeypatch, code=2)

    result = deploy_project(script="deploy")

    assert calls[0]["timeout"] == MAX_DEPLOY_SECONDS
    assert result["cwd"] == "."
    assert result["returncode"] == 2


def test_script_output_is_truncated_like_run_command(workspace, monkeypatch) -> None:
    _package_json(workspace, {"scripts": {"build": "vite build"}})
    _record_run(monkeypatch, stdout="x" * (MAX_COMMAND_OUTPUT * 2), stderr="y" * (MAX_COMMAND_OUTPUT * 2))

    result = build_project(script="build")

    assert len(result["stdout"]) == MAX_COMMAND_OUTPUT
    assert len(result["stderr"]) == MAX_COMMAND_OUTPUT


def test_a_hanging_npm_script_becomes_a_tool_error(workspace, monkeypatch) -> None:
    _package_json(workspace, {"scripts": {"build": "vite build"}})
    _raise_timeout(monkeypatch)

    with pytest.raises(ToolError, match="npm run build timed out after 180 seconds"):
        build_project(script="build")


# ── the read-only git surface ────────────────────────────────────────────────


def test_git_requires_a_subcommand(workspace) -> None:
    with pytest.raises(ToolError, match="Git subcommand is required"):
        _run_git([])


@pytest.mark.parametrize("subcommand", ["push", "clone", "commit", "config"])
def test_only_read_only_git_subcommands_are_accepted(workspace, subcommand: str) -> None:
    with pytest.raises(ToolError, match="not allowed: " + subcommand):
        _run_git([subcommand])


@pytest.mark.parametrize(
    "remote",
    ["git@github.com:owner/repo.git", "https://example.com/r.git", "http://x/r", "ssh://h/r"],
)
def test_remote_git_targets_are_refused(workspace, remote: str) -> None:
    """A read-only surface still reaches the network if a remote is accepted."""
    with pytest.raises(ToolError, match="Remote git targets"):
        _run_git(["log", remote])


def test_git_refuses_a_working_directory_that_does_not_exist(workspace, monkeypatch) -> None:
    calls = _record_run(monkeypatch)

    with pytest.raises(ToolError, match="Working directory does not exist"):
        git_status(cwd="ghost")

    assert calls == []


def test_git_status_runs_the_short_form_in_the_workspace(workspace, monkeypatch) -> None:
    calls = _record_run(monkeypatch, stdout=" M notes.txt\n")

    result = git_status()

    assert calls[0]["argv"] == ["git", "status", "--short"]
    assert calls[0]["cwd"] == workspace
    assert calls[0]["timeout"] == MAX_COMMAND_SECONDS
    assert result["command"] == "git status --short"
    assert result["cwd"] == "."
    assert result["stdout"] == " M notes.txt\n"


def test_git_diff_scopes_to_a_workspace_relative_path(workspace, monkeypatch) -> None:
    (workspace / "src").mkdir()
    (workspace / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    calls = _record_run(monkeypatch)

    git_diff(path="src/app.py")

    assert calls[0]["argv"] == ["git", "diff", "--", "src/app.py"]


def test_git_diff_without_a_path_still_terminates_the_option_list(workspace, monkeypatch) -> None:
    calls = _record_run(monkeypatch)

    git_diff()

    assert calls[0]["argv"] == ["git", "diff", "--"]


def test_git_diff_cannot_be_pointed_outside_the_workspace(workspace) -> None:
    with pytest.raises(ToolError, match="escapes the agent workspace"):
        git_diff(path="../../etc")


def test_git_log_clamps_the_requested_count(workspace, monkeypatch) -> None:
    calls = _record_run(monkeypatch)

    git_log(max_count=0)
    git_log(max_count=999)

    assert calls[0]["argv"][2] == "--max-count=1"
    assert calls[1]["argv"][2] == "--max-count=20"


@pytest.mark.parametrize("revision", ["-x", "HEAD~1..HEAD", "HEAD:file", "refs/heads/main", "a\\b"])
def test_git_show_refuses_revisions_that_could_name_a_path_or_a_flag(
    workspace, revision: str
) -> None:
    with pytest.raises(ToolError, match="Revision is not allowed"):
        git_show(revision=revision)


def test_git_show_passes_an_ordinary_revision_through(workspace, monkeypatch) -> None:
    calls = _record_run(monkeypatch, stdout="abc123 subject\n")

    result = git_show(revision="abc123")

    assert calls[0]["argv"] == ["git", "show", "--stat", "--oneline", "--decorate", "abc123"]
    assert result["returncode"] == 0


def test_a_hanging_git_command_becomes_a_tool_error(workspace, monkeypatch) -> None:
    _raise_timeout(monkeypatch)

    with pytest.raises(ToolError, match="Git command timed out"):
        git_status()
