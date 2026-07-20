"""Single-agent vs multi-agent harness consistency tests.

The two runtimes are intentionally different in mechanism (state machine vs
role pipeline — see the ``lattice_brain.runtime.multi_agent`` module docstring)
but must stay consistent where callers can observe them:

* the shared ``agent-run-contract/v1`` envelope,
* the canonical terminal-status vocabulary (``runtime/statuses.py``),
* fail-closed handling of both role-error result shapes (``error`` / ``reason``),
* the shared ``dispatch_tool`` pre_tool/post_tool hook seam, and
* proposal-first change governance (a governed tool stages a proposal instead
  of executing, while ``approve()`` excludes governed tools from the
  hard-block set — the v9.6.0 invariant).
"""

import asyncio
from pathlib import Path

from lattice_brain.runtime.contracts import (
    CONTRACT_ENVELOPE_KEYS,
    is_contract_member,
)
from lattice_brain.runtime.multi_agent import (
    MultiAgentOrchestrator,
    OrchestrationContext,
    default_role_runner,
)
from lattice_brain.runtime.statuses import RUN_TERMINAL_STATUSES
from latticeai.core.agent import (
    AgentDeps,
    AgentRunContext,
    AgentState,
    SingleAgentRuntime,
)


class _Req:
    message = "do the thing"
    conversation_id = None
    temperature = 0.2
    workspace_id = None
    source = "test"


def _single_agent_deps(replies, tool_calls, *, auto_approve=True, governor=None, hooks=None):
    """Minimal fake ports for the single-agent loop (mirrors test_agent_trace)."""
    queue = list(replies)

    async def generate_as(model_id, message, context, max_tokens, temperature):
        return queue.pop(0)

    async def generate(**kwargs):
        return '{"action": "noop"}'

    def execute_tool(name, args):
        tool_calls.append((name, dict(args)))
        return {"ok": True, "path": args.get("path", "")}

    policy = {
        "auto_approve": auto_approve, "risk": "write", "shell": False,
        "network": False, "destructive": False, "sandbox": "workspace",
        "rollback": "git",
    }
    return AgentDeps(
        generate_as=generate_as,
        generate=generate,
        execute_tool=execute_tool,
        policy_for=lambda name, args: dict(policy),
        risk_level=lambda p: p["risk"],
        check_role=lambda name, user: None,
        tool_governance={"write_file": dict(policy)},
        file_create_actions=frozenset({"write_file"}),
        recent_chat_context=lambda **kw: "",
        clear_history=lambda keep: {"ok": True},
        knowledge_save=lambda *a, **kw: None,
        audit=lambda *a, **kw: None,
        planner_prompt="p", executor_prompt="e", critic_prompt="c",
        memory_updater_prompt="m", agent_root=Path("/tmp"),
        hooks=hooks,
        change_governor=governor,
    )


# ── status vocabulary ───────────────────────────────────────────────────

def test_multi_agent_terminal_statuses_are_canonical():
    ok = MultiAgentOrchestrator().run("build a thing")
    assert ok.status in RUN_TERMINAL_STATUSES

    reviews = {"count": 0}

    def retry_once(role, ctx: OrchestrationContext):
        if role == "planner":
            ctx.plan = [{"index": 0, "description": "s", "status": "planned"}]
        elif role == "executor":
            ctx.executed = [{"index": 0, "status": "done"}]
        elif role == "reviewer":
            reviews["count"] += 1
            ctx.review = {"verdict": "retry" if reviews["count"] == 1 else "pass", "reason": "t"}
        return {"role": role}

    retried = MultiAgentOrchestrator(role_runner=retry_once).run("goal", max_retries=2)
    assert retried.status == "retried_ok"
    assert retried.status in RUN_TERMINAL_STATUSES

    def always_retry(role, ctx: OrchestrationContext):
        if role == "planner":
            ctx.plan = [{"index": 0, "description": "s", "status": "planned"}]
        elif role == "reviewer":
            ctx.review = {"verdict": "retry", "reason": "never"}
        return {"role": role}

    failed = MultiAgentOrchestrator(role_runner=always_retry).run("goal", max_retries=0)
    assert failed.status == "failed"
    assert failed.status in RUN_TERMINAL_STATUSES


# ── shared contract envelope ────────────────────────────────────────────

def test_contract_envelope_is_shared_across_runtimes():
    multi = MultiAgentOrchestrator().run("goal").as_dict()["contract"]
    assert is_contract_member(multi)
    assert multi["runtime"] == "multi_agent"
    assert multi["status"] in RUN_TERMINAL_STATUSES
    assert multi["is_terminal"] is True

    runtime = SingleAgentRuntime(_single_agent_deps([], []))
    ctx = AgentRunContext()
    ctx.state = AgentState.DONE
    single = runtime.contract(ctx, _Req(), run_id="run-1")
    assert is_contract_member(single)
    assert single["runtime"] == "single_agent"
    assert single["status"] == "done"
    assert single["is_terminal"] is True

    for key in CONTRACT_ENVELOPE_KEYS:
        assert key in multi
        assert key in single


# ── role-error result shapes ────────────────────────────────────────────

def test_llm_style_role_failure_reason_reaches_timeline():
    # llm_role_runner failures return {"status": "error", "reason": ...} and
    # set a fail-closed reject review; the terminal timeline event must carry
    # the real reason, not a generic "<role> role failed" placeholder.
    def llm_style_fail(role, ctx: OrchestrationContext):
        if role == "planner":
            ctx.review = {
                "outcome": "reject", "verdict": "fail",
                "reason": "planner: plan output unparseable (boom)",
                "raw_output": "not json",
            }
            return {"role": role, "status": "error",
                    "reason": "plan output unparseable (boom)", "raw": "not json"}
        return {"role": role}

    res = MultiAgentOrchestrator(role_runner=llm_style_fail).run("goal")
    assert res.status == "failed"
    assert res.review["outcome"] == "reject"  # fail-closed review preserved
    events = [t for t in res.timeline if t.get("event") == "execution_failed" and t.get("role")]
    assert events and "unparseable" in events[0]["reason"]
    assert events[0]["reason"] != "planner role failed"


