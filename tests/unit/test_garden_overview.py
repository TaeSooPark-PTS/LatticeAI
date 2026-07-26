"""Knowledge garden overview — four beds, one read (v9.9.7).

Review follow-up: "개인 지식 가든 뷰 강화 — 최근 들어온 것 / 모순 / 오래된 것 /
자주 쓰는 것을 한눈에". Living Brain answers health in aggregate; a gardener
asks four concrete questions. House rules verified here: "frequent" is real
graph degree rather than a guess, retrieval plumbing (Chunk nodes) is never
presented as a plant, and an unavailable graph yields empty beds instead of
invented ones.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from latticeai.api.brain_intelligence import create_brain_intelligence_router
from latticeai.services.brain_intelligence import BrainIntelligenceService


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


class FakeGraph:
    def __init__(self, nodes, edges):
        self._nodes = nodes
        self._edges = edges

    def graph(self, limit, **kwargs):
        return {"nodes": self._nodes, "edges": self._edges}


NODES = [
    {"id": "n-new", "type": "Document", "title": "이번 주 회의", "updated_at": _iso(1)},
    {"id": "n-new2", "type": "Note", "title": "새 메모", "updated_at": _iso(3)},
    {"id": "n-old", "type": "Document", "title": "작년 계획", "updated_at": _iso(200)},
    {"id": "n-hub", "type": "Concept", "title": "예산", "updated_at": _iso(30)},
    {"id": "n-chunk", "type": "Chunk", "title": "chunk 1", "updated_at": _iso(1)},
]
EDGES = [
    {"from": "n-new", "to": "n-hub", "type": "언급함"},
    {"from": "n-old", "to": "n-hub", "type": "언급함"},
    {"from": "n-new2", "to": "n-hub", "type": "언급함"},
    {"from": "n-new", "to": "n-chunk", "type": "포함함"},
    {"from": "n-new2", "to": "n-chunk", "type": "포함함"},
    {"from": "n-old", "to": "n-chunk", "type": "포함함"},
    {"from": "n-hub", "to": "n-chunk", "type": "포함함"},
]


def _service(nodes=NODES, edges=EDGES, enable_graph=True, contradictions=None):
    service = BrainIntelligenceService(
        knowledge_graph=FakeGraph(nodes, edges),
        memory_service=None,
        enable_graph=enable_graph,
    )
    service.contradictions = lambda **kw: {"items": contradictions or [], "count": len(contradictions or [])}
    return service


def test_recent_and_stale_beds_split_by_age():
    beds = _service().garden_overview()["beds"]
    assert [item["id"] for item in beds["recent"]["items"]] == ["n-new", "n-new2"]
    assert [item["id"] for item in beds["stale"]["items"]] == ["n-old"]
    # A node in neither window (30 days old) belongs to no age bed.
    assert "n-hub" not in {item["id"] for item in beds["recent"]["items"]}
    assert "n-hub" not in {item["id"] for item in beds["stale"]["items"]}


def test_frequent_bed_is_real_graph_degree():
    beds = _service().garden_overview()["beds"]
    frequent = beds["frequent"]["items"]
    assert frequent[0]["id"] == "n-hub"
    assert frequent[0]["degree"] == 4
    # Retrieval plumbing is never presented as a plant, however connected.
    assert all(item["type"] != "Chunk" for item in frequent)
    assert "n-chunk" not in {item["id"] for item in frequent}


def test_contradictions_bed_reports_the_memory_tier_verdict():
    rows = [{"id": "c1", "summary": "예산이 서로 다릅니다"}]
    overview = _service(contradictions=rows).garden_overview()
    bed = overview["beds"]["contradictions"]
    assert bed["count"] == 1
    assert bed["items"][0]["id"] == "c1"


def test_a_broken_contradiction_scan_empties_only_that_bed():
    service = _service()

    def boom(**kwargs):
        raise RuntimeError("contradiction scan exploded")

    service.contradictions = boom
    overview = service.garden_overview()
    assert overview["beds"]["contradictions"]["items"] == []
    assert overview["beds"]["recent"]["items"], "other beds must survive"
    assert overview["available"] is True


def test_no_graph_yields_empty_beds_not_invented_ones():
    overview = _service(enable_graph=False).garden_overview()
    assert overview["available"] is False
    for bed in overview["beds"].values():
        assert bed["items"] == []
        assert bed["count"] == 0


def test_limit_is_clamped_and_applied():
    many = [
        {"id": f"n{i}", "type": "Note", "title": f"메모 {i}", "updated_at": _iso(1)}
        for i in range(40)
    ]
    overview = _service(nodes=many, edges=[]).garden_overview(limit=5)
    assert len(overview["beds"]["recent"]["items"]) == 5
    assert overview["beds"]["recent"]["count"] == 40
    # Out-of-range limits clamp rather than explode.
    assert len(_service(nodes=many, edges=[]).garden_overview(limit=0)["beds"]["recent"]["items"]) == 1
    assert len(_service(nodes=many, edges=[]).garden_overview(limit=999)["beds"]["recent"]["items"]) == 40


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(
        create_brain_intelligence_router(
            service=_service(),
            require_user=lambda request: "u@x.com",
            gate_read=lambda request: None,
            gate_write=lambda request: None,
            append_audit_event=lambda *a, **k: None,
        )
    )
    return TestClient(app)


def test_router_exposes_the_garden_overview(client):
    response = client.get("/api/brain/garden")
    assert response.status_code == 200
    payload = response.json()
    assert set(payload["beds"]) == {"recent", "contradictions", "stale", "frequent"}
    assert payload["available"] is True
