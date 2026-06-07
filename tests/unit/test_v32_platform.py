"""Unit tests for the v3.2.0 platform additions.

Covers the hooks registry, agent registry, unified memory service, and the
agent-template marketplace — all exercised through their public interfaces with
no FastAPI / MLX dependency, mirroring the existing unit-test style.
"""

from __future__ import annotations

import pytest

from latticeai.core.hooks import HooksRegistry, HOOK_KINDS, BUILTIN_HOOKS
from latticeai.core.agent_registry import AgentRegistry, AGENT_TYPES
from latticeai.core.marketplace import TemplateCatalog, MARKETPLACE_VERSION
from latticeai.services.memory_service import MemoryService, TIERS


# ── Hooks registry ─────────────────────────────────────────────────────────
def test_hooks_builtins_and_kinds(tmp_path):
    reg = HooksRegistry(tmp_path / "hooks.json")
    listing = reg.list()
    assert listing["total"] == len(BUILTIN_HOOKS)
    assert set(listing["kinds"]) == set(HOOK_KINDS)
    # every hook carries the metadata the UI relies on
    for hook in listing["hooks"]:
        assert {"id", "name", "kind", "enabled", "order", "source"} <= set(hook)


def test_hooks_toggle_persists(tmp_path):
    path = tmp_path / "hooks.json"
    reg = HooksRegistry(path)
    reg.set_enabled("builtin:audit-agent-run", False)
    assert reg.get("builtin:audit-agent-run")["enabled"] is False
    # reload from disk
    assert HooksRegistry(path).get("builtin:audit-agent-run")["enabled"] is False


def test_hooks_register_inspect_remove(tmp_path):
    reg = HooksRegistry(tmp_path / "hooks.json")
    entry = reg.register(name="Lint Gate", kind="pre_tool", description="run lint", command="npm run lint")
    assert entry["id"].startswith("user:")
    assert reg.inspect(entry["id"])["advisory"] is True
    reg.remove(entry["id"])
    assert reg.get(entry["id"]) is None
    with pytest.raises(ValueError):
        reg.remove("builtin:audit-agent-run")


def test_hooks_register_rejects_bad_kind(tmp_path):
    reg = HooksRegistry(tmp_path / "hooks.json")
    with pytest.raises(ValueError):
        reg.register(name="x", kind="not-a-kind")


# ── Agent registry ─────────────────────────────────────────────────────────
def test_agent_registry_builtins(tmp_path):
    reg = AgentRegistry(tmp_path / "areg.json")
    listing = reg.list()
    assert listing["total"] == 5  # researcher/planner/executor/reviewer/release
    assert reg.get("agent:executor")["capabilities"]
    assert set(listing["types"]) == set(AGENT_TYPES)


def test_agent_registry_capabilities_index(tmp_path):
    reg = AgentRegistry(tmp_path / "areg.json")
    caps = reg.capabilities()
    assert "agent:executor" in caps["tool-use"]
    assert reg.discover("verification")[0]["id"] == "agent:reviewer"


def test_agent_registry_register_update_remove(tmp_path):
    path = tmp_path / "areg.json"
    reg = AgentRegistry(path)
    agent = reg.register(name="Doc Bot", agent_type="custom", capabilities=["summarize"])
    assert agent["id"] == "agent:custom:doc-bot"
    reg.update_config(agent["id"], {"model": "local"}, enabled=False)
    reloaded = AgentRegistry(path).get(agent["id"])
    assert reloaded["config"] == {"model": "local"} and reloaded["enabled"] is False
    AgentRegistry(path).remove(agent["id"])
    assert AgentRegistry(path).get(agent["id"]) is None


def test_agent_registry_builtin_not_removable(tmp_path):
    reg = AgentRegistry(tmp_path / "areg.json")
    with pytest.raises(ValueError):
        reg.remove("agent:planner")


# ── Memory service ─────────────────────────────────────────────────────────
class _FakeStore:
    def __init__(self):
        self._m = []

    def add(self, mid, kind, content, ws="personal"):
        self._m.append({"id": mid, "kind": kind, "content": content, "workspace_id": ws, "tags": []})

    def list_memories(self, user_email=None, kind=None, workspace_id=None):
        ms = [m for m in self._m if kind is None or m["kind"] == kind]
        if workspace_id is not None:
            ms = [m for m in ms if (m.get("workspace_id") or "personal") == workspace_id]
        return {"memories": list(reversed(ms))}

    def list_memory_snapshots(self, workspace_id=None, limit=50):
        return {"snapshots": [{"snapshot_id": "s1"}]}

    def search_memories(self, q, user_email=None, limit=20, workspace_id=None):
        ql = (q or "").lower()
        return {"memories": [m for m in self._m if ql in m["content"].lower()][:limit]}

    def delete_memory(self, mid):
        before = len(self._m)
        self._m = [m for m in self._m if m["id"] != mid]
        if len(self._m) == before:
            raise FileNotFoundError(mid)
        return {"status": "ok"}


def _svc(tmp_path):
    store = _FakeStore()
    store.add("m1", "workspace", "alpha note")
    store.add("m2", "workspace", "alpha note")  # duplicate of m1
    store.add("m3", "decisions", "ship v3.2", ws="org:acme")
    return MemoryService(store=store, data_dir=tmp_path, knowledge_graph=None, enable_graph=False)


def test_memory_manager_tiers(tmp_path):
    svc = _svc(tmp_path)
    mgr = svc.manager()
    assert set(mgr["tiers"]) == set(TIERS)
    assert mgr["usage"]["total_items"] >= 3
    ids = {s["id"] for s in mgr["sources"]}
    assert {"workspace", "project", "agent", "conversation", "graph", "vector"} == ids


def test_memory_recall_and_inspect(tmp_path):
    svc = _svc(tmp_path)
    res = svc.recall("alpha")
    assert {r["id"] for r in res["results"]} == {"m1", "m2"}
    assert svc.inspect("project")["count"] == 1


def test_memory_compact_dedupes(tmp_path):
    svc = _svc(tmp_path)
    out = svc.compact()
    assert out["compacted"] == 1
    assert svc.inspect("workspace")["count"] == 1


def test_memory_clear_requires_confirm(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(ValueError):
        svc.clear(scope="decisions")
    cleared = svc.clear(scope="decisions", confirm=True)
    assert cleared["count"] == 1


# ── Marketplace agent templates ────────────────────────────────────────────
def test_marketplace_has_five_named_agent_templates():
    catalog = TemplateCatalog()
    names = {t["name"] for t in catalog.list_templates(kind="agent")["templates"]}
    assert {
        "Research Assistant",
        "Coding Assistant",
        "Knowledge Curator",
        "Documentation Writer",
        "Workflow Builder",
    } <= names
    assert MARKETPLACE_VERSION == "3.3.0"


def test_marketplace_clone_and_roundtrip():
    catalog = TemplateCatalog()
    clone = catalog.clone_template("agent", "agent-coding-assistant", "My Coder")
    assert clone["metadata"]["cloned_from"] == "agent-coding-assistant"
    assert clone["id"] != "agent-coding-assistant"
    exported = catalog.export_template("agent", "agent-research-assistant")
    imported = catalog.import_template(exported)
    assert imported["id"] == "agent-research-assistant"
    assert imported["metadata"]["imported"] is True
