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
from latticeai.tools import ToolError

_AUTO_POLICY = {
    "auto_approve": True, "risk": "low", "shell": False, "network": False,
    "destructive": False, "sandbox": False, "rollback": "none",
}
_DESTRUCTIVE_POLICY = {
    "auto_approve": False, "risk": "destructive", "shell": True, "network": False,
    "destructive": True, "sandbox": False, "rollback": "none",
}
# Mirrors production write-tool governance when the change governor is wired:
# not auto-approved, so only the governor's additive/proposed verdicts (never
# the model) decide whether a write runs or is staged for review.
_GOVERNED_WRITE_POLICY = {
    "auto_approve": False, "risk": "write", "shell": False, "network": False,
    "destructive": False, "sandbox": "workspace", "rollback": "git",
}


class _EvalChangeGovernor:
    """Deterministic stand-in for ChangeProposalService's governor port.

    Mirrors the wire contract of
    :meth:`latticeai.services.change_proposals.ChangeProposalService.review`:
    ``None`` falls through, additive writes get ``allow_additive``, and
    mutations of "existing" paths come back ``proposed`` with a proposal id —
    exactly what the agent loop routes into the review queue.
    """

    governed_tools = frozenset({"write_file", "edit_file"})

    def __init__(self) -> None:
        self.proposals: List[Dict[str, Any]] = []

    def review(self, name, args, *, policy=None, user_email=None, workspace_id=None, conversation_id=None):
        if name not in self.governed_tools:
            return None
        path = str(args.get("path") or "")
        if path.startswith("existing"):
            proposal = {"id": f"eval-proposal-{len(self.proposals) + 1}", "path": path}
            self.proposals.append(proposal)
            return {
                "decision": "proposed",
                "classification": {"change_class": "mutation"},
                "proposal": proposal,
            }
        return {"decision": "allow_additive", "classification": {"change_class": "additive"}}


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
    # Exact ordered sequence of tool names that actually executed (successful
    # execute_tool calls only — proposed/blocked/failed calls never run).
    expect_tool_calls: List[str] = field(default_factory=list)
    # Wire a change governor so governed tools follow the proposal path.
    use_governor: bool = False
    max_steps: int = 8


class _Req:
    conversation_id = None
    temperature = 0.2
    workspace_id = None
    source = "agent_eval"

    def __init__(self, message: str) -> None:
        self.message = message


