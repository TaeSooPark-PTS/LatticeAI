"""The agent loop is now testable through its interface — no FastAPI, no MLX.

We drive SingleAgentRuntime with fake ports: an LLM that returns canned JSON per
phase, and a recording tool executor. This is the leverage the extraction
bought — before, exercising the state machine meant POSTing to a live /agent.
"""

import asyncio
from pathlib import Path

import pytest

from latticeai.core.agent import (
    AgentDeps,
    AgentRunContext,
    AgentState,
    AgentRuntime,
    SingleAgentRuntime,
    extract_action,
)
from lattice_brain.runtime.contracts import RuntimeBoundaryProtocol, contract_view, extract_contract


class FakeReq:
    """Duck-typed stand-in for server.py's AgentRequest pydantic model."""
    def __init__(self, message="do the thing"):
        self.message = message
        self.conversation_id = None
        self.source = "test"
        self.temperature = 0.1
        self.max_steps = 25
        self.executing_model = None
        self.reviewing_model = None


def _deps(scripted, tool_calls):
    """Build AgentDeps whose LLM replays `scripted` responses in order."""
    queue = list(scripted)

    async def generate_as(model_id, message, context, max_tokens, temperature):
        return queue.pop(0)

    async def generate(message, context, max_tokens, temperature):
        return '{"action":"memory","save_to_knowledge":false}'

    def execute_tool(name, args):
        tool_calls.append((name, args))
        return {"success": True, "path": args.get("path", "")}

    def policy_for(name, args):
        return {"risk": "write", "destructive": False, "shell": False,
                "network": False, "auto_approve": True, "sandbox": "workspace",
                "rollback": "git"}

    def rollback_file(path):
        return {"path": path, "ok": True, "stderr": ""}

    return AgentDeps(
        generate_as=generate_as,
        generate=generate,
        execute_tool=execute_tool,
        policy_for=policy_for,
        risk_level=lambda p: {"read": "low", "write": "medium"}.get(p["risk"], "medium"),
        check_role=lambda name, user: None,
        tool_governance={"edit_file": {"auto_approve": True}},
        file_create_actions=frozenset({"write_file", "edit_file"}),
        recent_chat_context=lambda conversation_id=None: "",
        clear_history=lambda keep_last: {"cleared": True},
        knowledge_save=lambda *a, **k: None,
        audit=lambda *a, **k: None,
        planner_prompt="PLAN", executor_prompt="EXEC",
        critic_prompt="CRIT", memory_updater_prompt="MEM",
        agent_root=Path("/tmp"),
        rollback_file=rollback_file,
    )


def test_full_cycle_plan_execute_verify_done():
    tool_calls = []
    scripted = [
        # planner
        '{"action":"plan","goal":"edit a file","steps":[{"action":"edit_file"}],"requires_approval":false}',
        # executor step 1: do a tool call
        '{"action":"edit_file","thoughts":"editing","args":{"path":"a.py","old_string":"x","new_string":"y"}}',
        # executor step 2: finish
        '{"action":"final","thoughts":"done","message":"완료"}',
        # critic
        '{"action":"verdict","verdict":"PASS","next_state":"DONE","reason":"looks good"}',
    ]
    rt = SingleAgentRuntime(_deps(scripted, tool_calls))
    req = FakeReq()
    ctx = AgentRunContext()

    async def run():
        ctx.state = AgentState.PLANNING
        await rt.plan(ctx, req, "ko", "tester")
        assert ctx.state == AgentState.WAITING_APPROVAL
        rt.approve(ctx, "tester")
        assert ctx.state == AgentState.EXECUTING
        await rt.run_to_completion(ctx, req, "ko", "tester", max_steps=25, max_retry=3)

    asyncio.run(run())

    assert ctx.state == AgentState.DONE
    assert ctx.final_message == "완료"
    assert ("edit_file", {"path": "a.py", "old_string": "x", "new_string": "y"}) in tool_calls
    contract = rt.contract(ctx, req, run_id="single-run-1")
    assert contract["schema_version"] == "agent-run-contract/v1"
    assert contract["runtime"] == "single_agent"
    assert contract["run_id"] == "single-run-1"
    assert extract_contract({"contract": contract})["run_id"] == "single-run-1"
    assert contract_view({"contract": contract})["kind"] == "agent_run"


def test_destructive_tool_is_blocked_not_executed():
    tool_calls = []
    deps = _deps(
        scripted=[
            '{"action":"edit_file","thoughts":"danger","args":{"path":"/etc/passwd"}}',
            '{"action":"final","message":"stop"}',
        ],
        tool_calls=tool_calls,
    )
    # override policy to mark this destructive
    deps.policy_for = lambda name, args: {
        "risk": "destructive", "destructive": True, "shell": False, "network": False,
        "auto_approve": False, "sandbox": "system", "rollback": "none",
    }
    rt = SingleAgentRuntime(deps)
    ctx = AgentRunContext()
    ctx.state = AgentState.EXECUTING

    asyncio.run(rt.execute(ctx, FakeReq(), "ko", "tester", max_steps=5))

    assert tool_calls == []  # destructive action never reached execute_tool
    assert any("BLOCKED" in (s.get("error") or "") for s in ctx.transcript)


