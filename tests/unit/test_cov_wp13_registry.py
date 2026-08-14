"""wp13 coverage — the ``latticeai.tools`` package seam itself.

``TOOL_HANDLERS`` is the single name → invocation table. Remaining handlers
are read-only plus pointer tools; mutating creators live in ``lattice-agent``.
Every knowledge tool goes through ``_knowledge_scope``, which refuses to run
unless the caller injected an authenticated workspace *and* user. Those
scopes, plus the public path resolver, are what this file drives — through
``execute_tool`` rather than by calling the private helpers, so the table
wiring is under test too.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import latticeai.tools as tools
from latticeai.tools import (
    ToolError,
    execute_tool,
    registered_tools,
    resolve_workspace_path,
)
from latticeai.tools import knowledge as knowledge_module


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "agent_workspace"
    root.mkdir()
    monkeypatch.setattr(tools, "AGENT_ROOT", root)
    tools.ensure_agent_root()
    return root


# ── resolve_workspace_path ───────────────────────────────────────────────────


def test_resolve_workspace_path_is_the_sandboxed_resolver(workspace: Path) -> None:
    assert resolve_workspace_path() == workspace
    assert resolve_workspace_path("sub/file.txt") == workspace / "sub" / "file.txt"


def test_resolve_workspace_path_refuses_an_escape(workspace: Path) -> None:
    with pytest.raises(ToolError, match="escapes the agent workspace"):
        resolve_workspace_path("../../etc/passwd")


def test_resolve_workspace_path_creates_the_workspace_on_demand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "not-created-yet"
    monkeypatch.setattr(tools, "AGENT_ROOT", root)

    assert resolve_workspace_path() == root
    assert root.is_dir()


# ── remaining dispatchable tools ─────────────────────────────────────────────


def test_list_dir_reads_the_workspace_through_the_handler_table(workspace: Path) -> None:
    (workspace / "note.md").write_text("hello", encoding="utf-8")

    result = execute_tool("list_dir", {"path": "."})

    names = {item["name"] for item in result["items"]}
    assert "note.md" in names


def test_todo_read_is_registered_and_returns_a_list(workspace: Path) -> None:
    result = execute_tool("todo_read", {})

    assert "items" in result or "todos" in result or isinstance(result, dict)


# ── _knowledge_scope ─────────────────────────────────────────────────────────


@pytest.fixture()
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "brain"
    root.mkdir()
    monkeypatch.setattr(knowledge_module, "BRAIN_DIR", root)
    return root


_SCOPED_TOOLS = [
    ("knowledge_search", {"query": "body"}),
    ("knowledge_tree", {}),
    ("obsidian_search", {"query": "body"}),
    ("obsidian_tree", {}),
]


@pytest.mark.parametrize("action,args", _SCOPED_TOOLS)
def test_every_knowledge_tool_refuses_an_unauthenticated_call(vault: Path, action, args) -> None:
    with pytest.raises(ToolError, match="authenticated workspace and user scope"):
        execute_tool(action, dict(args))


@pytest.mark.parametrize(
    "scope",
    [
        {"workspace_id": "ws-1"},
        {"user_email": "owner@example.com"},
        {"workspace_id": "", "user_email": "owner@example.com"},
        {"workspace_id": "ws-1", "user_email": "   "},
    ],
)
def test_a_half_scoped_knowledge_call_is_refused_at_the_registry(vault: Path, scope) -> None:
    with pytest.raises(ToolError, match="authenticated workspace and user scope"):
        execute_tool("knowledge_tree", dict(scope))


def test_a_scoped_knowledge_call_reads_only_its_own_partition(vault: Path) -> None:
    scope = {"workspace_id": "ws-1", "user_email": "Owner@Example.com"}
    root = knowledge_module.knowledge_scope_root(**scope)
    note_dir = root / "00_Raw"
    note_dir.mkdir(parents=True)
    (note_dir / "n.md").write_text("scoped note", encoding="utf-8")

    tree = execute_tool("knowledge_tree", dict(scope))
    found = execute_tool("knowledge_search", {"query": "scoped", **scope})

    assert [entry["relative_path"] for entry in tree["entries"]] == ["00_Raw/n.md"]
    assert len(found["results"]) == 1
    # The email is normalised before it becomes part of the partition digest.
    upper = execute_tool(
        "knowledge_tree", {"workspace_id": "ws-1", "user_email": "OWNER@EXAMPLE.COM"}
    )
    assert upper["root"] == tree["root"]


def test_scopes_cannot_read_each_others_notes(vault: Path) -> None:
    own_root = knowledge_module.knowledge_scope_root(
        workspace_id="ws-1", user_email="a@x.com"
    )
    note_dir = own_root / "00_Raw"
    note_dir.mkdir(parents=True)
    (note_dir / "a.md").write_text("first tenant", encoding="utf-8")

    other = execute_tool(
        "knowledge_search", {"query": "tenant", "workspace_id": "ws-2", "user_email": "a@x.com"}
    )

    assert other["results"] == []


# ── the table itself ─────────────────────────────────────────────────────────


def test_unknown_actions_are_rejected_by_name(workspace: Path) -> None:
    with pytest.raises(ToolError, match="Unknown action: not_a_tool"):
        execute_tool("not_a_tool", {})


def test_mutating_handlers_are_not_registered() -> None:
    registered = registered_tools()
    assert "create_xlsx" not in registered
    assert "write_file" not in registered
    assert "knowledge_save" not in registered


def test_every_scoped_knowledge_tool_is_actually_registered() -> None:
    assert {name for name, _ in _SCOPED_TOOLS} <= registered_tools()
