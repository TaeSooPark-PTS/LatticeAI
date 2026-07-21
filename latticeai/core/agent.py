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

import ast
import json
import logging
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, FrozenSet, List, Optional, Tuple

from lattice_brain.runtime.hooks import dispatch_tool
from lattice_brain.runtime.contracts import runtime_boundary_contract, single_agent_contract
from latticeai.core.agent_trace import LoopTrace
from latticeai.core.file_generation import infer_file_target, sanitize_write_content
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
    # Terminal, non-success: the run ended but completion could not be
    # verified (critic unavailable/unparseable, or a PASS with no execution
    # evidence). Never presented as success — the user must check the result.
    NEEDS_REVIEW     = "NEEDS_REVIEW"
    DONE             = "DONE"


# Terminal states — the agent loop exits when reaching one of these
AGENT_TERMINAL_STATES: FrozenSet[AgentState] = frozenset(
    {AgentState.DONE, AgentState.FAILED, AgentState.NEEDS_REVIEW}
)


class AgentRunContext:
    """Mutable state carrier passed through all agent phases."""
    __slots__ = ("state", "plan", "transcript", "retry_count",
                 "state_history", "corrections", "final_message", "rollback_log",
                 "executing_model", "reviewing_model", "approved_by_human", "trace")

    def __init__(self) -> None:
        self.state:           AgentState   = AgentState.IDLE
        self.trace:           LoopTrace    = LoopTrace()
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


def extract_action_details(raw: str) -> Tuple[Dict, List[str]]:
    """Parse one JSON action object out of an LLM response (tolerant of fences/prose).

    Returns ``(action, repairs)`` where ``repairs`` names every tolerance that
    was needed — the loop trace and the weak-model robustness harness consume
    it to measure how much help a given model needs.
    """
    repairs: List[str] = []
    # Small local models often prepend <think>...</think> reasoning that can
    # itself contain braces — drop it before locating the action object.
    text = _THINK_BLOCK_RE.sub("", raw).strip()
    if text != str(raw).strip():
        repairs.append("think_strip")
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
        repairs.append("fence")
    elif not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
            repairs.append("slice")

    action: Any = None
    try:
        action = json.loads(text)
    except json.JSONDecodeError:
        # Second chance for the most common small-model JSON slips: trailing
        # commas before a closing brace/bracket.
        repaired = re.sub(r",\s*([}\]])", r"\1", text)
        try:
            action = json.loads(repaired)
            repairs.append("trailing_comma")
        except json.JSONDecodeError as exc:
            # Last chance: weak models sometimes emit a Python dict literal
            # (single quotes, True/False/None). ast.literal_eval parses that
            # deterministically without evaluating code.
            try:
                literal = ast.literal_eval(text)
            except (ValueError, SyntaxError):
                raise ValueError(f"Agent did not return valid JSON: {exc}") from exc
            if not isinstance(literal, dict):
                raise ValueError(f"Agent did not return valid JSON: {exc}") from exc
            action = literal
            repairs.append("python_literal")

    if not isinstance(action, dict) or "action" not in action:
        raise ValueError("Agent JSON must include an action field.")
    return action, repairs


def extract_action(raw: str) -> Dict:
    """Back-compat wrapper over :func:`extract_action_details`."""
    action, _ = extract_action_details(raw)
    return action