def test_non_auto_plan_requires_explicit_human_approval():
    tool_calls = []
    scripted = [
        '{"action":"plan","goal":"edit a file","steps":[{"action":"edit_file"}],"requires_approval":true}',
    ]
    deps = _deps(scripted, tool_calls)
    deps.tool_governance = {"edit_file": {"auto_approve": False}}
    rt = SingleAgentRuntime(deps)
    ctx = AgentRunContext()

    async def run():
        ctx.state = AgentState.PLANNING
        await rt.plan(ctx, FakeReq(), "ko", "tester")
        rt.approve(ctx, "tester")

    asyncio.run(run())

    assert ctx.state == AgentState.FAILED
    assert tool_calls == []
    assert "명시 승인" in ctx.final_message


def test_human_approval_allows_non_auto_tool_execution():
    tool_calls = []
    scripted = [
        '{"action":"plan","goal":"edit a file","steps":[{"action":"edit_file"}],"requires_approval":true}',
        '{"action":"edit_file","args":{"path":"a.py","old_string":"x","new_string":"y"}}',
        '{"action":"final","message":"done"}',
    ]
    deps = _deps(scripted, tool_calls)
    deps.tool_governance = {"edit_file": {"auto_approve": False}}
    rt = SingleAgentRuntime(deps)
    ctx = AgentRunContext()

    async def run():
        ctx.state = AgentState.PLANNING
        await rt.plan(ctx, FakeReq(), "ko", "tester")
        rt.approve(ctx, "tester", approved_by_human=True)
        await rt.execute(ctx, FakeReq(), "ko", "tester", max_steps=5)

    asyncio.run(run())

    assert ("edit_file", {"path": "a.py", "old_string": "x", "new_string": "y"}) in tool_calls


def test_critic_retry_then_fail_after_budget():
    scripted = [
        '{"action":"final","message":"x"}',          # exec → verifying
        '{"action":"verdict","next_state":"EXECUTING","corrections":["redo"]}',  # retry 1
        '{"action":"final","message":"x"}',
        '{"action":"verdict","next_state":"EXECUTING"}',  # retry 2
        '{"action":"final","message":"x"}',
        '{"action":"verdict","next_state":"EXECUTING"}',  # retry 3
        '{"action":"final","message":"x"}',
        '{"action":"verdict","next_state":"EXECUTING"}',  # retry budget exceeded → FAILED
    ]
    rt = SingleAgentRuntime(_deps(scripted, []))
    ctx = AgentRunContext()
    ctx.state = AgentState.EXECUTING
    asyncio.run(rt.run_to_completion(ctx, FakeReq(), "ko", "tester", max_steps=25, max_retry=3))
    assert ctx.state == AgentState.FAILED


def test_rollback_uses_injected_port():
    rolled_paths = []

    def rollback_file(path):
        rolled_paths.append(path)
        return {"path": path, "ok": True, "stderr": ""}

    deps = _deps(scripted=[], tool_calls=[])
    deps.rollback_file = rollback_file
    rt = SingleAgentRuntime(deps)
    ctx = AgentRunContext()
    ctx.transcript.append({
        "state": AgentState.EXECUTING.value,
        "action": "edit_file",
        "args": {"path": "changed.py"},
        "governance": {"rollback": "git"},
        "result": {"path": "changed.py"},
    })

    rt.rollback(ctx, "tester")

    assert rolled_paths == ["changed.py"]
    assert ctx.state == AgentState.FAILED
    assert ctx.rollback_log == []
    assert ctx.transcript[-1]["rolled_back"] == [{"path": "changed.py", "ok": True, "stderr": ""}]


def test_legacy_agent_runtime_alias_is_preserved():
    assert AgentRuntime is SingleAgentRuntime


def test_single_agent_runtime_boundary_contract_is_explicit():
    runtime = SingleAgentRuntime(_deps(scripted=[], tool_calls=[]))
    assert isinstance(runtime, RuntimeBoundaryProtocol)
    boundary = runtime.boundary()
    assert boundary["schema_version"] == "runtime-boundary/v1"
    assert boundary["name"] == "SingleAgentRuntime"
    assert boundary["runtime"] == "single_agent"
    assert boundary["entrypoint"] == "latticeai.core.agent.SingleAgentRuntime"
    assert boundary["surface"] == "/agent"
    assert "latticeai.core.agent.AgentRuntime" in boundary["compatibility_aliases"]
    assert runtime.config()["boundary"] == boundary


def test_extract_action_tolerates_fences_and_prose():
    assert extract_action('```json\n{"action":"x"}\n```')["action"] == "x"
    assert extract_action('blah {"action":"y"} trailing')["action"] == "y"
    with pytest.raises(ValueError):
        extract_action("no json here")
    with pytest.raises(ValueError):
        extract_action('{"no_action_field": true}')
