"""Regression tests for the command tool's workspace boundary."""

from pathlib import Path

import pytest

import latticeai.tools as tools_module
from latticeai.tools import ToolError, run_command


@pytest.fixture
def command_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(tools_module, "AGENT_ROOT", tmp_path.resolve())
    tools_module.ensure_agent_root()
    return tmp_path


def test_run_command_reads_file_inside_workspace(command_workspace: Path) -> None:
    (command_workspace / "safe.txt").write_text("inside\n", encoding="utf-8")

    result = run_command("cat safe.txt")

    assert result["returncode"] == 0
    assert result["stdout"] == "inside\n"


def test_run_command_rejects_parent_traversal(command_workspace: Path) -> None:
    outside = command_workspace.parent / f"{command_workspace.name}-outside.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(ToolError, match="traversal"):
        run_command(f"cat ../{outside.name}")


def test_run_command_rejects_symlink_escape(command_workspace: Path) -> None:
    outside = command_workspace.parent / f"{command_workspace.name}-outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (command_workspace / "outside-link").symlink_to(outside)

    with pytest.raises(ToolError, match="escapes"):
        run_command("cat outside-link")


@pytest.mark.parametrize("command", [
    "python -c pass",
    "python3 -c pass",
    "node -e 1",
    "npm --version",
    "npx anything",
    "sed -n 1p safe.txt",
])
def test_run_command_rejects_general_purpose_interpreters(
    command_workspace: Path,
    command: str,
) -> None:
    with pytest.raises(ToolError, match="not allowed"):
        run_command(command)


def test_run_command_rejects_workspace_executable_path(command_workspace: Path) -> None:
    with pytest.raises(ToolError, match="Executable paths"):
        run_command("./ls")


@pytest.mark.parametrize("command", [
    "find . -exec cat {}",
    "find . -fprintf results.txt %p",
    "rg --pre cat needle",
    "rg --pre=cat needle",
])
def test_run_command_rejects_command_execution_and_output_flags(
    command_workspace: Path,
    command: str,
) -> None:
    with pytest.raises(ToolError, match="flags are not allowed"):
        run_command(command)
