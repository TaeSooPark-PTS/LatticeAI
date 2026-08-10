"""wpb03: Brain intelligence on a graph that is merely ordinary.

Every digest in this service classifies what it finds, and the suite drives
the interesting classifications: knowledge that is fresh, knowledge that has
gone stale, edges that record a contradiction, a graph layer that answers.
The branches below are what a normal Brain looks like — a node that is neither
new nor stale, an edge that records something other than a contradiction, an
edge stored with only one endpoint, a graph layer that is switched off, and a
contradictions scan whose payload is not the shape the garden expects.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from latticeai.services.brain_intelligence import BrainIntelligenceService


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


class _Graph:
    """Knowledge store stand-in returning one scripted graph slice."""

    def __init__(self, *, nodes: Optional[List[Dict[str, Any]]] = None,
                 edges: Optional[List[Dict[str, Any]]] = None) -> None:
        self.nodes = list(nodes or [])
        self.edges = list(edges or [])
        self.calls: List[Dict[str, Any]] = []

    def graph(self, limit: int, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append({"limit": limit, **kwargs})
        return {"nodes": list(self.nodes), "edges": list(self.edges)}


class _Proactive:
    """Graph-layer stand-in so the lazy real one is never constructed."""

    def __init__(self, node_pairs: Optional[List[Dict[str, Any]]] = None) -> None:
        self.node_pairs = list(node_pairs or [])
        self.contradiction_calls = 0

    def detect_contradictions(self, **_kwargs: Any) -> Dict[str, Any]:
        self.contradiction_calls += 1
        return {"node_pairs": list(self.node_pairs)}


def _service(graph: Optional[_Graph], *, enable_graph: bool = True,
             proactive: Optional[_Proactive] = None) -> BrainIntelligenceService:
    service = BrainIntelligenceService(knowledge_graph=graph, enable_graph=enable_graph)
    if proactive is not None:
        service._proactive_brain = proactive
    return service


# ── insights ────────────────────────────────────────────────────────────────


def test_a_node_that_is_neither_fresh_nor_stale_lands_in_no_bucket():
    graph = _Graph(nodes=[
        {"id": "n-mid", "type": "Note", "title": "중간 기록", "updated_at": _iso(20)},
        {"id": "n-new", "type": "Note", "title": "새 기록", "updated_at": _iso(1)},
    ])

    insights = _service(graph).insights()

    assert insights["activity"]["recent_nodes"] == 1
    assert [n["id"] for n in insights["activity"]["recent_samples"]] == ["n-new"]
    assert insights["attention"]["stale_nodes"] == 0
    assert insights["activity"]["trending_types"] == [{"type": "Note", "count": 1}]
    # Both nodes are disconnected, so the middle-aged one is still visible.
    assert insights["attention"]["orphan_nodes"] == 2


# ── garden overview ─────────────────────────────────────────────────────────


def test_a_half_stored_edge_only_credits_the_endpoint_it_actually_has():
    graph = _Graph(
        nodes=[
            {"id": "n-1", "type": "Note", "title": "허브", "updated_at": _iso(1)},
        ],
        edges=[{"id": "e-1", "to": "n-1", "type": "RELATES_TO"}],
    )

    garden = _service(graph, proactive=_Proactive()).garden_overview()

    frequent = garden["beds"]["frequent"]["items"]
    assert [item["id"] for item in frequent] == ["n-1"]
    assert frequent[0]["degree"] == 1, "the missing endpoint adds no degree"
    assert garden["available"] is True


def test_a_contradictions_scan_with_an_unexpected_payload_leaves_that_bed_empty(monkeypatch):
    graph = _Graph(nodes=[{"id": "n-1", "type": "Note", "title": "메모", "updated_at": _iso(1)}])
    service = _service(graph, proactive=_Proactive())
    monkeypatch.setattr(service, "contradictions", lambda **_kwargs: {"items": None})

    garden = service.garden_overview()

    assert garden["beds"]["contradictions"] == {"count": 0, "items": []}
    # The other beds are still filled from the same graph sample.
    assert garden["beds"]["recent"]["count"] == 1


# ── contradictions ──────────────────────────────────────────────────────────


def test_an_ordinary_relation_edge_is_not_read_as_a_contradiction():
    graph = _Graph(edges=[
        {"id": "e-1", "from": "n-1", "to": "n-2", "type": "MENTIONS"},
        {"id": "e-2", "from": "n-2", "to": "n-3", "type": "CONTRADICTS"},
    ])

    found = _service(graph, proactive=_Proactive()).contradictions()

    assert found["sources"]["graph_edges"] == 1
    assert [item["id"] for item in found["items"] if item["kind"] == "graph_edge"] == ["e-2"]


def test_with_the_graph_off_only_the_memory_scans_report():
    service = _service(None, enable_graph=False)

    found = service.contradictions()

    assert found["count"] == 0
    assert found["sources"] == {
        "memory_pairs": 0, "temporal": 0, "graph_edges": 0, "graph_node_pairs": 0
    }
    assert found["memories_scanned"] == 0


# ── consolidate ─────────────────────────────────────────────────────────────


def test_with_the_graph_off_consolidation_reports_no_graph_plan():
    service = _service(None, enable_graph=False)

    result = service.consolidate()

    assert result["mode"] == "dry_run"
    assert result["graph_consolidation"] is None
    assert result["duplicate_memories"] == []
    assert result["duplicate_edges"] == []
    assert result["pruned"] == 0
