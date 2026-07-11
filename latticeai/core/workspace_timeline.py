"""Timeline and audit logic extracted from WorkspaceOSStore for smaller class.

Composed via TimelineManager for cleaner architecture.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from .timeutil import now_iso as _now
from .workspace_os_utils import _listify, _parse_iso


def _audit_category(event: Dict[str, Any]) -> str:
    raw = str(event.get("event_type") or "").lower()
    if "model" in raw or "chat" in raw:
        return "model_usage"
    if "file" in raw or "document" in raw or "local" in raw:
        return "file_access"
    if "folder" in raw or "permission" in raw:
        return "folder_approval"
    if "sensitive" in raw or "secret" in raw:
        return "sensitive_data"
    if "admin" in raw or "user" in raw or "sso" in raw:
        return "admin_action"
    if "security" in raw or "auth" in raw or "login" in raw:
        return "security_event"
    return "workspace_event"


class WorkspaceTimeline:
    """Composable timeline/audit manager."""

    def __init__(self, store: Any):
        self._store = store

    def record_timeline_event(self, area: str, event_type: str, payload: Dict[str, Any], workspace_id: Optional[str] = None) -> Dict[str, Any]:
        state = self._store.load_state()
        events = state.setdefault("timeline", [])
        entry = {
            "area": area,
            "event_type": event_type,
            "timestamp": _now(),
            "payload": payload or {},
        }
        if workspace_id:
            entry["workspace_id"] = workspace_id
        events.append(entry)
        # keep bounded
        if len(events) > 10000:
            state["timeline"] = events[-8000:]
        self._store.save_state(state)
        # optional realtime
        if getattr(self._store, "event_sink", None):
            try:
                self._store.event_sink({**entry, "type": "timeline"})
            except Exception:
                pass
        return entry

    def filter_audit_timeline(
        self,
        audit_events: Iterable[Dict[str, Any]],
        *,
        user: Optional[str] = None,
        event_type: Optional[str] = None,
        model: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        since_dt = _parse_iso(since)
        until_dt = _parse_iso(until)
        filtered = []
        for event in audit_events:
            stamp = _parse_iso(event.get("timestamp"))
            if user and user.lower() not in str(event.get("user_email") or event.get("user") or "").lower():
                continue
            if event_type and event_type.lower() not in str(event.get("event_type") or "").lower():
                continue
            if model and model.lower() not in str(event).lower():
                continue
            if since_dt and stamp and stamp < since_dt:
                continue
            if until_dt and stamp and stamp > until_dt:
                continue
            filtered.append({
                **event,
                "category": _audit_category(event),
            })
        filtered.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
        return {"events": filtered[: max(1, min(limit, 1000))], "total": len(filtered)}

    def timeline(self, audit_events: Optional[Iterable[Dict[str, Any]]] = None, limit: int = 100, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        state = self._store.load_state()
        events: List[Dict[str, Any]] = []
        events.extend(self._store._scoped(_listify(state.get("timeline")), workspace_id))
        for snapshot in self._store._scoped(_listify(state.get("snapshots")), workspace_id):
            events.append({"area": "snapshot", "event_type": "snapshot", "timestamp": snapshot.get("created_at"), "workspace_id": self._store._record_workspace(snapshot), "payload": snapshot})
        for trace in self._store._scoped(_listify(state.get("traces")), workspace_id):
            events.append({"area": "graph", "event_type": "answer_trace", "timestamp": trace.get("created_at"), "workspace_id": self._store._record_workspace(trace), "payload": trace})
        for run in self._store._scoped(_listify(state.get("agent_runs")), workspace_id):
            events.append({"area": "agent", "event_type": "agent_run", "timestamp": run.get("created_at"), "workspace_id": self._store._record_workspace(run), "payload": run})
        for workflow in self._store._scoped(_listify(state.get("workflows")), workspace_id):
            events.append({"area": "workflow", "event_type": "workflow", "timestamp": workflow.get("created_at"), "workspace_id": self._store._record_workspace(workflow), "payload": workflow})
        for audit in audit_events or []:
            events.append({"area": "audit", "event_type": audit.get("event_type") or "audit", "timestamp": audit.get("timestamp"), "payload": audit})
        events.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
        return {"events": events[: max(1, min(limit, 500))]}
