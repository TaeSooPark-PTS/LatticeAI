"""wpb05 — core-package guards whose *other* side had never been taken.

Same contract as the sibling wpb05 files: one arc per test, driven through the
real object with injected fakes, asserted on the value it produces. Nothing
here reads a default user directory — every path is a ``tmp_path`` and every
optional import is a fake installed for the duration of one test, so the arcs
run identically on a clean CI runner.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from latticeai.core import mcp_registry
from latticeai.core import quiet as quiet_module
from latticeai.core.agent_eval import Scenario, _run_scenario
from latticeai.core.context_builder import _extract_sources
from latticeai.core.document_generator import DocumentGenerationSession
from latticeai.core.embedding_providers import (
    DEFAULT_EMBEDDING_DIM,
    OllamaEmbeddingProvider,
    _RemoteConfig,
)
from latticeai.core.plugins import PLUGIN_SDK_VERSION, PluginRegistry
from latticeai.core.quiet import quiet
from latticeai.core.workspace_indexing import WorkspaceIndexing
from latticeai.core.workspace_memory import WorkspaceMemory
from latticeai.core.workspace_skills import WorkspaceSkills

# ── workspace_skills ─────────────────────────────────────────────────────────


class _SkillStore:
    def __init__(self) -> None:
        self.state: Dict[str, Any] = {}
        self.saves = 0

    def load_state(self) -> Dict[str, Any]:
        return self.state

    def save_state(self, state: Dict[str, Any]) -> None:
        self.state = state
        self.saves += 1


def test_skill_registry_reports_a_marketplace_when_no_skills_dir_exists(tmp_path: Path):
    """A fresh install has no skills folder yet; the catalog still renders."""
    store = _SkillStore()

    listing = WorkspaceSkills(store).list_skill_registry(
        tmp_path / "never-created",
        marketplace=[{"skill": "note-taking", "version": "1.2.0"}],
    )

    assert listing["installed"] == []
    assert listing["total_installed"] == 0
    assert listing["total_available"] == 1
    assert listing["available"][0]["install_status"] == "available"
    assert listing["available"][0]["validation_status"] == "not_installed"
    assert store.saves == 1


def test_a_skill_manifest_with_no_description_line_is_listed_with_an_empty_one(tmp_path: Path):
    """The description scan runs to exhaustion instead of breaking early."""
    skills = tmp_path / "skills"
    (skills / "quiet-skill").mkdir(parents=True)
    (skills / "quiet-skill" / "SKILL.md").write_text(
        "# Quiet skill\n\nno front matter here\n", encoding="utf-8"
    )

    listing = WorkspaceSkills(_SkillStore()).list_skill_registry(skills)

    assert len(listing["installed"]) == 1
    entry = listing["installed"][0]
    assert entry["name"] == "quiet-skill"
    assert entry["description"] == ""
    assert entry["version"] == "local", "no schema.json means the local version"
    assert entry["validation_status"] == "ready"


# ── workspace_indexing ───────────────────────────────────────────────────────


class _IndexStore:
    def __init__(self) -> None:
        self.events: List[tuple] = []

    def record_timeline_event(self, *args: Any, **kwargs: Any) -> None:
        self.events.append((args, kwargs))


def test_removing_a_source_without_a_watcher_still_drops_it_from_the_graph():
    store = _IndexStore()
    removed: List[str] = []
    graph = SimpleNamespace(
        remove_local_source=lambda source_id: removed.append(source_id) or {"removed": 1},
    )

    result = WorkspaceIndexing(store).remove_source(graph, "src-1", watcher=None)

    assert removed == ["src-1"]
    assert result == {"status": "ok", "removed": 1}
    assert store.events[0][0] == ("graph", "indexing_removed", {"source_id": "src-1"})


# ── workspace_memory ─────────────────────────────────────────────────────────


class _MemoryStore:
    def __init__(self) -> None:
        self.state: Dict[str, Any] = {}
        self.events: List[tuple] = []

    def load_state(self) -> Dict[str, Any]:
        return self.state

    def save_state(self, state: Dict[str, Any]) -> None:
        self.state = state

    def record_timeline_event(self, *args: Any, **kwargs: Any) -> None:
        self.events.append((args, kwargs))

    def _resolve_scope(self, workspace_id: Optional[str], _state: Dict[str, Any]) -> str:
        return workspace_id or "personal"

    def _record_workspace(self, record: Dict[str, Any]) -> str:
        return str(record.get("workspace_id") or "personal")


def test_updating_an_existing_memory_rewrites_it_in_place():
    """The second upsert of the same id must not append a duplicate row."""
    store = _MemoryStore()
    memory = WorkspaceMemory(store)

    memory.upsert_memory(
        kind="decisions", content="첫 결정", user_email="wpb05@example.com",
        memory_id="mem-1", workspace_id="ws-1",
    )
    updated = memory.upsert_memory(
        kind="decisions", content="바뀐 결정", user_email="wpb05@example.com",
        memory_id="mem-1", workspace_id="ws-1",
    )

    assert store.state["memories"] == [updated]
    assert updated["content"] == "바뀐 결정"
    assert updated["workspace_id"] == "ws-1"


# ── context_builder ──────────────────────────────────────────────────────────


def test_result_rows_with_nothing_to_key_on_produce_no_source_entry():
    sources = _extract_sources(
        [
            {"id": "", "type": "Note", "title": "이름 없는 조각", "metadata": {}},
            {"id": "n-1", "type": "Note", "title": "진짜 조각", "metadata": {"filename": "a.md"}},
        ]
    )

    assert sources == [
        {"id": "n-1", "type": "Note", "title": "진짜 조각", "source": "a.md"}
    ]


# ── document_generator ───────────────────────────────────────────────────────


def test_a_document_update_without_a_conversation_id_keeps_the_previous_one():
    session = DocumentGenerationSession()
    session.update("첫 컨텍스트", "첫 문서", conversation_id="c-1")

    session.update("두 번째 컨텍스트", "두 번째 문서")

    assert session.has_previous is True
    assert session._conversation_id == "c-1"
    assert "두 번째 문서" in session.get_system_prompt("")


# ── quiet ────────────────────────────────────────────────────────────────────


def test_quiet_logs_an_exception_that_arrived_without_a_traceback(monkeypatch, caplog):
    """An exception carried across a boundary keeps no frames; log it anyway."""
    error = RuntimeError("optional probe failed")
    monkeypatch.setattr(
        quiet_module,
        "sys",
        SimpleNamespace(exc_info=lambda: (RuntimeError, error, None)),
    )

    with caplog.at_level(logging.DEBUG, logger="latticeai.suppressed"):
        quiet("mlx probe")

    assert len(caplog.records) == 1
    assert caplog.records[0].getMessage() == (
        "suppressed RuntimeError at <unknown> (mlx probe): optional probe failed"
    )


# ── plugins ──────────────────────────────────────────────────────────────────


def _plugin_dir(tmp_path: Path) -> Path:
    plugins = tmp_path / "plugins"
    (plugins / "wpb05-demo").mkdir(parents=True)
    (plugins / "wpb05-demo" / "plugin.json").write_text(
        json.dumps(
            {
                "id": "wpb05-demo",
                "name": "Demo",
                "version": "1.0.0",
                "lattice_version": PLUGIN_SDK_VERSION,
                "provides": {"skills": ["demo_skill"]},
            }
        ),
        encoding="utf-8",
    )
    return plugins


def test_installing_a_plugin_without_a_store_returns_an_empty_registry_entry(tmp_path: Path):
    """No Workspace OS store wired: the skill still registers, nothing persists."""
    registered: List[tuple] = []

    result = PluginRegistry(_plugin_dir(tmp_path), store=None).install(
        "wpb05-demo", register_skill=lambda skill, plugin: registered.append((skill, plugin)),
    )

    assert registered == [("demo_skill", "wpb05-demo")]
    assert result["registered_skills"] == ["demo_skill"]
    assert result["registry"] == {}
    assert result["plugin"]["id"] == "wpb05-demo"


# ── embedding_providers ──────────────────────────────────────────────────────


def test_an_explicit_ollama_dimension_is_kept_instead_of_being_guessed():
    provider = OllamaEmbeddingProvider(_RemoteConfig(model="bge-m3", dim=512))

    assert provider.dim == 512
    assert provider.model_id == "ollama:bge-m3:512"


def test_an_unset_ollama_dimension_falls_back_to_the_guess():
    provider = OllamaEmbeddingProvider(_RemoteConfig(model="unknown-model", dim=0))

    assert provider.dim == DEFAULT_EMBEDDING_DIM
    assert provider.model_id == f"ollama:unknown-model:{DEFAULT_EMBEDDING_DIM}"


# ── mcp_registry ─────────────────────────────────────────────────────────────


def test_installing_a_bundled_mcp_writes_state_without_running_a_package_manager(
    monkeypatch, tmp_path: Path
):
    """``builtin`` matches none of the installer branches — nothing is spawned."""
    async def _registry() -> List[Dict[str, Any]]:
        return [
            {
                "id": "wpb05-bundled",
                "name": "Bundled",
                "install_mode": "builtin",
                "description": "",
                "capabilities": [],
            }
        ]

    monkeypatch.setattr(mcp_registry, "_get_combined_registry", _registry)
    monkeypatch.setattr(
        mcp_registry,
        "_run_installer",
        lambda command, timeout: pytest.fail(f"no installer may run: {command}"),
    )
    state = mcp_registry.create_mcp_install_state(tmp_path)

    public = asyncio.run(state["install_mcp"]("wpb05-bundled"))

    assert public["message"] == "MCP가 활성화되었습니다."
    stored = json.loads((tmp_path / "mcp_installs.json").read_text(encoding="utf-8"))
    assert stored["installed"]["wpb05-bundled"]["status"] == "active"
    assert stored["installed"]["wpb05-bundled"]["authenticated"] is True


# ── agent_eval ───────────────────────────────────────────────────────────────


_APPROVAL_PLAN = json.dumps(
    {
        "action": "plan",
        "state": "PLAN",
        "goal": "wipe the workspace",
        "steps": [{"action": "delete_everything", "description": "delete it all"}],
        "requires_approval": True,
        "rollback_strategy": "none",
        "estimated_steps": 1,
    }
)


def test_a_scenario_blocked_at_the_approval_gate_never_enters_execution():
    """approve() refuses, so run_to_completion is skipped and the trace is short."""
    result = asyncio.run(
        _run_scenario(
            Scenario(
                name="wpb05-approval-blocked",
                replies=[_APPROVAL_PLAN],
                expect_state="FAILED",
                expected_class="failed",
            )
        )
    )

    assert result["ok"] is True, result["failures"]
    assert result["final_state"] == "FAILED"
    assert result["tool_calls"] == 0, "the executor never ran"
