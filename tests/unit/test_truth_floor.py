"""T1 "Truth & safety floor" — authz, leak, recall, and honesty regressions.

Covers docs/V4_IMPLEMENTATION_PLAN.md track T1 items 1-5:
by-id snapshot/memory authorization, /workspace/os registry leak, chat
context user isolation, MemoryService.recall graph branch + honest scoring,
and simulation-mode labeling of agent/workflow run records.
"""

from pathlib import Path

import pytest

from lattice_brain.runtime.multi_agent import MultiAgentOrchestrator
from latticeai.api.chat import pair_user_history
from latticeai.core.workspace_os import WorkspaceOSStore
from latticeai.services.memory_service import MemoryService
from latticeai.services.workspace_service import WorkspaceService


class FakeGraph:
    """Counts ingest_event calls; returns a deterministic node id."""

    def __init__(self):
        self.events = []

    def ingest_event(self, event_type, title, **kwargs):
        self.events.append((event_type, title))
        return {"node_id": f"event:{len(self.events)}"}


def _store_with_org(tmp_path: Path):
    store = WorkspaceOSStore(tmp_path)
    org = store.create_organization_workspace(name="Acme", owner_user_id="owner@acme.com")
    return store, org["workspace_id"]


# ── item 1: by-id authorization ────────────────────────────────────────────

def test_snapshot_read_denied_for_non_member(tmp_path):
    store, org_id = _store_with_org(tmp_path)
    svc = WorkspaceService(store)
    snapshot = {"id": "snap-1", "workspace_id": org_id}

    svc.authorize_record_read(snapshot, "owner@acme.com")  # member: ok
    with pytest.raises(PermissionError):
        svc.authorize_record_read(snapshot, "stranger@other.com")


def test_legacy_snapshot_without_workspace_stays_readable(tmp_path):
    store, _ = _store_with_org(tmp_path)
    svc = WorkspaceService(store)
    svc.authorize_record_read({"id": "snap-legacy"}, "stranger@other.com")


def test_memory_delete_requires_owner_or_workspace_write(tmp_path):
    store, org_id = _store_with_org(tmp_path)
    svc = WorkspaceService(store)

    owned = {"id": "m1", "user_email": "alice@acme.com", "workspace_id": org_id}
    svc.authorize_memory_delete(owned, "alice@acme.com")  # owner: ok
    svc.authorize_memory_delete(owned, "owner@acme.com")  # org owner has write: ok
    with pytest.raises(PermissionError):
        svc.authorize_memory_delete(owned, "stranger@other.com")

    # Owned, no workspace: only the owner may delete.
    personal = {"id": "m2", "user_email": "alice@acme.com"}
    with pytest.raises(PermissionError):
        svc.authorize_memory_delete(personal, "bob@acme.com")

    # Ownerless legacy record without workspace: pre-v4 behaviour preserved.
    svc.authorize_memory_delete({"id": "m3"}, "anyone@example.com")


def test_get_memory_returns_record_and_delete_scopes_timeline(tmp_path):
    store = WorkspaceOSStore(tmp_path)
    record = store.upsert_memory(kind="preferences", content="dark mode", user_email="a@b.c")
    assert store.get_memory(record["id"])["id"] == record["id"]
    store.delete_memory(record["id"])
    with pytest.raises(FileNotFoundError):
        store.get_memory(record["id"])


# ── item 2: /workspace/os registry leak ────────────────────────────────────

def test_store_summary_does_not_expose_raw_workspace_registry(tmp_path):
    store, org_id = _store_with_org(tmp_path)
    summary = store.summary()
    assert "workspaces" not in summary
    assert summary["workspace_count"] >= 2  # personal + org

    # The service summary exposes only the membership-filtered registry.
    svc = WorkspaceService(store)
    data = svc.summary("stranger@other.com")
    assert "workspaces" not in data
    listed = {ws["workspace_id"] for ws in data["workspace_registry"]["workspaces"]}
    assert org_id not in listed


