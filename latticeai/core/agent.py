"""Single-agent runtime — the Discover→Plan→Implement→Verify state machine.

This module is the deep single-agent loop: a small interface (``AgentDeps`` ports +
``SingleAgentRuntime.run_to_completion``) over the whole role-phased state machine
(planner → executor → critic → rollback → memory). It carries no FastAPI,
no globals, and no I/O of its own — every collaborator is injected through
``AgentDeps``.

Two adapters justify the seam:

* production wires ``AgentDeps`` from ``latticeai.server_app``'s ``LLMRouter``, governance
  map, audit log, and prompts;
* tests pass fake ports (an LLM that returns canned JSON, a recording tool
  executor) and drive a full PLAN→EXECUTE→VERIFY→DONE cycle without a server.

HTTP concerns — request parsing, chat-history persistence, response shaping,
scheduling the background memory update — stay in the app layer. This module
only owns the state machine.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, FrozenSet, List, Optional

from lattice_brain.runtime.hooks import dispatch_tool
from lattice_brain.runtime.contracts import runtime_boundary_contract, single_agent_contract
from latticeai.core.tool_registry import SCOPED_KNOWLEDGE_TOOLS
from latticeai.tools import ToolError


class AgentState(str, Enum):
    IDLE             = "IDLE"
    PLANNING         = "PLANNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    EXECUTING        = "EXECUTING"
    VERIFYING        = "VERIFYING"
    FAILED           = "FAILED"
    ROLLBACK         = "ROLLBACK"
    DONE             = "DONE"


# Terminal states — the agent loop exits when reaching one of these
AGENT_TERMINAL_STATES: FrozenSet[AgentState] = frozenset({AgentState.DONE, AgentState.FAILED})


class AgentRunContext:
    """Mutable state carrier passed through all agent phases."""
    __slots__ = ("state", "plan", "transcript", "retry_count",
                 "state_history", "corrections", "final_message", "rollback_log",
                 "executing_model", "reviewing_model", "approved_by_human")

    def __init__(self) -> None:
        self.state:           AgentState   = AgentState.IDLE
        self.plan:            dict         = {}
        self.transcript:      list         = []
        self.retry_count:     int          = 0
        self.state_history:   list         = []
        self.corrections:     list         = []
        self.final_message:   str          = ""
        self.rollback_log:    list         = []
        self.executing_model: Optional[str] = None
        self.reviewing_model: Optional[str] = None
        self.approved_by_human: bool       = False


_THINK_BLOCK_RE = re.compile(
    r"<(think|thinking|reasoning)>.*?</\1>", flags=re.DOTALL | re.IGNORECASE
)


def extract_action(raw: str) -> Dict:
    """Parse one JSON action object out of an LLM response (tolerant of fences/prose)."""
    # Small local models often prepend <think>...</think> reasoning that can
    # itself contain braces — drop it before locating the action object.
    text = _THINK_BLOCK_RE.sub("", raw).strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    elif not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]

    try:
        action = json.loads(text)
    except json.JSONDecodeError:
        # Second chance for the most common small-model JSON slips: trailing
        # commas before a closing brace/bracket.
        repaired = re.sub(r",\s*([}\]])", r"\1", text)
        try:
            action = json.loads(repaired)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Agent did not return valid JSON: {exc}") from exc

    if not isinstance(action, dict) or "action" not in action:
        raise ValueError("Agent JSON must include an action field.")
    return action


@dataclass
class AgentDeps:
    """The ports a :class:`SingleAgentRuntime` needs from the outside world.

    Everything the state machine touches is here, so the loop can be exercised
    against fakes. See module docstring for the two-adapter rationale.
    """

    # ── LLM port ─────────────────────────────────────────────────────
    # generate_as(model_id, message, context, max_tokens, temperature) -> str
    generate_as: Callable[..., Awaitable[Any]]
    # generate(message, context, max_tokens, temperature) -> str
    generate: Callable[..., Awaitable[Any]]

    # ── tool port ────────────────────────────────────────────────────
    execute_tool: Callable[[str, dict], dict]
    policy_for: Callable[[str, dict], dict]        # name, args -> governance policy
    risk_level: Callable[[dict], str]              # policy -> "low"|"medium"|"high"
    check_role: Callable[[str, str], None]         # tool_name, user -> raises if not allowed
    tool_governance: Dict[str, dict]               # name -> policy (for auto_approve set)
    file_create_actions: FrozenSet[str]

    # ── context / memory / audit ports ───────────────────────────────
    recent_chat_context: Callable[..., str]        # (conversation_id=...) -> str
    clear_history: Callable[[int], dict]
    knowledge_save: Callable[..., Any]
    audit: Callable[..., None]                     # (event, **kw) -> None

    # ── prompts + config ─────────────────────────────────────────────
    planner_prompt: str
    executor_prompt: str
    critic_prompt: str
    memory_updater_prompt: str
    agent_root: Path

    # ── rollback port (optional) ─────────────────────────────────────
    # Production injects this from the tool dispatch service so this pure
    # state machine does not shell out directly. Tests can pass a recorder.
    rollback_file: Optional[Callable[[str], Dict[str, Any]]] = None

    # ── lifecycle hooks port (optional) ──────────────────────────────
    # When present, every tool execution fires the shared pre_tool/post_tool
    # lifecycle, so the agent tool path no longer bypasses hooks.
    hooks: Any = None

    # ── brain memory port (optional) ─────────────────────────────────
    # When present, completed-run learnings become typed Experience records
    # through the unified ingestion pipeline (with provenance), replacing
    # the vault markdown dump.
    brain_memory: Any = None


class SingleAgentRuntime:
    """Drives the agent state machine over injected :class:`AgentDeps`."""

    def __init__(self, deps: AgentDeps) -> None:
        self.deps = deps

    def boundary(self) -> Dict[str, Any]:
        return runtime_boundary_contract(
            name="SingleAgentRuntime",
            runtime="single_agent",
            entrypoint="latticeai.core.agent.SingleAgentRuntime",
            surface="/agent",
            owns="single-agent PLAN / EXECUTE / VERIFY state machine over injected ports",
            compatibility_aliases=[],
        )

    def config(self) -> Dict[str, Any]:
        return {
            "boundary": self.boundary(),
            "states": [state.value for state in AgentState],
            "terminal_states": sorted(state.value for state in AGENT_TERMINAL_STATES),
            "execution_mode": "injected_ports",
        }

    def contract(self, ctx: AgentRunContext, req: Any, *, run_id: Optional[str] = None) -> Dict[str, Any]:
        """Expose the shared agent-run contract for the single-agent loop."""
        return single_agent_contract(ctx=ctx, goal=getattr(req, "message", ""), run_id=run_id)

    # ── PLAN ─────────────────────────────────────────────────────────
    async def plan(
        self, ctx: AgentRunContext, req: Any, lang_hint: str, current_user: str,
        model_id: Optional[str] = None,
    ) -> None:
        """PLAN: Planner role produces a structured plan JSON."""
        d = self.deps
        context = (
            f"{d.planner_prompt}\n\n"
            f"[LANGUAGE HINT: {lang_hint}]\n"
            f"Workspace root: {d.agent_root}\n\n"
            f"User request: {req.message}"
        )
        raw = await d.generate_as(
            model_id,
            message="Produce a JSON execution plan for this request.",
            context=context, max_tokens=1024, temperature=0.1,
        )
        try:
            plan = extract_action(str(raw))
        except ValueError:
            plan = {
                "action": "plan", "state": "PLAN",
                "goal": req.message, "steps": [],
                "requires_approval": False, "rollback_strategy": "none", "estimated_steps": 1,
            }
        ctx.plan = plan
        ctx.transcript.append({
            "state": AgentState.PLANNING.value,
            "goal": plan.get("goal", req.message),
            "steps": plan.get("steps", []),
            "requires_approval": plan.get("requires_approval", False),
            "rollback_strategy": plan.get("rollback_strategy", "none"),
            "estimated_steps": plan.get("estimated_steps", 1),
        })
        ctx.state = AgentState.WAITING_APPROVAL

    # ── APPROVAL ─────────────────────────────────────────────────────
    def approve(self, ctx: AgentRunContext, current_user: str, *, approved_by_human: bool = False) -> None:
        """APPROVAL: Check governance, log decision, auto-approve (future: UI prompt)."""
        d = self.deps
        auto_approve_tools = {name for name, p in d.tool_governance.items() if p["auto_approve"]}
        steps = ctx.plan.get("steps", [])
        non_auto = [s.get("action") for s in steps if s.get("action") not in auto_approve_tools]
        requires = ctx.plan.get("requires_approval", False) or bool(non_auto)

        ctx.transcript.append({
            "state": AgentState.WAITING_APPROVAL.value,
            "requires_approval": requires,
            "non_auto_approve_steps": non_auto,
            "decision": "human_approved" if requires and approved_by_human else ("blocked_pending_approval" if requires else "auto_approved"),
        })
        d.audit(
            "agent_approval", user_email=current_user,
            requires_approval=requires,
            non_auto_steps=non_auto,
            decision="human_approved" if requires and approved_by_human else ("blocked_pending_approval" if requires else "auto_approved"),
        )
        if requires and not approved_by_human:
            ctx.final_message = (
                "이 작업에는 명시 승인이 필요한 도구가 포함되어 있어 자동 실행을 중단했습니다. "
                "human_in_loop 승인 흐름으로 다시 실행해 주세요."
            )
            ctx.state = AgentState.FAILED
            return
        ctx.approved_by_human = bool(approved_by_human)
        ctx.state = AgentState.EXECUTING

    # ── EXECUTE ──────────────────────────────────────────────────────
    async def execute(
        self, ctx: AgentRunContext, req: Any, lang_hint: str,
        current_user: str, max_steps: int, model_id: Optional[str] = None,
    ) -> None:
        """EXECUTE: Executor role calls tools one at a time until final or budget exhausted."""
        d = self.deps
        exec_count = sum(1 for s in ctx.transcript if s.get("state") == AgentState.EXECUTING.value)
        budget = max(1, max_steps - exec_count)
        parse_failures = 0

        for _ in range(budget):
            corrections_hint = (
                "\n\nCritic corrections from previous attempt:\n"
                + "\n".join(f"- {c}" for c in ctx.corrections)
            ) if ctx.corrections else ""

            request_workspace = getattr(req, "workspace_id", None)
            recent_kwargs = {
                "conversation_id": req.conversation_id,
                "user_email": current_user or None,
            }
            if request_workspace is not None:
                recent_kwargs["workspace_id"] = request_workspace
            recent_conversation = d.recent_chat_context(**recent_kwargs) or "(none)"
            context = (
                f"{d.executor_prompt}\n\n"
                f"[LANGUAGE HINT: {lang_hint}]\n"
                f"Workspace root: {d.agent_root}\n\n"
                f"PLAN:\n{json.dumps(ctx.plan, ensure_ascii=False)}\n\n"
                f"Recent conversation:\n{recent_conversation}\n\n"
                f"User request: {req.message}{corrections_hint}\n\n"
                f"Execution transcript:\n{json.dumps(ctx.transcript, ensure_ascii=False, indent=2)}"
            )
            raw = await d.generate_as(
                model_id,
                message="Execute the next step.",
                context=context, max_tokens=4096, temperature=req.temperature,
            )
            try:
                action = extract_action(str(raw))
            except ValueError as exc:
                parse_failures += 1
                ctx.transcript.append({
                    "state": AgentState.EXECUTING.value, "action": "parse_error",
                    "raw": str(raw)[:400], "error": str(exc),
                })
                if parse_failures >= 3:
                    break
                # Weak models often need one concrete reminder of the wire
                # format; feed it through the corrections channel and retry
                # instead of aborting the whole run on the first slip.
                hint = (
                    'Your last reply was not a single JSON action object. Reply with '
                    'EXACTLY one JSON object like {"thoughts": "...", "action": '
                    '"tool_name", "args": {...}} and nothing else.'
                )
                if hint not in ctx.corrections:
                    ctx.corrections.append(hint)
                continue

            name     = action.get("action")
            thoughts = str(action.get("thoughts") or "")[:600]
            args     = action.get("args") or {}

            if name in SCOPED_KNOWLEDGE_TOOLS:
                # Scope is server-owned, never model-owned. Overwrite any
                # claimed values before policy evaluation, audit, and dispatch.
                args = dict(args)
                args["workspace_id"] = request_workspace or "personal"
                args["user_email"] = current_user or "local"

            if name == "final":
                ctx.final_message = action.get("message", "작업을 완료했습니다.")
                ctx.transcript.append({
                    "state": AgentState.EXECUTING.value, "action": "final", "thoughts": thoughts,
                })
                ctx.state = AgentState.VERIFYING
                return

            # Loop guard
            exec_steps = [s for s in ctx.transcript if s.get("state") == AgentState.EXECUTING.value]
            last = exec_steps[-1] if exec_steps else None
            if (
                name in d.file_create_actions and last
                and last.get("action") == name
                and (last.get("args") or {}) == args
                and "result" in last
            ):
                ctx.transcript.append({
                    "state": AgentState.EXECUTING.value, "action": name,
                    "error": "LOOP_DETECTED: identical action+args repeated — halted.",
                })
                break

            if name == "clear_history":
                result = d.clear_history(args.get("keep_last", 0))
                ctx.transcript.append({
                    "state": AgentState.EXECUTING.value, "action": name,
                    "thoughts": thoughts, "args": args, "result": result,
                })
                continue

            policy = d.policy_for(name, args)
            risk   = d.risk_level(policy)

            if policy["risk"] == "destructive":
                ctx.transcript.append({
                    "state": AgentState.EXECUTING.value, "action": name,
                    "thoughts": thoughts, "args": args, "risk": risk,
                    "governance": dict(policy),
                    "error": f"BLOCKED: destructive action '{name}' not permitted in agent mode.",
                })
                d.audit(
                    "agent_blocked", user_email=current_user, source=getattr(req, "source", None) or "agent",
                    action=name, reason="destructive", governance=dict(policy),
                )
                continue

            if not policy["auto_approve"] and not ctx.approved_by_human:
                d.audit(
                    "agent_exec", user_email=current_user, source=getattr(req, "source", None) or "agent",
                    state=AgentState.EXECUTING.value, action=name, risk=risk,
                    shell=policy["shell"], network=policy["network"],
                    destructive=policy["destructive"], sandbox=policy["sandbox"],
                    rollback=policy["rollback"],
                    args={k: v for k, v in args.items() if k != "content"},
                )
                ctx.transcript.append({
                    "state": AgentState.EXECUTING.value, "action": name,
                    "thoughts": thoughts, "args": args, "risk": risk,
                    "governance": dict(policy),
                    "error": f"BLOCKED: action '{name}' requires explicit approval.",
                })
                continue

            try:
                d.check_role(name, current_user)
                # Shared tool lifecycle: pre_tool (may block) → execute → post_tool.
                result = dispatch_tool(
                    d.hooks, name, args,
                    lambda: d.execute_tool(name, args),
                    user_email=current_user, source="agent",
                )
                ctx.transcript.append({
                    "state": AgentState.EXECUTING.value, "action": name,
                    "thoughts": thoughts, "args": args,
                    "risk": risk, "governance": dict(policy), "result": result,
                })
            except (ToolError, KeyError, TypeError, PermissionError) as exc:
                ctx.transcript.append({
                    "state": AgentState.EXECUTING.value, "action": name,
                    "thoughts": thoughts, "args": args,
                    "risk": risk, "governance": dict(policy), "error": str(exc),
                })

        ctx.state = AgentState.VERIFYING

    # ── VERIFY ───────────────────────────────────────────────────────
    async def verify(
        self, ctx: AgentRunContext, req: Any, lang_hint: str, current_user: str,
        max_retry: int = 3, model_id: Optional[str] = None,
    ) -> None:
        """VERIFYING: Critic role evaluates transcript → DONE / EXECUTING (retry) / ROLLBACK / FAILED."""
        d = self.deps
        context = (
            f"{d.critic_prompt}\n\n"
            f"[LANGUAGE HINT: {lang_hint}]\n\n"
            f"Original request: {req.message}\n"
            f"Plan goal: {ctx.plan.get('goal', req.message)}\n\n"
            f"Full transcript:\n{json.dumps(ctx.transcript, ensure_ascii=False, indent=2)}"
        )
        raw = await d.generate_as(
            model_id,
            message="Review the execution transcript and return your verdict JSON.",
            context=context, max_tokens=512, temperature=0.1,
        )
        try:
            verdict = extract_action(str(raw))
        except ValueError:
            verdict = {"action": "verdict", "verdict": "PASS", "next_state": "DONE",
                       "reason": "Critic parse failed — assuming pass.", "corrections": [], "confidence": 0.7}

        ctx.corrections = verdict.get("corrections", [])
        # Normalize legacy verdict next_state strings to current AgentState names
        raw_next = verdict.get("next_state", "DONE")
        next_s = {"COMPLETE": "DONE", "RETRY": "EXECUTING"}.get(raw_next, raw_next)

        ctx.transcript.append({
            "state": AgentState.VERIFYING.value,
            "verdict":     verdict.get("verdict", "PASS"),
            "reason":      verdict.get("reason", ""),
            "corrections": ctx.corrections,
            "confidence":  verdict.get("confidence", 0.9),
            "next_state":  next_s,
        })

        if verdict.get("verdict") == "PASS" or next_s == "DONE":
            if not ctx.final_message:
                ctx.final_message = verdict.get("reason", "작업이 완료되었습니다.")
            ctx.state = AgentState.DONE
        elif next_s == "ROLLBACK":
            ctx.state = AgentState.ROLLBACK
        elif next_s == "EXECUTING":
            if ctx.retry_count >= max_retry:
                ctx.final_message = "처리 중 문제가 발생했습니다. 다시 시도해 주세요."
                ctx.state = AgentState.FAILED
            else:
                ctx.retry_count += 1
                ctx.transcript.append({
                    "state": AgentState.EXECUTING.value,
                    "retry_attempt": ctx.retry_count,
                    "corrections": ctx.corrections,
                })
                ctx.state = AgentState.EXECUTING
        else:
            ctx.final_message = verdict.get("reason", "검증자가 인식되지 않은 다음 상태를 반환했습니다.")
            ctx.state = AgentState.FAILED

    # ── ROLLBACK ─────────────────────────────────────────────────────
    def rollback(self, ctx: AgentRunContext, current_user: str) -> None:
        """ROLLBACK: attempt git checkout for each edited file, then FAILED."""
        d = self.deps
        rolled: List[dict] = []
        for step in ctx.transcript:
            if step.get("state") != AgentState.EXECUTING.value:
                continue
            gov = step.get("governance", {})
            if gov.get("rollback") != "git":
                continue
            result = step.get("result", {})
            if not isinstance(result, dict):
                result = {}
            path = result.get("path") or (step.get("args") or {}).get("path", "")
            if not path:
                continue
            if d.rollback_file is None:
                rolled.append({"path": path, "ok": False, "error": "rollback_file port is not configured"})
                continue
            try:
                rolled.append(d.rollback_file(str(path)))
            except Exception as exc:
                rolled.append({"path": path, "ok": False, "error": str(exc)})

        ctx.transcript.append({"state": AgentState.ROLLBACK.value, "rolled_back": rolled})
        recovered = [r["path"] for r in rolled if r.get("ok")]
        ctx.final_message = (
            f"실행 실패로 롤백했습니다. 복구 파일: {recovered}"
            if recovered
            else "롤백을 시도했으나 복구할 파일이 없거나 git이 초기화되지 않았습니다."
        )
        d.audit("agent_rollback", user_email=current_user, rolled_back=rolled)
        # Rollback is a recovery from a failed verification — terminal state is FAILED
        ctx.state = AgentState.FAILED

    # ── MEMORY ───────────────────────────────────────────────────────
    async def memory_update(self, ctx: AgentRunContext, req: Any, current_user: str) -> None:
        """Background: Memory Updater role extracts learnings after DONE."""
        d = self.deps
        context = (
            f"{d.memory_updater_prompt}\n\n"
            f"Completed task: {req.message}\n\n"
            f"Last 5 transcript steps:\n{json.dumps(ctx.transcript[-5:], ensure_ascii=False)}"
        )
        try:
            raw = await d.generate(
                message="Extract learnings from this completed task.",
                context=context, max_tokens=256, temperature=0.1,
            )
            mem = extract_action(str(raw))
            if mem.get("save_to_knowledge") and mem.get("learnings"):
                learnings = "\n".join(mem["learnings"])
                if d.brain_memory is not None:
                    # This runtime is LLM-driven — its learnings are real
                    # experiences and enter the brain with provenance.
                    d.brain_memory.record_experience(
                        f"Agent: {req.message[:60]}",
                        learnings,
                        run={
                            "mode": "llm",
                            "status": "ok",
                            "agent_id": "agent:executor",
                            "steps": len(ctx.transcript),
                        },
                        user_email=current_user or None,
                    )
                else:
                    d.knowledge_save(
                        learnings,
                        folder="30_Projects",
                        title=f"Agent: {req.message[:60]}",
                    )
        except Exception as exc:
            # Never crash a completed run, but never swallow silently either.
            logging.warning("agent memory update failed: %s", exc)

    # ── DRIVE LOOP ───────────────────────────────────────────────────
    async def run_to_completion(
        self, ctx: AgentRunContext, req: Any, lang_hint: str,
        current_user: str, max_steps: int, max_retry: int,
    ) -> None:
        """Run EXECUTING → VERIFYING → ROLLBACK loop until a terminal state."""
        while ctx.state not in AGENT_TERMINAL_STATES:
            ctx.state_history.append(ctx.state.value)
            if len(ctx.state_history) > 200:
                ctx.final_message = "에이전트 상태 머신이 최대 반복(200)에 도달해 중단했습니다."
                ctx.state = AgentState.FAILED
                break

            if ctx.state == AgentState.EXECUTING:
                await self.execute(ctx, req, lang_hint, current_user, max_steps,
                                   model_id=ctx.executing_model)
            elif ctx.state == AgentState.VERIFYING:
                await self.verify(ctx, req, lang_hint, current_user, max_retry,
                                  model_id=ctx.reviewing_model)
            elif ctx.state == AgentState.ROLLBACK:
                self.rollback(ctx, current_user)
            else:
                ctx.state = AgentState.FAILED

        ctx.state_history.append(ctx.state.value)
