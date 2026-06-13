"""Unit tests for the v2.0 Workflow Designer engine."""

import pytest

from lattice_brain.workflow import (
    WorkflowEngine,
    WorkflowError,
    export_workflow,
    import_workflow,
    normalize_definition,
    validate_definition,
)


def _linear(nodes_extra=None):
    return {
        "name": "demo",
        "nodes": [
            {"id": "t", "type": "trigger", "config": {"trigger": "manual"}, "next": "a"},
            {"id": "a", "type": "tool", "name": "noop", "config": {"tool": "list_dir"}, "next": "o"},
            {"id": "o", "type": "output", "config": {}, "next": None},
            *(nodes_extra or []),
        ],
    }


# ── validation ────────────────────────────────────────────────────────────────

def test_valid_definition():
    assert validate_definition(_linear()) == []


def test_missing_trigger_fails():
    wf = {"name": "x", "nodes": [{"id": "o", "type": "output", "config": {}, "next": None}]}
    errors = validate_definition(wf)
    assert any("trigger" in e for e in errors)


def test_unknown_node_type_fails():
    wf = {"name": "x", "nodes": [
        {"id": "t", "type": "trigger", "next": "z"},
        {"id": "z", "type": "frobnicate", "next": None},
    ]}
    assert any("unknown type" in e for e in validate_definition(wf))


def test_dangling_edge_fails():
    wf = {"name": "x", "nodes": [{"id": "t", "type": "trigger", "next": "ghost"}]}
    assert any("unknown node 'ghost'" in e for e in validate_definition(wf))


def test_condition_requires_branches():
    wf = {"name": "x", "nodes": [
        {"id": "t", "type": "trigger", "next": "c"},
        {"id": "c", "type": "condition", "config": {}},
    ]}
    assert any("branches" in e for e in validate_definition(wf))


def test_legacy_steps_normalize_and_validate():
    legacy = {"name": "legacy", "steps": [{"action": "foo"}, {"action": "bar"}]}
    normalized = normalize_definition(legacy)
    assert normalized["nodes"][0]["type"] == "trigger"
    assert validate_definition(legacy) == []


# ── execution ─────────────────────────────────────────────────────────────────

def test_run_ok_with_runner():
    engine = WorkflowEngine({"tool": lambda node, context: {"ran": node["id"]}})
    run = engine.run(_linear(), inputs={"x": 1})
    assert run.status == "ok"
    assert run.timeline[0]["type"] == "trigger"
    assert any(s.get("type") == "output" for s in run.timeline)


def test_run_partial_when_runner_missing():
    run = WorkflowEngine({}).run(_linear())
    assert run.status == "partial"
    assert any(s.get("status") == "skipped" for s in run.timeline)


def test_run_failed_on_runner_error():
    def boom(node, context):
        raise RuntimeError("kaboom")
    run = WorkflowEngine({"tool": boom}).run(_linear())
    assert run.status == "failed"
    assert any(s.get("status") == "error" for s in run.timeline)


def test_run_invalid_definition_fails_fast():
    run = WorkflowEngine({}).run({"name": "x", "nodes": [{"id": "t", "type": "trigger", "next": "ghost"}]})
    assert run.status == "failed"
    assert run.timeline[0]["type"] == "validation"


def test_condition_branches_and_output_capture():
    wf = {"name": "c", "nodes": [
        {"id": "t", "type": "trigger", "next": "set"},
        {"id": "set", "type": "tool", "config": {"tool": "x"}, "next": "cond"},
        {"id": "cond", "type": "condition", "config": {"left": "flag", "op": "=="}, "branches": {"true": "yes", "false": "no"}},
        {"id": "yes", "type": "output", "config": {"value": "YES"}, "next": None},
        {"id": "no", "type": "output", "config": {"value": "NO"}, "next": None},
    ]}
    # condition compares context['flag'] == config.right (None); not equal → false branch.
    run = WorkflowEngine({"tool": lambda node, context: "done"}).run(wf, inputs={"flag": "x"})
    assert run.status == "ok"
    assert run.outputs.get("no") == "NO"


def test_cycle_is_guarded():
    wf = {"name": "loop", "nodes": [
        {"id": "t", "type": "trigger", "next": "a"},
        {"id": "a", "type": "tool", "config": {"tool": "x"}, "next": "a"},  # self-loop
    ]}
    run = WorkflowEngine({"tool": lambda node, context: 1}).run(wf)
    assert run.status == "failed"
    assert any("exceeded" in (s.get("reason") or "") for s in run.timeline)


# ── export / import ───────────────────────────────────────────────────────────

def test_export_import_roundtrip():
    exported = export_workflow(_linear())
    imported = import_workflow(exported)
    assert validate_definition(imported) == []
    assert len(imported["nodes"]) == 3


def test_import_invalid_raises():
    with pytest.raises(WorkflowError):
        import_workflow({"name": "x", "nodes": [{"id": "t", "type": "trigger", "next": "ghost"}]})
