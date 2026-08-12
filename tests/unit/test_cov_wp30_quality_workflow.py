"""wp30 coverage — quality-layer fallbacks and workflow validation/suspension.

Quality: the embedding labeller's unembedded fallback and drift maths, the
reranker's never-raise contract, duplicate-edge merging, and the benchmark
runner's honest zeros when a fixture carries nothing judgeable. Workflow: the
validation errors a designer actually hits, the no-eval condition evaluator,
the lifecycle hooks that must fire even on a rejected definition, and the
approval suspension cursor when the context cannot be serialized.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import lattice_brain.graph.rerank as rerank_mod
import lattice_brain.workflow as workflow_mod
from lattice_brain.quality import (
    EmbeddingFallbackLabeller,
    GraphEdgeQualityManager,
    RerankerInterface,
    RetrievalBenchmarkRunner,
    StructuredContextAssembler,
)
from lattice_brain.workflow import (
    ApprovalRequired,
    WorkflowEngine,
    WorkflowError,
    _entry_node,
    _evaluate_condition,
    import_workflow,
    validate_definition,
)


class _RecordingHooks:
    def __init__(self):
        self.fired = []

    def fire_hook(self, name, event, *, payload=None):
        self.fired.append((name, event, payload))
        return {"status": "ok"}


def _linear(*, trigger_next="act"):
    return {
        "id": "wf1",
        "name": "linear",
        "nodes": [
            {"id": "trigger", "type": "trigger", "config": {}, "next": trigger_next},
            {"id": "act", "type": "tool", "name": "act", "config": {}, "next": "output"},
            {"id": "output", "type": "output", "config": {}, "next": None},
        ],
    }


# ── embedding fallback labelling ─────────────────────────────────────────────

def test_label_without_an_embedding_is_an_honest_low_confidence_fallback():
    labeller = EmbeddingFallbackLabeller()
    label = labeller.label("v1")
    assert label.label == "unembedded_fallback"
    assert label.confidence == 0.3
    assert label.drift_score == 0.0
    assert label.needs_reindex is False
    assert labeller.generate_reindex_plan() == []


def test_drift_is_measured_against_the_previously_labelled_vector():
    labeller = EmbeddingFallbackLabeller()
    labeller.label("v1", [1.0, 0.0, 0.0, 0.0])

    stable = labeller.label("v1", [1.0, 0.0, 0.0, 0.0])
    assert stable.drift_score == pytest.approx(0.0, abs=1e-6)
    assert stable.needs_reindex is False

    rotated = labeller.label("v1", [0.0, 1.0, 0.0, 0.0])
    assert rotated.drift_score == pytest.approx(1.0, abs=1e-6)
    assert rotated.needs_reindex is True
    assert labeller.generate_reindex_plan() == ["v1"]

    # A dimension change cannot be compared: maximal distance, not a crash.
    resized = labeller.label("v1", [1.0, 2.0])
    assert resized.drift_score == 1.0


# ── reranker never raises ────────────────────────────────────────────────────

def test_reranker_falls_back_to_fused_order_when_the_shared_helper_fails(monkeypatch):
    def _explode(query, matches, *, top_k=5):
        raise RuntimeError("cross encoder unavailable")

    monkeypatch.setattr(rerank_mod, "rerank_matches", _explode)
    candidates = [
        {"id": "a", "fused_score": 0.1},
        {"id": "b", "fused_score": 0.9},
        {"id": "c"},
    ]
    ranked = RerankerInterface().rerank("q", candidates, top_k=2)
    assert [item["id"] for item in ranked] == ["b", "a"]
    assert ranked[0]["rerank_score"] == 0.9


# ── graph edge quality ───────────────────────────────────────────────────────

def test_detect_duplicate_edges_reports_only_the_repeated_key():
    manager = GraphEdgeQualityManager()
    edges = [
        {"id": "e1", "source": "a", "target": "b", "type": "rel", "confidence": 0.2},
        {"id": "e2", "source": "a", "target": "b", "type": "rel", "confidence": 0.9},
        {"id": "e3", "source": "b", "target": "c", "type": "rel"},
    ]
    assert manager.detect_duplicate_edges(edges) == ["e2"]


# ── structured context ───────────────────────────────────────────────────────

def test_unknown_sections_fall_back_to_facts():
    assembled = StructuredContextAssembler().assemble(
        [{"id": "i1", "section": "Nonexistent"}, {"id": "i2", "section": "Decisions"}]
    )
    assert [item["id"] for item in assembled["Facts"]] == ["i1"]
    assert [item["id"] for item in assembled["Decisions"]] == ["i2"]


# ── retrieval benchmark runner ───────────────────────────────────────────────

def test_benchmark_reports_zeros_for_unjudged_and_unlabelled_fixtures():
    runner = RetrievalBenchmarkRunner()
    assert runner.summary() == {"status": "no runs"}

    unjudged = runner.run_fixture("plain", ["how do I export?", "where is my data?"])
    assert unjudged["judged"] == 0
    assert unjudged["recall@5"] == 0.0
    assert unjudged["must_include_hit_rate"] == 1.0

    # A judged query with no relevant set contributes nothing rather than 1/0.
    unlabelled = runner.run_fixture("judged", [{"retrieved": ["d1"], "relevant": []}])
    assert unlabelled["judged"] == 1
    assert unlabelled["recall@5"] == 0.0
    assert runner.summary()["total_runs"] == 2


# ── workflow validation ──────────────────────────────────────────────────────

def test_validate_reports_an_empty_node_list(monkeypatch):
    monkeypatch.setattr(workflow_mod, "normalize_definition", lambda wf: {"nodes": []})
    assert validate_definition({"nodes": [{"id": "trigger", "type": "trigger"}]}) == [
        "workflow has no nodes"
    ]


def test_validate_reports_duplicate_ids_extra_triggers_and_missing_ids():
    errors = validate_definition(
        {
            "nodes": [
                {"id": "n", "type": "trigger", "next": None},
                {"id": "n", "type": "trigger", "next": None},
                {"type": "tool", "next": None},
            ]
        }
    )
    assert "duplicate node ids" in errors
    assert "workflow must have exactly one trigger node" in errors
    assert "node missing id" in errors


def test_import_workflow_refuses_non_objects_and_invalid_graphs():
    with pytest.raises(WorkflowError, match="must be a JSON object"):
        import_workflow(["not", "a", "dict"])
    with pytest.raises(WorkflowError, match="unknown node"):
        import_workflow({"nodes": [{"id": "t", "type": "trigger", "next": "ghost"}]})
    imported = import_workflow({"name": "n", "nodes": _linear()["nodes"]})
    assert imported["metadata"]["imported"] is True


def test_entry_node_falls_back_to_the_first_node():
    nodes = [{"id": "a", "type": "tool"}, {"id": "b", "type": "output"}]
    assert _entry_node(nodes)["id"] == "a"
    assert _entry_node([]) is None
    assert _entry_node([{"id": "t", "type": "trigger"}])["id"] == "t"


# ── condition evaluation (no eval) ───────────────────────────────────────────

def test_condition_operators_fail_closed_onto_false():
    context = {"count": 3, "name": "lattice", "flag": 0}
    assert _evaluate_condition({"left": "count"}, context) is True
    assert _evaluate_condition({"left": "flag"}, context) is False
    assert _evaluate_condition({"left": "name", "op": "!=", "right": "other"}, context) is True
    assert _evaluate_condition({"left": "name", "op": "contains", "right": "tice"}, context) is True
    assert _evaluate_condition({"left": "count", "op": ">", "right": 1}, context) is True
    assert _evaluate_condition({"left": "count", "op": "<=", "right": 2}, context) is False
    # Non-numeric comparison and unknown operators both resolve to False.
    assert _evaluate_condition({"left": "name", "op": ">", "right": 2}, context) is False
    assert _evaluate_condition({"left": "count", "op": "matches", "right": 3}, context) is False
    # A missing key falls back to the literal in the config.
    assert _evaluate_condition({"left": "absent", "left_value": "x", "op": "=="}, context) is False


# ── engine lifecycle ─────────────────────────────────────────────────────────

def test_invalid_definitions_still_fire_the_workflow_end_hook():
    hooks = _RecordingHooks()
    run = WorkflowEngine(hooks=hooks).run(
        {"id": "wf-bad", "nodes": [{"id": "t", "type": "trigger", "next": "ghost"}]}
    )
    assert run.status == "failed"
    assert run.timeline[0]["type"] == "validation"
    assert [(name, event) for name, event, _ in hooks.fired] == [
        ("pre_workflow", "workflow.start"),
        ("post_workflow", "workflow.end"),
    ]
    assert hooks.fired[-1][2] == {"workflow_id": "wf-bad", "status": "failed"}


def test_approval_suspension_snapshots_context_and_fires_the_paused_hook():
    hooks = _RecordingHooks()

    def _needs_approval(node, context):
        raise ApprovalRequired("write_file needs approval", tool="write_file",
                               args={"path": "/tmp/x"}, permission={"mode": "ask"})

    engine = WorkflowEngine({"tool": _needs_approval}, hooks=hooks)
    # A tuple key is JSON-unserializable, so the snapshot degrades to inputs.
    run = engine.run(_linear(), inputs={("not", "json"): 1})

    assert run.status == "awaiting_approval"
    assert run.paused_node == "act"
    assert run.pending_approval["tool"] == "write_file"
    assert set(run.paused_context) == {"inputs"}
    assert ("post_workflow", "workflow.paused") in [(n, e) for n, e, _ in hooks.fired]
    assert hooks.fired[-1][2]["node"] == "act"
