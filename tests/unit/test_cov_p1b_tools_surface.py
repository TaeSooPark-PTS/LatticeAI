"""Coverage for the surviving compute tools.

The two ``/tools/*`` HTTP routes this module also covered went in v11.8.0 with
``latticeai/api/tools.py``: nothing called them, and the parser behind
``read_document`` is still exercised through ``POST /worker/parse``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import latticeai.tools as tools
from latticeai.tools import ToolError
from latticeai.tools import commands as command_tools
from latticeai.tools.filesystem import (
    grep,
    list_dir,
    read_file,
    search_files,
    workspace_tree,
)
from latticeai.tools.knowledge import knowledge_search, knowledge_tree
from latticeai.tools.local_files import local_list, local_read


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch):
    root = tmp_path / "agent"
    root.mkdir()
    (root / "notes").mkdir()
    (root / "notes" / "a.md").write_text("hello lattice\nsecond line\n", encoding="utf-8")
    monkeypatch.setattr(tools, "AGENT_ROOT", root)
    return root


def test_git_helpers_cover_refusals_and_success(workspace, monkeypatch):
    with pytest.raises(ToolError):
        command_tools._run_git([])
    with pytest.raises(ToolError):
        command_tools._run_git(["push"])
    with pytest.raises(ToolError):
        command_tools._run_git(["status", "git@example.com:x.git"])
    with pytest.raises(ToolError):
        command_tools._run_git(["status"], cwd="missing-dir")

    class Done:
        returncode = 0
        stdout = "ok" * 100
        stderr = ""

    def fake_run(*_a, **_k):
        return Done()

    monkeypatch.setattr(command_tools.subprocess, "run", fake_run)
    assert command_tools.git_status()["returncode"] == 0
    assert command_tools.git_diff(path="notes/a.md")["returncode"] == 0
    assert command_tools.git_log(max_count=0)["returncode"] == 0
    assert command_tools.git_show("HEAD")["returncode"] == 0
    with pytest.raises(ToolError):
        command_tools.git_show("-bad")
    with pytest.raises(ToolError):
        command_tools.git_show("a..b")

    def timeout(*_a, **_k):
        raise command_tools.subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(command_tools.subprocess, "run", timeout)
    with pytest.raises(ToolError):
        command_tools.git_status()


def test_filesystem_read_list_grep_search(workspace):
    listed = list_dir("notes")
    assert listed
    tree = workspace_tree(".")
    assert tree
    body = read_file("notes/a.md")
    assert "hello" in str(body)
    hits = grep("lattice", path="notes")
    assert hits
    found = search_files("a.md")
    assert found


def test_knowledge_and_local_reads(tmp_path: Path, monkeypatch, workspace):
    brain = tmp_path / "brain"
    (brain / "notes").mkdir(parents=True)
    (brain / "notes" / "k.md").write_text("remember this", encoding="utf-8")
    monkeypatch.setattr("latticeai.tools.knowledge.BRAIN_DIR", brain)
    tree = knowledge_tree()
    assert tree is not None
    search = knowledge_search("remember")
    assert search is not None
    listed = local_list(str(workspace / "notes"))
    assert listed
    read = local_read(str(workspace / "notes" / "a.md"))
    assert read