def normalize_plan(plan: Any, user_message: str) -> Tuple[Dict[str, Any], List[str]]:
    """Enforce the minimal plan schema so execution never starts adrift.

    A weak planner that returns junk steps / a missing goal previously flowed
    straight into the executor, which then had to reconstruct intent from the
    raw request. Normalization keeps the loop honest: ``goal`` is always a
    non-empty string, ``steps`` only contains dicts with an ``action``, and an
    empty plan for an obvious file-creation request gets a deterministic
    single ``write_file`` step instead of leaving the executor to improvise.

    Returns ``(plan, fixes)`` where ``fixes`` names every applied repair —
    the loop trace records them so plan quality is observable per model.
    """
    fixes: List[str] = []
    if not isinstance(plan, dict):
        plan = {}
        fixes.append("plan_not_object")
    plan = dict(plan)

    goal = str(plan.get("goal") or "").strip()
    if not goal:
        plan["goal"] = user_message
        fixes.append("goal_defaulted")

    raw_steps = plan.get("steps")
    steps = [
        s for s in (raw_steps if isinstance(raw_steps, list) else [])
        if isinstance(s, dict) and s.get("action")
    ]
    if raw_steps and steps != raw_steps:
        fixes.append("steps_filtered")
    if not steps:
        inferred = infer_file_target(user_message)
        if inferred:
            steps = [{
                "action": "write_file",
                "args": {"path": inferred},
                "description": f"Create {inferred} for: {user_message[:120]}",
            }]
            fixes.append("heuristic_file_step")
    plan["steps"] = steps

    try:
        estimated = int(plan.get("estimated_steps") or 0)
    except (TypeError, ValueError):
        estimated = 0
        fixes.append("estimated_steps_invalid")
    plan["estimated_steps"] = max(1, estimated, len(steps))
    plan["requires_approval"] = bool(plan.get("requires_approval", False))
    if not isinstance(plan.get("rollback_strategy"), str):
        plan["rollback_strategy"] = "none"
    return plan, fixes


_TRIVIAL_LEARNING_RE = re.compile(
    r"^(파일(을|이)?\s*(만들|생성|작성|저장)|작업(을|이)?\s*(완료|성공)|성공적으로"
    r"|task\s+(was\s+)?complet|file\s+(was\s+)?(creat|written|saved)"
    r"|(successfully\s+)?(created|completed|finished|done)\b)",
    re.IGNORECASE,
)


