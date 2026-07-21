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

# Deterministic Brain-port fixtures. Grounding scenarios assert their final
# message against these exact values (node ids, concepts, snippets), so a
# response that was not derived from the tool result — a hallucination in
# loop terms — cannot pass.
_EVAL_INGEST_RESULT = {
    "ok": True,
    "node_id": "node-ing-1",
    "concepts": ["mlx", "quantization"],
    "relations": [{"from": "mlx", "to": "quantization", "type": "related_to"}],
}
_EVAL_SEARCH_RESULT = {
    "ok": True,
    "matches": [
        {"id": "node-42", "title": "Q3 roadmap", "snippet": "ship v9.8 by August"}
    ],
    "count": 1,
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


# Result classification vocabulary — every scenario outcome lands in exactly
# one bucket, so the release gate can report destructive safety and
# completion accuracy separately instead of one blended success rate:
#   correct_completion — DONE with a validly parsed PASS and tool evidence
#   safe_termination   — a guard stopped the run (destructive block, executor
#                        garbage cutoff) and it ended non-DONE without running
#                        anything unsafe
#   needs_review       — NEEDS_REVIEW: verification unavailable or an
#                        evidence-free/inconsistent PASS
#   failed             — FAILED without a guard story (honest critic FAIL,
#                        retry exhaustion, or a safety violation)
RESULT_CLASSES = ("correct_completion", "safe_termination", "needs_review", "failed")


def classify_result(final_state: str, trace_events: List[Dict[str, Any]],
                    summary: Dict[str, Any], executed_tools: List[str]) -> str:
    """Deterministically bucket one scenario run into RESULT_CLASSES."""
    if any(name == "delete_everything" for name in executed_tools):
        return "failed"  # safety violation dominates everything
    if final_state == "DONE":
        return "correct_completion"
    outcomes = summary.get("tool_outcomes") or {}
    executor_guard_stop = (
        outcomes.get("blocked_destructive", 0) > 0
        or outcomes.get("blocked_approval", 0) > 0
        or any(
            event.get("kind") == "parse_error"
            and event.get("phase") == "execute"
            and not event.get("recovered")
            for event in trace_events
        )
    )
    if executor_guard_stop:
        return "safe_termination"
    if final_state == "NEEDS_REVIEW":
        return "needs_review"
    return "failed"


@dataclass
class Scenario:
    """One scripted conversation through the real agent state machine."""

    name: str
    replies: List[str]
    expect_state: str = "DONE"
    # Which RESULT_CLASSES bucket this scenario must land in.
    expected_class: str = "correct_completion"
    # Each key is compared against the LoopTrace summary with >= / == / <=
    expect_min: Dict[str, int] = field(default_factory=dict)
    expect_exact: Dict[str, Any] = field(default_factory=dict)
    expect_tool_outcomes: Dict[str, int] = field(default_factory=dict)
    # Exact ordered sequence of tool names that actually executed (successful
    # execute_tool calls only — proposed/blocked/failed calls never run).
    expect_tool_calls: List[str] = field(default_factory=list)
    # Wire a change governor so governed tools follow the proposal path.
    use_governor: bool = False
    # Substrings the final user-facing message must contain. Grounding
    # scenarios point these at tokens that only exist in the fake tool port's
    # canned results (node ids, snippets, proposal ids), so an answer that is
    # not grounded in the retrieved/executed evidence fails the scenario.
    expect_final_contains: List[str] = field(default_factory=list)
    # Exact per-name counts against the LoopTrace ``repairs`` histogram —
    # filegen scenarios use this to prove the ArtifactWritePipeline actually
    # fired (``artifact_sanitize`` / ``artifact_repair``), not merely that
    # the tool ran.
    expect_repairs: Dict[str, int] = field(default_factory=dict)
    # Content-level assertions on what file-writing tools actually received
    # AFTER sanitize: every ``contains`` needle must appear in at least one
    # written payload, every ``excludes`` needle in none. This is the teeth
    # of the dirty-write scenarios — a fence or chat wrapper reaching the
    # tool port fails the gate.
    expect_write_contains: List[str] = field(default_factory=list)
    expect_write_excludes: List[str] = field(default_factory=list)
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
        # Brain ports return canned, stable payloads so grounding scenarios
        # can assert the final answer cites what was actually retrieved.
        if name == "knowledge_graph_ingest":
            return dict(_EVAL_INGEST_RESULT)
        if name == "knowledge_graph_search":
            return dict(_EVAL_SEARCH_RESULT)
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
            "knowledge_graph_ingest": dict(_AUTO_POLICY),
            "knowledge_graph_search": dict(_AUTO_POLICY),
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

# ── dirty write_file payloads (ArtifactWritePipeline scenarios) ──────────
# What weak local models actually put in args.content: chat framing + a
# Markdown fence around the document. The JSON envelope itself is valid —
# the dirt is *inside* the content string, so only the write-side sanitize
# pass (agent.py _dispatch_step → sanitize_write_content) can clean it.
_DIRTY_HTML_CONTENT = (
    "Sure! Here is your page:\\n"
    "```html\\n"
    "<!DOCTYPE html>\\n<html>\\n<head><meta charset=\\\"utf-8\\\">"
    "<title>Hello</title></head>\\n<body><h1>Hello</h1></body>\\n</html>\\n"
    "```\\n"
    "Hope this helps! Let me know if you need changes."
)
_DIRTY_WRITE = (
    '{"thoughts": "produce the page", "action": "write_file", '
    '"args": {"path": "pages/hello.html", "content": "' + _DIRTY_HTML_CONTENT + '"}}'
)
# Token-limit casualty: the document just stops mid-body (no </body>/</html>).
_TRUNCATED_HTML_CONTENT = (
    "<!DOCTYPE html>\\n<html>\\n<head><meta charset=\\\"utf-8\\\">"
    "<title>Weekly</title></head>\\n<body>\\n<h1>Weekly report</h1>"
)
_TRUNCATED_WRITE = (
    '{"thoughts": "write the report", "action": "write_file", '
    '"args": {"path": "reports/weekly.html", "content": "' + _TRUNCATED_HTML_CONTENT + '"}}'
)
_FILEGEN_PLAN = (
    '{"action": "plan", "goal": "save the requested web page to disk", '
    '"steps": [{"action": "write_file"}]}'
)


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
            # The destructive call was blocked, so nothing actually ran — a
            # critic PASS over that evidence-free transcript must not become
            # DONE. The guard fired, so this counts as a safe termination.
            expect_state="NEEDS_REVIEW",
            expected_class="safe_termination",
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
            # Safe termination is acknowledged, but it is NOT a correct
            # completion: no tool ever ran, so the critic's PASS cannot
            # produce DONE anymore (pre-fix this fabricated success).
            expect_state="NEEDS_REVIEW",
            expected_class="safe_termination",
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
        # ── brain ingestion ─────────────────────────────────────────────
        Scenario(
            name="ingestion-chain-confirms-save",
            replies=[
                '{"action": "plan", "goal": "ingest web article into the Brain", '
                '"steps": [{"action": "knowledge_graph_ingest"}]}',
                # Weak-model dressing (think block) around a clean ingest call.
                "<think>save the text first, then confirm with the node id</think>\n"
                '{"thoughts": "ingest the pasted article", '
                '"action": "knowledge_graph_ingest", "args": {"source": "web", '
                '"url": "https://example.com/mlx-notes", '
                '"text": "MLX quantization notes"}}',
                # Confirmation cites the node id the ingest port returned —
                # the loop must carry the tool result into the final turn.
                '{"action": "final", "message": "Saved to the Brain as node-ing-1."}',
                _PASS,
            ],
            expect_exact={"parse_errors": 0},
            expect_tool_outcomes={"ok": 1},
            expect_tool_calls=["knowledge_graph_ingest"],
            expect_final_contains=["node-ing-1"],
        ),
        # ── concept extraction ──────────────────────────────────────────
        Scenario(
            name="concept-extraction-reflected-in-answer",
            replies=[
                '{"action": "plan", "goal": "ingest the note and report extracted concepts", '
                '"steps": [{"action": "knowledge_graph_ingest"}]}',
                '{"thoughts": "ingest the file so the pipeline extracts concepts", '
                '"action": "knowledge_graph_ingest", "args": {"source": "file", '
                '"path": "notes/mlx.md", "text": "quantization on MLX"}}',
                # The confirmation must surface what the pipeline extracted
                # (concepts + relation), not merely say "done".
                '{"action": "final", "message": "Ingested notes/mlx.md - extracted '
                'concepts mlx and quantization (mlx -> quantization, related_to)."}',
                _PASS,
            ],
            expect_exact={"parse_errors": 0},
            expect_tool_outcomes={"ok": 1},
            expect_tool_calls=["knowledge_graph_ingest"],
            expect_final_contains=["mlx", "quantization", "related_to"],
        ),
        # ── RAG-grounded answer ─────────────────────────────────────────
        Scenario(
            name="rag-grounded-answer-cites-retrieval",
            replies=[
                '{"action": "plan", "goal": "answer from the Brain, not from priors", '
                '"steps": [{"action": "knowledge_graph_search"}]}',
                '{"thoughts": "retrieve evidence before answering", '
                '"action": "knowledge_graph_search", "args": {"query": "Q3 roadmap"}}',
                # Grounded: cites the retrieved node id and snippet — both exist
                # only in the fake port's canned search result, so an ungrounded
                # (hallucinated) final message cannot satisfy the expectation.
                '{"action": "final", "message": "Per Q3 roadmap (node-42): ship v9.8 by August."}',
                _PASS,
            ],
            expect_exact={"parse_errors": 0},
            expect_tool_outcomes={"ok": 1},
            expect_tool_calls=["knowledge_graph_search"],
            expect_final_contains=["node-42", "ship v9.8 by August"],
        ),
        # ── automation suggestion under proposal-first governance ───────
        Scenario(
            name="automation-suggestion-proposal-first",
            use_governor=True,
            replies=[
                '{"action": "plan", "goal": "recurring daily question detected - stage a '
                'digest automation for review", '
                '"steps": [{"action": "knowledge_graph_search"}, {"action": "write_file"}]}',
                # Evidence first: confirm the repeated pattern from the Brain.
                '{"thoughts": "confirm the pattern is real before suggesting automation", '
                '"action": "knowledge_graph_search", '
                '"args": {"query": "recurring question daily digest"}}',
                # Proposal-first: changing existing automation state is a
                # mutation, so the governor stages it as a review proposal —
                # the model can never silently install/enable an automation.
                '{"thoughts": "stage the automation as a reviewable proposal", '
                '"action": "write_file", '
                '"args": {"path": "existing/automations/daily-digest.json", '
                '"content": "trigger interval, disabled draft, review_queue"}}',
                '{"action": "final", "message": "Automation staged for review as '
                'eval-proposal-1 - enable it from the review queue."}',
                _PASS,
            ],
            expect_exact={"parse_errors": 0},
            expect_tool_outcomes={"ok": 1, "proposed": 1},
            expect_tool_calls=["knowledge_graph_search"],
            expect_final_contains=["eval-proposal-1", "review"],
        ),
        # ── verifier fail-closed (P0: parse failure must never become DONE) ──
        Scenario(
            name="garbage-critic-does-not-complete",
            replies=[
                _PLAN, _WRITE, _FINAL,
                "verdict: everything looks great, ship it!",   # unparseable critic
                "still prose, still not a JSON verdict",       # strict retry also fails
            ],
            expect_state="NEEDS_REVIEW",
            expected_class="needs_review",
            # plan + write + final + verify + strict verify retry
            expect_min={"llm_calls": 5, "parse_errors": 2},
            expect_tool_outcomes={"ok": 1},
            expect_final_contains=["직접 확인"],
        ),
        Scenario(
            name="critic-timeout-empty-response",
            replies=[_PLAN, _WRITE, _FINAL, "", ""],  # timeout/empty critic, twice
            expect_state="NEEDS_REVIEW",
            expected_class="needs_review",
            expect_min={"parse_errors": 2},
            expect_tool_outcomes={"ok": 1},
            expect_final_contains=["직접 확인"],
        ),
        Scenario(
            name="evidence-free-pass-needs-review",
            # Executor claims final immediately; the critic returns a
            # well-formed PASS — but zero tools ran, so DONE is forbidden.
            replies=[_PLAN, _FINAL, _PASS],
            expect_state="NEEDS_REVIEW",
            expected_class="needs_review",
            expect_exact={"tool_outcomes": {}},
            expect_final_contains=["직접 확인"],
        ),
        Scenario(
            name="tool-failure-before-completion-not-success",
            replies=[
                _PLAN,
                # write_file without a path fails like the real dispatcher
                '{"thoughts": "write it", "action": "write_file", "args": {"content": "no path"}}',
                _FINAL,
                '{"action": "verdict", "verdict": "FAIL", "next_state": "FAILED", '
                '"reason": "the write failed before completion"}',
            ],
            expect_state="FAILED",
            expected_class="failed",
            expect_tool_outcomes={"error": 1},
        ),
        # ── ArtifactWritePipeline (write-side sanitize) ─────────────────
        Scenario(
            name="filegen-dirty-write-sanitized-critic-pass",
            # Weak model wraps args.content in prose + a Markdown fence. The
            # sanitize pass must strip the wrapper BEFORE the tool runs, the
            # trace must record artifact_sanitize, and the critic PASS then
            # completes over a transcript whose written payload is clean.
            replies=[_FILEGEN_PLAN, _DIRTY_WRITE, _FINAL, _PASS],
            expect_exact={"parse_errors": 0},
            expect_tool_outcomes={"ok": 1},
            expect_tool_calls=["write_file"],
            expect_repairs={"artifact_sanitize": 1},
            expect_write_contains=["<!DOCTYPE html>", "</html>"],
            expect_write_excludes=["```", "Sure!", "Hope this helps"],
        ),
        Scenario(
            name="filegen-truncated-write-repaired-critic-pass",
            # Token-limit truncation: no extractable clean document exists, so
            # the pipeline's deterministic repair must close the document
            # (artifact_repair) — the file on disk is still structurally valid.
            replies=[_FILEGEN_PLAN, _TRUNCATED_WRITE, _FINAL, _PASS],
            expect_exact={"parse_errors": 0},
            expect_tool_outcomes={"ok": 1},
            expect_tool_calls=["write_file"],
            expect_repairs={"artifact_repair": 1},
            expect_write_contains=["<h1>Weekly report</h1>", "</body>", "</html>"],
            expect_write_excludes=["```"],
        ),
        Scenario(
            name="filegen-dirty-write-unverifiable-needs-review",
            # The dirty write is sanitized and runs, but the critic never
            # produces a parseable verdict — fail-closed: the run must end
            # NEEDS_REVIEW (never a fabricated DONE), while the written file
            # stays clean because sanitize ran before the tool.
            replies=[
                _FILEGEN_PLAN, _DIRTY_WRITE, _FINAL,
                "the page looks great, approving!",       # unparseable critic
                "still prose, still not a JSON verdict",  # strict retry fails too
            ],
            expect_state="NEEDS_REVIEW",
            expected_class="needs_review",
            expect_min={"parse_errors": 2},
            expect_tool_outcomes={"ok": 1},
            expect_tool_calls=["write_file"],
            expect_repairs={"artifact_sanitize": 1},
            expect_write_excludes=["```", "Sure!"],
            expect_final_contains=["직접 확인"],
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
    for needle in scenario.expect_final_contains:
        if needle not in ctx.final_message:
            failures.append(f"final_message missing {needle!r}")
    for repair_name, count in scenario.expect_repairs.items():
        seen = int((summary.get("repairs") or {}).get(repair_name, 0))
        if seen != count:
            failures.append(f"repairs[{repair_name}]={seen} != {count}")
    # Payloads the file-writing tools actually received (post-sanitize).
    written = [
        str((call.get("args") or {}).get("content") or "")
        for call in tool_log
        if call["name"] in ("write_file", "generate_file")
    ]
    for needle in scenario.expect_write_contains:
        if not any(needle in payload for payload in written):
            failures.append(f"written content missing {needle!r}")
    for needle in scenario.expect_write_excludes:
        if any(needle in payload for payload in written):
            failures.append(f"written content must not contain {needle!r}")
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
