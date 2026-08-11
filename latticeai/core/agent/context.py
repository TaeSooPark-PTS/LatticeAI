"""The mutable state one agent run carries from phase to phase.

Every phase reads and writes this object and nothing else: the loop itself is
stateless, so a run is exactly what its :class:`AgentRunContext` says it is.
``__slots__`` is deliberate — a typo'd attribute is an error, not a silently
ignored write that a later phase never sees.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from latticeai.core.agent_state import AgentState
from latticeai.core.agent_trace import LoopTrace


class AgentRunContext:
    """Mutable state carrier passed through all agent phases."""
    __slots__ = ("state", "plan", "transcript", "retry_count",
                 "state_history", "corrections", "final_message", "rollback_log",
                 "executing_model", "reviewing_model", "approved_by_human", "trace",
                 "on_step", "project_context", "permission_mode",
                 "self_model_summary")

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
        # Self-Model summary resolved once per run (v11.2.0). The executor
        # prompt is rebuilt on every turn of the loop; reading the profile
        # once and reusing it keeps a graph query off that hot path — and
        # keeps every turn of one run describing the same person. ``None``
        # means "not resolved yet"; ``""`` is a resolved, empty profile.
        self.self_model_summary: Optional[str] = None
