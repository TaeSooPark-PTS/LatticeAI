"""T7b: the multi-agent orchestrator becomes real when a model is loaded.

Honesty contract: mode='llm' runs call the model for plan/execution/review;
unparseable model output FAILS the run with the raw output preserved (never
a silent fall-back to fabricated deterministic artifacts); without a model
the orchestrator stays deterministic and labeled mode='simulation'.
"""

from lattice_brain.runtime.multi_agent import MultiAgentOrchestrator, llm_role_runner
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
    runtime.agent_registry = None

    runtime.llm_generate = None
    runtime.llm_available = lambda: False
    assert runtime.build_orchestrator(None, None).mode == "simulation"

    runtime.llm_generate = lambda *a, **k: PLAN
    runtime.llm_available = lambda: True
    assert runtime.build_orchestrator(None, None).mode == "llm"

    # Model bridge wired but no model loaded → still honest simulation.
    runtime.llm_available = lambda: False
    assert runtime.build_orchestrator(None, None).mode == "simulation"


def test_context_provider_uses_scoped_memory_service_recall():
    runtime = PlatformRuntime.__new__(PlatformRuntime)
    seen = {}

    def recall(query, **kwargs):
        seen.update({"query": query, **kwargs})
        return {
            "results": [
                {
                    "source": "graph",
                    "title": "Roadmap",
                    "snippet": "The scanned roadmap says the launch is Friday.",
                }
            ]
        }

    runtime.memory_recall = recall
    runtime.store = None
    context = runtime._context_provider("owner@example.com", "org:acme")("prepare launch follow-ups")

    assert context == ["[graph] Roadmap: The scanned roadmap says the launch is Friday."]
    assert seen["user_email"] == "owner@example.com"
    assert seen["workspace_id"] == "org:acme"
    assert seen["limit"] == 8


def test_context_provider_falls_back_to_newest_scoped_memories():
    runtime = PlatformRuntime.__new__(PlatformRuntime)
    runtime.memory_recall = lambda *args, **kwargs: {"results": []}

    class Store:
        @staticmethod
        def search_memories(*args, **kwargs):
            return {"memories": []}

        @staticmethod
        def list_memories(*args, **kwargs):
            # The store contract is newest-first.
            return {"memories": [
                {"content": "newest knowledge"},
                {"content": "older knowledge"},
            ]}

    runtime.store = Store()
    context = runtime._context_provider("owner@example.com", "org:acme")("unmatched automation goal")

    assert context == ["newest knowledge", "older knowledge"]


def test_legacy_recipe_prompt_becomes_grounded_agent_goal():
    runtime = PlatformRuntime.__new__(PlatformRuntime)
    captured = {}

    def run_agent(goal, user, scope, **kwargs):
        captured.update({"goal": goal, "user": user, "scope": scope, **kwargs})
        return {"status": "ok"}

    runtime.run_agent = run_agent
    runner = runtime._agent_node_runner("owner@example.com", "personal")
    runner(
        node={"config": {"prompt": "Review new Brain memory", "mode": "draft"}},
        context={"inputs": {"__trigger__": {"source_type": "note"}}},
    )

    assert captured["goal"] == "Review new Brain memory"
    assert captured["roles"] == ["researcher", "planner", "executor", "reviewer"]
    assert captured["with_workflow"] is False


def test_custom_registry_agents_execute():
    """A registered custom agent id in the pipeline actually runs with its
    persisted config — registration is no longer a UI illusion."""
    entry = {
        "id": "agent:custom:summarizer", "name": "Summarizer", "enabled": True,
        "config": {"system_prompt": "You summarize text.", "max_tokens": 256},
    }
    seen = {}

    def generate(message, context="", max_tokens=0, temperature=0.0):
        seen["context"] = context
        seen["max_tokens"] = max_tokens
        return "a tight summary"

    runner = llm_role_runner(
        generate=generate, planner_prompt="P", critic_prompt="C",
        custom_agents={entry["id"]: entry},
    )
    result = MultiAgentOrchestrator(
        role_runner=runner, mode="llm", custom_agents={entry["id"]: entry},
    ).run("summarize the doc", roles=["agent:custom:summarizer"])
    assert result.roles_run == ["agent:custom:summarizer"]
    assert seen["context"] == "You summarize text."
    assert seen["max_tokens"] == 256
    assert result.output == "a tight summary"


def test_custom_agent_in_simulation_skips_honestly():
    entry = {"id": "agent:custom:summarizer", "name": "Summarizer", "enabled": True, "config": {}}
    from lattice_brain.runtime.multi_agent import default_role_runner

    result = MultiAgentOrchestrator(
        role_runner=default_role_runner(), mode="simulation",
        custom_agents={entry["id"]: entry},
    ).run("goal", roles=["agent:custom:summarizer"])
    role_entries = [t for t in result.timeline if t.get("event") == "role"]
    assert role_entries and role_entries[0]["result"]["status"] == "skipped"
    assert "loaded model" in role_entries[0]["result"]["reason"]
