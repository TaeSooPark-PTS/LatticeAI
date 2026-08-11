"""PLAN and APPROVAL — deciding what to do, and whether it may be done.

The planner turns the request into a structured plan; the approval gate then
answers one question about that plan under the run's autonomy dial: does a step
in it need a human to say yes? :meth:`approval_requirements` is the read-only
half of exactly the predicate :meth:`approve` enforces, so the HTTP layer can
pause a run instead of failing it closed without ever weakening the gate.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from latticeai.core.agent_helpers import extract_action_details, normalize_plan
from latticeai.core.agent_permission import non_auto_plan_steps
from latticeai.core.agent_state import AgentState
from latticeai.core.permission_mode import plan_requires_approval

from ._contract import AgentCore as _Core
from .context import AgentRunContext


class _PlanningMixin(_Core):
    """The PLAN and APPROVAL phases of :class:`SingleAgentRuntime`."""

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
