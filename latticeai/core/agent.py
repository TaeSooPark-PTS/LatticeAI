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
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    FrozenSet,
    List,
    Mapping,
    Optional,
    Tuple,
)

from lattice_brain.runtime.contracts import (
    runtime_boundary_contract,
    single_agent_contract,
)
from lattice_brain.runtime.hooks import dispatch_tool
from latticeai.core.agent_helpers import (
    PhaseBudgets,
    TranscriptBudget,
    _truncate_strings,
    artifact_checklist,
    compact_transcript,
    extract_action,
    extract_action_details,
    files_written,
    filter_learnings,
    format_artifact_checklist,
    format_requirement_coverage,
    normalize_plan,
    requirement_coverage,
)
from latticeai.core.agent_permission import (
    block_reason_for_tool,
    non_auto_plan_steps,
    resolve_deps_mode,
)
from latticeai.core.agent_profiles import AgentProfile, profile_for_model

# The state vocabulary and the pure helpers live in sibling modules so this one
# holds only the loop. They are re-exported (see ``__all__``) because callers —
# the HTTP layer, run_store, the eval harness, and the tests — have always
# imported them from here, and that contract does not change.
from latticeai.core.agent_state import AGENT_TERMINAL_STATES, AgentState
from latticeai.core.agent_trace import LoopTrace
from latticeai.core.file_generation import (
    generate_file_content,
    infer_file_target,
    sanitize_write_content,
)
from latticeai.core.permission_mode import (
    PermissionMode,
    is_circuit_breaker,
    plan_requires_approval,
    should_stage_proposal,
)
from latticeai.core.tool_registry import SCOPED_KNOWLEDGE_TOOLS
from latticeai.tools import ToolError

__all__ = [
    # this module
    "AgentDeps",
    "AgentRunContext",
    "SingleAgentRuntime",
    # re-exported from agent_state
    "AGENT_TERMINAL_STATES",
    "AgentState",
    # re-exported from agent_helpers
    "PhaseBudgets",
    "TranscriptBudget",
    "artifact_checklist",
    "compact_transcript",
    "extract_action",
    "extract_action_details",
    "files_written",
    "filter_learnings",
    "format_artifact_checklist",
    "format_requirement_coverage",
    "normalize_plan",
    "requirement_coverage",
]


class AgentRunContext:
    """Mutable state carrier passed through all agent phases."""
    __slots__ = ("state", "plan", "transcript", "retry_count",
                 "state_history", "corrections", "final_message", "rollback_log",
                 "executing_model", "reviewing_model", "approved_by_human", "trace",
                 "on_step", "project_context", "permission_mode")

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
        # Per-run step observer (review Wave 1.1): the HTTP layer attaches a
        # callback here so live SSE clients see progress while EXECUTING.
        # Never serialized; a broken observer never breaks the loop.
        self.on_step: Optional[Callable[[Dict[str, Any]], None]] = None
        # Multi-turn project loop (v9.9.6): a prompt block describing where the
        # project stands — files already produced, open TODOs, the last honest
        # verification. Empty for a standalone run, which behaves exactly as
        # before. Set by the HTTP layer, read by plan/execute/verify.
        self.project_context: str = ""
        # Autonomy dial resolved once per run (v9.9.8). The HTTP layer stamps
        # the user/workspace-scoped mode here so the plan gate and every
        # per-tool gate in the same run agree; ``None`` falls back to the
        # process-wide resolver on ``deps``.
        self.permission_mode: Optional[str] = None


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
    policy_for: Callable[[str, dict], Mapping[str, Any]]   # name, args -> policy
    risk_level: Callable[[Any], str]               # policy -> "low"|"medium"|"high"
    check_role: Callable[[str, str], None]         # tool_name, user -> raises if not allowed
    tool_governance: Mapping[str, Mapping[str, Any]]  # name -> policy (auto_approve set)
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

    # ── snapshot rollback ports (optional, review L7) ────────────────
    # git-only rollback left non-git workspaces and newly created files
    # unrecoverable. ``snapshot_file(path)`` captures pre-write state
    # ({"existed", "content", "too_large"}) before a file-create action;
    # ``restore_snapshot(path, content)`` restores it (content=None deletes
    # a file the run created). Both are production-wired with workspace
    # path safety; tests pass recorders.
    snapshot_file: Optional[Callable[[str], Dict[str, Any]]] = None
    restore_snapshot: Optional[Callable[[str, Optional[str]], Dict[str, Any]]] = None

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

    # ── phase budgets (optional) ─────────────────────────────────────
    # Per-phase token caps (plan/execute/verify/memory). None reads the
    # environment once at first use; tests inject a fixed PhaseBudgets.
    phase_budgets: Optional[PhaseBudgets] = None

    # ── transcript shaping (optional) ────────────────────────────────
    # Executor/critic prompt window caps. None reads the environment once;
    # tests inject a fixed TranscriptBudget.
    transcript_budget: Optional["TranscriptBudget"] = None

    # ── step observer port (optional) ────────────────────────────────
    # Default per-runtime observer for live step events; a per-run observer
    # can also be attached on AgentRunContext.on_step. Both are advisory.
    on_step: Optional[Callable[[Dict[str, Any]], None]] = None

    # ── agent profile (optional, v9.9.7) ─────────────────────────────
    # How hard the loop works to keep a weak model on contract. None selects
    # per-run from the executing model id (``profile_for_model``); tests
    # inject a fixed profile.
    agent_profile: Optional["AgentProfile"] = None

    # ── permission mode port (optional, v9.9.8) ──────────────────────
    # The autonomy dial. Either a static mode, or a resolver callable that
    # accepts ``user_email``/``workspace_id`` scope kwargs (preferred) or no
    # arguments at all — see ``call_mode_source``. ``None`` means strict,
    # which is exactly the pre-9.9.8 behaviour.
    permission_mode: Any = None


