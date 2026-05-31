"""Unit tests for the v2.0 Multi-Agent Runtime orchestrator."""

from latticeai.core.multi_agent import (
    AGENT_ROLES,
    MultiAgentOrchestrator,
    OrchestrationContext,
    default_role_runner,
)


def test_default_pipeline_runs_and_passes():
    res = MultiAgentOrchestrator().run("Build a thing")
    assert res.status == "ok"
    assert res.roles_run == ["planner", "executor", "reviewer"]
    assert len(res.plan) >= 1
    assert res.review.get("verdict") == "pass"


def test_full_role_pipeline_with_research_and_release():
    res = MultiAgentOrchestrator().run("Ship it", roles=list(AGENT_ROLES))
    assert res.roles_run[0] == "researcher"
    assert "release" in res.roles_run


def test_handoff_entries_present_in_timeline():
    res = MultiAgentOrchestrator().run("Do work")
    handoffs = [t for t in res.timeline if t.get("event") == "handoff"]
    assert handoffs, "expected at least one handoff between roles"
    assert any(t.get("event") == "role" for t in res.timeline)


def test_retry_on_failing_review_then_pass():
    # A stateful runner: reviewer says retry the first time, pass the second.
    state = {"reviews": 0}

    def runner(role, ctx: OrchestrationContext):
        if role == "planner":
            ctx.plan = [{"index": 0, "description": "step", "status": "planned"}]
            return {"role": role, "steps": 1}
        if role == "executor":
            ctx.executed = [{"index": 0, "status": "done"}]
            ctx.output = "executed"
            return {"role": role, "executed": 1}
        if role == "reviewer":
            state["reviews"] += 1
            verdict = "retry" if state["reviews"] == 1 else "pass"
            ctx.review = {"verdict": verdict, "reason": "test", "confidence": 0.5}
            return {"role": role, **ctx.review}
        return {"role": role}

    res = MultiAgentOrchestrator(role_runner=runner).run("goal", max_retries=2)
    assert res.retries == 1
    assert res.status == "retried_ok"
    assert state["reviews"] == 2


def test_retry_budget_exhausted_fails():
    def always_retry(role, ctx: OrchestrationContext):
        if role == "planner":
            ctx.plan = [{"index": 0, "description": "s", "status": "planned"}]
        elif role == "executor":
            ctx.executed = [{"index": 0, "status": "done"}]
        elif role == "reviewer":
            ctx.review = {"verdict": "retry", "reason": "never happy"}
        return {"role": role}

    res = MultiAgentOrchestrator(role_runner=always_retry).run("goal", max_retries=1)
    assert res.retries == 1
    assert res.status == "failed"


def test_executor_invokes_injected_workflow_runner():
    calls = []
    runner = default_role_runner(
        workflow_runner=lambda wf, ctx: calls.append(wf) or {"workflow_run_id": "wr-1"},
    )
    orch = MultiAgentOrchestrator(role_runner=runner)
    res = orch.run("goal", inputs={"workflow": "wf-123", "steps": ["only-step"]})
    assert calls == ["wf-123"]
    assert res.status == "ok"


def test_context_provider_feeds_researcher():
    runner = default_role_runner(context_provider=lambda goal: ["mem-a", "mem-b"])
    res = MultiAgentOrchestrator(role_runner=runner).run("goal", roles=["researcher", "planner", "executor", "reviewer"])
    research_steps = [t for t in res.timeline if t.get("event") == "role" and t.get("role") == "researcher"]
    assert research_steps and research_steps[0]["result"]["context_items"] == 2
