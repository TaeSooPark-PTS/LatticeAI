"""wp24 coverage — ``lattice_brain.runtime.multi_agent``.

The orchestrator is pure: no model, no tools, no I/O. Every seam it has is an
injected callable, so these tests drive the real code with scripted runners —
a ``generate`` that returns canned model text, workflow/plugin runners that
either return a value or raise — and assert what the run *records*: which step
failed and why, whether an unparseable model reply fails the run closed instead
of being rubber-stamped, and that secrets never reach a context packet.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from lattice_brain.runtime import multi_agent
from lattice_brain.runtime.multi_agent import (
    CORE_PIPELINE,
    MultiAgentOrchestrator,
    OrchestrationContext,
    _extract_json_object,
    _redact,
    default_role_runner,
    llm_role_runner,
)


def _ctx(**kwargs) -> OrchestrationContext:
    kwargs.setdefault("goal", "ship the release")
    return OrchestrationContext(**kwargs)


def _scripted_generate(replies):
    """A ``generate`` bridge that replays canned model output in order."""
    queue = list(replies)

    def generate(_message, context="", max_tokens=0, temperature=0.0):
        return queue.pop(0) if queue else "{}"

    return generate


# ── redaction / value objects ───────────────────────────────────────────────


def test_redact_masks_secret_fields_and_keeps_the_payload_json_safe():
    class _Opaque:
        def __str__(self):
            return "<opaque>"

    clean = _redact({
        "api_key": "sk-live-123",
        "nested": {"session_token": "t-1", "keep": "visible"},
        "pair": ("one", {"password": "hunter2"}),
        "items": [1, 2.5, True, None],
        "object": _Opaque(),
    })

    assert clean["api_key"] == "[redacted]"
    assert clean["nested"] == {"session_token": "[redacted]", "keep": "visible"}
    assert clean["pair"] == ["one", {"password": "[redacted]"}]
    assert clean["items"] == [1, 2.5, True, None]
    assert clean["object"] == "<opaque>"


def test_a_handoff_with_an_unknown_status_is_normalized_to_completed():
    ctx = _ctx()

    record = ctx.handoff("planner", "executor", "start work", status="teleported")

    assert record["status"] == "completed"
    assert record["completed_at"]
    assert [event["event"] for event in ctx.timeline] == [
        "handoff_created", "handoff_accepted", "handoff_completed", "handoff",
    ]


# ── deterministic runner ────────────────────────────────────────────────────


def test_the_planner_adopts_requested_steps_and_attaches_the_plugin():
    runner = default_role_runner()
    ctx = _ctx(inputs={
        "steps": [{"name": "collect inputs"}, {"description": "write it up", "status": "todo"}, "verify"],
        "plugin": "git-insights",
    })

    result = runner("planner", ctx)

    assert result["steps"] == 3
    assert [step["description"] for step in ctx.plan] == [
        "collect inputs", "write it up", "verify",
    ]
    assert [step["index"] for step in ctx.plan] == [0, 1, 2]
    # A caller-supplied status is preserved; a missing one defaults to planned.
    assert [step["status"] for step in ctx.plan] == ["planned", "todo", "planned"]
    assert ctx.plan[0]["plugin"] == "git-insights"


def test_the_executor_records_plugin_output_and_plugin_failure():
    ok_runner = default_role_runner(plugin_runner=lambda name, _ctx: {"plugin": name, "ok": True})
    ctx = _ctx(inputs={"plugin": "git-insights"})
    ok_runner("planner", ctx)

    result = ok_runner("executor", ctx)

    first = result["results"][0]
    assert first["plugin_result"] == {"plugin": "git-insights", "ok": True}
    assert first["status"] == "done"
    assert ctx.plugin_outputs == [{"plugin": "git-insights", "ok": True}]

    def boom(_name, _ctx):
        raise RuntimeError("plugin crashed")

    failing = default_role_runner(plugin_runner=boom)
    ctx2 = _ctx(inputs={"plugin": "git-insights"})
    failing("planner", ctx2)

    failed = failing("executor", ctx2)["results"][0]
    assert failed["plugin_error"] == "plugin crashed"
    assert failed["status"] == "error"
    assert ctx2.plan[0]["status"] == "failed"


# ── model-output recovery ───────────────────────────────────────────────────


def test_a_candidate_that_parses_to_a_non_object_is_skipped(monkeypatch):
    real_loads = json.loads

    def loads(text):
        # Simulate a parser handing back a valid JSON *array* for the first span.
        return ["not", "an", "object"] if "decoy" in text else real_loads(text)

    monkeypatch.setattr(multi_agent, "json", SimpleNamespace(loads=loads))

    assert _extract_json_object('{"decoy": 1} and then {"goal": "real"}') == {"goal": "real"}


# ── llm runner ──────────────────────────────────────────────────────────────


def test_a_custom_agent_whose_generation_fails_marks_the_run_rejected():
    def generate(*_args, **_kwargs):
        raise RuntimeError("model unloaded")

    runner = llm_role_runner(
        generate=generate, planner_prompt="PLAN", critic_prompt="CRIT",
        custom_agents={"summarizer": {"name": "Summarizer", "config": {"max_tokens": 256}}},
    )
    ctx = _ctx()

    result = runner("summarizer", ctx)

    assert result["status"] == "error"
    assert "custom agent generation failed" in result["reason"]
    assert ctx.review["outcome"] == "reject"
    assert ctx.inputs["__llm_failure__"]["role"] == "summarizer"


def test_a_plan_with_no_steps_fails_the_run_instead_of_inventing_one():
    runner = llm_role_runner(
        generate=_scripted_generate(['{"goal": "x", "steps": []}']),
        planner_prompt="PLAN", critic_prompt="CRIT",
    )
    ctx = _ctx()

    result = runner("planner", ctx)

    assert result["status"] == "error"
    assert result["reason"] == "model returned a plan with no steps"
    assert ctx.plan == []


def test_the_model_plan_carries_the_requested_workflow_and_plugin():
    runner = llm_role_runner(
        generate=_scripted_generate(['{"steps": [{"description": "do it"}]}']),
        planner_prompt="PLAN", critic_prompt="CRIT",
    )
    ctx = _ctx(inputs={"workflow": "wf-1", "plugin": "git-insights"})

    result = runner("planner", ctx)

    assert result["steps"] == 1
    assert ctx.plan[0]["workflow"] == "wf-1"
    assert ctx.plan[0]["plugin"] == "git-insights"
    assert ctx.plan_review["outcome"] == "approve"


def test_the_executor_is_skipped_after_an_upstream_model_failure():
    runner = llm_role_runner(
        generate=_scripted_generate(["not json at all"]),
        planner_prompt="PLAN", critic_prompt="CRIT",
    )
    ctx = _ctx()
    runner("planner", ctx)

    result = runner("executor", ctx)

    assert result["status"] == "error"
    assert result["reason"] == "skipped — planner failed"


def test_the_reviewer_fails_closed_on_an_upstream_model_failure():
    runner = llm_role_runner(
        generate=_scripted_generate(["still not json"]),
        planner_prompt="PLAN", critic_prompt="CRIT",
    )
    ctx = _ctx()
    runner("planner", ctx)

    review = runner("reviewer", ctx)

    assert review["outcome"] == "reject"
    assert review["reason"] == "planner output unparseable"
    assert review["raw_output"] == "still not json"


def test_the_model_executor_records_workflow_plugin_and_generation_outcomes():
    plan = '{"steps": [{"description": "run the workflow"}]}'
    runner = llm_role_runner(
        generate=_scripted_generate([plan, "step done"]),
        planner_prompt="PLAN", critic_prompt="CRIT",
        workflow_runner=lambda name, _ctx: {"workflow": name, "status": "ok"},
        plugin_runner=lambda name, _ctx: {"plugin": name},
    )
    ctx = _ctx(inputs={"workflow": "wf-1", "plugin": "git-insights"})
    runner("planner", ctx)

    outcome = runner("executor", ctx)["results"][0]

    assert outcome["workflow_result"] == {"workflow": "wf-1", "status": "ok"}
    assert outcome["plugin_result"] == {"plugin": "git-insights"}
    assert outcome["result"] == "step done"
    assert outcome["status"] == "done"
    assert ctx.output == "step done"


def test_the_model_executor_marks_a_step_failed_when_every_seam_raises():
    def raising(*_args, **_kwargs):
        raise RuntimeError("seam is down")

    plan = '{"steps": [{"description": "run the workflow"}]}'
    replies = [plan]

    def generate(_message, context="", max_tokens=0, temperature=0.0):
        if replies:
            return replies.pop(0)
        raise RuntimeError("generation failed")

    runner = llm_role_runner(
        generate=generate, planner_prompt="PLAN", critic_prompt="CRIT",
        workflow_runner=raising, plugin_runner=raising,
    )
    ctx = _ctx(inputs={"workflow": "wf-1", "plugin": "git-insights"})
    runner("planner", ctx)

    outcome = runner("executor", ctx)["results"][0]

    assert outcome["workflow_error"] == "seam is down"
    assert outcome["plugin_error"] == "seam is down"
    assert outcome["error"] == "generation failed"
    assert outcome["status"] == "error"
    assert ctx.plan[0]["status"] == "failed"
    assert ctx.output.startswith("Completed 0/1 step(s)")


def test_roles_without_model_behaviour_reuse_the_deterministic_runner():
    runner = llm_role_runner(
        generate=_scripted_generate([]),
        planner_prompt="PLAN", critic_prompt="CRIT",
        context_provider=lambda goal: [f"memory about {goal}"],
    )
    ctx = _ctx()

    research = runner("researcher", ctx)

    assert research["context_items"] == 1
    assert research["memory_snapshot"]["scope"] == "short_term"
    assert ctx.research == ["memory about ship the release"]


# ── pipeline ────────────────────────────────────────────────────────────────


def test_a_pipeline_of_unknown_roles_falls_back_to_the_core_pipeline():
    result = MultiAgentOrchestrator().run("ship it", roles=["archivist", "auditor"])

    assert result.roles_run == list(CORE_PIPELINE)
    assert result.status == "ok"
    start = result.timeline[0]
    assert start["event"] == "start"
    assert start["pipeline"] == list(CORE_PIPELINE)


@pytest.mark.parametrize("bad_status", ["teleported", ""])
def test_handoff_status_normalization_is_total(bad_status):
    ctx = _ctx()
    assert ctx.handoff("planner", "executor", status=bad_status)["status"] == "completed"
