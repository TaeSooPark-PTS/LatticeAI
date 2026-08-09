"""wp35: BrainIntelligenceService + CommandCenterService degradation paths.

Both services take every backend as a keyword collaborator, so each scenario
injects a fake store / memory / queue that answers or fails exactly once. The
contract under test is the same everywhere: a broken backend degrades one
section, never the whole report.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from latticeai.services.brain_intelligence import BrainIntelligenceService
from latticeai.services.command_center import CommandCenterService


class FakeKG:
    """Graph store stand-in for ``BrainIntelligenceService``."""

    def __init__(
        self,
        nodes: Optional[List[Dict[str, Any]]] = None,
        edges: Optional[List[Dict[str, Any]]] = None,
        *,
        graph_error: Optional[Exception] = None,
    ):
        self._nodes = nodes or []
        self._edges = edges or []
        self._graph_error = graph_error

    def graph(self, limit=None, **kwargs):
        if self._graph_error is not None:
            raise self._graph_error
        return {"nodes": list(self._nodes), "edges": list(self._edges)}


class FakeMemory:
    def __init__(self, items=None, *, inspect_error=None, prune_error=None):
        self._items = items or []
        self._inspect_error = inspect_error
        self._prune_error = prune_error

    def inspect(self, tier, **kwargs):
        if self._inspect_error is not None:
            raise self._inspect_error
        return {"items": list(self._items)}

    def prune(self, **kwargs):
        if self._prune_error is not None:
            raise self._prune_error
        return {"count": len(kwargs.get("ids") or [])}


def _install_proactive(monkeypatch, factory):
    import lattice_brain.graph.proactive as proactive_mod

    monkeypatch.setattr(proactive_mod, "ProactiveBrain", factory)


# ── timestamp parsing through the health report ──────────────────────────────


def test_health_report_tolerates_blank_and_unparsable_timestamps():
    kg = FakeKG(
        nodes=[
            {"id": "a", "updated_at": ""},
            {"id": "b", "updated_at": "definitely-not-a-timestamp"},
            {"id": "c", "updated_at": "2020-01-01T00:00:00"},
        ],
        edges=[],
    )

    report = BrainIntelligenceService(knowledge_graph=kg).health_report()

    freshness = report["dimensions"]["freshness"]
    # Only the one node with a readable stamp counts, and it is long stale.
    assert freshness["stale_nodes"] == 1
    assert freshness["score"] == 0
    assert report["graph_available"] is True


def test_contradiction_edges_are_promoted_to_a_recommended_action():
    kg = FakeKG(
        nodes=[{"id": "a", "updated_at": "2020-01-01T00:00:00+00:00"}],
        edges=[{"id": "e1", "from": "a", "to": "b", "type": "CONTRADICTS"}],
    )

    report = BrainIntelligenceService(knowledge_graph=kg).health_report()

    action_ids = [action["id"] for action in report["recommended_actions"]]
    assert "resolve_contradictions" in action_ids
    assert report["dimensions"]["consistency"]["contradiction_edges"] == 1


def test_graph_sample_failure_degrades_every_graph_dimension():
    kg = FakeKG(graph_error=RuntimeError("graph store offline"))

    report = BrainIntelligenceService(knowledge_graph=kg).health_report()

    assert report["graph_available"] is False
    assert report["dimensions"]["freshness"]["status"] == "unavailable"
    assert report["dimensions"]["connectivity"]["status"] == "unavailable"


def test_index_status_failure_degrades_only_embedding_coverage():
    class IndexingKG(FakeKG):
        def index_status(self):
            raise RuntimeError("index unreadable")

    report = BrainIntelligenceService(
        knowledge_graph=IndexingKG(nodes=[{"id": "a", "updated_at": ""}])
    ).health_report()

    assert report["dimensions"]["embedding_coverage"]["status"] == "unavailable"
    assert report["dimensions"]["connectivity"]["status"] == "ok"


def test_memory_read_failure_yields_no_contradictions_rather_than_an_error():
    service = BrainIntelligenceService(
        knowledge_graph=FakeKG(),
        memory_service=FakeMemory(inspect_error=RuntimeError("memory offline")),
    )

    found = service.contradictions()

    assert found["memories_scanned"] == 0
    assert found["items"] == []


def test_proactive_initialization_failure_is_reported_as_unavailable(monkeypatch):
    def boom(store, **kwargs):
        raise RuntimeError("proactive brain unavailable")

    _install_proactive(monkeypatch, boom)
    service = BrainIntelligenceService(knowledge_graph=FakeKG())

    assert service.graph_duplicates()["available"] is False
    assert service.quality_report() == {
        "available": False,
        "generated_at": service.quality_report()["generated_at"],
    }


# ── vector freshness contract ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw_status", "expected"),
    [("needs_reindex", "pending"), ("something-else", "unavailable")],
)
def test_vector_freshness_normalizes_store_reported_status(raw_status, expected):
    class FreshnessKG(FakeKG):
        def vector_freshness(self):
            return {"status": raw_status, "pending_items": 3, "total_items": 9}

    report = BrainIntelligenceService(knowledge_graph=FreshnessKG()).vector_freshness()

    assert report["status"] == expected
    assert report["pending_items"] == 3
    assert report["total_items"] == 9


def test_vector_freshness_falls_back_to_index_status_and_reports_ready():
    class IndexOnlyKG(FakeKG):
        def index_status(self):
            return {"pending_items": 0, "source_items": 7}

    report = BrainIntelligenceService(knowledge_graph=IndexOnlyKG()).vector_freshness()

    assert report == {
        "status": "ready",
        "pending_items": 0,
        "total_items": 7,
        "detail": "vector index is up to date",
    }


def test_vector_freshness_reports_an_unreadable_index_status():
    class BrokenIndexKG(FakeKG):
        def index_status(self):
            raise RuntimeError("index locked")

    report = BrainIntelligenceService(knowledge_graph=BrokenIndexKG()).vector_freshness()

    assert report["status"] == "unavailable"
    assert "index locked" in report["detail"]


# ── garden overview ──────────────────────────────────────────────────────────


def test_garden_overview_clamps_a_nonsense_limit_and_skips_undated_nodes():
    kg = FakeKG(
        nodes=[
            {"id": "a", "type": "Concept", "title": "no stamp"},
            {"id": "b", "type": "Concept", "title": "old", "updated_at": "2020-01-01T00:00:00+00:00"},
            {"id": "c", "type": "Chunk", "title": "plumbing", "updated_at": "2020-01-01T00:00:00+00:00"},
        ],
        edges=[],
    )

    overview = BrainIntelligenceService(knowledge_graph=kg).garden_overview(limit="eight")

    assert overview["available"] is True
    assert overview["beds"]["stale"]["count"] == 1
    assert [item["id"] for item in overview["beds"]["stale"]["items"]] == ["b"]
    assert overview["beds"]["recent"]["count"] == 0


# ── proactive-backed reports ─────────────────────────────────────────────────


def test_proactive_scan_failures_degrade_each_report_independently(monkeypatch):
    class BrokenProactive:
        def __init__(self, store, **kwargs):
            self.store = store

        def find_duplicates(self, **kwargs):
            raise RuntimeError("duplicate scan failed")

        def quality_report(self, **kwargs):
            raise RuntimeError("quality report failed")

        def detect_contradictions(self, **kwargs):
            raise RuntimeError("contradiction scan failed")

        def consolidate_duplicates(self, **kwargs):
            raise RuntimeError("consolidation plan failed")

    _install_proactive(monkeypatch, BrokenProactive)
    service = BrainIntelligenceService(
        knowledge_graph=FakeKG(),
        memory_service=FakeMemory([{"id": "m1", "content": "same note"}]),
    )

    duplicates = service.graph_duplicates()
    quality = service.quality_report()
    contradictions = service.contradictions()
    consolidation = service.consolidate()

    assert duplicates["available"] is False
    assert duplicates["error"] == "duplicate scan failed"
    assert quality == {
        "available": False,
        "error": "quality report failed",
        "generated_at": quality["generated_at"],
    }
    assert contradictions["sources"]["graph_node_pairs"] == 0
    assert consolidation["graph_consolidation"] is None


def test_consolidation_survives_a_failing_prune():
    memory = FakeMemory(
        [
            {"id": "m1", "content": "duplicate note"},
            {"id": "m2", "content": "duplicate note"},
        ],
        prune_error=RuntimeError("prune backend offline"),
    )
    service = BrainIntelligenceService(knowledge_graph=FakeKG(), memory_service=memory)

    result = service.consolidate(apply=True)

    assert result["mode"] == "applied"
    assert result["duplicate_memory_count"] == 1
    assert result["pruned"] == 0


# ── command center ───────────────────────────────────────────────────────────


class _Boom:
    """Every collaborator method raises, one section at a time."""

    def __init__(self, error="backend offline"):
        self._error = RuntimeError(error)

    def history(self, **kwargs):
        raise self._error

    def list_workflows(self, **kwargs):
        raise self._error

    def graph(self, **kwargs):
        raise self._error

    def stats(self):
        raise self._error

    def list(self, **kwargs):
        raise self._error

    def health_report(self, **kwargs):
        raise self._error

    def suggestions(self, **kwargs):
        raise self._error

    def keyword_search(self, query, **kwargs):
        raise self._error


def test_briefing_degrades_every_section_independently():
    broken = _Boom()
    service = CommandCenterService(
        conversation_store=broken,
        knowledge_graph=broken,
        store=broken,
        search_service=broken,
        brain_intelligence=broken,
        automation_intelligence=broken,
        review_queue=broken,
    )

    briefing = service.briefing(user_email="u@e.co", workspace_id="team")

    sections = briefing["sections"]
    assert sections["knowledge"] == {"available": False, "recent": []}
    assert sections["conversations"]["messages"] == 0
    assert sections["automations"] == {"available": True, "total": 0, "enabled": 0, "drafts": 0}
    assert sections["review"] == {"available": False, "pending": 0}
    assert sections["health"] == {"available": False}
    assert sections["suggestions"] == {"available": False, "count": 0, "top": []}
    assert sections["hygiene"]["available"] is False
    assert briefing["quick_actions"] == [
        {"id": "ask-brain", "kind": "chat", "count": 0, "target": "/brain"}
    ]


def test_empty_knowledge_graph_suggests_connecting_a_source():
    class EmptyKG:
        def graph(self, **kwargs):
            return {"nodes": []}

    service = CommandCenterService(knowledge_graph=EmptyKG())

    briefing = service.briefing()

    assert briefing["sections"]["knowledge"] == {
        "available": True,
        "recent": [],
        "sampled_nodes": 0,
    }
    assert "connect-knowledge" in [a["id"] for a in briefing["quick_actions"]]


def test_unreadable_curation_stamp_is_treated_as_stale():
    class BigKG:
        def graph(self, **kwargs):
            return {"nodes": []}

        def stats(self):
            return {"nodes": {"Concept": 250}}

        def last_noise_curate_at(self):
            return "not-a-timestamp"

    briefing = CommandCenterService(knowledge_graph=BigKG()).briefing()

    hygiene = briefing["sections"]["hygiene"]
    assert hygiene["suggest_noise_curate"] is True
    assert hygiene["node_count"] == 250
    assert "curate-noise" in [a["id"] for a in briefing["quick_actions"]]


def test_search_degrades_knowledge_but_still_answers_from_other_surfaces():
    class Conversations:
        def history(self, **kwargs):
            return [
                {"role": "user", "content": "unrelated", "conversation_id": "c0"},
                {"role": "user", "content": "release plan", "conversation_id": "c1"},
                {"role": "assistant", "content": "release notes", "conversation_id": "c2"},
            ]

    class Store:
        def list_workflows(self, **kwargs):
            return {
                "workflows": [
                    {"id": "w1", "name": "release digest", "metadata": {"automation_state": "enabled"}},
                    {"id": "w2", "name": "release backup", "metadata": {}},
                ]
            }

    service = CommandCenterService(
        conversation_store=Conversations(),
        knowledge_graph=_Boom(),
        store=Store(),
        search_service=_Boom(),
    )

    result = service.search("release", limit=1)

    kinds = {group["kind"]: group["items"] for group in result["groups"]}
    assert "knowledge" not in kinds
    assert len(kinds["conversation"]) == 1
    assert kinds["conversation"][0]["conversation_id"] == "c2"
    assert len(kinds["automation"]) == 1
    assert kinds["automation"][0]["id"] == "w1"
    assert result["total"] == 2


def test_conversation_search_keeps_one_hit_per_conversation():
    class Conversations:
        def history(self, **kwargs):
            return [
                {"role": "user", "content": "release plan", "conversation_id": "c1"},
                {"role": "assistant", "content": "release notes", "conversation_id": "c1"},
                {"role": "user", "content": "nothing here", "conversation_id": "c2"},
            ]

    service = CommandCenterService(conversation_store=Conversations(), enable_graph=False)

    result = service.search("release")

    conversation = next(g for g in result["groups"] if g["kind"] == "conversation")
    assert [item["conversation_id"] for item in conversation["items"]] == ["c1"]
