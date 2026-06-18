"""Unit tests for the v3.2.0 platform additions.

Covers the hooks registry, agent registry, unified memory service, and the
agent-template marketplace — all exercised through their public interfaces with
no FastAPI / MLX dependency, mirroring the existing unit-test style.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lattice_brain.runtime.hooks import HooksRegistry, HOOK_KINDS, BUILTIN_HOOKS
from latticeai.core.agent_registry import AgentRegistry, AGENT_TYPES
from latticeai.core.marketplace import TemplateCatalog, MARKETPLACE_VERSION
from latticeai.api.memory import create_memory_router
from latticeai.services.memory_service import MemoryService, TIERS

ROOT = Path(__file__).resolve().parents[2]


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
    assert mgr["brain_readiness"]["source"] == "memory_service"
    assert mgr["brain_readiness"]["signals"]["memory_count"] >= 3
    ids = {s["id"] for s in mgr["sources"]}
    assert {"workspace", "project", "agent", "conversation", "graph", "vector"} == ids


def test_memory_brain_quality_summary_uses_backend_growth_signals(tmp_path):
    class _FakeKG:
        def stats(self):
            return {"nodes": {"concept": 4}, "edges": {"relates": 3}}

        def index_status(self):
            return {"vector_counts": {"node": 4}}

    store = _FakeStore()
    store.add("m1", "workspace", "alpha note")
    svc = MemoryService(store=store, data_dir=tmp_path, knowledge_graph=_FakeKG(), enable_graph=True)

    summary = svc.brain_quality_summary(user_email="user@example.com", workspace_id="personal")
    assert summary["state"] == "alive"
    assert summary["depth"] == 5
    assert summary["title_key"] == "brain.readiness.alive"
    assert summary["action_key"] == "brain.readiness.map"
    assert summary["signals"] == {
        "memory_count": 2,
        "concept_count": 4,
        "relationship_count": 3,
        "healthy_sources": 5,
    }


def test_memory_brain_proof_shows_model_independent_recall(tmp_path):
    class _FakeKG:
        def stats(self):
            return {"nodes": {"concept": 4}, "edges": {"relates": 3}}

        def index_status(self):
            return {"vector_counts": {"node": 4}}

        def search(self, query, limit):
            return {"matches": [{"id": "node:alpha", "title": "Alpha plan", "summary": f"{query} graph context"}]}

    store = _FakeStore()
    store.add("m1", "decisions", "alpha launch decision")
    svc = MemoryService(store=store, data_dir=tmp_path, knowledge_graph=_FakeKG(), enable_graph=True)

    proof = svc.brain_proof(
        user_email="user@example.com",
        workspace_id="personal",
        active_model="local:model-a",
        recall_query="alpha",
    )

    assert proof["model_continuity"]["active_model"] == "local:model-a"
    # Capability is a design property (always on); proof requires evidence.
    assert proof["model_continuity"]["capability"] is True
    assert proof["model_continuity"]["survives_model_switch"] is True
    assert proof["model_continuity"]["proven"] is True
    assert proof["proofs"]["durable_items"] >= 5
    assert proof["proofs"]["has_durable_evidence"] is True
    assert proof["claims"]["can_recall_user_context"] is True
    assert proof["claims"]["keeps_context_across_models"] is True
    assert proof["claims"]["is_knowledge_store"] is True
    assert proof["recall"]["items"][0]["title"] in {"decisions", "Alpha plan"}


def test_memory_brain_proof_empty_brain_keeps_capability_but_no_proof(tmp_path):
    """An empty brain must advertise the capability but never *claim proof*.

    No durable evidence on disk means survives_model_switch / proven /
    continuity claim must all be false, so the first-run screen cannot show a
    hollow badge.
    """
    svc = MemoryService(store=_FakeStore(), data_dir=tmp_path, knowledge_graph=None, enable_graph=False)

    proof = svc.brain_proof(user_email="user@example.com", workspace_id="personal", active_model="local:model-a")

    assert proof["model_continuity"]["capability"] is True
    assert proof["model_continuity"]["survives_model_switch"] is False
    assert proof["model_continuity"]["proven"] is False
    assert proof["proofs"]["durable_items"] == 0
    assert proof["proofs"]["has_durable_evidence"] is False
    assert proof["claims"]["keeps_context_across_models"] is False
    assert proof["claims"]["is_knowledge_store"] is False
    assert proof["recall"]["items"] == []


def test_memory_brain_proof_endpoint_separates_capability_from_proof(tmp_path):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    store = _FakeStore()
    store.add("m1", "workspace", "alpha launch decision")
    svc = MemoryService(store=store, data_dir=tmp_path, knowledge_graph=None, enable_graph=False)

    app = fastapi.FastAPI()
    app.include_router(
        create_memory_router(
            service=svc,
            require_user=lambda request: "user@example.com",
            get_current_user=lambda request: "user@example.com",
            gate_read=lambda request: "personal",
            gate_write=lambda request: "personal",
            append_audit_event=lambda *a, **k: None,
            active_model_getter=lambda: "local:model-a",
        )
    )
    client = TestClient(app)

    body = client.get("/api/memory/brain-proof", params={"q": "alpha"}).json()
    assert body["model_continuity"]["active_model"] == "local:model-a"
    assert body["model_continuity"]["capability"] is True
    assert body["model_continuity"]["proven"] is True
    assert body["proofs"]["has_durable_evidence"] is True


def test_memory_brain_proof_default_recall_query_is_workspace_scoped(tmp_path):
    class _FakeConversationStore(_FakeStore):
        def __init__(self):
            super().__init__()
            self._history = [
                {
                    "role": "user",
                    "content": "own personal alpha decision",
                    "user_email": "user@example.com",
                    "workspace_id": "personal",
                },
                {
                    "role": "user",
                    "content": "own org beta secret",
                    "user_email": "user@example.com",
                    "workspace_id": "org:acme",
                },
                {
                    "role": "user",
                    "content": "other personal gamma secret",
                    "user_email": "other@example.com",
                    "workspace_id": "personal",
                },
            ]

        def history(self):
            return list(self._history)

    conversation_store = _FakeConversationStore()
    svc = MemoryService(
        store=_FakeStore(),
        data_dir=tmp_path,
        knowledge_graph=None,
        enable_graph=False,
        conversation_store=conversation_store,
    )

    proof = svc.brain_proof(user_email="user@example.com", workspace_id="personal", recall_query="")

    assert proof["recall"]["query"] == "own personal alpha decision"
    assert "gamma" not in proof["recall"]["query"]
    assert "beta" not in proof["recall"]["query"]


def test_memory_brain_proof_default_recall_query_normalizes_personal_workspace(tmp_path):
    class _FakeConversationStore:
        def history(self):
            return [
                {
                    "role": "user",
                    "content": "own implicit personal decision",
                    "user_email": "user@example.com",
                    "workspace_id": None,
                },
                {
                    "role": "user",
                    "content": "own explicit personal decision",
                    "user_email": "user@example.com",
                    "workspace_id": "personal",
                },
            ]

    svc = MemoryService(
        store=_FakeStore(),
        data_dir=tmp_path,
        knowledge_graph=None,
        enable_graph=False,
        conversation_store=_FakeConversationStore(),
    )

    proof = svc.brain_proof(user_email="user@example.com", workspace_id=None, recall_query="")

    assert proof["recall"]["query"] == "own explicit personal decision"


def test_memory_brain_proof_workspace_memory_seed_normalizes_personal_workspace(tmp_path):
    store = _FakeStore()
    store.add("m1", "workspace", "implicit personal workspace memory", ws=None)
    store.add("m2", "workspace", "org workspace memory", ws="org:acme")
    svc = MemoryService(store=store, data_dir=tmp_path, knowledge_graph=None, enable_graph=False)

    proof = svc.brain_proof(user_email="user@example.com", workspace_id=None, recall_query="")

    assert proof["recall"]["query"] == "implicit personal workspace memory"
    assert "org" not in proof["recall"]["query"]


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
    release = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    catalog = TemplateCatalog()
    names = {t["name"] for t in catalog.list_templates(kind="agent")["templates"]}
    assert {
        "Research Assistant",
        "Coding Assistant",
        "Knowledge Curator",
        "Documentation Writer",
        "Workflow Builder",
    } <= names
    assert MARKETPLACE_VERSION == release


def test_marketplace_clone_and_roundtrip():
    catalog = TemplateCatalog()
    clone = catalog.clone_template("agent", "agent-coding-assistant", "My Coder")
    assert clone["metadata"]["cloned_from"] == "agent-coding-assistant"
    assert clone["id"] != "agent-coding-assistant"
    exported = catalog.export_template("agent", "agent-research-assistant")
    imported = catalog.import_template(exported)
    assert imported["id"] == "agent-research-assistant"
    assert imported["metadata"]["imported"] is True
