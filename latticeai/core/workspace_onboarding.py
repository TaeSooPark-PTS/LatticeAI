"""First-run onboarding progress.

Extracted from ``WorkspaceOSStore``. Owns the ``onboarding`` branch of the
state document: which named step the person is on, what each step recorded, and
whether the whole run is finished.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .timeutil import now_iso as _now
from .workspace_os_constants import ONBOARDING_STEPS

__all__ = ["WorkspaceOnboarding", "ONBOARDING_STATUSES"]

#: The statuses a single step may hold. Anything else is a caller bug, not a
#: value to store — an unknown status would silently stall the flow, because
#: `current_step` only advances on `complete`/`skipped`.
ONBOARDING_STATUSES = frozenset({"pending", "running", "complete", "failed", "skipped"})


class WorkspaceOnboarding:
    """Tracks the named first-run steps and where the person stopped."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def status(
        self,
        users: Optional[Dict[str, Any]] = None,
        graph_stats: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        state = self.store.load_state()
        users = users or {}
        admins = [
            email for email, user in users.items()
            if isinstance(user, dict) and user.get("role") == "admin"
        ]
        onboarding = state.get("onboarding") or {}
        steps = onboarding.get("steps") or {}
        return {
            **onboarding,
            "steps": [steps.get(step, {"id": step, "status": "pending"}) for step in ONBOARDING_STEPS],
            "has_account": bool(users),
            "has_admin": bool(admins) or bool(users),
            "graph_ready": bool(graph_stats and not graph_stats.get("disabled")),
            "required_steps": list(ONBOARDING_STEPS),
        }

    def update_step(
        self,
        step: str,
        *,
        status: str = "complete",
        data: Optional[Dict[str, Any]] = None,
        error: str = "",
        user_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        if step not in ONBOARDING_STEPS:
            raise ValueError(f"unknown onboarding step: {step}")
        if status not in ONBOARDING_STATUSES:
            raise ValueError(f"unknown onboarding status: {status}")
        state = self.store.load_state()
        onboarding = state.setdefault("onboarding", {})
        steps = onboarding.setdefault("steps", {})
        record = steps.setdefault(step, {"id": step})
        record.update({
            "id": step,
            "status": status,
            "data": data or record.get("data") or {},
            "error": error,
            "updated_at": _now(),
            "user_email": user_email,
        })
        if status in {"complete", "skipped"}:
            index = ONBOARDING_STEPS.index(step)
            if step == "complete":
                onboarding["completed"] = True
                onboarding["completed_at"] = _now()
                onboarding["current_step"] = "complete"
            elif index + 1 < len(ONBOARDING_STEPS):
                onboarding["current_step"] = ONBOARDING_STEPS[index + 1]
        elif status == "failed":
            onboarding["current_step"] = step
        self.store.save_state(state)
        self.store.record_timeline_event(
            "workspace", "onboarding_step", {"step": step, "status": status}
        )
        return self.status()

    def complete(
        self,
        data: Optional[Dict[str, Any]] = None,
        user_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        for step in ONBOARDING_STEPS:
            self.update_step(
                step,
                status="complete",
                data=data if step == "complete" else None,
                user_email=user_email,
            )
        return self.status()