def filter_learnings(learnings: List[Any]) -> List[str]:
    """Drop trivial/duplicate learnings before they enter the brain.

    "파일을 만들었다"-class statements restate what the transcript already
    records and pollute recall. A learning survives when it is long enough to
    carry information and is not a bare completion announcement.
    """
    kept: List[str] = []
    seen: set = set()
    for raw in learnings or []:
        text = str(raw or "").strip()
        if len(text) < 12:
            continue
        if _TRIVIAL_LEARNING_RE.match(text) and len(text) < 48:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        kept.append(text)
    return kept


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

    # ── change governor port (optional) ──────────────────────────────
    # When present, file writes are classified centrally: additive creates
    # run with minimal friction, while mutations/deletions of existing
    # content are staged as review proposals instead of applied. The port is
    # ``review(name, args, policy=..., user_email=..., workspace_id=...)``
    # returning None (fall through to the classic gates) or a verdict dict.
    change_governor: Any = None


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
        ctx.trace.llm_call("plan", model=model_id)
        try:
            plan, plan_repairs = extract_action_details(str(raw))
            ctx.trace.repair("plan", repairs=plan_repairs)
        except ValueError as exc:
            ctx.trace.parse_error("plan", error=str(exc), recovered=True)
            plan = {
                "action": "plan", "state": "PLAN",
                "goal": req.message, "steps": [],
                "requires_approval": False, "rollback_strategy": "none", "estimated_steps": 1,
            }
        plan, plan_fixes = normalize_plan(plan, req.message)
        if plan_fixes:
            ctx.trace.repair("plan", repairs=plan_fixes)
        ctx.plan = plan
        ctx.transcript.append({
            "state": AgentState.PLANNING.value,
            "goal": plan.get("goal", req.message),
            "steps": plan.get("steps", []),
            "requires_approval": plan.get("requires_approval", False),
            "rollback_strategy": plan.get("rollback_strategy", "none"),
            "estimated_steps": plan.get("estimated_steps", 1),
            **({"plan_fixes": plan_fixes} if plan_fixes else {}),
        })
        ctx.state = AgentState.WAITING_APPROVAL

    # ── APPROVAL ─────────────────────────────────────────────────────
    def approve(self, ctx: AgentRunContext, current_user: str, *, approved_by_human: bool = False) -> None:
        """APPROVAL: Check governance, log decision, auto-approve (future: UI prompt)."""
        d = self.deps
        auto_approve_tools = {name for name, p in d.tool_governance.items() if p["auto_approve"]}
        # Governor-managed tools never hard-block the plan: each call is
        # classified at execution time — additive creates run, mutations and
        # deletions of existing content become review proposals.
        governed_tools = (
            frozenset(getattr(d.change_governor, "governed_tools", frozenset()))
            if d.change_governor is not None else frozenset()
        )
        steps = ctx.plan.get("steps", [])
        non_auto = [
            s.get("action") for s in steps
            if s.get("action") not in auto_approve_tools
            and s.get("action") not in governed_tools
        ]
        requires = ctx.plan.get("requires_approval", False) or bool(non_auto)

        ctx.transcript.append({
            "state": AgentState.WAITING_APPROVAL.value,
            "requires_approval": requires,
            "non_auto_approve_steps": non_auto,
            "decision": "human_approved" if requires and approved_by_human else ("blocked_pending_approval" if requires else "auto_approved"),
        })
        decision = "human_approved" if requires and approved_by_human else ("blocked_pending_approval" if requires else "auto_approved")
        ctx.trace.decision("approve", decision=decision, non_auto_steps=len(non_auto))
        d.audit(
            "agent_approval", user_email=current_user,
            requires_approval=requires,
            non_auto_steps=non_auto,
            decision=decision,
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
            request_workspace = getattr(req, "workspace_id", None)
            context = self._executor_context(ctx, req, lang_hint, current_user, request_workspace)
            raw = await d.generate_as(
                model_id,
                message="Execute the next step.",
                context=context, max_tokens=4096, temperature=req.temperature,
            )
            ctx.trace.llm_call("execute", model=model_id)
            try:
                action, exec_repairs = extract_action_details(str(raw))
                ctx.trace.repair("execute", repairs=exec_repairs)
            except ValueError as exc:
                parse_failures += 1
                if self._note_parse_failure(ctx, raw, exc, parse_failures):
                    break
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
                ctx.trace.decision("execute", decision="final")
                ctx.state = AgentState.VERIFYING
                return

            # Loop guard
            if self._is_repeated_create(ctx, name, args):
                ctx.transcript.append({
                    "state": AgentState.EXECUTING.value, "action": name,
                    "error": "LOOP_DETECTED: identical action+args repeated — halted.",
                })
                ctx.trace.decision("execute", decision="loop_detected", tool=name)
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

            proposed, governor_allows_additive = self._governor_review(
                ctx, name, thoughts, args, policy, risk, current_user, request_workspace,
                conversation_id=getattr(req, "conversation_id", None),
            )
            if proposed:
                continue

            if self._blocked_by_gates(
                ctx, req, name, thoughts, args, policy, risk,
                current_user, governor_allows_additive,
            ):
                continue

            self._dispatch_step(ctx, name, thoughts, args, policy, risk, current_user)

        ctx.state = AgentState.VERIFYING

    def _executor_context(
        self, ctx: AgentRunContext, req: Any, lang_hint: str,
        current_user: str, request_workspace: Optional[str],
    ) -> str:
        """Assemble one executor turn's prompt (plan, corrections, recent chat)."""
        d = self.deps
        corrections_hint = (
            "\n\nCritic corrections from previous attempt:\n"
            + "\n".join(f"- {c}" for c in ctx.corrections)
        ) if ctx.corrections else ""

        recent_kwargs = {
            "conversation_id": req.conversation_id,
            "user_email": current_user or None,
        }
        if request_workspace is not None:
            recent_kwargs["workspace_id"] = request_workspace
        recent_conversation = d.recent_chat_context(**recent_kwargs) or "(none)"
        return (
            f"{d.executor_prompt}\n\n"
            f"[LANGUAGE HINT: {lang_hint}]\n"
            f"Workspace root: {d.agent_root}\n\n"
            f"PLAN:\n{json.dumps(ctx.plan, ensure_ascii=False)}\n\n"
            f"Recent conversation:\n{recent_conversation}\n\n"
            f"User request: {req.message}{corrections_hint}\n\n"
            f"Execution transcript:\n{json.dumps(ctx.transcript, ensure_ascii=False, indent=2)}"
        )

    def _note_parse_failure(
        self, ctx: AgentRunContext, raw: Any, exc: ValueError, parse_failures: int,
    ) -> bool:
        """Record one executor parse slip; True when the run should stop retrying."""
        ctx.transcript.append({
            "state": AgentState.EXECUTING.value, "action": "parse_error",
            "raw": str(raw)[:400], "error": str(exc),
        })
        if parse_failures >= 3:
            ctx.trace.parse_error("execute", error=str(exc), recovered=False)
            return True
        ctx.trace.parse_error("execute", error=str(exc), recovered=True)
        # Weak models often need one concrete reminder of the wire
        # format; feed it through the corrections channel and retry
        # instead of aborting the whole run on the first slip.
        hint = (
            'Your last reply was not a single JSON action object. Reply with '
            'EXACTLY one JSON object like {"thoughts": "...", "action": '
            '"tool_name", "args": {...}} and nothing else.'
        )
        if parse_failures >= 2:
            # Escalate: name the valid tools so the model stops
            # inventing action names or prose.
            valid = ", ".join(sorted(self.deps.tool_governance.keys()))
            hint = (
                f"{hint} Valid action values are: {valid}, final. "
                'Use {"action": "final", "message": "..."} to finish.'
            )
        if hint not in ctx.corrections:
            ctx.corrections.append(hint)
            ctx.trace.correction("execute", hint=hint)
        return False

    def _is_repeated_create(self, ctx: AgentRunContext, name: Any, args: dict) -> bool:
        """Loop guard: the same file-create action+args re-issued right after a result."""
        exec_steps = [s for s in ctx.transcript if s.get("state") == AgentState.EXECUTING.value]
        last = exec_steps[-1] if exec_steps else None
        return bool(
            name in self.deps.file_create_actions and last
            and last.get("action") == name
            and (last.get("args") or {}) == args
            and "result" in last
        )

    def _governor_review(
        self, ctx: AgentRunContext, name: str, thoughts: str, args: dict,
        policy: dict, risk: str, current_user: str, request_workspace: Optional[str],
        conversation_id: Optional[str] = None,
    ) -> Tuple[bool, bool]:
        """Central change-class governance: create-new runs with minimal
        friction, change/delete-existing becomes a review proposal.

        Returns ``(proposed, governor_allows_additive)``: ``proposed`` means the
        step was staged as a proposal (skip execution); ``allows_additive`` lets
        an additive create pass the classic approval gate.
        """
        d = self.deps
        if d.change_governor is None:
            return False, False
        verdict = d.change_governor.review(
            name, args, policy=dict(policy),
            user_email=current_user, workspace_id=request_workspace,
            conversation_id=conversation_id,
        )
        if verdict is not None and verdict.get("decision") == "proposed":
            proposal = verdict.get("proposal") or {}
            ctx.trace.tool("execute", name=name, outcome="proposed", risk=risk)
            ctx.transcript.append({
                "state": AgentState.EXECUTING.value, "action": name,
                "thoughts": thoughts, "args": {k: v for k, v in args.items() if k != "content"},
                "risk": risk, "governance": dict(policy),
                "result": {
                    "proposed": True,
                    "proposal_id": proposal.get("id"),
                    "note": "기존 내용을 바꾸는 작업이라 변경 제안으로 저장했습니다. 검토함에서 승인하면 적용됩니다.",
                },
            })
            d.audit(
                "agent_change_proposed", user_email=current_user,
                action=name, proposal_id=proposal.get("id"),
                change_class=(verdict.get("classification") or {}).get("change_class"),
            )
            return True, False
        return False, (verdict is not None and verdict.get("decision") == "allow_additive")

    def _blocked_by_gates(
        self, ctx: AgentRunContext, req: Any, name: str, thoughts: str, args: dict,
        policy: dict, risk: str, current_user: str, governor_allows_additive: bool,
    ) -> bool:
        """Classic destructive / explicit-approval gates; True when the step was blocked."""
        d = self.deps
        if policy["risk"] == "destructive":
            ctx.trace.tool("execute", name=name, outcome="blocked_destructive", risk=risk)
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
            return True

        if not policy["auto_approve"] and not ctx.approved_by_human and not governor_allows_additive:
            d.audit(
                "agent_exec", user_email=current_user, source=getattr(req, "source", None) or "agent",
                state=AgentState.EXECUTING.value, action=name, risk=risk,
                shell=policy["shell"], network=policy["network"],
                destructive=policy["destructive"], sandbox=policy["sandbox"],
                rollback=policy["rollback"],
                args={k: v for k, v in args.items() if k != "content"},
            )
            ctx.trace.tool("execute", name=name, outcome="blocked_approval", risk=risk)
            ctx.transcript.append({
                "state": AgentState.EXECUTING.value, "action": name,
                "thoughts": thoughts, "args": args, "risk": risk,
                "governance": dict(policy),
                "error": f"BLOCKED: action '{name}' requires explicit approval.",
            })
            return True
        return False

    def _dispatch_step(
        self, ctx: AgentRunContext, name: str, thoughts: str, args: dict,
        policy: dict, risk: str, current_user: str,
    ) -> None:
        """Role check + shared tool lifecycle, recorded on the transcript either way."""
        d = self.deps
        sanitize_meta: Optional[Dict[str, Any]] = None
        if name == "write_file" and isinstance(args.get("content"), str):
            # ArtifactWritePipeline: the executor's args.content is untrusted
            # model output. The same extract→validate→repair guarantee as the
            # direct chat path applies here, so a weak model driving the JSON
            # loop can never persist fenced/chatty/truncated payloads.
            cleaned, meta = sanitize_write_content(
                str(args.get("path") or ""), args["content"],
                user_request=str(ctx.plan.get("goal") or thoughts or name),
            )
            if meta.get("sanitized"):
                args = dict(args)
                args["content"] = cleaned
                sanitize_meta = meta
                ctx.trace.repair(
                    "execute",
                    repairs=[
                        "artifact_repair" if meta.get("repaired") else "artifact_sanitize"
                    ],
                )
        try:
            d.check_role(name, current_user)
            # Shared tool lifecycle: pre_tool (may block) → execute → post_tool.
            result = dispatch_tool(
                d.hooks, name, args,
                lambda: d.execute_tool(name, args),
                user_email=current_user, source="agent",
            )
            ctx.trace.tool("execute", name=name, outcome="ok", risk=risk)
            ctx.transcript.append({
                "state": AgentState.EXECUTING.value, "action": name,
                "thoughts": thoughts, "args": args,
                "risk": risk, "governance": dict(policy), "result": result,
                **({"content_sanitize": sanitize_meta} if sanitize_meta else {}),
            })
        except (ToolError, KeyError, TypeError, PermissionError) as exc:
            ctx.trace.tool("execute", name=name, outcome="error", risk=risk)
            ctx.transcript.append({
                "state": AgentState.EXECUTING.value, "action": name,
                "thoughts": thoughts, "args": args,
                "risk": risk, "governance": dict(policy), "error": str(exc),
            })

    # ── VERIFY ───────────────────────────────────────────────────────
    def _has_execution_evidence(self, ctx: AgentRunContext) -> bool:
        """Deterministic evidence check: at least one executing step actually
        produced a result (tool ran, or a governed change was staged as a
        proposal). ``final``/parse-error/blocked steps carry no result and do
        not count — a critic PASS over an evidence-free transcript must not
        become DONE."""
        for step in ctx.transcript:
            if step.get("state") != AgentState.EXECUTING.value:
                continue
            if step.get("action") in (None, "final", "parse_error"):
                continue
            if isinstance(step.get("result"), dict):
                return True
        return False

    async def verify(
        self, ctx: AgentRunContext, req: Any, lang_hint: str, current_user: str,
        max_retry: int = 3, model_id: Optional[str] = None,
    ) -> None:
        """VERIFYING: Critic role evaluates transcript → DONE / EXECUTING (retry) / ROLLBACK / NEEDS_REVIEW / FAILED.

        Fail-closed: a critic whose output cannot be parsed (after one strict
        repair retry) never fabricates a PASS — the run terminates as
        NEEDS_REVIEW so the user is told to check the result themselves.
        """
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
        ctx.trace.llm_call("verify", model=model_id)
        verdict: Optional[Dict[str, Any]] = None
        try:
            verdict, verdict_repairs = extract_action_details(str(raw))
            ctx.trace.repair("verify", repairs=verdict_repairs)
        except ValueError as exc:
            # One strict repair retry — re-ask the critic for the exact wire
            # format instead of fabricating a verdict.
            ctx.trace.parse_error("verify", error=str(exc), recovered=True)
            strict_context = (
                f"{context}\n\n"
                "Your previous verdict was not parseable JSON. Reply with EXACTLY one "
                'JSON object like {"action": "verdict", "verdict": "PASS", '
                '"next_state": "DONE", "reason": "...", "corrections": []} '
                "and nothing else. verdict must be PASS or FAIL; next_state must be "
                "one of DONE, EXECUTING, ROLLBACK, FAILED."
            )
            raw = await d.generate_as(
                model_id,
                message="Return your verdict as one strict JSON object.",
                context=strict_context, max_tokens=512, temperature=0.0,
            )
            ctx.trace.llm_call("verify", model=model_id)
            try:
                verdict, verdict_repairs = extract_action_details(str(raw))
                ctx.trace.repair("verify", repairs=verdict_repairs)
            except ValueError as retry_exc:
                ctx.trace.parse_error("verify", error=str(retry_exc), recovered=False)
                verdict = None

        has_evidence = self._has_execution_evidence(ctx)

        if verdict is None:
            # Verifier unavailable — fail closed, never DONE.
            ctx.transcript.append({
                "state": AgentState.VERIFYING.value,
                "verdict": "UNAVAILABLE",
                "reason": "critic output unparseable after strict retry",
                "verifier_available": False,
                "verdict_valid": False,
                "evidence": has_evidence,
            })
            ctx.trace.decision(
                "verify", decision="verification_unavailable",
                verifier_available=False, verdict_valid=False, evidence=has_evidence,
            )
            ctx.final_message = (
                "검증을 완료하지 못했습니다 — 검증 모델의 응답을 해석할 수 없었습니다. "
                "실행 결과를 직접 확인해 주시고, 필요하면 다시 시도해 주세요."
            )
            ctx.state = AgentState.NEEDS_REVIEW
            return

        ctx.corrections = verdict.get("corrections", [])
        # Normalize legacy verdict next_state strings to current AgentState names
        raw_next = verdict.get("next_state", "")
        next_s = {"COMPLETE": "DONE", "RETRY": "EXECUTING"}.get(raw_next, raw_next)

        ctx.transcript.append({
            "state": AgentState.VERIFYING.value,
            "verdict":     verdict.get("verdict", ""),
            "reason":      verdict.get("reason", ""),
            "corrections": ctx.corrections,
            "confidence":  verdict.get("confidence", 0.9),
            "next_state":  next_s,
            "verifier_available": True,
            "verdict_valid": True,
            "evidence": has_evidence,
        })

        ctx.trace.decision(
            "verify", decision=str(verdict.get("verdict", "")), next_state=next_s,
            verifier_available=True, verdict_valid=True, evidence=has_evidence,
        )
        if verdict.get("verdict") == "PASS":
            # DONE requires both: a validly parsed PASS verdict AND
            # deterministic execution evidence in the transcript. A PASS over
            # an evidence-free run is not a completion.
            if not has_evidence:
                ctx.trace.decision("verify", decision="needs_review_no_evidence")
                ctx.final_message = (
                    "검증자는 통과를 보고했지만 실제 실행 근거(도구 실행 기록)가 없어 "
                    "완료로 처리하지 않았습니다. 결과를 직접 확인해 주세요."
                )
                ctx.state = AgentState.NEEDS_REVIEW
                return
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
                ctx.trace.retry("verify", attempt=ctx.retry_count)
                ctx.transcript.append({
                    "state": AgentState.EXECUTING.value,
                    "retry_attempt": ctx.retry_count,
                    "corrections": ctx.corrections,
                })
                ctx.state = AgentState.EXECUTING
        elif next_s == "DONE":
            # Contradictory verdict: the critic asked for DONE without a PASS.
            # The loose "or next_state == DONE" success path is gone — this is
            # a non-success that the user must review.
            ctx.trace.decision("verify", decision="needs_review_inconsistent_verdict")
            ctx.final_message = (
                "검증 결과가 일관되지 않아 완료로 처리하지 않았습니다. "
                "실행 결과를 직접 확인해 주세요."
            )
            ctx.state = AgentState.NEEDS_REVIEW
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
        ctx.trace.decision(
            "rollback", decision="rolled_back",
            attempted=len(rolled), recovered=sum(1 for r in rolled if r.get("ok")),
        )
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
            kept_learnings = filter_learnings(mem.get("learnings") or [])
            if mem.get("save_to_knowledge") and kept_learnings:
                learnings = "\n".join(kept_learnings)
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
