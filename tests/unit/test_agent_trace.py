"""Agent loop observability tests (v9.6.0).

Covers the LoopTrace event stream/summary, the repair-tracking action parser
(including the new python-literal tolerance), and that a full fake-model
agent run produces a coherent trace summary.
"""

import asyncio
from pathlib import Path

import pytest

from latticeai.core.agent import (
    AgentDeps,
    AgentRunContext,
    AgentState,
    SingleAgentRuntime,
    extract_action,
    extract_action_details,
)
from latticeai.core.agent_trace import LoopTrace


# ── LoopTrace ───────────────────────────────────────────────────────────

def test_trace_summary_counts_by_kind():
    trace = LoopTrace(clock=lambda: "t0")
    trace.llm_call("plan", model="m1")
    trace.llm_call("execute")
    trace.parse_error("execute", error="bad json", recovered=True)
    trace.parse_error("execute", error="bad json again", recovered=False)
    trace.repair("execute", repairs=["fence", "trailing_comma"])
    trace.repair("execute", repairs=["fence"])
    trace.correction("execute", hint="use JSON")
    trace.tool("execute", name="write_file", outcome="ok", risk="medium")
    trace.tool("execute", name="run_shell", outcome="blocked_approval", risk="high")
    trace.retry("verify", attempt=1)

    summary = trace.summary()
    assert summary["llm_calls"] == 2
    assert summary["parse_errors"] == 2
    assert summary["parse_recovered"] == 1
    assert summary["corrections"] == 1
    assert summary["retries"] == 1
    assert summary["tool_outcomes"] == {"ok": 1, "blocked_approval": 1}
    assert summary["repairs"] == {"fence": 2, "trailing_comma": 1}
    assert summary["truncated_events"] == 0
    assert all(event["at"] == "t0" for event in trace.events)


def test_trace_caps_events_instead_of_growing_unbounded():
    trace = LoopTrace(clock=lambda: "t")
    for _ in range(600):
        trace.llm_call("execute")
    assert len(trace.events) == 500
    assert trace.summary()["truncated_events"] == 100


def test_empty_repairs_record_nothing():
    trace = LoopTrace(clock=lambda: "t")
    trace.repair("execute", repairs=[])
    assert trace.events == []


# ── extract_action_details repairs ──────────────────────────────────────

def test_clean_json_needs_no_repairs():
    action, repairs = extract_action_details('{"action": "final", "message": "done"}')
    assert action["action"] == "final"
    assert repairs == []


def test_fenced_json_records_fence_repair():
    action, repairs = extract_action_details('Here you go:\n```json\n{"action": "final"}\n```')
    assert action["action"] == "final"
    assert "fence" in repairs


def test_think_block_and_slice_repairs():
    raw = '<think>{not json}</think>The answer: {"action": "final", "message": "ok"}'
    action, repairs = extract_action_details(raw)
    assert action["action"] == "final"
    assert "think_strip" in repairs
    assert "slice" in repairs


def test_trailing_comma_repair():
    action, repairs = extract_action_details('{"action": "final", "message": "ok",}')
    assert action["action"] == "final"
    assert "trailing_comma" in repairs


def test_python_literal_repair_handles_single_quotes():
    action, repairs = extract_action_details("{'action': 'write_file', 'args': {'path': 'a.txt'}}")
    assert action["action"] == "write_file"
    assert "python_literal" in repairs


def test_python_literal_repair_handles_python_booleans():
    action, repairs = extract_action_details("{'action': 'final', 'done': True, 'note': None}")
    assert action["done"] is True
    assert "python_literal" in repairs


def test_invalid_payload_still_raises():
    with pytest.raises(ValueError):
        extract_action("no braces at all")
    with pytest.raises(ValueError):
        extract_action('{"no_action_key": 1}')


# ── full-loop trace via fake model ──────────────────────────────────────

class _Req:
    message = "write a note"
    conversation_id = None
    temperature = 0.2
    workspace_id = None
    source = "test"


def _deps(replies):
    replies = list(replies)

    async def generate_as(model_id, message, context, max_tokens, temperature):
        return replies.pop(0)

    async def generate(**kwargs):
        return '{"action": "noop"}'

    policy = {
        "auto_approve": True, "risk": "low", "shell": False, "network": False,
        "destructive": False, "sandbox": False, "rollback": "none",
    }
    return AgentDeps(
        generate_as=generate_as,
        generate=generate,
        execute_tool=lambda name, args: {"ok": True, "path": args.get("path", "")},
        policy_for=lambda name, args: dict(policy),
        risk_level=lambda p: p["risk"],
        check_role=lambda name, user: None,
        tool_governance={"write_file": dict(policy)},
        file_create_actions=frozenset({"write_file"}),
        recent_chat_context=lambda **kw: "",
        clear_history=lambda keep: {"ok": True},
        knowledge_save=lambda *a, **kw: None,
        audit=lambda *a, **kw: None,
        planner_prompt="plan", executor_prompt="exec", critic_prompt="critic",
        memory_updater_prompt="mem", agent_root=Path("/tmp"),
    )


def test_full_run_produces_trace_summary():
    # plan → (parse slip → recovered) → tool call → final → verify PASS
    deps = _deps([
        '{"action": "plan", "goal": "write", "steps": [{"action": "write_file"}]}',
        "utter prose, not json",
        '{"action": "write_file", "args": {"path": "note.txt", "content": "hi"}}',
        '{"action": "final", "message": "done"}',
        '{"action": "verdict", "verdict": "PASS", "next_state": "DONE"}',
    ])
    runtime = SingleAgentRuntime(deps)
    ctx = AgentRunContext()
    ctx.state = AgentState.PLANNING

    async def run():
        await runtime.plan(ctx, _Req(), "en", "user@test")
        runtime.approve(ctx, "user@test")
        await runtime.run_to_completion(ctx, _Req(), "en", "user@test", max_steps=6, max_retry=1)

    asyncio.run(run())
    assert ctx.state == AgentState.DONE
    summary = ctx.trace.summary()
    assert summary["llm_calls"] == 5
    assert summary["parse_errors"] == 1
    assert summary["parse_recovered"] == 1
    assert summary["corrections"] == 1
    assert summary["tool_outcomes"] == {"ok": 1}
    decisions = [e for e in ctx.trace.events if e["kind"] == "decision"]
    assert any(e["decision"] == "auto_approved" for e in decisions)
    assert any(e["decision"] == "final" for e in decisions)
    assert any(e["decision"] == "PASS" for e in decisions)
