"""wp18 — automation intelligence + execution policy, at their edges.

The service half covers the degradation seams (history / graph / store reads
that raise), the filters that drop non-questions, the confidence gate, and
suggestion lookup. The execution half covers every dry-run node description,
the honest run summaries, and the two best-effort surfaces that must never
raise (last-execution read, failed-run review enqueue).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from latticeai.services import automation_intelligence as intelligence
from latticeai.services.automation_execution import (
    build_last_execution,
    dry_run_report,
    enqueue_failed_execution,
    last_execution_view,
    summarize_workflow_run,
)
from latticeai.services.automation_intelligence import AutomationIntelligenceService
from latticeai.services.brain_automation import build_brain_automation_workflow


class FakeConversations:
    def __init__(self, items: Optional[List[Dict[str, Any]]] = None, *,
                 explode: bool = False) -> None:
        self.items = items or []
        self.explode = explode

    def history(self, **_kwargs):
        if self.explode:
            raise RuntimeError("conversation history unavailable")
        return self.items


class FakeGraph:
    def __init__(self, *, sources: Any = None, search_result: Any = None,
                 sources_explode: bool = False, search_explode: bool = False) -> None:
        self._sources = sources
        self._search_result = search_result
        self.sources_explode = sources_explode
        self.search_explode = search_explode

    def local_sources(self):
        if self.sources_explode:
            raise RuntimeError("source index unavailable")
        return {"sources": list(self._sources or [])}

    def search(self, query, limit=30, **_kwargs):
        if self.search_explode:
            raise RuntimeError("graph search unavailable")
        return self._search_result


class FakeStore:
    def __init__(self, workflows: Optional[List[Dict[str, Any]]] = None, *,
                 explode: bool = False, runs: Any = None,
                 runs_explode: bool = False) -> None:
        self.workflows = workflows or []
        self.explode = explode
        self.runs = runs
        self.runs_explode = runs_explode

    def list_workflows(self, **_kwargs):
        if self.explode:
            raise RuntimeError("workflow index unavailable")
        return {"workflows": list(self.workflows)}

    def list_workflow_runs(self, workflow_id=None, limit=50, workspace_id=None):
        if self.runs_explode:
            raise RuntimeError("run index unavailable")
        return {"runs": list(self.runs or [])[:limit]}


def _msg(content: str, *, role: str = "user", ts: str = "2026-07-19T09:00:00"):
    return {"role": role, "content": content, "timestamp": ts}


def _repeated() -> List[Dict[str, Any]]:
    return [
        _msg("오늘 기억 정리해줘", ts="2026-07-18T08:00:00"),
        _msg("오늘 기억 정리해줘 부탁", ts="2026-07-19T08:00:00"),
    ]


def _service(*, conversations=None, graph=None, store=None):
    return AutomationIntelligenceService(
        conversation_store=conversations,
        knowledge_graph=graph,
        store=store,
        enable_graph=graph is not None,
    )


# ── clustering helper ───────────────────────────────────────────────────

def test_similarity_of_an_empty_signature_is_zero():
    left = intelligence._signature(intelligence._tokens("오늘 기억 정리"))
    assert intelligence._similarity(left, frozenset()) == 0.0
    assert intelligence._similarity(frozenset(), left) == 0.0
    assert intelligence._similarity(left, left) == 1.0


# ── history mining degradation ──────────────────────────────────────────

def test_unreadable_history_yields_no_patterns():
    report = _service(conversations=FakeConversations(explode=True)).question_patterns()
    assert report["patterns"] == []
    assert report["questions_scanned"] == 0


def test_statements_and_token_poor_questions_are_not_patterns():
    conversations = FakeConversations([
        # No question signal at all → never even scanned as a question.
        _msg("회의록 초안 저장했다", ts="2026-07-18T08:00:00"),
        _msg("회의록 초안 저장했다", ts="2026-07-19T08:00:00"),
        # Question-shaped but every token is a stopword → no usable signature.
        _msg("뭐야 뭐야 뭐야?", ts="2026-07-18T09:00:00"),
        _msg("뭐야 뭐야 뭐야?", ts="2026-07-19T09:00:00"),
    ])
    report = _service(conversations=conversations).question_patterns()
    assert report["questions_scanned"] == 2, "only the question-shaped rows count"
    assert report["patterns"] == []


# ── knowledge graph degradation ─────────────────────────────────────────

def test_unreadable_source_index_yields_no_source_suggestions():
    graph = FakeGraph(sources_explode=True)
    report = _service(conversations=FakeConversations(), graph=graph).suggestions()
    assert report["suggestions"] == []


def test_failed_graph_grounding_reports_unavailable_not_zero():
    graph = FakeGraph(search_explode=True)
    suggestion = _service(
        conversations=FakeConversations(_repeated()), graph=graph,
    ).suggestions()["suggestions"][0]
    assert suggestion["confidence_factors"]["kg_related_nodes"] is None


def test_unexpected_graph_search_shape_is_treated_as_unavailable():
    graph = FakeGraph(search_result=["not", "a", "report"])
    suggestion = _service(
        conversations=FakeConversations(_repeated()), graph=graph,
    ).suggestions()["suggestions"][0]
    assert suggestion["confidence_factors"]["kg_related_nodes"] is None


def test_graph_grounding_counts_matches_when_available():
    graph = FakeGraph(search_result={"matches": [{"id": "n1"}, {"id": "n2"}]})
    suggestion = _service(
        conversations=FakeConversations(_repeated()), graph=graph,
    ).suggestions()["suggestions"][0]
    assert suggestion["confidence_factors"]["kg_related_nodes"] == 2


# ── store degradation ───────────────────────────────────────────────────

def test_unreadable_workflow_index_still_produces_suggestions_and_overview():
    store = FakeStore(explode=True)
    service = _service(conversations=FakeConversations(_repeated()), store=store)

    report = service.suggestions()
    assert len(report["suggestions"]) == 1
    assert report["suggestions"][0]["installed"] is False
    assert report["suggestions"][0]["workflow_id"] is None

    overview = service.overview()
    assert overview["installed"] == []
    assert len(overview["suggestions"]) == 1


# ── confidence gate ─────────────────────────────────────────────────────

def test_questions_below_the_confidence_gate_are_suppressed(monkeypatch):
    monkeypatch.setattr(intelligence, "_MIN_SUGGESTION_CONFIDENCE", 0.95)
    report = _service(conversations=FakeConversations(_repeated())).suggestions()
    assert report["suggestions"] == []
    assert report["quality"]["suppressed_low_confidence"] == 1
    assert report["quality"]["min_confidence"] == 0.95


# ── suggestion lookup ───────────────────────────────────────────────────

def test_find_suggestion_matches_by_id_and_returns_none_otherwise():
    service = _service(conversations=FakeConversations(_repeated()))
    known = service.suggestions()["suggestions"][0]
    assert service.find_suggestion(known["id"])["title"] == known["title"]
    assert service.find_suggestion("sug-q-does-not-exist") is None
    assert _service(conversations=FakeConversations()).find_suggestion("any") is None


# ── dry-run node descriptions ───────────────────────────────────────────

def _mixed_workflow() -> Dict[str, Any]:
    return {
        "id": "wf-mixed",
        "name": "Mixed",
        "nodes": [
            {"id": "trigger", "type": "trigger", "name": "Start",
             "config": {"trigger": "interval"}, "next": "t"},
            {"id": "t", "type": "tool", "config": {"tool": "kg_search"}, "next": "s"},
            {"id": "s", "type": "skill", "name": "Summarize", "config": {},
             "next": "p"},
            {"id": "p", "type": "plugin", "config": {"plugin": "notion"},
             "next": "c"},
            {"id": "c", "type": "condition", "config": {"left": "x", "op": "truthy"},
             "branches": {"true": "output", "false": "output"}, "next": "output"},
            {"id": "output", "type": "output", "config": {}, "next": None},
        ],
        "metadata": {"created_from": "automation_suggestion", "creates": ["digest"]},
    }


def test_dry_run_describes_every_executable_node_family():
    report = dry_run_report(_mixed_workflow())
    assert report["status"] == "ok"
    assert report["side_effects"] is False
    would = {step["node"]: step["would"] for step in report["steps"]}
    assert would["trigger"].startswith("skipped in a manual run")
    assert "wait for the user-enabled schedule" in would["trigger"]
    assert would["t"] == "run tool 'kg_search'"
    assert would["s"] == "run skill 'Summarize'", "falls back to the node name"
    assert would["p"] == "run plugin 'notion'"
    assert would["c"] == "evaluate a branch condition"
    assert would["output"] == "deliver the draft to the review inbox"
    # tool + skill + plugin are the executable steps; trigger/condition/output are not.
    assert report["summary"].startswith("3 step(s)")
    assert "(digest)" in report["summary"]


def test_dry_run_describes_an_unknown_node_type_without_guessing():
    report = dry_run_report({
        "name": "Odd",
        "nodes": [
            {"id": "trigger", "type": "trigger", "config": {"trigger": "brain_event"},
             "next": "x"},
            {"id": "x", "type": "webhook", "config": {}, "next": None},
        ],
    })
    assert report["steps"][1]["would"] == "run node 'x'"
    assert report["status"] == "invalid"
    assert any("unknown type" in error for error in report["validation_errors"])


# ── run summaries ───────────────────────────────────────────────────────

def test_failed_run_without_step_detail_falls_back_to_the_output_error():
    summary = summarize_workflow_run({
        "status": "failed",
        "timeline": [{"status": "ok"}],
        "outputs": {"error": "model runtime unavailable"},
    })
    assert summary == "failed after 1 step(s): model runtime unavailable"


def test_paused_and_in_flight_runs_read_honestly():
    assert summarize_workflow_run({"status": "awaiting_approval"}).startswith("paused")
    for status in ("queued", "running", "cancelling"):
        assert summarize_workflow_run({"status": status}) == "still running"


def test_last_execution_view_survives_an_unreadable_run_index():
    workflow = build_brain_automation_workflow("daily-memory-digest")
    workflow["id"] = "wf-auto"
    stamp = build_last_execution(mode="dry_run", status="ok", summary="stamped")
    workflow["metadata"]["last_execution"] = stamp
    view = last_execution_view(workflow, store=FakeStore(runs_explode=True))
    assert view == stamp


def test_last_execution_view_without_a_store_returns_the_stamp_or_nothing():
    workflow = build_brain_automation_workflow("daily-memory-digest")
    assert last_execution_view(workflow) is None
    workflow["metadata"]["last_execution"] = build_last_execution(
        mode="live", status="ok", summary="done", run_id="run-1",
    )
    assert last_execution_view(workflow)["run_id"] == "run-1"


# ── failed-run review enqueue ───────────────────────────────────────────

def test_failed_execution_enqueue_is_a_no_op_without_a_queue():
    assert enqueue_failed_execution(
        None, workflow={"id": "wf-1", "name": "Digest"}, run_id="run-1",
        error="boom",
    ) is None


def test_failed_execution_enqueue_never_raises_when_the_queue_does():
    class ExplodingQueue:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            raise RuntimeError("review queue write failed")

    queue = ExplodingQueue()
    assert enqueue_failed_execution(
        queue, workflow={"id": "wf-1", "name": "Digest"}, run_id="run-1",
        error="draft agent crashed", user_email="user@example.com",
        workspace_id="personal",
    ) is None
    assert queue.calls[0]["title"] == "Automation failed: Digest"
    assert queue.calls[0]["payload"]["run_id"] == "run-1"


# ── recipe catalogue ────────────────────────────────────────────────────

def test_building_an_unknown_recipe_raises_key_error():
    with pytest.raises(KeyError):
        build_brain_automation_workflow("no-such-recipe")
