"""Local Computer Memory: the opt-in that lets the Brain watch this machine.

Extracted from ``WorkspaceOSStore``. Off by default and impossible to enable
without a recorded consent — the check lives here so there is one place to read
when asking "what did this machine agree to observe".
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .timeutil import now_iso as _now
from .workspace_os_utils import _json_hash

__all__ = ["WorkspaceComputerMemory", "DEFAULT_COMPUTER_MEMORY_SCOPES"]

#: Folders the feature offers to watch when the person names none. Chosen to be
#: recognisable rather than exhaustive: the point of the list is that a person
#: reading the consent dialog knows what it means.
DEFAULT_COMPUTER_MEMORY_SCOPES = ("Downloads", "Documents", "Repositories")


class WorkspaceComputerMemory:
    """Consent state and activity log for watching the local computer."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def configure(
        self,
        *,
        enabled: bool,
        approved_by: Optional[str],
        consent: Optional[Dict[str, Any]] = None,
        scopes: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        consent = consent or {}
        if enabled and not consent.get("approved"):
            raise PermissionError("Local Computer Memory requires explicit approval.")
        state = self.store.load_state()
        config = state.setdefault("computer_memory", {})
        config.update({
            "enabled": bool(enabled),
            "approved": bool(enabled),
            "approved_at": _now() if enabled else config.get("approved_at"),
            "approved_by": approved_by if enabled else config.get("approved_by"),
            "scopes": scopes or config.get("scopes") or list(DEFAULT_COMPUTER_MEMORY_SCOPES),
            "consent": consent,
        })
        state.setdefault("feature_flags", {})["local_computer_memory"] = bool(enabled)
        self.store.save_state(state)
        self.store.record_timeline_event(
            "memory",
            "computer_memory_configured",
            {"enabled": bool(enabled), "approved_by": approved_by},
        )
        return config

    def record_activity(self, activity: Dict[str, Any], graph: Any = None) -> Dict[str, Any]:
        state = self.store.load_state()
        config = state.setdefault("computer_memory", {})
        if not config.get("enabled"):
            return {"status": "ignored", "reason": "local computer memory is disabled"}
        record = {
            "id": f"activity-{_json_hash([activity, _now()])[:16]}",
            "timestamp": _now(),
            **activity,
        }
        config.setdefault("activities", []).append(record)
        if graph is not None:
            # A graph that refuses the event must not lose the activity: the
            # record is kept either way and carries the reason it did not land.
            try:
                graph.ingest_event(
                    "ComputerActivity",
                    str(activity.get("summary") or activity.get("path") or "Computer activity")[:120],
                    source="workspace_os",
                    metadata=record,
                )
            except Exception as exc:
                record["graph_error"] = str(exc)
        self.store.save_state(state)
        self.store.record_timeline_event("memory", "computer_activity", {"activity_id": record["id"]})
        return {"status": "ok", "activity": record}
