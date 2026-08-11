"""The runtime itself: shared state, the budgets, and the drive loop.

:class:`SingleAgentRuntime` is assembled from the four phase mixins. What lives
*here* is what all four need — the injected ports, the per-phase token and
transcript budgets, the autonomy dial, the step observer, the boundary/config
contracts — plus :meth:`run_to_completion`, which drives EXECUTING → VERIFYING
→ ROLLBACK until a terminal state.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional

from lattice_brain.runtime.contracts import (
    runtime_boundary_contract,
    single_agent_contract,
)
from latticeai.core.agent_helpers import PhaseBudgets, TranscriptBudget
from latticeai.core.agent_permission import resolve_deps_mode
from latticeai.core.agent_profiles import AgentProfile, profile_for_model
from latticeai.core.agent_state import AGENT_TERMINAL_STATES, AgentState
from latticeai.core.permission_mode import PermissionMode
from latticeai.tools import document_output_target

from .context import AgentRunContext
from .deps import AgentDeps
from .execution import _ExecutionMixin
from .planning import _PlanningMixin
from .recovery import _RecoveryMixin
from .verification import _VerificationMixin


class SingleAgentRuntime(
    _PlanningMixin, _ExecutionMixin, _VerificationMixin, _RecoveryMixin
):
    """Drives the agent state machine over injected :class:`AgentDeps`.

    The four mixins define disjoint method sets, so resolution order changes
    nothing at runtime: this class exposes exactly the methods it exposed when
    all four phases lived in one 1,465-line module.
    """

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

    def _governed_path_exists(self, name: str, path: str) -> bool:
        """Does this tool call's *real* target already exist?

        The document creators sanitize ``filename`` into their own output
        directory, so the raw argument is resolved through
        :func:`document_output_target` first — checking it verbatim would
        inspect a path nothing ever writes and the fail-closed overwrite guard
        would never fire. Workspace-relative paths resolve under
        ``deps.agent_root``; absolute paths (home-sandbox writes) are honored
        as-is. Never raises: governance must not be able to crash the loop, and
        an unresolvable path degrades to "new file", which the remaining gates
        still cover.
        """
        try:
            candidate = Path(document_output_target(name, path) or path)
            if not candidate.is_absolute():
                candidate = Path(self.deps.agent_root) / candidate
            return candidate.exists()
        except Exception:  # noqa: BLE001 — classification is best-effort
            return False

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