def _build_deps(
    replies: List[str],
    tool_log: List[Dict[str, Any]],
    *,
    governor: Any = None,
) -> AgentDeps:
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
        # File-producing tools fail like the real dispatcher when the model
        # forgets the target path — scenarios use this to exercise recovery.
        if name in ("write_file", "generate_file") and not args.get("path"):
            raise ToolError(f"{name} requires args.path")
        tool_log.append({"name": name, "args": args})
        return {"ok": True, "path": args.get("path", "")}

    def policy_for(name: str, args: dict) -> dict:
        if name == "delete_everything":
            return dict(_DESTRUCTIVE_POLICY)
        if governor is not None and name in ("write_file", "edit_file"):
            return dict(_GOVERNED_WRITE_POLICY)
        return dict(_AUTO_POLICY)

    write_policy = dict(_GOVERNED_WRITE_POLICY) if governor is not None else dict(_AUTO_POLICY)
    return AgentDeps(
        generate_as=generate_as,
        generate=generate,
        execute_tool=execute_tool,
        policy_for=policy_for,
        risk_level=lambda p: p["risk"],
        check_role=lambda name, user: None,
        tool_governance={
            "write_file": write_policy,
            "read_file": dict(_AUTO_POLICY),
            "generate_file": dict(_AUTO_POLICY),
            "delete_everything": dict(_DESTRUCTIVE_POLICY),
        },
        file_create_actions=frozenset({"write_file", "generate_file"}),
        recent_chat_context=lambda **kw: "",
        clear_history=lambda keep: {"ok": True},
        knowledge_save=lambda *a, **kw: None,
        audit=lambda *a, **kw: None,
        planner_prompt="plan", executor_prompt="exec", critic_prompt="critic",
        memory_updater_prompt="mem", agent_root=Path("/tmp/agent-eval"),
        change_governor=governor,
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
        # ── file generation ─────────────────────────────────────────────
        Scenario(
            name="file-generation-happy-path",
            replies=[
                '{"action": "plan", "goal": "generate landing page", '
                '"steps": [{"action": "generate_file"}]}',
                # Small local models wrap the payload in reasoning + fences;
                # the loop must still extract one clean tool call.
                "<think>layout first, then hero copy</think>\n"
                '```json\n{"thoughts": "produce the full document", '
                '"action": "generate_file", "args": {"path": "site/index.html", '
                '"content": "<!DOCTYPE html><html><body>hello</body></html>"}}\n```',
                _FINAL,
                _PASS,
            ],
            expect_exact={"parse_errors": 0},
            expect_tool_outcomes={"ok": 1},
            expect_tool_calls=["generate_file"],
        ),
        Scenario(
            name="file-generation-bad-args-recovers",
            replies=[
                '{"action": "plan", "goal": "generate report", '
                '"steps": [{"action": "generate_file"}]}',
                # Malformed tool call: the model forgot the target path, the
                # tool port fails like the real dispatcher would…
                '{"thoughts": "write it", "action": "generate_file", '
                '"args": {"content": "<html></html>"}}',
                # …and the next turn recovers with a complete call.
                '{"thoughts": "add the missing path", "action": "generate_file", '
                '"args": {"path": "reports/q3.html", "content": "<html></html>"}}',
                _FINAL,
                _PASS,
            ],
            expect_tool_outcomes={"error": 1, "ok": 1},
            expect_tool_calls=["generate_file"],
        ),
        # ── multi-step workflow ─────────────────────────────────────────
        Scenario(
            name="multi-step-workflow-chain",
            replies=[
                '{"action": "plan", "goal": "read spec, generate report, save summary", '
                '"steps": [{"action": "read_file"}, {"action": "generate_file"}, '
                '{"action": "write_file"}]}',
                '{"thoughts": "step 1: read the source spec", '
                '"action": "read_file", "args": {"path": "spec.md"}}',
                '{"thoughts": "step 2: the spec asks for an HTML report", '
                '"action": "generate_file", "args": {"path": "report.html", '
                '"content": "<html><body>report</body></html>"}}',
                '{"thoughts": "step 3: persist a short summary next to it", '
                '"action": "write_file", "args": {"path": "summary.txt", '
                '"content": "report generated"}}',
                _FINAL,
                _PASS,
            ],
            expect_exact={"parse_errors": 0},
            expect_min={"llm_calls": 6},
            expect_tool_outcomes={"ok": 3},
            expect_tool_calls=["read_file", "generate_file", "write_file"],
        ),
        # ── governed-tool proposal path ─────────────────────────────────
        Scenario(
            name="governed-write-proposal-path",
            use_governor=True,
            replies=[
                # write_file is NOT auto-approved here, but it is governed —
                # approve() must not hard-block the plan (core invariant:
                # governed tools are excluded from the non-auto set).
                '{"action": "plan", "goal": "update existing page, add new note", '
                '"steps": [{"action": "write_file"}]}',
                # Mutation of existing content → staged as proposal, not written.
                '{"thoughts": "rewrite the existing page", "action": "write_file", '
                '"args": {"path": "existing/site.html", "content": "<new>"}}',
                # Additive create → governor allows it to run immediately.
                '{"thoughts": "add a fresh note", "action": "write_file", '
                '"args": {"path": "fresh/new-note.md", "content": "hello"}}',
                _FINAL,
                _PASS,
            ],
            expect_exact={"parse_errors": 0},
            expect_tool_outcomes={"proposed": 1, "ok": 1},
            expect_tool_calls=["write_file"],
        ),
    ]


async def _run_scenario(scenario: Scenario) -> Dict[str, Any]:
    tool_log: List[Dict[str, Any]] = []
    governor = _EvalChangeGovernor() if scenario.use_governor else None
    deps = _build_deps(scenario.replies, tool_log, governor=governor)
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
    executed = [call["name"] for call in tool_log]
    if scenario.expect_tool_calls and executed != scenario.expect_tool_calls:
        failures.append(f"tool_calls={executed} != {scenario.expect_tool_calls}")
    if governor is not None and summary["tool_outcomes"].get("proposed", 0) != len(governor.proposals):
        failures.append(
            f"governor proposals={len(governor.proposals)} != "
            f"traced proposed={summary['tool_outcomes'].get('proposed', 0)}"
        )
    return {
        "name": scenario.name,
        "ok": not failures,
        "failures": failures,
        "final_state": ctx.state.value,
        "summary": summary,
        "tool_calls": len(tool_log),
        "executed_tools": executed,
        "proposals": len(governor.proposals) if governor is not None else 0,
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