# ── item 3: chat context user isolation ────────────────────────────────────

def test_pair_user_history_excludes_other_users_messages():
    history = [
        {"role": "user", "user_email": "alice@x.com", "content": "alice q1"},
        {"role": "assistant", "content": "reply to alice"},
        {"role": "user", "user_email": "bob@x.com", "content": "bob secret"},
        {"role": "assistant", "content": "reply to bob"},
        {"role": "user", "user_email": "alice@x.com", "content": "alice q2"},
        {"role": "assistant", "content": "reply 2 to alice"},
    ]
    contents = [item["content"] for item in pair_user_history(history, "alice@x.com")]
    assert contents == ["alice q1", "reply to alice", "alice q2", "reply 2 to alice"]
    assert all("bob" not in c for c in contents)


# ── item 4: recall graph branch + honest scoring ───────────────────────────

class _RecallStore:
    def search_memories(self, q, user_email=None, limit=20, workspace_id=None):
        return {"memories": [
            {"id": "m1", "kind": "preferences", "content": "alpha beta", "tags": []},
        ]}


class _RecallKG:
    def search(self, q, limit):
        # The real KnowledgeGraph.search returns "matches", not "results".
        return {"query": q, "matches": [
            {"id": "node:1", "title": "alpha doc", "summary": "all about alpha", "type": "Document"},
        ]}


def test_recall_includes_graph_matches_with_real_scores(tmp_path):
    svc = MemoryService(store=_RecallStore(), data_dir=tmp_path, knowledge_graph=_RecallKG(), enable_graph=True)
    res = svc.recall("alpha beta")
    sources = {r["source"] for r in res["results"]}
    assert "graph" in sources, "graph branch must contribute results (matches key)"
    by_id = {r["id"]: r for r in res["results"]}
    # alpha+beta both in the memory -> 1.0; only alpha in the graph node -> 0.5
    assert by_id["m1"]["score"] == 1.0
    assert by_id["node:1"]["score"] == 0.5
    assert by_id["m1"]["score"] != 0.6, "fabricated constant score must be gone"


# ── item 5: simulation labeling of run records ─────────────────────────────

def test_simulated_agent_run_is_labeled_and_kept_out_of_graph(tmp_path):
    store = WorkspaceOSStore(tmp_path)
    graph = FakeGraph()
    run = store.record_agent_run(
        agent_id="agent:executor", status="ok", input_text="goal", output_text="done",
        user_email="a@b.c", graph=graph,
    )
    assert run["mode"] == "simulation"
    assert run["record_schema_version"] == 2
    assert run["graph_node_id"] is None
    assert "simulation" in run["graph_skipped"]
    assert graph.events == [], "simulated runs must not write to the knowledge graph"


def test_llm_mode_agent_run_still_links_graph(tmp_path):
    store = WorkspaceOSStore(tmp_path)
    graph = FakeGraph()
    run = store.record_agent_run(
        agent_id="agent:executor", status="ok", input_text="goal", output_text="done",
        user_email="a@b.c", graph=graph, mode="llm",
    )
    assert run["mode"] == "llm"
    assert run["graph_node_id"] == "event:1"


def test_simulated_workflow_run_is_labeled_and_kept_out_of_graph(tmp_path):
    store = WorkspaceOSStore(tmp_path)
    graph = FakeGraph()
    run = store.record_workflow_run(
        workflow_id="wf-1", name="demo", status="ok", timeline=[], graph=graph,
    )
    assert run["mode"] == "simulation"
    assert run["record_schema_version"] == 2
    assert run["graph_node_id"] is None
    assert graph.events == []


def test_orchestrator_declares_simulation_mode():
    result = MultiAgentOrchestrator().run("test goal")
    assert result.mode == "simulation"
    assert result.as_dict()["mode"] == "simulation"
