"""The seam the four phase mixins share.

``SingleAgentRuntime`` is assembled from the planning, execution, verification
and recovery mixins. Each of them reads state it does not own and calls back
into helpers defined on the runtime itself — the point of the split is that
"how a plan is approved" and "how a tool call is gated" stop sharing a
1,465-line file, not that they stop sharing ``self``.

Typing-only, exactly like :mod:`lattice_brain.ingestion._contract`: the
declarations below are never the implementation, so the MRO and every method
resolution stay byte-for-byte what the single-file class had. Adding a
cross-mixin call without declaring it here is a type error.
"""

from __future__ import annotations

from typing import Any, FrozenSet, Optional

from latticeai.core.agent_helpers import PhaseBudgets, TranscriptBudget
from latticeai.core.agent_profiles import AgentProfile
from latticeai.core.permission_mode import PermissionMode

from .context import AgentRunContext
from .deps import AgentDeps


class AgentCore:
    """What any phase mixin may assume about ``self``.

    Never instantiated directly — see the module docstring. Members are
    declared, not implemented: the implementation lives in ``runtime.py`` or in
    whichever mixin owns it.
    """

    # ── State owned by SingleAgentRuntime.__init__ ───────────────────────────
    deps: AgentDeps
    _env_phase_budgets: Optional[PhaseBudgets]
    _env_transcript_budget: Optional[TranscriptBudget]

    # ── runtime.py: budgets, resolved from deps or the environment once ──────
    @property
    def phase_budgets(self) -> PhaseBudgets:
        raise NotImplementedError

    @property
    def transcript_budget(self) -> TranscriptBudget:
        raise NotImplementedError

    # ── runtime.py: the autonomy dial every gate in one run agrees on ────────
    def resolve_permission_mode(
        self,
        ctx: Optional[AgentRunContext] = None,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> PermissionMode:
        raise NotImplementedError

    # ── runtime.py: change-governance classification helpers ─────────────────
    def _governed_tools(self) -> FrozenSet[str]:
        raise NotImplementedError

    def _governed_path_exists(self, name: str, path: str) -> bool:
        raise NotImplementedError

    # ── runtime.py: how hard the loop works to keep this model on contract ───
    def profile_for(self, model_id: Optional[str]) -> AgentProfile:
        raise NotImplementedError

    # ── runtime.py: advisory telemetry every phase emits ─────────────────────
    def _emit_step(
        self, ctx: AgentRunContext, phase: str, event: str, **details: Any
    ) -> None:
        raise NotImplementedError

    # ── runtime.py: project-session prompt block, or "" for a standalone run ─
    @staticmethod
    def _project_block(ctx: AgentRunContext) -> str:
        raise NotImplementedError