class SingleAgentRuntime:
    """Drives the agent state machine over injected :class:`AgentDeps`."""

    def __init__(self, deps: AgentDeps) -> None:
        self.deps = deps
        self._env_phase_budgets: Optional[PhaseBudgets] = None
        self._env_transcript_budget: Optional[TranscriptBudget] = None

    @property
    def phase_budgets(self) -> PhaseBudgets:
        # getattr twice: partially-constructed runtimes/deps (tests build them
        # via __new__ or minimal fakes) still get working default budgets.
        injected = getattr(self.deps, "phase_budgets", None)
        if injected is not None:
            return injected
        cached = getattr(self, "_env_phase_budgets", None)
        if cached is None:
            cached = PhaseBudgets.from_env()
            self._env_phase_budgets = cached
        return cached

    @property
    def transcript_budget(self) -> TranscriptBudget:
        injected = getattr(self.deps, "transcript_budget", None)
        if injected is not None:
            return injected
        cached = getattr(self, "_env_transcript_budget", None)
        if cached is None:
            cached = TranscriptBudget.from_env()
            self._env_transcript_budget = cached
        return cached

    # ── permission mode (v9.9.8) ─────────────────────────────────────
    def resolve_permission_mode(
        self,
        ctx: Optional[AgentRunContext] = None,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> PermissionMode:
        """Autonomy dial for this run.

        A mode stamped on ``ctx`` wins, so the plan a user approved and every
        tool step in the same run are judged by one dial even if the stored
        preference changes mid-run. Otherwise the resolver on ``deps`` is
        consulted with the caller's scope — resolving unscoped would collapse
        every caller onto the process-wide default.
        """
        return resolve_deps_mode(
            self.deps, ctx, user_email=user_email, workspace_id=workspace_id,
        )

    def _governed_tools(self) -> FrozenSet[str]:
        governor = getattr(self.deps, "change_governor", None)
        if governor is None:
            return frozenset()
        return frozenset(getattr(governor, "governed_tools", frozenset()))

    def profile_for(self, model_id: Optional[str]) -> AgentProfile:
        """Loop profile for the model actually executing this run (v9.9.7).

        An injected ``AgentDeps.agent_profile`` wins (tests, explicit config);
        otherwise the profile is derived from the model id, so a small local
        model gets the compact loop without any extra configuration.
        """
        injected = getattr(self.deps, "agent_profile", None)
        if injected is not None:
            return injected
        return profile_for_model(model_id)

    def _emit_step(self, ctx: AgentRunContext, phase: str, event: str, **details: Any) -> None:
        """Fire the per-run / deps step observers (review Wave 1.1).

        Observers power the live step timeline in the UI. They are pure
        telemetry: any observer failure is logged and swallowed — the loop
        itself must never notice.
        """
        payload: Dict[str, Any] = {"phase": phase, "event": event}
        for key, value in details.items():
            if value is not None:
                payload[key] = value
        for observer in (getattr(ctx, "on_step", None), getattr(self.deps, "on_step", None)):
            if observer is None:
                continue
            try:
                observer(dict(payload))
            except Exception as exc:  # noqa: BLE001 — observers are advisory
                logging.warning("agent step observer failed: %s", exc)

    @staticmethod
    def _project_block(ctx: AgentRunContext) -> str:
        """Project-session context for prompts, or "" for a standalone run.

        Multi-turn project loop (v9.9.6): a later run must see the files the
        project already produced and what is still open, instead of planning
        from a blank workspace every time.
        """
        summary = str(getattr(ctx, "project_context", "") or "").strip()
        return f"\n\n[PROJECT SESSION]\n{summary}" if summary else ""

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
        project_block = self._project_block(ctx)
        context = (
            f"{d.planner_prompt}\n\n"
            f"[LANGUAGE HINT: {lang_hint}]\n"
            f"Workspace root: {d.agent_root}{project_block}\n\n"
            f"User request: {req.message}"
        )
        raw = await d.generate_as(
            model_id,
            message="Produce a JSON execution plan for this request.",
            context=context, max_tokens=self.phase_budgets.plan_tokens, temperature=0.1,
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
        self._emit_step(
            ctx, "plan", "planned",
            goal=str(plan.get("goal") or "")[:200],
            steps=len(plan.get("steps") or []),
            requires_approval=bool(plan.get("requires_approval", False)),
        )
        ctx.state = AgentState.WAITING_APPROVAL

    # ── APPROVAL ─────────────────────────────────────────────────────
    def approval_requirements(self, ctx: AgentRunContext) -> Dict[str, Any]:
        """Read-only preview of the approval gate for a planned run.

        Shares the exact predicate :meth:`approve` enforces, so the HTTP
        layer can pause a run as ``awaiting_approval`` (with a plan summary
        for the user) instead of letting it fail closed — without ever
        weakening the gate itself.
        """
        d = self.deps
        mode = self.resolve_permission_mode(ctx)
        # Governor-managed tools never hard-block the plan: each call is
        # classified at execution time — additive creates run, mutations and
        # deletions of existing content become review proposals.
        governed_tools = self._governed_tools()
        steps = ctx.plan.get("steps", [])
        non_auto = non_auto_plan_steps(
            mode, steps, d.tool_governance or {}, governed_tools=governed_tools,
        )
        requires = plan_requires_approval(
            mode,
            non_auto_steps=non_auto,
            plan_flag=bool(ctx.plan.get("requires_approval", False)),
        )
        lines = [
            f"{index}. {step.get('description') or step.get('action') or '?'}"
            for index, step in enumerate(steps, start=1)
        ]
        summary = str(ctx.plan.get("goal") or "").strip()
        if lines:
            summary = (summary + "\n" if summary else "") + "\n".join(lines)
        return {
            "requires_approval": requires,
            "non_auto_steps": non_auto,
            "permission_mode": mode.value,
            "plan_summary": summary,
        }

    def approve(self, ctx: AgentRunContext, current_user: str, *, approved_by_human: bool = False) -> None:
        """APPROVAL: Check governance, log decision, auto-approve (future: UI prompt)."""
        d = self.deps
        requirements = self.approval_requirements(ctx)
        non_auto = requirements["non_auto_steps"]
        requires = requirements["requires_approval"]

        ctx.transcript.append({
            "state": AgentState.WAITING_APPROVAL.value,
            "requires_approval": requires,
            "non_auto_approve_steps": non_auto,
            "decision": "human_approved" if requires and approved_by_human else ("blocked_pending_approval" if requires else "auto_approved"),
        })
        decision = "human_approved" if requires and approved_by_human else ("blocked_pending_approval" if requires else "auto_approved")
        ctx.trace.decision("approve", decision=decision, non_auto_steps=len(non_auto))
        self._emit_step(ctx, "approval", "decision", decision=decision)
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
        profile = self.profile_for(model_id)
        exec_count = sum(1 for s in ctx.transcript if s.get("state") == AgentState.EXECUTING.value)
        budget = max(1, max_steps - exec_count)
        parse_failures = 0

        for _ in range(budget):
            request_workspace = getattr(req, "workspace_id", None)
            context = self._executor_context(
                ctx, req, lang_hint, current_user, request_workspace, profile=profile
            )
            raw = await d.generate_as(
                model_id,
                message="Execute the next step.",
                context=context, max_tokens=self.phase_budgets.execute_tokens,
                temperature=req.temperature,
            )
            ctx.trace.llm_call("execute", model=model_id)
            try:
                action, exec_repairs = extract_action_details(str(raw))
                ctx.trace.repair("execute", repairs=exec_repairs)
            except ValueError as exc:
                parse_failures += 1
                if self._note_parse_failure(ctx, raw, exc, parse_failures, profile):
                    # Direct-path fallback (v9.9.7): a small model that cannot
                    # hold the tool-call protocol can still write a file. Run
                    # the plan's own file steps without asking for any JSON.
                    if profile.direct_path_fallback and await self._direct_file_path(
                        ctx, req, current_user, model_id
                    ):
                        ctx.state = AgentState.VERIFYING
                        return
                    break
                continue

            name     = str(action.get("action") or "")
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
                self._emit_step(ctx, "execute", "final")
                ctx.state = AgentState.VERIFYING
                return

            # Loop guard
            if self._is_repeated_create(ctx, name, args):
                ctx.transcript.append({
                    "state": AgentState.EXECUTING.value, "action": name,
                    "error": "LOOP_DETECTED: identical action+args repeated — halted.",
                })
                ctx.trace.decision("execute", decision="loop_detected", tool=name)
                self._emit_step(ctx, "execute", "blocked", action=name, reason="loop_detected")
                break

            if name == "clear_history":
                result = d.clear_history(args.get("keep_last", 0))
                ctx.transcript.append({
                    "state": AgentState.EXECUTING.value, "action": name,
                    "thoughts": thoughts, "args": args, "result": result,
                })
                self._emit_step(ctx, "execute", "tool", action=name, ok=True)
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
        profile: Optional[AgentProfile] = None,
    ) -> str:
        """Assemble one executor turn's prompt (plan, corrections, recent chat)."""
        d = self.deps
        # Only the latest corrections steer the next attempt — stale hints
        # from earlier retries dilute weak models (review Wave 0.3).
        active_corrections = ctx.corrections[-3:]
        corrections_hint = (
            "\n\nCritic corrections from previous attempt:\n"
            + "\n".join(f"- {c}" for c in active_corrections)
        ) if active_corrections else ""

        recent_kwargs = {
            "conversation_id": req.conversation_id,
            "user_email": current_user or None,
        }
        if request_workspace is not None:
            recent_kwargs["workspace_id"] = request_workspace
        recent_conversation = d.recent_chat_context(**recent_kwargs) or "(none)"
        budget = self.transcript_budget
        # A small model drowns in a long transcript far sooner than a large
        # one, so the profile may narrow the window (v9.9.7).
        window = min(budget.window, profile.transcript_window) if profile else budget.window
        bounded_transcript = compact_transcript(
            ctx.transcript,
            window=window,
            result_chars=budget.result_chars,
        )
        # Mid-run workspace awareness (review L5): later steps must see what
        # this run already produced instead of a stale workspace picture.
        written = files_written(ctx.transcript, d.file_create_actions)
        written_hint = (
            "\n\nFiles written by this run so far (they exist in the workspace now):\n"
            + "\n".join(f"- {path}" for path in written)
        ) if written else ""
        return (
            f"{d.executor_prompt}\n\n"
            f"[LANGUAGE HINT: {lang_hint}]\n"
            f"Workspace root: {d.agent_root}{self._project_block(ctx)}\n\n"
            f"PLAN:\n{json.dumps(ctx.plan, ensure_ascii=False)}{written_hint}\n\n"
            f"Recent conversation:\n{recent_conversation}\n\n"
            f"User request: {req.message}{corrections_hint}\n\n"
            f"Execution transcript:\n{json.dumps(bounded_transcript, ensure_ascii=False, indent=2)}"
        )

    def _note_parse_failure(
        self, ctx: AgentRunContext, raw: Any, exc: ValueError, parse_failures: int,
        profile: Optional[AgentProfile] = None,
    ) -> bool:
        """Record one executor parse slip; True when the run should stop retrying."""
        profile = profile or self.profile_for(None)
        ctx.transcript.append({
            "state": AgentState.EXECUTING.value, "action": "parse_error",
            "raw": str(raw)[:400], "error": str(exc),
        })
        if parse_failures >= profile.parse_failure_budget:
            ctx.trace.parse_error("execute", error=str(exc), recovered=False)
            self._emit_step(ctx, "execute", "parse_error", recovered=False)
            return True
        ctx.trace.parse_error("execute", error=str(exc), recovered=True)
        self._emit_step(ctx, "execute", "parse_error", recovered=True)
        # Weak models often need one concrete reminder of the wire
        # format; feed it through the corrections channel and retry
        # instead of aborting the whole run on the first slip.
        hint = (
            'Your last reply was not a single JSON action object. Reply with '
            'EXACTLY one JSON object like {"thoughts": "...", "action": '
            '"tool_name", "args": {...}} and nothing else.'
        )
        if parse_failures >= profile.escalate_after:
            # Escalate: name the valid tools so the model stops
            # inventing action names or prose. The compact profile escalates
            # a slip earlier — a small model needs the list sooner.
            valid = ", ".join(sorted(self.deps.tool_governance.keys()))
            hint = (
                f"{hint} Valid action values are: {valid}, final. "
                'Use {"action": "final", "message": "..."} to finish.'
            )
        if hint not in ctx.corrections:
            ctx.corrections.append(hint)
            ctx.trace.correction("execute", hint=hint)
        return False

    async def _direct_file_path(
        self, ctx: AgentRunContext, req: Any, current_user: str,
        model_id: Optional[str],
    ) -> bool:
        """Write the plan's file steps without asking the model for JSON (v9.9.7).

        The compact profile's escape hatch. A 1–4B local model that cannot hold
        the tool-call protocol can still write a file, so when JSON tool calls
        are exhausted the loop drops the protocol entirely: it takes the paths
        the *planner* already chose and asks only for file content in plain
        text, through the same validated
        :func:`~latticeai.core.file_generation.generate_file_content` pipeline
        the direct chat path uses.

        Returns True when at least one file was actually written. Honest
        failure modes: no planned paths, a governor that stages the write as a
        proposal, or a tool error all return False and leave the run to end as
        it would have — this never fabricates evidence.
        """
        d = self.deps
        planned: List[str] = []
        for step in ctx.plan.get("steps") or []:
            if not isinstance(step, dict) or step.get("action") not in d.file_create_actions:
                continue
            path = str((step.get("args") or {}).get("path") or "").strip()
            if path and path not in planned:
                planned.append(path)
        if not planned:
            inferred = infer_file_target(getattr(req, "message", "") or "")
            if inferred:
                planned = [inferred]
        if not planned:
            return False

        goal = str(ctx.plan.get("goal") or getattr(req, "message", "") or "")

        async def _generate(context: str) -> Any:
            return await d.generate_as(
                model_id,
                message="Write the file content.",
                context=context,
                max_tokens=self.phase_budgets.execute_tokens,
                temperature=0.2,
            )

        wrote = False
        for path in planned[:6]:
            try:
                content, meta = await generate_file_content(
                    _generate,
                    target_path=path,
                    user_request=goal,
                    bundle_files=planned if len(planned) > 1 else None,
                )
            except Exception as exc:  # noqa: BLE001 — fallback must not raise
                logging.warning("direct file path generation failed for %s: %s", path, exc)
                continue
            ctx.trace.llm_call("execute", model=model_id)
            ctx.trace.repair("execute", repairs=["direct_path_fallback"])
            args = {"path": path, "content": content}
            policy = d.policy_for("write_file", args)
            risk = d.risk_level(policy)
            before = len(ctx.transcript)
            self._dispatch_step(ctx, "write_file", "direct path fallback", args, policy, risk, current_user)
            last = ctx.transcript[-1] if len(ctx.transcript) > before else {}
            if isinstance(last.get("result"), dict) and not last["result"].get("proposed"):
                wrote = True
                last["direct_path"] = True
                last["generation"] = {"repaired": bool(meta.get("repaired"))}
        if wrote:
            ctx.trace.decision("execute", decision="direct_path_fallback", files=len(planned))
            self._emit_step(ctx, "execute", "direct_path", files=len(planned))
            ctx.final_message = (
                "도구 호출 형식을 계속 벗어나서, 계획에 있던 파일을 직접 생성했습니다. "
                "내용을 확인해 주세요."
            )
        return wrote

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
        policy: Mapping[str, Any], risk: str, current_user: str, request_workspace: Optional[str],
        conversation_id: Optional[str] = None,
    ) -> Tuple[bool, bool]:
        """Central change-class governance: create-new runs with minimal
        friction, change/delete-existing becomes a review proposal.

        Returns ``(proposed, governor_allows_additive)``: ``proposed`` means the
        step was staged as a proposal (skip execution); ``allows_additive`` lets
        an additive create pass the classic approval gate.

        Under a mode that does not stage proposals (``trusted`` / ``bypass``)
        the decision is made *before* the governor is consulted, because
        ``review`` persists a proposal as a side effect — reviewing first and
        discarding the verdict afterwards would apply the change *and* leave an
        orphan proposal pending in the Review Center.
        """
        d = self.deps
        if d.change_governor is None:
            return False, False

        mode = self.resolve_permission_mode(
            ctx, user_email=current_user, workspace_id=request_workspace,
        )
        if not should_stage_proposal(mode, proposal_required=True):
            if name not in self._governed_tools():
                return False, False
            if policy.get("destructive") or policy.get("risk") == "destructive":
                # Let the destructive gate downstream own the block + transcript.
                return False, False
            d.audit(
                "agent_change_auto_applied",
                user_email=current_user,
                workspace_id=request_workspace,
                action=name,
                path=str(args.get("path") or "") or None,
                permission_mode=mode.value,
                note="permission mode auto-applies mutation with audit",
            )
            return False, True

        verdict = d.change_governor.review(
            name, args, policy=dict(policy),
            user_email=current_user, workspace_id=request_workspace,
            conversation_id=conversation_id,
        )
        if verdict is not None and verdict.get("decision") == "proposed":
            proposal = verdict.get("proposal") or {}
            ctx.trace.tool("execute", name=name, outcome="proposed", risk=risk)
            self._emit_step(ctx, "execute", "proposed", action=name)
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
        policy: Mapping[str, Any], risk: str, current_user: str, governor_allows_additive: bool,
    ) -> bool:
        """Destructive / circuit-breaker / explicit-approval gates.

        Returns True when the step was blocked. The active permission mode can
        widen what runs without an extra approval prompt, but never widens a
        circuit breaker or the destructive gate.
        """
        d = self.deps
        mode = self.resolve_permission_mode(
            ctx,
            user_email=current_user,
            workspace_id=getattr(req, "workspace_id", None),
        )
        # Hard denials first — mode-invariant. A circuit breaker (root/home
        # paths, `rm -rf /` style commands) and a destructive policy are both
        # audited as ``blocked`` with the reason that actually fired, rather
        # than being flattened into the approval path.
        breaker = is_circuit_breaker(name, policy, args)
        hard_deny = breaker or (
            "destructive policy"
            if policy["risk"] == "destructive" or policy.get("destructive")
            else None
        )
        if hard_deny:
            error = (
                f"BLOCKED: destructive action '{name}' not permitted in agent mode."
                if hard_deny == "destructive policy"
                else f"BLOCKED: {hard_deny}"
            )
            ctx.trace.tool("execute", name=name, outcome="blocked_destructive", risk=risk)
            self._emit_step(ctx, "execute", "blocked", action=name, reason="destructive")
            ctx.transcript.append({
                "state": AgentState.EXECUTING.value, "action": name,
                "thoughts": thoughts, "args": args, "risk": risk,
                "governance": dict(policy),
                "permission_mode": mode.value,
                "error": error,
            })
            d.audit(
                "agent_blocked", user_email=current_user, source=getattr(req, "source", None) or "agent",
                action=name, reason="destructive", governance=dict(policy),
            )
            return True

        reason = block_reason_for_tool(
            mode, name, policy, args,
            approved_by_human=bool(ctx.approved_by_human),
            governor_allows_additive=governor_allows_additive,
        )
        if reason is None:
            return False

        d.audit(
            "agent_exec", user_email=current_user, source=getattr(req, "source", None) or "agent",
            state=AgentState.EXECUTING.value, action=name, risk=risk,
            shell=policy["shell"], network=policy["network"],
            destructive=policy["destructive"], sandbox=policy["sandbox"],
            rollback=policy["rollback"],
            permission_mode=mode.value,
            args={k: v for k, v in args.items() if k != "content"},
        )
        ctx.trace.tool("execute", name=name, outcome="blocked_approval", risk=risk)
        self._emit_step(ctx, "execute", "blocked", action=name, reason="approval")
        ctx.transcript.append({
            "state": AgentState.EXECUTING.value, "action": name,
            "thoughts": thoughts, "args": args, "risk": risk,
            "governance": dict(policy),
            "permission_mode": mode.value,
            "error": reason,
        })
        return True

    def _dispatch_step(
        self, ctx: AgentRunContext, name: str, thoughts: str, args: dict,
        policy: Mapping[str, Any], risk: str, current_user: str,
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
        step_index = 1 + sum(
            1 for s in ctx.transcript
            if s.get("state") == AgentState.EXECUTING.value
            and s.get("action") not in (None, "final", "parse_error")
        )
        if (
            name in d.file_create_actions
            and d.snapshot_file is not None
            and args.get("path")
        ):
            # Pre-write snapshot (review L7): the first capture per path is
            # the true pre-run state — later writes to the same path must
            # not overwrite it. Best-effort: a snapshot failure never
            # blocks the write, it only narrows rollback options.
            path_str = str(args["path"])
            if not any(entry.get("path") == path_str for entry in ctx.rollback_log):
                try:
                    pre = d.snapshot_file(path_str)
                    ctx.rollback_log.append({"path": path_str, **(pre or {})})
                except Exception as exc:  # noqa: BLE001
                    logging.warning("pre-write snapshot failed for %s: %s", path_str, exc)
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
            self._emit_step(
                ctx, "execute", "tool", action=name, ok=True, step=step_index,
                path=str(args.get("path")) if args.get("path") else None,
            )
        except (ToolError, KeyError, TypeError, PermissionError) as exc:
            ctx.trace.tool("execute", name=name, outcome="error", risk=risk)
            ctx.transcript.append({
                "state": AgentState.EXECUTING.value, "action": name,
                "thoughts": thoughts, "args": args,
                "risk": risk, "governance": dict(policy), "error": str(exc),
            })
            self._emit_step(
                ctx, "execute", "tool", action=name, ok=False, step=step_index,
                path=str(args.get("path")) if args.get("path") else None,
            )

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
        # The critic must see every step (evidence completeness), but not
        # every byte of tool output — long bodies are capped per string so
        # verification stays affordable on long runs (review Wave 0.3).
        verify_transcript = _truncate_strings(
            ctx.transcript, self.transcript_budget.verify_chars
        )
        # Deterministic artifact facts (review L4): the critic sees the
        # sanitize/repair honesty flags per written file, not just prose.
        checklist = artifact_checklist(ctx.transcript, d.file_create_actions)
        checklist_hint = (
            f"\n\n{format_artifact_checklist(checklist)}" if checklist else ""
        )
        # Requirement coverage (review 루프 §2): the critic previously judged
        # "did this fulfill the request?" from prose alone. It now also sees
        # which requested files actually exist and which requirements the user
        # spelled out.
        coverage = requirement_coverage(
            req.message, ctx.transcript, d.file_create_actions
        )
        context = (
            f"{d.critic_prompt}\n\n"
            f"[LANGUAGE HINT: {lang_hint}]\n\n"
            f"Original request: {req.message}\n"
            f"Plan goal: {ctx.plan.get('goal', req.message)}{checklist_hint}"
            f"{format_requirement_coverage(coverage)}\n\n"
            f"Full transcript:\n{json.dumps(verify_transcript, ensure_ascii=False, indent=2)}"
        )
        raw = await d.generate_as(
            model_id,
            message="Review the execution transcript and return your verdict JSON.",
            context=context, max_tokens=self.phase_budgets.verify_tokens, temperature=0.1,
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
                context=strict_context, max_tokens=self.phase_budgets.verify_tokens,
                temperature=0.0,
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
            self._emit_step(ctx, "verify", "verdict", verdict="UNAVAILABLE")
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
        self._emit_step(
            ctx, "verify", "verdict",
            verdict=str(verdict.get("verdict", "")), next_state=next_s,
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
            if not coverage["complete"]:
                # A PASS that leaves a *requested file* unwritten is not a
                # completion — this is a fact, not a judgement, so it is
                # enforced rather than merely reported to the critic.
                missing = ", ".join(coverage["missing_files"])
                ctx.trace.decision(
                    "verify", decision="needs_review_missing_files",
                    missing=len(coverage["missing_files"]),
                )
                ctx.transcript.append({
                    "state": AgentState.VERIFYING.value,
                    "requirement_coverage": coverage,
                })
                ctx.final_message = (
                    f"요청한 파일 중 일부가 만들어지지 않아 완료로 처리하지 않았습니다: {missing}"
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
    def _snapshot_for(self, ctx: AgentRunContext, path: str) -> Optional[Dict[str, Any]]:
        for entry in ctx.rollback_log:
            if entry.get("path") == path:
                return entry
        return None

    def _rollback_one(self, ctx: AgentRunContext, path: str, gov: Dict[str, Any]) -> Dict[str, Any]:
        """Recover one path: git when governed and available, else the
        pre-write snapshot, else an honest ``mode="none"`` (review L7)."""
        d = self.deps
        if gov.get("rollback") == "git" and d.rollback_file is not None:
            try:
                result = dict(d.rollback_file(str(path)))
            except Exception as exc:  # noqa: BLE001
                result = {"path": path, "ok": False, "error": str(exc)}
            if result.get("ok"):
                result["mode"] = "git"
                return result
        snapshot = self._snapshot_for(ctx, str(path))
        if snapshot is not None and d.restore_snapshot is not None and not snapshot.get("too_large"):
            content = snapshot.get("content") if snapshot.get("existed") else None
            try:
                restored = dict(d.restore_snapshot(str(path), content))
            except Exception as exc:  # noqa: BLE001
                restored = {"path": path, "ok": False, "error": str(exc)}
            restored.setdefault("path", path)
            restored["mode"] = "snapshot"
            return restored
        return {
            "path": path, "ok": False, "mode": "none",
            "error": "no rollback available (git not applicable, no usable snapshot)",
        }

    def rollback(self, ctx: AgentRunContext, current_user: str) -> None:
        """ROLLBACK: recover written files (git → snapshot → none), then FAILED."""
        d = self.deps
        rolled: List[dict] = []
        seen_paths: set = set()
        for step in ctx.transcript:
            if step.get("state") != AgentState.EXECUTING.value:
                continue
            if not isinstance(step.get("result"), dict):
                continue
            gov = step.get("governance", {}) or {}
            path = step["result"].get("path") or (step.get("args") or {}).get("path", "")
            if not path or str(path) in seen_paths:
                continue
            if gov.get("rollback") != "git" and step.get("action") not in d.file_create_actions:
                continue
            seen_paths.add(str(path))
            rolled.append(self._rollback_one(ctx, str(path), gov))

        ctx.transcript.append({"state": AgentState.ROLLBACK.value, "rolled_back": rolled})
        ctx.trace.decision(
            "rollback", decision="rolled_back",
            attempted=len(rolled), recovered=sum(1 for r in rolled if r.get("ok")),
        )
        recovered = [f"{r['path']} ({r.get('mode')})" for r in rolled if r.get("ok")]
        ctx.final_message = (
            f"실행 실패로 롤백했습니다. 복구 파일: {recovered}"
            if recovered
            else "롤백을 시도했으나 복구할 파일이 없거나 git/스냅샷 복구 수단이 없습니다."
        )
        d.audit("agent_rollback", user_email=current_user, rolled_back=rolled)
        self._emit_step(ctx, "rollback", "rolled_back", recovered=len(recovered))
        # Rollback is a recovery from a failed verification — terminal state is FAILED
        ctx.state = AgentState.FAILED

    # ── MEMORY ───────────────────────────────────────────────────────
    async def memory_update(self, ctx: AgentRunContext, req: Any, current_user: str) -> None:
        """Background: Memory Updater role extracts learnings from a terminal run.

        Terminal-state learning policy (review §4.2 L6): DONE runs record what
        worked; FAILED / NEEDS_REVIEW runs record what went wrong — failure is
        exactly the experience worth remembering. The run status stored with
        the experience is the *actual* terminal state, never a blanket "ok".
        """
        d = self.deps
        terminal = ctx.state.value if ctx.state in AGENT_TERMINAL_STATES else "UNKNOWN"
        outcome_hint = (
            "The task completed successfully."
            if ctx.state == AgentState.DONE
            else (
                f"The task ended as {terminal} — extract what went wrong and "
                "what to do differently next time, not a success story."
            )
        )
        context = (
            f"{d.memory_updater_prompt}\n\n"
            f"Task: {req.message}\n"
            f"Terminal status: {terminal}. {outcome_hint}\n\n"
            f"Last 5 transcript steps:\n{json.dumps(ctx.transcript[-5:], ensure_ascii=False)}"
        )
        try:
            raw = await d.generate(
                message="Extract learnings from this completed task.",
                context=context, max_tokens=self.phase_budgets.memory_tokens, temperature=0.1,
            )
            mem = extract_action(str(raw))
            kept_learnings = filter_learnings(mem.get("learnings") or [])
            if mem.get("save_to_knowledge") and kept_learnings:
                learnings = "\n".join(kept_learnings)
                status_label = {
                    AgentState.DONE: "ok",
                    AgentState.NEEDS_REVIEW: "needs_review",
                    AgentState.FAILED: "failed",
                }.get(ctx.state, "unknown")
                if d.brain_memory is not None:
                    # This runtime is LLM-driven — its learnings are real
                    # experiences and enter the brain with provenance.
                    d.brain_memory.record_experience(
                        f"Agent: {req.message[:60]}",
                        learnings,
                        run={
                            "mode": "llm",
                            "status": status_label,
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
        self._emit_step(ctx, "terminal", "state", state=ctx.state.value)
