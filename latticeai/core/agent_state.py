"""The agent loop's state vocabulary.

Its own module because both sides of the runtime need it and neither can own
it: :mod:`latticeai.core.agent` holds the state machine, and
:mod:`latticeai.core.agent_helpers` holds the pure functions the machine calls.
If the enum lived in ``agent``, the helpers would have to import the module
that imports them — so they would fall back to writing ``"EXECUTING"`` as a
bare string, and a rename of the enum value would silently stop matching.

Both names stay importable from :mod:`latticeai.core.agent`, which is where
every existing caller expects them.
"""

from __future__ import annotations

from enum import Enum
from typing import FrozenSet


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


__all__ = ["AgentState", "AGENT_TERMINAL_STATES"]
