"""wp31: the memory router's manager, inspect, recall and maintenance routes.

Existing suites reach ``brain-brief`` / ``brain-proof`` / ``prune`` and the
``clear`` refusal; the manager, quality summary, tier listing, inspection
(including its unknown-source 404), recall, compact, rebuild and the confirmed
clear were never executed.

The service is a real :class:`~latticeai.services.memory_service.MemoryService`
over a real ``WorkspaceOSStore`` in ``tmp_path`` — memory scoping is the whole
point of these routes, so the store must be the one that enforces it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from latticeai.api.memory import create_memory_router
from latticeai.core.workspace_os import WorkspaceOSStore
from latticeai.services.memory_service import MemoryService

USER = "owner@example.com"


class _Scope:
    def __init__(self, workspace_id: str = "personal") -> None:
        self.workspace_id = workspace_id

    def read(self, request: Request) -> str:
        return self.workspace_id

    def write(self, request: Request) -> str:
        return self.workspace_id


@pytest.fixture()
def memory(tmp_path):
    store = WorkspaceOSStore(tmp_path / "data")
    # Explicit ids: the store derives an id from (kind, content, user, *now*),
    # so two identical upserts collapse or not depending on which second they
    # land in. Naming them keeps the duplicate pair (for compact) deterministic.
    for memory_id, kind, content in (
        ("mem-dup-a", "decisions", "we ship the hybrid retriever first"),
        ("mem-dup-b", "decisions", "we ship the hybrid retriever first"),
        ("mem-pref", "preferences", "prefer local models over cloud"),
    ):
        store.upsert_memory(
            kind=kind,
            content=content,
            user_email=USER,
            workspace_id="personal",
            memory_id=memory_id,
        )
    store.create_memory_snapshot(
        label="wp31 snapshot", reason="test", user_email=USER, workspace_id="personal"
    )
    service = MemoryService(store=store, data_dir=tmp_path / "data", enable_graph=False)
    scope = _Scope()
    audits: List[Tuple[str, Dict[str, Any]]] = []

    app = FastAPI()
    app.include_router(
        create_memory_router(
            service=service,
            require_user=lambda request: USER,
            get_current_user=lambda request: USER,
            gate_read=scope.read,
            gate_write=scope.write,
            append_audit_event=lambda event, **payload: audits.append((event, payload)),
        )
    )
    return TestClient(app), store, scope, audits


def test_manager_reports_every_tier_with_health(memory):
    client, _store, _scope, _audits = memory

    body = client.get("/api/memory/manager").json()

    sources = {source["id"]: source for source in body["sources"]}
    assert set(sources) == {
        "workspace",
        "project",
        "agent",
        "conversation",
        "graph",
        "vector",
    }
    assert sources["workspace"]["count"] == 3
    assert sources["agent"]["count"] == 1
    assert sources["conversation"]["health"] == "empty"
    assert sources["graph"]["health"] == "unavailable"
    assert body["graph_enabled"] is False
    assert body["health"] == "degraded"
    assert body["usage"]["total_items"] == 7


def test_brain_quality_summary_is_the_manager_readiness_block(memory):
    client, _store, _scope, _audits = memory

    summary = client.get("/api/memory/brain-quality").json()
    manager = client.get("/api/memory/manager").json()

    assert summary["signals"] == manager["brain_readiness"]["signals"]
    assert summary["source"] == "memory_service"
    assert summary["signals"]["memory_count"] == 4


def test_tiers_lists_the_tier_and_workspace_kind_vocabularies(memory):
    client, _store, _scope, _audits = memory

    body = client.get("/api/memory/tiers").json()

    assert body["tiers"] == [
        "workspace",
        "project",
        "agent",
        "conversation",
        "graph",
        "vector",
    ]
    assert "decisions" in body["workspace_kinds"]


def test_inspect_returns_items_for_a_known_source(memory):
    client, _store, _scope, _audits = memory

    workspace = client.get(
        "/api/memory/inspect", params={"source": "workspace", "limit": 2}
    ).json()
    agent = client.get("/api/memory/inspect", params={"source": "agent"}).json()

    assert workspace["source"] == "workspace"
    assert workspace["count"] == 2
    assert agent["count"] == 1
    assert agent["items"][0]["label"] == "wp31 snapshot"


def test_inspect_404s_an_unknown_source(memory):
    client, _store, _scope, _audits = memory

    response = client.get("/api/memory/inspect", params={"source": "nope"})

    assert response.status_code == 404
    assert "nope" in response.json()["detail"]


def test_recall_matches_stored_memory_content(memory):
    client, _store, _scope, _audits = memory

    hit = client.post(
        "/api/memory/recall", json={"query": "hybrid retriever", "limit": 5}
    ).json()
    miss = client.post(
        "/api/memory/recall", json={"query": "zzzz-nothing-here", "limit": 5}
    ).json()

    assert hit["query"] == "hybrid retriever"
    assert hit["status"] == "ok"
    assert sorted(item["id"] for item in hit["results"]) == ["mem-dup-a", "mem-dup-b"]
    assert hit["results"][0]["matched_terms"] == ["hybrid", "retriever"]
    assert miss["results"] == []
    assert miss["count"] == 0


def test_compact_dedupes_identical_memories_and_audits(memory):
    client, store, _scope, audits = memory

    result = client.post("/api/memory/compact")

    assert result.status_code == 200
    assert result.json()["compacted"] == 1
    assert result.json()["removed"] == ["mem-dup-b"]
    remaining = store.list_memories(user_email=USER, workspace_id="personal")["memories"]
    assert sorted(item["id"] for item in remaining) == ["mem-dup-a", "mem-pref"]
    assert audits == [("memory_compact", {"user_email": USER, "compacted": 1})]


def test_rebuild_reports_unavailable_without_a_graph(memory):
    client, _store, _scope, audits = memory

    vector = client.post("/api/memory/rebuild", json={"target": "vector"}).json()
    unknown = client.post("/api/memory/rebuild", json={"target": "galaxy"}).json()

    assert vector["status"] == "unavailable"
    assert unknown["status"] == "error"
    assert "galaxy" in unknown["detail"]
    assert [payload["status"] for _event, payload in audits] == [
        "unavailable",
        "error",
    ]


def test_clear_requires_confirmation_and_then_removes_the_kind(memory):
    client, store, _scope, audits = memory

    unconfirmed = client.post(
        "/api/memory/clear", json={"scope": "decisions", "confirm": False}
    )
    confirmed = client.post(
        "/api/memory/clear", json={"scope": "decisions", "confirm": True}
    )

    assert unconfirmed.status_code == 400
    assert "confirm" in unconfirmed.json()["detail"]
    assert confirmed.status_code == 200
    assert confirmed.json()["cleared"] == "decisions"
    kinds = {
        item["kind"]
        for item in store.list_memories(user_email=USER, workspace_id="personal")[
            "memories"
        ]
    }
    assert "decisions" not in kinds
    assert audits == [("memory_clear", {"user_email": USER, "scope": "decisions"})]