def test_raised_role_failure_reason_reaches_timeline():
    # A raised exception surfaces as {"status": "error", "error": ...}.
    def raising(role, ctx: OrchestrationContext):
        if role == "planner":
            raise RuntimeError("kaboom")
        return {"role": role}

    res = MultiAgentOrchestrator(role_runner=raising).run("goal")
    assert res.status == "failed"
    assert res.review["outcome"] == "reject"
    events = [t for t in res.timeline if t.get("event") == "execution_failed" and t.get("role")]
    assert events and "kaboom" in events[0]["reason"]


# ── governed tools: proposal, not execution ─────────────────────────────

class _ProposingGovernor:
    """Fake change governor: every reviewed call becomes a proposal."""

    governed_tools = frozenset({"write_file"})

    def __init__(self):
        self.reviews = []

    def review(self, name, args, *, policy=None, user_email=None, workspace_id=None, conversation_id=None):
        self.reviews.append((name, dict(args)))
        return {
            "decision": "proposed",
            "proposal": {"id": "prop-1"},
            "classification": {"change_class": "mutation"},
        }


def test_governed_tool_produces_proposal_not_execution():
    tool_calls = []
    governor = _ProposingGovernor()
    deps = _single_agent_deps(
        [
            '{"action": "plan", "goal": "update", "steps": [{"action": "write_file"}]}',
            '{"action": "write_file", "args": {"path": "site.html", "content": "<new>"}}',
            '{"action": "final", "message": "done"}',
            '{"action": "verdict", "verdict": "PASS", "next_state": "DONE"}',
        ],
        tool_calls,
        auto_approve=False,  # non-auto tool: only the governor exclusion lets the plan through
        governor=governor,
    )
    runtime = SingleAgentRuntime(deps)
    ctx = AgentRunContext()
    ctx.state = AgentState.PLANNING

    async def run():
        await runtime.plan(ctx, _Req(), "en", "u@t")
        runtime.approve(ctx, "u@t")
        # v9.6.0 invariant: approve() excludes governed tools from the
        # hard-block set, so the governed plan auto-approves and each call is
        # classified at execution time instead.
        assert ctx.state == AgentState.EXECUTING
        await runtime.run_to_completion(ctx, _Req(), "en", "u@t", max_steps=5, max_retry=1)

    asyncio.run(run())

    assert ctx.state == AgentState.DONE
    assert tool_calls == []  # never executed
    assert governor.reviews and governor.reviews[0][0] == "write_file"
    assert ctx.trace.summary()["tool_outcomes"] == {"proposed": 1}
    proposed = [
        s for s in ctx.transcript
        if isinstance(s.get("result"), dict) and s["result"].get("proposed")
    ]
    assert proposed and proposed[0]["result"]["proposal_id"] == "prop-1"
    decisions = [e for e in ctx.trace.events if e["kind"] == "decision"]
    assert any(e["decision"] == "auto_approved" for e in decisions)


# ── shared dispatch_tool hook seam ──────────────────────────────────────

class _BlockingPreToolHooks:
    """Fake hooks registry: blocks every pre_tool dispatch."""

    def __init__(self):
        self.fired = []

    def fire_hook(self, kind, event, *, payload=None, user_email=None,
                  workspace_id=None, **kwargs):
        self.fired.append((kind, event))
        if kind == "pre_tool":
            return {"blocked": True, "block_reason": "blocked by pre_tool hook"}
        return {"blocked": False}


def test_blocking_pre_tool_hook_gates_single_agent_tool_call():
    tool_calls = []
    hooks = _BlockingPreToolHooks()
    deps = _single_agent_deps(
        [
            '{"action": "write_file", "args": {"path": "a.txt", "content": "x"}}',
            '{"action": "final", "message": "stop"}',
        ],
        tool_calls,
        hooks=hooks,
    )
    runtime = SingleAgentRuntime(deps)
    ctx = AgentRunContext()
    ctx.state = AgentState.EXECUTING

    asyncio.run(runtime.execute(ctx, _Req(), "en", "u@t", max_steps=5))

    assert tool_calls == []  # blocked before execute_tool
    assert ("pre_tool", "tool.write_file") in hooks.fired
    assert not any(kind == "post_tool" for kind, _ in hooks.fired)
    errors = [s.get("error") for s in ctx.transcript if s.get("error")]
    assert errors and "pre_tool" in errors[0]
    assert ctx.trace.summary()["tool_outcomes"] == {"error": 1}


def test_multi_agent_blocked_tool_step_fails_closed():
    # The multi-agent runtime never executes tools itself; a blocked injected
    # runner (the workflow tool node raises for non-approved tools) must fail
    # the step and the run — never convert a blocked call into a success.
    def blocked_workflow(wf, ctx):
        raise PermissionError("tool 'write_file' requires explicit approval")

    runner = default_role_runner(workflow_runner=blocked_workflow)
    res = MultiAgentOrchestrator(role_runner=runner).run(
        "goal", inputs={"workflow": "wf-1", "steps": ["only-step"]}, max_retries=0,
    )
    assert res.status == "failed"
    assert res.status in RUN_TERMINAL_STATUSES
    role_events = [t for t in res.timeline if t.get("event") == "role" and t.get("role") == "executor"]
    executed = role_events[0]["result"]["results"]
    assert executed[0]["status"] == "error"
    assert "approval" in executed[0]["workflow_error"]
