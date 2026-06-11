"""T7b: the multi-agent orchestrator becomes real when a model is loaded.

Honesty contract: mode='llm' runs call the model for plan/execution/review;
unparseable model output FAILS the run with the raw output preserved (never
a silent fall-back to fabricated deterministic artifacts); without a model
the orchestrator stays deterministic and labeled mode='simulation'.
"""

from latticeai.core.multi_agent import MultiAgentOrchestrator, llm_role_runner
from latticeai.services.platform_runtime import PlatformRuntime

PLAN = '{"goal": "g", "steps": [{"description": "analyze"}, {"description": "write summary"}]}'
APPROVE = '{"approve": true, "reason": "result is correct"}'


def _runner(script):
    """generate() that pops scripted responses per call."""
    calls = []

    def generate(message, context="", max_tokens=0, temperature=0.0):
        calls.append(message)
        return script.pop(0)

    return llm_role_runner(generate=generate, planner_prompt="PLAN", critic_prompt="CRITIC"), calls


def test_llm_run_succeeds_with_real_model_output():
    runner, calls = _runner([PLAN, "step one result", "step two result", APPROVE])
    result = MultiAgentOrchestrator(role_runner=runner, mode="llm").run("summarize the report")
    assert result.mode == "llm"
    assert result.status == "ok"
    assert "step one result" in result.output
    assert len(result.plan) == 2
    assert all(s["status"] == "done" for s in result.plan)
    assert result.review["outcome"] == "approve"
    assert len(calls) == 4, "planner + 2 steps + critic"


def test_unparseable_plan_fails_run_with_raw_preserved():
    runner, _ = _runner(["I think you should maybe do some things?"])
    result = MultiAgentOrchestrator(role_runner=runner, mode="llm").run("goal", max_retries=0)
    assert result.status == "failed", "parse failure must fail the run, not fabricate a plan"
    assert result.review["reason"].startswith("planner")
    assert "maybe do some things" in (result.review.get("raw_output") or "")


def test_unparseable_critique_fails_closed():
    runner, _ = _runner([PLAN, "r1", "r2", "looks good to me!"])
    result = MultiAgentOrchestrator(role_runner=runner, mode="llm").run("goal", max_retries=0)
    assert result.status == "failed"
    assert "critic output unparseable" in result.review["reason"]
    assert "looks good" in (result.review.get("raw_output") or "")


def test_critic_retry_then_approve():
    retry = '{"approve": false, "reason": "missing detail"}'
    runner, _ = _runner([PLAN, "a", "b", retry, "a2", "b2", APPROVE])
    result = MultiAgentOrchestrator(role_runner=runner, mode="llm").run("goal", max_retries=1)
    assert result.status == "retried_ok"
    assert result.retries == 1


def test_build_orchestrator_selects_mode_honestly():
    runtime = PlatformRuntime.__new__(PlatformRuntime)
    runtime.store = None
    runtime.registry = type("R", (), {"execute_action": staticmethod(lambda *a, **k: None)})()
    runtime.hooks = None
    runtime.get_tool_permission = lambda *a, **k: {}
    runtime.run_workflow_by_id = lambda *a, **k: {}
    runtime.plugin_capability_runners = lambda *a, **k: {}
    runtime._context_provider = lambda user, scope: (lambda goal: [])

    runtime.llm_generate = None
    runtime.llm_available = lambda: False
    assert runtime.build_orchestrator(None, None).mode == "simulation"

    runtime.llm_generate = lambda *a, **k: PLAN
    runtime.llm_available = lambda: True
    assert runtime.build_orchestrator(None, None).mode == "llm"

    # Model bridge wired but no model loaded → still honest simulation.
    runtime.llm_available = lambda: False
    assert runtime.build_orchestrator(None, None).mode == "simulation"
