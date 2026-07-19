"""Deterministic agent-loop evaluation harness (v9.6.0).

The Brain has a safety harness (governance, approval gates, audit) but until
9.6.0 no *evaluation* harness: nothing measured whether the reasoning loop
actually completes tasks, recovers from weak-model formatting slips, or
respects its guards. This module closes that gap without any model:

* every scenario scripts the exact model replies (including the malformed
  outputs small local models really produce — think blocks, Python dict
  literals, trailing commas, prose) and drives the real
  :class:`~latticeai.core.agent.SingleAgentRuntime` state machine over fake
  ports;
* expectations are asserted against the loop's own :class:`LoopTrace`
  summary, so the harness measures the same observability surface the API
  exposes;
* the result is a scoreboard (`scenarios`, `passed`, `success_rate`,
  aggregate recovery stats) consumed by ``scripts/agent_eval.py`` as a
  release gate.

Deterministic by construction: no model, no network, no filesystem writes
(the tool port records calls in memory).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from latticeai.core.agent import (
    AgentDeps,
    AgentRunContext,
    AgentState,
    SingleAgentRuntime,
)

_AUTO_POLICY = {
    "auto_approve": True, "risk": "low", "shell": False, "network": False,
    "destructive": False, "sandbox": False, "rollback": "none",
}
_DESTRUCTIVE_POLICY = {
    "auto_approve": False, "risk": "destructive", "shell": True, "network": False,
    "destructive": True, "sandbox": False, "rollback": "none",
}


@dataclass
class Scenario:
    """One scripted conversation through the real agent state machine."""

    name: str
    replies: List[str]
    expect_state: str = "DONE"
    # Each key is compared against the LoopTrace summary with >= / == / <=
    expect_min: Dict[str, int] = field(default_factory=dict)
    expect_exact: Dict[str, Any] = field(default_factory=dict)
    expect_tool_outcomes: Dict[str, int] = field(default_factory=dict)
    max_steps: int = 8


class _Req:
    conversation_id = None
    temperature = 0.2
    workspace_id = None
    source = "agent_eval"

    def __init__(self, message: str) -> None:
        self.message = message


def _build_deps(replies: List[str], tool_log: List[Dict[str, Any]]) -> AgentDeps:
    queue = list(replies)

    async def generate_as(model_id, message, context, max_tokens, temperature):
        if not queue:
            # A scenario that exhausts its script means the loop asked for
            # more turns than expected — end it deterministically.
            return '{"action": "verdict", "verdict": "PASS", "next_state": "DONE", "reason": "script exhausted"}'
        return queue.pop(0)

    async def generate(**kwargs):
        return '{"action": "noop"}'

    def execute_tool(name: str, args: dict) -> dict:
        tool_log.append({"name": name, "args": args})
        return {"ok": True, "path": args.get("path", "")}

    def policy_for(name: str, args: dict) -> dict:
        if name == "delete_everything":
            return dict(_DESTRUCTIVE_POLICY)
        return dict(_AUTO_POLICY)

    return AgentDeps(
        generate_as=generate_as,
        generate=generate,
        execute_tool=execute_tool,
        policy_for=policy_for,
        risk_level=lambda p: p["risk"],
        check_role=lambda name, user: None,
        tool_governance={
            "write_file": dict(_AUTO_POLICY),
            "read_file": dict(_AUTO_POLICY),
            "delete_everything": dict(_DESTRUCTIVE_POLICY),
        },
        file_create_actions=frozenset({"write_file"}),
        recent_chat_context=lambda **kw: "",
        clear_history=lambda keep: {"ok": True},
        knowledge_save=lambda *a, **kw: None,
        audit=lambda *a, **kw: None,
        planner_prompt="plan", executor_prompt="exec", critic_prompt="critic",
        memory_updater_prompt="mem", agent_root=Path("/tmp/agent-eval"),
    )


_PLAN = '{"action": "plan", "goal": "task", "steps": [{"action": "write_file"}]}'
_WRITE = '{"action": "write_file", "args": {"path": "note.txt", "content": "hi"}}'
_FINAL = '{"action": "final", "message": "done"}'
_PASS = '{"action": "verdict", "verdict": "PASS", "next_state": "DONE", "reason": "ok"}'


def default_scenarios() -> List[Scenario]:
    return [
        Scenario(
            name="happy-path",
            replies=[_PLAN, _WRITE, _FINAL, _PASS],
            expect_exact={"parse_errors": 0},
            expect_tool_outcomes={"ok": 1},
        ),
        Scenario(
            name="weak-model-format-gauntlet",
            replies=[
                "<think>let me plan</think>```json\n" + _PLAN + "\n```",
                "Sure! Here is the action:\n" + _WRITE,
                "{'action': 'final', 'message': 'done', 'happy': True}",
                '{"action": "verdict", "verdict": "PASS", "next_state": "DONE",}',
            ],
            expect_exact={"parse_errors": 0},
            expect_min={"llm_calls": 4},
            expect_tool_outcomes={"ok": 1},
        ),
        Scenario(
            name="prose-slip-recovers-with-correction",
            replies=[_PLAN, "I will now write the file for you.", _WRITE, _FINAL, _PASS],
            expect_min={"parse_errors": 1, "parse_recovered": 1, "corrections": 1},
            expect_tool_outcomes={"ok": 1},
        ),
        Scenario(
            name="double-slip-escalates-tool-list",
            replies=[
                _PLAN,
                "chatty non-json reply",
                "another chatty reply",
                _WRITE,
                _FINAL,
                _PASS,
            ],
            expect_min={"parse_errors": 2, "corrections": 2},
            expect_tool_outcomes={"ok": 1},
        ),
        Scenario(
            name="destructive-action-blocked",
            replies=[
                _PLAN,
                '{"action": "delete_everything", "args": {}}',
                _FINAL,
                _PASS,
            ],
            expect_tool_outcomes={"blocked_destructive": 1},
        ),
        Scenario(
            name="identical-action-loop-detected",
            replies=[_PLAN, _WRITE, _WRITE, _PASS],
            expect_tool_outcomes={"ok": 1},
            expect_min={"llm_calls": 4},
        ),
        Scenario(
            name="critic-retry-then-done",
            replies=[
                _PLAN,
                _WRITE,
                _FINAL,
                '{"action": "verdict", "verdict": "FAIL", "next_state": "EXECUTING", "corrections": ["also mention the date"]}',
                _FINAL,
                _PASS,
            ],
            expect_min={"retries": 1},
        ),
        Scenario(
            name="unrecoverable-garbage-still-terminates",
            replies=[_PLAN, "garbage", "more garbage", "still garbage", _PASS],
            expect_state="DONE",
            expect_min={"parse_errors": 3},
            expect_exact={"tool_outcomes": {}},
        ),
    ]


async def _run_scenario(scenario: Scenario) -> Dict[str, Any]:
    tool_log: List[Dict[str, Any]] = []
    deps = _build_deps(scenario.replies, tool_log)
    runtime = SingleAgentRuntime(deps)
    ctx = AgentRunContext()
    ctx.state = AgentState.PLANNING
    req = _Req("agent eval task")

    await runtime.plan(ctx, req, "en", "eval@local")
    runtime.approve(ctx, "eval@local")
    if ctx.state == AgentState.EXECUTING:
        await runtime.run_to_completion(
            ctx, req, "en", "eval@local", max_steps=scenario.max_steps, max_retry=2
        )

    summary = ctx.trace.summary()
    failures: List[str] = []
    if ctx.state.value != scenario.expect_state:
        failures.append(f"state={ctx.state.value} expected={scenario.expect_state}")
    for key, minimum in scenario.expect_min.items():
        if int(summary.get(key) or 0) < minimum:
            failures.append(f"{key}={summary.get(key)} < {minimum}")
    for key, exact in scenario.expect_exact.items():
        if summary.get(key) != exact:
            failures.append(f"{key}={summary.get(key)} != {exact}")
    for outcome, count in scenario.expect_tool_outcomes.items():
        if summary["tool_outcomes"].get(outcome, 0) != count:
            failures.append(
                f"tool_outcomes[{outcome}]={summary['tool_outcomes'].get(outcome, 0)} != {count}"
            )
    return {
        "name": scenario.name,
        "ok": not failures,
        "failures": failures,
        "final_state": ctx.state.value,
        "summary": summary,
        "tool_calls": len(tool_log),
    }


def run_agent_eval(scenarios: List[Scenario] | None = None) -> Dict[str, Any]:
    """Run every scenario and reduce to a release-gate scoreboard."""

    async def _run_all() -> List[Dict[str, Any]]:
        return [await _run_scenario(s) for s in (scenarios or default_scenarios())]

    results = asyncio.run(_run_all())
    passed = [r for r in results if r["ok"]]
    total_parse_errors = sum(r["summary"]["parse_errors"] for r in results)
    total_recovered = sum(r["summary"]["parse_recovered"] for r in results)
    return {
        "scenarios": len(results),
        "passed": len(passed),
        "success_rate": round(len(passed) / len(results), 4) if results else 0.0,
        "parse_errors": total_parse_errors,
        "parse_recovered": total_recovered,
        "recovery_rate": round(total_recovered / total_parse_errors, 4) if total_parse_errors else 1.0,
        "results": results,
    }


__all__ = ["Scenario", "default_scenarios", "run_agent_eval"]
