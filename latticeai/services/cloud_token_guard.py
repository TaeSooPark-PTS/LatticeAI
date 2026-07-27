"""Token / cost guardrails for hybrid cloud turns (Phase 2).

Keeps cloud usage bounded:

* per-turn token estimate must stay under ``max_tokens_per_turn``
* optional session budget (in-memory, process-local) tracks cumulative usage

These are soft product guardrails, not hard billing meters. Real provider
usage is still authoritative when the cloud returns usage fields.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


@dataclass
class TokenBudget:
    max_tokens_per_turn: int = field(
        default_factory=lambda: _env_int("LATTICEAI_CLOUD_MAX_TOKENS_PER_TURN", 2500)
    )
    max_tokens_per_session: int = field(
        default_factory=lambda: _env_int("LATTICEAI_CLOUD_MAX_TOKENS_PER_SESSION", 50000)
    )
    session_used: int = 0

    def check_turn(self, estimated_input_tokens: int) -> Optional[str]:
        """Return a refusal reason, or None if the turn is allowed."""
        if estimated_input_tokens > self.max_tokens_per_turn:
            return (
                f"estimated input tokens {estimated_input_tokens} exceed "
                f"per-turn limit {self.max_tokens_per_turn}"
            )
        if self.session_used + estimated_input_tokens > self.max_tokens_per_session:
            return (
                f"session budget would exceed {self.max_tokens_per_session} "
                f"(used={self.session_used}, turn={estimated_input_tokens})"
            )
        return None

    def record(self, tokens: int) -> None:
        self.session_used += max(0, int(tokens or 0))

    def snapshot(self) -> Dict[str, Any]:
        return {
            "max_tokens_per_turn": self.max_tokens_per_turn,
            "max_tokens_per_session": self.max_tokens_per_session,
            "session_used": self.session_used,
            "session_remaining": max(0, self.max_tokens_per_session - self.session_used),
        }


_LOCK = threading.Lock()
_BUDGETS: Dict[str, TokenBudget] = {}


def budget_for(scope_key: str) -> TokenBudget:
    """Process-local budget keyed by user|workspace."""
    key = str(scope_key or "global")
    with _LOCK:
        if key not in _BUDGETS:
            _BUDGETS[key] = TokenBudget()
        return _BUDGETS[key]


def reset_budget(scope_key: str) -> None:
    with _LOCK:
        _BUDGETS.pop(str(scope_key or "global"), None)


__all__ = ["TokenBudget", "budget_for", "reset_budget"]
