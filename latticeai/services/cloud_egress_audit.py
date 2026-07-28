"""Audit record for the one path where knowledge leaves the machine.

Mode *changes* were audited from 10.1.0; the send itself was not. For a product
whose central claim is that nothing leaves without consent, the absence of a
record on the only egress path meant the claim could not be checked after the
fact — not by the user, not by anyone reviewing an incident.

What is recorded is deliberately about *shape*, never content: which node ids
went, how many, the token estimate, the provider and model, the resolved mode,
and the scope. The compact payload text is not recorded — writing the outbound
knowledge into a second on-disk location to prove we were careful with it would
be its own leak.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_AUDIT: Optional[Callable[..., None]] = None


def bind_egress_audit(audit: Optional[Callable[..., None]]) -> None:
    """Install the process-wide audit sink (called from runtime wiring)."""
    global _AUDIT
    with _LOCK:
        _AUDIT = audit


def _sink() -> Optional[Callable[..., None]]:
    with _LOCK:
        return _AUDIT


def record_cloud_egress(
    *,
    node_ids: List[str],
    token_estimate: int,
    mode: str,
    provider: str,
    model: Optional[str] = None,
    user_email: Optional[str] = None,
    workspace_id: Optional[str] = None,
    outcome: str = "sent",
    detail: Optional[str] = None,
) -> Dict[str, Any]:
    """Record one cloud send (or refusal) and return the event.

    Returns the event even when no sink is bound so callers and tests can
    assert on it. A failing audit sink never blocks or breaks the turn — but it
    is logged at warning, because a silent audit failure is the same as no
    audit at all.
    """
    event: Dict[str, Any] = {
        "event": "cloud_egress",
        "outcome": outcome,
        "mode": mode,
        "provider": provider,
        "model": model,
        "node_ids": list(node_ids),
        "node_count": len(node_ids),
        "token_estimate": int(token_estimate),
        "user_email": user_email,
        "workspace_id": workspace_id,
    }
    if detail:
        event["detail"] = detail

    sink = _sink()
    if sink is None:
        # Not an error: unit tests and headless helpers run without wiring.
        logger.debug("cloud egress (no audit sink bound): %s", event)
        return event
    try:
        sink(**event)
    except Exception:  # noqa: BLE001
        logger.warning("cloud egress audit sink failed; the send still happened", exc_info=True)
    return event


__all__ = ["bind_egress_audit", "record_cloud_egress"]
