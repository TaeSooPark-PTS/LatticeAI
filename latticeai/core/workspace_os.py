"""Workspace OS persistence and orchestration primitives.

This module keeps the 1.0 Workspace OS surface intentionally local-first:
state is stored as JSON under the configured LatticeAI data directory, graph
operations are additive, and snapshots are immutable files that can be
exported or compared without mutating the live knowledge graph.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import zipfile
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from lattice_brain.runtime.contracts import realtime_event_contract, run_record_contract, workflow_run_contract

# Extracted pure helpers (keeps this module smaller and focused on the store).
from .workspace_os_utils import (
    _atomic_write_json,
    _deep_merge,
    _json_hash,
    _listify,
    _now,
    _parse_iso,
    _safe_slug,
    _snapshot_graph_import_payload,
    remove_skill_directory,
)

__all__ = [
    "WORKSPACE_OS_VERSION",
    "WORKSPACE_TYPES",
    "DEFAULT_WORKSPACE_ID",
    "WORKSPACE_ROLES",
    "WORKSPACE_PERMISSIONS",
    "ROLE_PERMISSIONS",
    "WORKSPACE_AREAS",
    "ONBOARDING_STEPS",
    "MEMORY_KINDS",
    "EXECUTION_EVENT_TYPES",
    "WorkspaceOSStore",
    "remove_skill_directory",
]

WORKSPACE_OS_VERSION = "8.1.0"

# Workspace types separate single-user Personal workspaces from shared
# Organization workspaces. Both keep the same local-first JSON store; the type
# only changes how membership and permissions are evaluated.
WORKSPACE_TYPES = ("personal", "organization")

DEFAULT_WORKSPACE_ID = "personal"

# Role hierarchy for Organization workspaces. Personal workspaces always grant
# their single local user the owner role.
WORKSPACE_ROLES = ("owner", "admin", "member", "viewer")

# Capability-style permissions. Kept intentionally small so Enterprise editions
# can layer advanced RBAC/ABAC on top via the enterprise seam without changing
# these community defaults.
WORKSPACE_PERMISSIONS = ("read", "write", "manage_members", "manage_workspace")

ROLE_PERMISSIONS: Dict[str, set] = {
    "owner": {"read", "write", "manage_members", "manage_workspace"},
    "admin": {"read", "write", "manage_members", "manage_workspace"},
    "member": {"read", "write"},
    "viewer": {"read"},
}

WORKSPACE_AREAS = [
    "graph",
    "snapshot",
    "memory",
    "agent",
    "workflow",
    "plugins",
    "skills",
    "marketplace",
    "timeline",
]

ONBOARDING_STEPS = [
    "account",
    "admin",
    "hardware",
    "model_recommendation",
    "model_install",
    "model_connection",
    "folder_connection",
    "first_question",
    "complete",
]

MEMORY_KINDS = {
    "short_term",
    "workspace",
    "preferences",
    "decisions",
    "working_style",
    "frequently_used_tools",
    "long_term",
}

EXECUTION_EVENT_TYPES = {
    "agent_started",
    "handoff_created",
    "handoff_accepted",
    "handoff_completed",
    "review_requested",
    "review_approved",
    "review_rejected",
    "retry_requested",
    "workflow_started",
    "workflow_completed",
    "plugin_started",
    "plugin_completed",
    "execution_failed",
    "execution_cancelled",
    "execution_interrupted",
}

RUN_ACTIVE_STATUSES = {"queued", "running", "in_progress", "retrying", "cancelling"}
RUN_TERMINAL_STATUSES = {"ok", "retried_ok", "failed", "rejected", "cancelled", "interrupted", "partial"}

DEFAULT_AGENTS = [
    {
        "id": "agent:planner",
        "name": "Planner",
        "role": "Breaks workspace goals into executable plans.",
        "status": "available",
        "relationships": ["agent:executor", "agent:reviewer"],
    },
    {
        "id": "agent:executor",
        "name": "Executor",
        "role": "Runs approved tool and code workflows.",
        "status": "available",
        "relationships": ["agent:planner", "agent:reviewer"],
    },
    {
        "id": "agent:reviewer",
        "name": "Reviewer",
        "role": "Checks outputs, tests, and regressions.",
        "status": "available",
        "relationships": ["agent:executor", "agent:release"],
    },
    {
        "id": "agent:researcher",
        "name": "Researcher",
        "role": "Finds and curates relevant workspace knowledge.",
        "status": "available",
        "relationships": ["agent:planner"],
    },
    {
        "id": "agent:release",
        "name": "Release Agent",
        "role": "Coordinates versioning, packaging, and release checks.",
        "status": "available",
        "relationships": ["agent:reviewer"],
    },
]


class WorkspaceOSStore:
    """Local-first state store for Workspace OS APIs."""

    def __init__(self, data_dir: Path | str, *, event_sink: Optional[Callable[[Dict[str, Any]], Any]] = None):
        self.data_dir = Path(data_dir).expanduser()
        self.state_path = self.data_dir / "workspace_os.json"
        self.sqlite_path = self.data_dir / "knowledge_graph.sqlite"
        self.snapshots_dir = self.data_dir / "workspace_snapshots"
        self.exports_dir = self.data_dir / "workspace_exports"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        # Optional realtime hook: fired on every timeline event so the Realtime
        # bus receives all workspace activity without per-call wiring.
        # Defaults to None → zero behavior change for existing callers/tests.
        self.event_sink = event_sink

    def _connect_state_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.sqlite_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS workspace_os_state ("
            "id TEXT PRIMARY KEY, state_json TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS workspace_os_meta ("
            "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        return conn

    def _load_sqlite_state(self) -> Optional[Dict[str, Any]]:
        try:
            with self._connect_state_db() as conn:
                row = conn.execute(
                    "SELECT state_json FROM workspace_os_state WHERE id='current'"
                ).fetchone()
            if not row:
                return None
            data = json.loads(row[0])
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _save_sqlite_state(self, state: Dict[str, Any]) -> None:
        payload = json.dumps(state, ensure_ascii=False)
        with self._connect_state_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO workspace_os_state(id, state_json, updated_at) VALUES('current', ?, ?)",
                (payload, state.get("updated_at") or _now()),
            )

    def _import_json_state_once(self, default: Dict[str, Any]) -> Dict[str, Any]:
        if not self.state_path.exists():
            return default
        try:
            loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                return default
        except Exception:
            return default
        try:
            backup = self.state_path.with_name(
                f"{self.state_path.name}.pre-sqlite.{_now().replace(':', '-')}.json"
            )
            if not any(self.state_path.parent.glob(f"{self.state_path.name}.pre-sqlite.*.json")):
                shutil.copy2(self.state_path, backup)
        except Exception:
            pass
        return _deep_merge(default, loaded)

    @staticmethod
    def _new_workspace_record(
        *,
        workspace_id: str,
        name: str,
        workspace_type: str,
        owner_user_id: Optional[str],
        settings: Optional[Dict[str, Any]] = None,
        members: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        if workspace_type not in WORKSPACE_TYPES:
            raise ValueError(f"unknown workspace type: {workspace_type}")
        now = _now()
        member_list = list(members or [])
        if owner_user_id and not any(m.get("user_id") == owner_user_id for m in member_list):
            member_list.insert(0, {"user_id": owner_user_id, "role": "owner", "added_at": now})
        return {
            "workspace_id": workspace_id,
            "id": workspace_id,
            "name": name,
            "type": workspace_type,
            "owner_user_id": owner_user_id,
            "members": member_list,
            "roles": {role: sorted(perms) for role, perms in ROLE_PERMISSIONS.items()},
            "status": "active",
            "areas": list(WORKSPACE_AREAS),
            "settings": settings or {},
            "created_at": now,
            "updated_at": now,
        }

    def _migrate_workspaces(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Non-destructive upgrade of legacy workspace entries to the v1.1 model.

        Existing 1.0.x state files stored minimal ``{id,name,type,areas}`` dicts.
        This backfills membership/role/timestamp fields without dropping data and
        guarantees the default Personal workspace always exists.
        """
        workspaces = state.get("workspaces")
        if not isinstance(workspaces, dict):
            workspaces = {}
        migrated: Dict[str, Any] = {}
        for ws_id, ws in workspaces.items():
            if not isinstance(ws, dict):
                continue
            ws_type = ws.get("type") if ws.get("type") in WORKSPACE_TYPES else "organization"
            if ws_id == DEFAULT_WORKSPACE_ID:
                ws_type = "personal"
            base = self._new_workspace_record(
                workspace_id=ws_id,
                name=ws.get("name") or ws_id,
                workspace_type=ws_type,
                owner_user_id=ws.get("owner_user_id"),
                settings=ws.get("settings") or {},
                members=ws.get("members") if isinstance(ws.get("members"), list) else None,
            )
            # Preserve any pre-existing timestamps / status from the loaded record.
            base["created_at"] = ws.get("created_at") or base["created_at"]
            base["updated_at"] = ws.get("updated_at") or base["updated_at"]
            base["status"] = ws.get("status") or base["status"]
            migrated[ws_id] = base
        if DEFAULT_WORKSPACE_ID not in migrated:
            migrated[DEFAULT_WORKSPACE_ID] = self._new_workspace_record(
                workspace_id=DEFAULT_WORKSPACE_ID,
                name="Personal Workspace",
                workspace_type="personal",
                owner_user_id=None,
            )
        state["workspaces"] = migrated
        active = state.get("active_workspace")
        if active not in migrated:
            state["active_workspace"] = DEFAULT_WORKSPACE_ID
        return state

    def migrate_workspace_identities(self, email_to_id: Dict[str, str]) -> int:
        """Rewrite workspace membership identities from legacy emails to UUIDs.

        The migration is additive and in-place: workspace records, memberships,
        and owner fields keep their shape, only identity string values change.
        """
        if not email_to_id:
            return 0
        normalized = {str(email).strip().lower(): user_id for email, user_id in email_to_id.items() if user_id}
        state = self.load_state()
        changed = 0
        for ws in (state.get("workspaces") or {}).values():
            owner = str(ws.get("owner_user_id") or "").strip().lower()
            if owner in normalized and ws.get("owner_user_id") != normalized[owner]:
                ws["owner_user_id"] = normalized[owner]
                changed += 1
            for member in _listify(ws.get("members")):
                member_id = str(member.get("user_id") or "").strip().lower()
                if member_id in normalized and member.get("user_id") != normalized[member_id]:
                    member["user_id"] = normalized[member_id]
                    member["updated_at"] = _now()
                    changed += 1
            members = _listify(ws.get("members"))
            deduped = []
            seen_members = set()
            for member in members:
                member_id = member.get("user_id")
                if member_id and member_id in seen_members:
                    changed += 1
                    continue
                if member_id:
                    seen_members.add(member_id)
                deduped.append(member)
            if len(deduped) != len(members):
                ws["members"] = deduped
        if changed:
            state["updated_at"] = _now()
            self.save_state(state)
            self.record_timeline_event("workspace", "identity_uuid_migrated", {"records": changed})
        return changed

    def _default_state(self) -> Dict[str, Any]:
        return {
            "version": WORKSPACE_OS_VERSION,
            "identity": "AI Workspace OS",
            "created_at": _now(),
            "updated_at": _now(),
            "active_workspace": DEFAULT_WORKSPACE_ID,
            "workspaces": {
                DEFAULT_WORKSPACE_ID: self._new_workspace_record(
                    workspace_id=DEFAULT_WORKSPACE_ID,
                    name="Personal Workspace",
                    workspace_type="personal",
                    owner_user_id=None,
                ),
            },
            "feature_flags": {
                "workspace_os": True,
                "graph_trace": True,
                "snapshots": True,
                "personal_memory": True,
                "multi_agent_graph": True,
                "workflow_graph": True,
                "skill_marketplace": True,
                "local_computer_memory": False,
                "organization_workspaces": True,
                "enterprise_seam": True,
                "plugin_sdk": True,
                "workflow_designer": True,
                "multi_agent_runtime": True,
                "realtime_collaboration": True,
                "agent_handoff": True,
                "agent_context_packets": True,
                "review_retry_loops": True,
                "timeline_replay": True,
                "agent_memory": True,
                "agent_planning": True,
                "marketplace_foundation": True,
                "realtime_execution_observability": True,
            },
            "onboarding": {
                "completed": False,
                "current_step": "account",
                "steps": {
                    step: {
                        "id": step,
                        "status": "pending",
                        "data": {},
                        "error": "",
                        "updated_at": None,
                    }
                    for step in ONBOARDING_STEPS
                },
            },
            "snapshots": [],
            "traces": [],
            "memories": [],
            "memory_snapshots": [],
            "agents": list(DEFAULT_AGENTS),
            "agent_runs": [],
            "handoffs": [],
            "workflows": [],
            "workflow_runs": [],
            "review_items": [],
            "skill_registry": {},
            "plugin_registry": {},
            "template_registry": {},
            "computer_memory": {
                "enabled": False,
                "approved": False,
                "approved_at": None,
                "approved_by": None,
                "scopes": ["Downloads", "Documents", "Repositories"],
                "activities": [],
                "notice": "Local Computer Memory is OFF by default and requires explicit approval.",
            },
            "timeline": [],
        }

    def load_state(self) -> Dict[str, Any]:
        default = self._default_state()
        loaded = self._load_sqlite_state()
        imported = loaded is None
        if loaded is None:
            loaded = self._import_json_state_once(default)
        state = _deep_merge(default, loaded)
        state["version"] = WORKSPACE_OS_VERSION
        self._migrate_workspaces(state)
        if imported:
            self.save_state(state)
        return state

    def save_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        state["version"] = WORKSPACE_OS_VERSION
        state["updated_at"] = _now()
        self._save_sqlite_state(state)
        _atomic_write_json(self.state_path, state)
        return state

    def record_timeline_event(self, area: str, event_type: str, payload: Dict[str, Any], workspace_id: Optional[str] = None) -> Dict[str, Any]:
        state = self.load_state()
        event = {
            "id": f"timeline-{_json_hash([area, event_type, payload, _now()])[:16]}",
            "area": area,
            "event_type": event_type,
            "timestamp": _now(),
            "workspace_id": self._resolve_scope(workspace_id, state),
            "payload": payload,
        }
        event["contract"] = realtime_event_contract({"seq": event["id"], "received_at": event["timestamp"], **event})
        state.setdefault("timeline", []).append(event)
        self.save_state(state)
        if self.event_sink is not None:
            try:
                self.event_sink(event)
            except Exception:
                # Realtime delivery is best-effort and must never break a write.
                pass
        return event

    def _emit_execution_event(
        self,
        *,
        area: str,
        event_type: str,
        payload: Dict[str, Any],
        workspace_id: Optional[str],
    ) -> None:
        """Best-effort execution observability event for the realtime feed."""
        if event_type not in EXECUTION_EVENT_TYPES:
            return
        try:
            self.record_timeline_event(area, event_type, payload, workspace_id=workspace_id)
        except Exception:
            pass

    def _emit_replayable_timeline_events(
        self,
        *,
        area: str,
        run_id: str,
        timeline: List[Dict[str, Any]],
        workspace_id: Optional[str],
    ) -> None:
        for index, item in enumerate(timeline or []):
            event_type = item.get("event") or item.get("event_type")
            if event_type in EXECUTION_EVENT_TYPES:
                payload = {k: v for k, v in item.items() if k not in {"context_packet"}}
                payload["run_id"] = run_id
                payload["timeline_index"] = index
                self._emit_execution_event(area=area, event_type=event_type, payload=payload, workspace_id=workspace_id)

    def summary(self) -> Dict[str, Any]:
        state = self.load_state()
        return {
            "version": WORKSPACE_OS_VERSION,
            "identity": state.get("identity"),
            "active_workspace": state.get("active_workspace"),
            # The raw workspace registry (with member lists) must not leak to
            # non-members; WorkspaceService.summary() adds a membership-filtered
            # "workspace_registry" instead.
            "workspace_count": len(state.get("workspaces") or {}),
            "navigation": list(WORKSPACE_AREAS),
            "feature_flags": state.get("feature_flags"),
            "updated_at": state.get("updated_at"),
            "counts": {
                "snapshots": len(_listify(state.get("snapshots"))),
                "traces": len(_listify(state.get("traces"))),
                "memories": len(_listify(state.get("memories"))),
                "memory_snapshots": len(_listify(state.get("memory_snapshots"))),
                "agent_runs": len(_listify(state.get("agent_runs"))),
                "handoffs": len(_listify(state.get("handoffs"))),
                "workflows": len(_listify(state.get("workflows"))),
                "workflow_runs": len(_listify(state.get("workflow_runs"))),
                "skills": len(state.get("skill_registry") or {}),
                "plugins": len(state.get("plugin_registry") or {}),
                "templates": len(state.get("template_registry") or {}),
                "timeline": len(_listify(state.get("timeline"))),
            },
            "onboarding": state.get("onboarding"),
            "storage": {
                "state_path": str(self.state_path),
                "snapshots_dir": str(self.snapshots_dir),
                "exports_dir": str(self.exports_dir),
            },
        }

    # ------------------------------------------------------------------
    # Organization workspaces, membership, and roles
    # ------------------------------------------------------------------

    def _active_workspace_id(self, state: Optional[Dict[str, Any]] = None) -> str:
        state = state or self.load_state()
        active = state.get("active_workspace") or DEFAULT_WORKSPACE_ID
        if active not in (state.get("workspaces") or {}):
            return DEFAULT_WORKSPACE_ID
        return active

    def _resolve_scope(self, workspace_id: Optional[str], state: Optional[Dict[str, Any]] = None) -> str:
        """Resolve the workspace a write should be tagged with.

        ``None`` falls back to the active workspace (Personal by default), so
        legacy callers keep writing to the Personal workspace unchanged.
        """
        if workspace_id:
            return str(workspace_id)
        return self._active_workspace_id(state)

    @staticmethod
    def _record_workspace(record: Dict[str, Any]) -> str:
        """Workspace a stored record belongs to (legacy records map to Personal)."""
        return str(record.get("workspace_id") or DEFAULT_WORKSPACE_ID)

    def _scoped(self, records: List[Dict[str, Any]], workspace_id: Optional[str]) -> List[Dict[str, Any]]:
        if not workspace_id:
            return records
        target = str(workspace_id)
        return [item for item in records if self._record_workspace(item) == target]

    def list_workspaces(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        state = self.load_state()
        workspaces = state.get("workspaces") or {}
        items = []
        for ws in workspaces.values():
            if user_id and ws.get("type") == "organization":
                role = self._member_role(ws, user_id)
                if role is None:
                    continue
            items.append(self._workspace_public(ws, user_id))
        items.sort(key=lambda w: (w.get("type") != "personal", w.get("created_at") or ""))
        return {
            "active_workspace": self._active_workspace_id(state),
            "workspaces": items,
            "roles": list(WORKSPACE_ROLES),
            "permissions": {role: sorted(perms) for role, perms in ROLE_PERMISSIONS.items()},
        }

    def _workspace_public(self, ws: Dict[str, Any], user_id: Optional[str] = None) -> Dict[str, Any]:
        return {
            "workspace_id": ws.get("workspace_id") or ws.get("id"),
            "id": ws.get("workspace_id") or ws.get("id"),
            "name": ws.get("name"),
            "type": ws.get("type"),
            "owner_user_id": ws.get("owner_user_id"),
            "status": ws.get("status", "active"),
            "member_count": len(_listify(ws.get("members"))),
            "members": _listify(ws.get("members")),
            "settings": ws.get("settings") or {},
            "created_at": ws.get("created_at"),
            "updated_at": ws.get("updated_at"),
            "your_role": self._member_role(ws, user_id) if user_id else ("owner" if ws.get("type") == "personal" else None),
        }

    def get_workspace(self, workspace_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        state = self.load_state()
        ws = (state.get("workspaces") or {}).get(workspace_id)
        if not ws:
            raise FileNotFoundError(workspace_id)
        return self._workspace_public(ws, user_id)

    def create_organization_workspace(
        self,
        *,
        name: str,
        owner_user_id: Optional[str],
        settings: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not str(name or "").strip():
            raise ValueError("workspace name is required")
        state = self.load_state()
        workspaces = state.setdefault("workspaces", {})
        base = _safe_slug(f"org-{name}")
        workspace_id = base
        suffix = 2
        while workspace_id in workspaces:
            workspace_id = f"{base}-{suffix}"
            suffix += 1
        record = self._new_workspace_record(
            workspace_id=workspace_id,
            name=name.strip(),
            workspace_type="organization",
            owner_user_id=owner_user_id,
            settings=settings or {},
        )
        workspaces[workspace_id] = record
        self.save_state(state)
        self.record_timeline_event("workspace", "workspace_created", {"workspace_id": workspace_id, "type": "organization"})
        return self._workspace_public(record, owner_user_id)

    @staticmethod
    def _member_role(ws: Dict[str, Any], user_id: Optional[str]) -> Optional[str]:
        if ws.get("type") == "personal":
            return "owner"
        owner = ws.get("owner_user_id")
        # Local single-user / no-auth mode: an ownerless org is owned by the
        # local user (who has no identity), so they can manage what they create.
        if not owner and not user_id:
            return "owner"
        if user_id and user_id == owner:
            return "owner"
        for member in _listify(ws.get("members")):
            if member.get("user_id") == user_id:
                return member.get("role")
        return None

    def get_member_role(self, workspace_id: str, user_id: Optional[str]) -> Optional[str]:
        ws = (self.load_state().get("workspaces") or {}).get(workspace_id)
        if not ws:
            raise FileNotFoundError(workspace_id)
        return self._member_role(ws, user_id)

    def has_permission(self, workspace_id: str, user_id: Optional[str], permission: str) -> bool:
        try:
            role = self.get_member_role(workspace_id, user_id)
        except FileNotFoundError:
            return False
        if role is None:
            return False
        return permission in ROLE_PERMISSIONS.get(role, set())

    def _require_permission(self, ws: Dict[str, Any], actor: Optional[str], permission: str) -> None:
        role = self._member_role(ws, actor)
        if role is None or permission not in ROLE_PERMISSIONS.get(role, set()):
            raise PermissionError(
                f"'{actor or 'anonymous'}' lacks '{permission}' on workspace '{ws.get('workspace_id')}'"
            )

    def _load_org(self, state: Dict[str, Any], workspace_id: str) -> Dict[str, Any]:
        ws = (state.get("workspaces") or {}).get(workspace_id)
        if not ws:
            raise FileNotFoundError(workspace_id)
        if ws.get("type") != "organization":
            raise ValueError("operation only valid for organization workspaces")
        return ws

    def update_workspace(
        self,
        workspace_id: str,
        *,
        name: Optional[str] = None,
        settings: Optional[Dict[str, Any]] = None,
        actor: Optional[str] = None,
    ) -> Dict[str, Any]:
        state = self.load_state()
        ws = self._load_org(state, workspace_id)
        self._require_permission(ws, actor, "manage_workspace")
        if name is not None and str(name).strip():
            ws["name"] = str(name).strip()
        if settings is not None:
            ws["settings"] = {**(ws.get("settings") or {}), **settings}
        ws["updated_at"] = _now()
        self.save_state(state)
        self.record_timeline_event("workspace", "workspace_updated", {"workspace_id": workspace_id})
        return self._workspace_public(ws, actor)

    def archive_workspace(self, workspace_id: str, *, actor: Optional[str] = None) -> Dict[str, Any]:
        """Soft-archive an organization workspace. Data is never deleted."""
        state = self.load_state()
        ws = self._load_org(state, workspace_id)
        self._require_permission(ws, actor, "manage_workspace")
        ws["status"] = "archived"
        ws["updated_at"] = _now()
        if state.get("active_workspace") == workspace_id:
            state["active_workspace"] = DEFAULT_WORKSPACE_ID
        self.save_state(state)
        self.record_timeline_event("workspace", "workspace_archived", {"workspace_id": workspace_id})
        return self._workspace_public(ws, actor)

    def add_member(self, workspace_id: str, *, user_id: str, role: str = "member", actor: Optional[str] = None) -> Dict[str, Any]:
        if role not in WORKSPACE_ROLES:
            raise ValueError(f"unknown role: {role}")
        if not str(user_id or "").strip():
            raise ValueError("user_id is required")
        state = self.load_state()
        ws = self._load_org(state, workspace_id)
        self._require_permission(ws, actor, "manage_members")
        members = ws.setdefault("members", [])
        existing = next((m for m in members if m.get("user_id") == user_id), None)
        if existing:
            existing["role"] = role
            existing["updated_at"] = _now()
        else:
            members.append({"user_id": user_id, "role": role, "added_at": _now()})
        ws["updated_at"] = _now()
        self.save_state(state)
        self.record_timeline_event("workspace", "member_added", {"workspace_id": workspace_id, "user_id": user_id, "role": role})
        return self._workspace_public(ws, actor)

    def update_member_role(self, workspace_id: str, *, user_id: str, role: str, actor: Optional[str] = None) -> Dict[str, Any]:
        if role not in WORKSPACE_ROLES:
            raise ValueError(f"unknown role: {role}")
        state = self.load_state()
        ws = self._load_org(state, workspace_id)
        self._require_permission(ws, actor, "manage_members")
        if user_id == ws.get("owner_user_id") and role != "owner":
            raise ValueError("cannot demote the workspace owner")
        member = next((m for m in _listify(ws.get("members")) if m.get("user_id") == user_id), None)
        if not member:
            raise FileNotFoundError(user_id)
        member["role"] = role
        member["updated_at"] = _now()
        ws["updated_at"] = _now()
        self.save_state(state)
        self.record_timeline_event("workspace", "member_role_updated", {"workspace_id": workspace_id, "user_id": user_id, "role": role})
        return self._workspace_public(ws, actor)

    def remove_member(self, workspace_id: str, *, user_id: str, actor: Optional[str] = None) -> Dict[str, Any]:
        state = self.load_state()
        ws = self._load_org(state, workspace_id)
        self._require_permission(ws, actor, "manage_members")
        if user_id == ws.get("owner_user_id"):
            raise ValueError("cannot remove the workspace owner")
        members = _listify(ws.get("members"))
        kept = [m for m in members if m.get("user_id") != user_id]
        if len(kept) == len(members):
            raise FileNotFoundError(user_id)
        ws["members"] = kept
        ws["updated_at"] = _now()
        self.save_state(state)
        self.record_timeline_event("workspace", "member_removed", {"workspace_id": workspace_id, "user_id": user_id})
        return self._workspace_public(ws, actor)

    def set_active_workspace(self, workspace_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        state = self.load_state()
        ws = (state.get("workspaces") or {}).get(workspace_id)
        if not ws:
            raise FileNotFoundError(workspace_id)
        if ws.get("type") == "organization" and self._member_role(ws, user_id) is None:
            raise PermissionError(f"'{user_id or 'anonymous'}' is not a member of '{workspace_id}'")
        state["active_workspace"] = workspace_id
        self.save_state(state)
        self.record_timeline_event("workspace", "workspace_activated", {"workspace_id": workspace_id})
        return self._workspace_public(ws, user_id)

    def workspace_summary(self, workspace_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        state = self.load_state()
        ws = (state.get("workspaces") or {}).get(workspace_id)
        if not ws:
            raise FileNotFoundError(workspace_id)
        public = self._workspace_public(ws, user_id)
        public["counts"] = {
            "snapshots": len(self._scoped(_listify(state.get("snapshots")), workspace_id)),
            "memories": len(self._scoped(_listify(state.get("memories")), workspace_id)),
            "memory_snapshots": len(self._scoped(_listify(state.get("memory_snapshots")), workspace_id)),
            "agent_runs": len(self._scoped(_listify(state.get("agent_runs")), workspace_id)),
            "handoffs": len(self._scoped(_listify(state.get("handoffs")), workspace_id)),
            "workflows": len(self._scoped(_listify(state.get("workflows")), workspace_id)),
            "workflow_runs": len(self._scoped(_listify(state.get("workflow_runs")), workspace_id)),
            "traces": len(self._scoped(_listify(state.get("traces")), workspace_id)),
            "timeline": len(self._scoped(_listify(state.get("timeline")), workspace_id)),
        }
        return public

    # ------------------------------------------------------------------
    # Onboarding
    # ------------------------------------------------------------------

    def onboarding_status(self, users: Optional[Dict[str, Any]] = None, graph_stats: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        state = self.load_state()
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

    def update_onboarding_step(
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
        if status not in {"pending", "running", "complete", "failed", "skipped"}:
            raise ValueError(f"unknown onboarding status: {status}")
        state = self.load_state()
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
        self.save_state(state)
        self.record_timeline_event("workspace", "onboarding_step", {"step": step, "status": status})
        return self.onboarding_status()

    def complete_onboarding(self, data: Optional[Dict[str, Any]] = None, user_email: Optional[str] = None) -> Dict[str, Any]:
        for step in ONBOARDING_STEPS:
            self.update_onboarding_step(step, status="complete", data=data if step == "complete" else None, user_email=user_email)
        return self.onboarding_status()

    # ------------------------------------------------------------------
    # Graph answer traces
    # ------------------------------------------------------------------

    def build_graph_trace(self, question: str, graph: Any, context: str = "", *, limit: int = 8) -> Dict[str, Any]:
        if graph is None:
            return {
                "source_files": [],
                "graph_nodes": [],
                "graph_edges": [],
                "confidence": 0.0,
                "retrieval_metadata": {
                    "query": question,
                    "matched_nodes": 0,
                    "graph_enabled": False,
                    "context_chars": len(context or ""),
                },
            }

        matches: List[Dict[str, Any]] = []
        search_error = ""
        try:
            matches = graph.search(question, limit=limit).get("matches", [])
        except Exception as exc:
            search_error = str(exc)
            matches = []

        source_files: List[Dict[str, Any]] = []
        seen_sources = set()
        for match in matches:
            meta = match.get("metadata") or {}
            source = (
                meta.get("relative_path")
                or meta.get("file_path")
                or meta.get("filename")
                or meta.get("blob_path")
                or meta.get("source")
            )
            if source and source not in seen_sources:
                seen_sources.add(source)
                source_files.append({
                    "source": source,
                    "node_id": match.get("id"),
                    "node_title": match.get("title"),
                    "node_type": match.get("type"),
                    "jump": {
                        "graph": f"/graph?node={match.get('id')}",
                        "source": source,
                    },
                })

        edges: List[Dict[str, Any]] = []
        edge_seen = set()
        for match in matches[:5]:
            node_id = match.get("id")
            if not node_id:
                continue
            try:
                for edge in graph.neighbors(node_id).get("edges", []):
                    key = (edge.get("from"), edge.get("to"), edge.get("type"))
                    if key in edge_seen:
                        continue
                    edge_seen.add(key)
                    edges.append(edge)
                    if len(edges) >= 24:
                        break
            except Exception:
                continue

        if matches:
            confidence = min(0.95, 0.35 + min(len(matches), limit) / max(limit, 1) * 0.45 + (0.10 if edges else 0.0))
        else:
            confidence = 0.05 if context else 0.0

        return {
            "source_files": source_files,
            "graph_nodes": matches,
            "graph_edges": edges,
            "confidence": round(confidence, 4),
            "retrieval_metadata": {
                "query": question,
                "matched_nodes": len(matches),
                "matched_edges": len(edges),
                "graph_enabled": True,
                "context_chars": len(context or ""),
                "search_error": search_error,
            },
        }

    def record_trace(
        self,
        *,
        question: str,
        response: str,
        conversation_id: Optional[str],
        user_email: Optional[str],
        trace: Dict[str, Any],
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        state = self.load_state()
        trace_id = f"trace-{_json_hash([question, response, conversation_id, _now()])[:16]}"
        record = {
            "id": trace_id,
            "question": question,
            "response_preview": str(response or "")[:700],
            "conversation_id": conversation_id,
            "user_email": user_email,
            "workspace_id": self._resolve_scope(workspace_id, state),
            "created_at": _now(),
            **trace,
        }
        state.setdefault("traces", []).append(record)
        self.save_state(state)
        self.record_timeline_event("graph", "answer_trace", {"trace_id": trace_id, "conversation_id": conversation_id})
        return record

    def list_traces(self, conversation_id: Optional[str] = None, limit: int = 50, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        traces = self._scoped(_listify(self.load_state().get("traces")), workspace_id)
        if conversation_id:
            traces = [trace for trace in traces if trace.get("conversation_id") == conversation_id]
        return {"traces": list(reversed(traces[-max(1, min(limit, 200)):]))}

    # ------------------------------------------------------------------
    # Indexing dashboard
    # ------------------------------------------------------------------

    def build_indexing_dashboard(self, graph: Any, watcher_status: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if graph is None:
            return {
                "sources": [],
                "watcher": watcher_status or {"available": False, "active": {}},
                "totals": {"success": 0, "failed": 0, "nodes": 0, "edges": 0},
            }
        stats = graph.stats()
        sources = graph.local_sources().get("sources", [])
        watcher_status = watcher_status or {"available": False, "active": {}}
        active = watcher_status.get("active", {})
        dashboard_sources = []
        total_success = 0
        total_failed = 0
        for source in sources:
            file_status = source.get("file_status") or {}
            success = int(file_status.get("indexed") or 0)
            failed = sum(int(file_status.get(key) or 0) for key in ("failed", "inaccessible", "skipped_empty_text"))
            total_success += success
            total_failed += failed
            watch = active.get(source.get("id")) or {}
            dashboard_sources.append({
                "id": source.get("id"),
                "label": source.get("label"),
                "root_path": source.get("root_path"),
                "status": source.get("status"),
                "watch_enabled": bool(source.get("watch_enabled")),
                "watch_active": source.get("id") in active,
                "watch_status": watch,
                "success_count": success,
                "failure_count": failed,
                "last_run_at": source.get("last_scanned_at") or source.get("updated_at"),
                "file_status": file_status,
                "include_ocr": bool(source.get("include_ocr")),
            })
        return {
            "sources": dashboard_sources,
            "watcher": watcher_status,
            "totals": {
                "success": total_success,
                "failed": total_failed,
                "nodes": sum(int(v or 0) for v in (stats.get("nodes") or {}).values()),
                "edges": sum(int(v or 0) for v in (stats.get("edges") or {}).values()),
                "local_sources": stats.get("local_sources", len(sources)),
            },
            "graph_stats": stats,
        }

    def pause_indexing(self, graph: Any, source_id: str, watcher: Any = None) -> Dict[str, Any]:
        result = graph.set_local_source_watch(source_id, False)
        watch = watcher.stop_source(source_id) if watcher else {"stopped": False, "source_id": source_id}
        self.record_timeline_event("graph", "indexing_paused", {"source_id": source_id})
        return {"status": "ok", "source": result, "watch": watch}

    def resume_indexing(self, graph: Any, source_id: str, watcher: Any = None) -> Dict[str, Any]:
        result = graph.set_local_source_watch(source_id, True)
        watch = {"watching": False, "source_id": source_id}
        source = next((item for item in graph.local_sources().get("sources", []) if item.get("id") == source_id), None)
        if watcher and source:
            watch = watcher.start_source(source)
        self.record_timeline_event("graph", "indexing_resumed", {"source_id": source_id})
        return {"status": "ok", "source": result, "watch": watch}

    def remove_index_source(self, graph: Any, source_id: str, watcher: Any = None) -> Dict[str, Any]:
        if watcher:
            watcher.stop_source(source_id)
        if not hasattr(graph, "remove_local_source"):
            raise ValueError("graph store does not support removing local sources")
        result = graph.remove_local_source(source_id)
        self.record_timeline_event("graph", "indexing_removed", {"source_id": source_id})
        return {"status": "ok", **result}

    # ------------------------------------------------------------------
    # Snapshots, Time Machine, and diffs
    # ------------------------------------------------------------------

    def create_snapshot(
        self,
        *,
        name: str,
        graph: Any,
        history: Iterable[Dict[str, Any]],
        settings: Dict[str, Any],
        models: Dict[str, Any],
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        scope = self._resolve_scope(workspace_id)
        graph_payload = {"nodes": [], "edges": []}
        graph_stats = {}
        local_sources = {"sources": []}
        if graph is not None:
            graph_payload = graph.graph(limit=2000)
            graph_stats = graph.stats()
            local_sources = graph.local_sources()
        chat = list(history or [])
        snapshot_body = {
            "version": WORKSPACE_OS_VERSION,
            "name": name or "Workspace snapshot",
            "created_at": _now(),
            "workspace": scope,
            "workspace_id": scope,
            "graph": graph_payload,
            "graph_stats": graph_stats,
            "chat": chat,
            "settings": settings,
            "indexed_folders": local_sources.get("sources", []),
            "models": models,
        }
        snapshot_id = f"snapshot-{datetime.now().strftime('%Y%m%d%H%M%S')}-{_json_hash(snapshot_body)[:10]}"
        snapshot_body["id"] = snapshot_id
        path = self.snapshots_dir / f"{snapshot_id}.json"
        _atomic_write_json(path, snapshot_body)

        state = self.load_state()
        meta = {
            "id": snapshot_id,
            "name": snapshot_body["name"],
            "created_at": snapshot_body["created_at"],
            "workspace_id": scope,
            "path": str(path),
            "node_count": len(graph_payload.get("nodes") or []),
            "edge_count": len(graph_payload.get("edges") or []),
            "chat_count": len(chat),
            "model_count": len(models.get("loaded_models") or []),
            "indexed_folder_count": len(local_sources.get("sources") or []),
        }
        state.setdefault("snapshots", []).append(meta)
        self.save_state(state)
        self.record_timeline_event("snapshot", "snapshot_saved", {"snapshot_id": snapshot_id, "name": name})
        return {"snapshot": meta}

    def list_snapshots(self, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        snapshots = self._scoped(_listify(self.load_state().get("snapshots")), workspace_id)
        return {"snapshots": list(reversed(snapshots))}

    def get_snapshot(self, snapshot_id: str) -> Dict[str, Any]:
        path = self.snapshots_dir / f"{_safe_slug(snapshot_id)}.json"
        if not path.exists():
            state = self.load_state()
            meta = next((item for item in _listify(state.get("snapshots")) if item.get("id") == snapshot_id), None)
            if meta:
                path = Path(meta.get("path") or path)
        if not path.exists():
            raise FileNotFoundError(snapshot_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def snapshot_view(self, snapshot_id: str, area: str) -> Dict[str, Any]:
        snapshot = self.get_snapshot(snapshot_id)
        if area == "graph":
            return {"snapshot_id": snapshot_id, "graph": snapshot.get("graph") or {}, "graph_stats": snapshot.get("graph_stats") or {}}
        if area == "chat":
            return {"snapshot_id": snapshot_id, "chat": snapshot.get("chat") or []}
        if area == "decision":
            nodes = (snapshot.get("graph") or {}).get("nodes") or []
            return {"snapshot_id": snapshot_id, "decisions": [node for node in nodes if node.get("type") == "Decision"]}
        return {"snapshot_id": snapshot_id, "snapshot": snapshot}

    def export_snapshot(self, snapshot_id: str) -> Dict[str, Any]:
        snapshot = self.get_snapshot(snapshot_id)
        export_path = self.exports_dir / f"{_safe_slug(snapshot_id)}.zip"
        with zipfile.ZipFile(export_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("snapshot.json", json.dumps(snapshot, ensure_ascii=False, indent=2))
            zf.writestr("graph.json", json.dumps(snapshot.get("graph") or {}, ensure_ascii=False, indent=2))
            zf.writestr("chat.json", json.dumps(snapshot.get("chat") or [], ensure_ascii=False, indent=2))
            zf.writestr("settings.json", json.dumps(snapshot.get("settings") or {}, ensure_ascii=False, indent=2))
            zf.writestr("indexed_folders.json", json.dumps(snapshot.get("indexed_folders") or [], ensure_ascii=False, indent=2))
            zf.writestr("models.json", json.dumps(snapshot.get("models") or {}, ensure_ascii=False, indent=2))
        self.record_timeline_event("snapshot", "snapshot_exported", {"snapshot_id": snapshot_id, "path": str(export_path)})
        return {"snapshot_id": snapshot_id, "export_path": str(export_path), "bytes": export_path.stat().st_size}

    def restore_snapshot(
        self,
        snapshot_id: str,
        *,
        graph: Any,
        workspace_id: Optional[str] = None,
        user_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Restore a snapshot additively, preserving all current user data.

        v4 snapshots are immutable checkpoints. Restoring one must not delete
        newer graph nodes, chat history, memories, workspaces, or settings, so
        this operation imports the snapshot graph in ``merge`` mode and records a
        durable restore event. It is a real restore path for lost/missing graph
        data, with rollback safety because current state remains intact.
        """

        snapshot = self.get_snapshot(snapshot_id)
        scope = self._resolve_scope(workspace_id or snapshot.get("workspace_id"))
        if graph is None or not hasattr(graph, "import_graph_data"):
            raise ValueError("knowledge graph import is required for snapshot restore")
        artifact = _snapshot_graph_import_payload(snapshot.get("graph") or {}, workspace_id=scope)
        import_result = graph.import_graph_data(artifact, mode="merge", dry_run=False)
        restore_id = f"restore-{datetime.now().strftime('%Y%m%d%H%M%S')}-{_json_hash([snapshot_id, scope, user_email, _now()])[:10]}"
        record = {
            "id": restore_id,
            "snapshot_id": snapshot_id,
            "workspace_id": scope,
            "restored_at": _now(),
            "restored_by": user_email,
            "mode": "merge",
            "graph": import_result,
            "settings_preserved": True,
            "chat_preserved": True,
        }
        state = self.load_state()
        state.setdefault("snapshot_restores", []).append(record)
        self.save_state(state)
        self.record_timeline_event(
            "snapshot",
            "snapshot_restored",
            {"snapshot_id": snapshot_id, "restore_id": restore_id, "mode": "merge", "graph": import_result},
            workspace_id=scope,
        )
        return {"restored": True, "restore": record}

    def compare_snapshots(self, before_id: str, after_id: str) -> Dict[str, Any]:
        before = self.get_snapshot(before_id)
        after = self.get_snapshot(after_id)
        before_nodes = {node.get("id"): node for node in (before.get("graph") or {}).get("nodes") or [] if node.get("id")}
        after_nodes = {node.get("id"): node for node in (after.get("graph") or {}).get("nodes") or [] if node.get("id")}

        def edge_key(edge: Dict[str, Any]) -> str:
            return "|".join(str(edge.get(key) or "") for key in ("from", "to", "type"))

        before_edges = {edge_key(edge): edge for edge in (before.get("graph") or {}).get("edges") or []}
        after_edges = {edge_key(edge): edge for edge in (after.get("graph") or {}).get("edges") or []}

        added_nodes = [after_nodes[key] for key in sorted(set(after_nodes) - set(before_nodes))]
        removed_nodes = [before_nodes[key] for key in sorted(set(before_nodes) - set(after_nodes))]
        changed_nodes = [
            {"before": before_nodes[key], "after": after_nodes[key]}
            for key in sorted(set(before_nodes) & set(after_nodes))
            if _json_hash(before_nodes[key]) != _json_hash(after_nodes[key])
        ]
        added_edges = [after_edges[key] for key in sorted(set(after_edges) - set(before_edges))]
        removed_edges = [before_edges[key] for key in sorted(set(before_edges) - set(after_edges))]

        before_decisions = {key: value for key, value in before_nodes.items() if value.get("type") == "Decision"}
        after_decisions = {key: value for key, value in after_nodes.items() if value.get("type") == "Decision"}
        decisions_changed = [
            {"before": before_decisions.get(key), "after": after_decisions.get(key)}
            for key in sorted(set(before_decisions) | set(after_decisions))
            if _json_hash(before_decisions.get(key)) != _json_hash(after_decisions.get(key))
        ]

        return {
            "before": before_id,
            "after": after_id,
            "nodes_added": added_nodes,
            "nodes_removed": removed_nodes,
            "nodes_changed": changed_nodes,
            "edges_added": added_edges,
            "edges_removed": removed_edges,
            "decisions_changed": decisions_changed,
            "summary": {
                "nodes_added": len(added_nodes),
                "nodes_removed": len(removed_nodes),
                "nodes_changed": len(changed_nodes),
                "edges_added": len(added_edges),
                "edges_removed": len(removed_edges),
                "decisions_changed": len(decisions_changed),
            },
        }

    def timeline(self, audit_events: Optional[Iterable[Dict[str, Any]]] = None, limit: int = 100, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        state = self.load_state()
        events: List[Dict[str, Any]] = []
        events.extend(self._scoped(_listify(state.get("timeline")), workspace_id))
        for snapshot in self._scoped(_listify(state.get("snapshots")), workspace_id):
            events.append({"area": "snapshot", "event_type": "snapshot", "timestamp": snapshot.get("created_at"), "workspace_id": self._record_workspace(snapshot), "payload": snapshot})
        for trace in self._scoped(_listify(state.get("traces")), workspace_id):
            events.append({"area": "graph", "event_type": "answer_trace", "timestamp": trace.get("created_at"), "workspace_id": self._record_workspace(trace), "payload": trace})
        for run in self._scoped(_listify(state.get("agent_runs")), workspace_id):
            events.append({"area": "agent", "event_type": "agent_run", "timestamp": run.get("created_at"), "workspace_id": self._record_workspace(run), "payload": run})
        for workflow in self._scoped(_listify(state.get("workflows")), workspace_id):
            events.append({"area": "workflow", "event_type": "workflow", "timestamp": workflow.get("created_at"), "workspace_id": self._record_workspace(workflow), "payload": workflow})
        for audit in audit_events or []:
            events.append({"area": "audit", "event_type": audit.get("event_type") or "audit", "timestamp": audit.get("timestamp"), "payload": audit})
        events.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
        return {"events": events[: max(1, min(limit, 500))]}

    # ------------------------------------------------------------------
    # Personal memory
    # ------------------------------------------------------------------

    def upsert_memory(
        self,
        *,
        kind: str,
        content: str,
        user_email: Optional[str],
        tags: Optional[List[str]] = None,
        memory_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        graph: Any = None,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if kind not in MEMORY_KINDS:
            raise ValueError(f"unknown memory kind: {kind}")
        if not str(content or "").strip():
            raise ValueError("content is required")
        state = self.load_state()
        memories = _listify(state.get("memories"))
        now = _now()
        memory_id = memory_id or f"memory-{_json_hash([kind, content, user_email, now])[:16]}"
        existing = next((item for item in memories if item.get("id") == memory_id), None)
        record = existing or {
            "id": memory_id,
            "created_at": now,
        }
        record.update({
            "kind": kind,
            "content": content,
            "user_email": user_email,
            "tags": tags or [],
            "metadata": {**(metadata or {}), "memory_scope": kind},
            "workspace_id": self._resolve_scope(workspace_id, state) if existing is None else self._record_workspace(record),
            "updated_at": now,
        })
        if graph is not None:
            try:
                ingested = graph.ingest_event(
                    "Memory",
                    f"{kind}: {content[:80]}",
                    user_email=user_email,
                    source="workspace_os",
                    metadata={"memory_id": memory_id, "kind": kind, "tags": tags or []},
                )
                record["graph_node_id"] = ingested.get("node_id")
            except Exception as exc:
                record["graph_error"] = str(exc)
        if existing is None:
            memories.append(record)
        state["memories"] = memories
        self.save_state(state)
        self.record_timeline_event("memory", "memory_upserted", {"memory_id": memory_id, "kind": kind}, workspace_id=record.get("workspace_id"))
        return record

    def list_memories(self, user_email: Optional[str] = None, kind: Optional[str] = None, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        memories = self._scoped(_listify(self.load_state().get("memories")), workspace_id)
        if user_email:
            memories = [item for item in memories if item.get("user_email") in {None, user_email}]
        if kind:
            memories = [item for item in memories if item.get("kind") == kind]
        return {"memories": list(reversed(memories))}

    def search_memories(self, query: str, user_email: Optional[str] = None, limit: int = 20, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        q = str(query or "").lower().strip()
        memories = self.list_memories(user_email=user_email, workspace_id=workspace_id).get("memories", [])
        if q:
            memories = [
                item for item in memories
                if q in str(item.get("content") or "").lower()
                or q in " ".join(item.get("tags") or []).lower()
                or q in str(item.get("kind") or "").lower()
            ]
        return {"query": query, "memories": memories[: max(1, min(limit, 100))]}

    def get_memory(self, memory_id: str) -> Dict[str, Any]:
        record = next(
            (item for item in _listify(self.load_state().get("memories")) if item.get("id") == memory_id),
            None,
        )
        if record is None:
            raise FileNotFoundError(memory_id)
        return record

    def delete_memory(self, memory_id: str) -> Dict[str, Any]:
        state = self.load_state()
        memories = _listify(state.get("memories"))
        target = next((item for item in memories if item.get("id") == memory_id), None)
        if target is None:
            raise FileNotFoundError(memory_id)
        state["memories"] = [item for item in memories if item.get("id") != memory_id]
        self.save_state(state)
        self.record_timeline_event(
            "memory", "memory_deleted", {"memory_id": memory_id}, workspace_id=target.get("workspace_id")
        )
        return {"status": "ok", "memory_id": memory_id}

    def create_memory_snapshot(
        self,
        *,
        label: str = "memory snapshot",
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
        memory_ids: Optional[List[str]] = None,
        reason: str = "",
    ) -> Dict[str, Any]:
        """Persist a replayable point-in-time memory view without mutation."""
        state = self.load_state()
        scope = self._resolve_scope(workspace_id, state)
        memories = self._scoped(_listify(state.get("memories")), scope)
        if user_email:
            memories = [item for item in memories if item.get("user_email") in {None, user_email}]
        if memory_ids:
            allowed = set(memory_ids)
            memories = [item for item in memories if item.get("id") in allowed]
        snapshot = {
            "id": f"memory-snapshot-{_json_hash([label, scope, memories, _now()])[:16]}",
            "label": label,
            "reason": reason,
            "workspace_id": scope,
            "user_email": user_email,
            "memory_count": len(memories),
            "memories": memories,
            "created_at": _now(),
        }
        state.setdefault("memory_snapshots", []).append(snapshot)
        self.save_state(state)
        self.record_timeline_event("memory", "memory_snapshot", {"snapshot_id": snapshot["id"], "memory_count": len(memories)}, workspace_id=scope)
        return snapshot

    def list_memory_snapshots(self, workspace_id: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
        snapshots = self._scoped(_listify(self.load_state().get("memory_snapshots")), workspace_id)
        return {"snapshots": list(reversed(snapshots[-max(1, min(limit, 200)):]))}

    # ------------------------------------------------------------------
    # Agent and workflow graph
    # ------------------------------------------------------------------

    def list_agents(self, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        state = self.load_state()
        runs = self._scoped(_listify(state.get("agent_runs")), workspace_id)
        return {"agents": _listify(state.get("agents")), "runs": list(reversed(runs[-100:]))}

    def record_agent_run(
        self,
        *,
        agent_id: str,
        status: str,
        input_text: str,
        output_text: str,
        user_email: Optional[str],
        timeline: Optional[List[Dict[str, Any]]] = None,
        relationships: Optional[List[str]] = None,
        handoffs: Optional[List[Dict[str, Any]]] = None,
        context_packets: Optional[List[Dict[str, Any]]] = None,
        plan: Optional[List[Dict[str, Any]]] = None,
        plan_review: Optional[Dict[str, Any]] = None,
        review_history: Optional[List[Dict[str, Any]]] = None,
        retry_history: Optional[List[Dict[str, Any]]] = None,
        memory_snapshots: Optional[List[Dict[str, Any]]] = None,
        graph: Any = None,
        workspace_id: Optional[str] = None,
        mode: str = "simulation",
    ) -> Dict[str, Any]:
        state = self.load_state()
        resolved_workspace = self._resolve_scope(workspace_id, state)
        run = {
            "id": f"agent-run-{_json_hash([agent_id, input_text, output_text, _now()])[:16]}",
            "record_schema_version": 2,
            "agent_id": agent_id,
            "mode": mode,
            "status": status,
            "input": input_text,
            "output_preview": output_text[:1000],
            "user_email": user_email,
            "workspace_id": resolved_workspace,
            "relationships": relationships or [],
            "timeline": timeline or [],
            "handoffs": handoffs or [],
            "context_packets": context_packets or [],
            "plan": plan or [],
            "plan_review": plan_review or {},
            "review_history": review_history or [],
            "retry_history": retry_history or [],
            "memory_snapshots": memory_snapshots or [],
            "created_at": _now(),
        }
        if mode == "simulation":
            # Simulated runs are replay scaffolding, not experiences — they must
            # never enter the knowledge graph as real provenance.
            run["graph_node_id"] = None
            run["graph_skipped"] = "simulation runs are not recorded in the knowledge graph"
        elif graph is not None:
            try:
                ingested = graph.ingest_event(
                    "AgentRun",
                    f"{agent_id} {status}",
                    user_email=user_email,
                    source="workspace_os",
                    metadata={"run_id": run["id"], "agent_id": agent_id, "status": status, "mode": mode},
                )
                run["graph_node_id"] = ingested.get("node_id")
            except Exception as exc:
                run["graph_error"] = str(exc)
        if handoffs:
            stored_handoffs = state.setdefault("handoffs", [])
            for handoff in handoffs:
                stored = {
                    **handoff,
                    "run_id": run["id"],
                    "workspace_id": resolved_workspace,
                }
                stored_handoffs.append(stored)
            state["handoffs"] = stored_handoffs
        state.setdefault("agent_runs", []).append(run)
        self.save_state(state)
        self._emit_replayable_timeline_events(area="agent", run_id=run["id"], timeline=run["timeline"], workspace_id=resolved_workspace)
        if status == "failed":
            self._emit_execution_event(area="agent", event_type="execution_failed", payload={"run_id": run["id"], "agent_id": agent_id, "status": status}, workspace_id=resolved_workspace)
        self.record_timeline_event("agent", "agent_run", {"run_id": run["id"], "agent_id": agent_id, "status": status}, workspace_id=resolved_workspace)
        run["contract"] = run_record_contract(run)
        state = self.load_state()
        for item in _listify(state.get("agent_runs")):
            if item.get("id") == run["id"]:
                item["contract"] = run["contract"]
                break
        self.save_state(state)
        return run

    def update_agent_run(
        self,
        run_id: str,
        *,
        workspace_id: Optional[str] = None,
        graph: Any = None,
        patch: Optional[Dict[str, Any]] = None,
        **fields: Any,
    ) -> Dict[str, Any]:
        """Patch a persisted agent run without changing its id.

        Async execution creates a durable queued/running row before work starts,
        then updates that same row as progress, cancellation, or a terminal
        result arrives. This keeps old run lists/read APIs compatible while
        avoiding duplicate "placeholder + final" records.
        """
        updates = {**(patch or {}), **fields}
        state = self.load_state()
        run = next((item for item in _listify(state.get("agent_runs")) if item.get("id") == run_id), None)
        if run is None or (workspace_id and self._record_workspace(run) != str(workspace_id)):
            raise FileNotFoundError(run_id)
        resolved_workspace = self._record_workspace(run)
        old_timeline_len = len(run.get("timeline") or [])

        output_text = updates.pop("output_text", None)
        if output_text is not None:
            run["output_preview"] = str(output_text)[:1000]
        for key, value in updates.items():
            run[key] = value
        status = str(run.get("status") or "")
        run["updated_at"] = _now()
        if status in RUN_TERMINAL_STATUSES:
            run.setdefault("completed_at", _now())

        handoffs = updates.get("handoffs")
        if isinstance(handoffs, list):
            stored_handoffs = [
                item for item in _listify(state.get("handoffs"))
                if item.get("run_id") != run_id
            ]
            for handoff in handoffs:
                if isinstance(handoff, dict):
                    stored_handoffs.append({**handoff, "run_id": run_id, "workspace_id": resolved_workspace})
            state["handoffs"] = stored_handoffs

        if (
            status in RUN_TERMINAL_STATUSES
            and run.get("mode") != "simulation"
            and graph is not None
            and not run.get("graph_node_id")
        ):
            try:
                ingested = graph.ingest_event(
                    "AgentRun",
                    f"{run.get('agent_id')} {status}",
                    user_email=run.get("user_email"),
                    source="workspace_os",
                    metadata={
                        "run_id": run_id,
                        "agent_id": run.get("agent_id"),
                        "status": status,
                        "mode": run.get("mode"),
                    },
                )
                run["graph_node_id"] = ingested.get("node_id")
            except Exception as exc:
                run["graph_error"] = str(exc)

        self.save_state(state)
        run["contract"] = run_record_contract(run)
        state = self.load_state()
        for item in _listify(state.get("agent_runs")):
            if item.get("id") == run_id:
                item["contract"] = run["contract"]
                break
        self.save_state(state)

        timeline = run.get("timeline") or []
        if len(timeline) > old_timeline_len:
            self._emit_replayable_timeline_events(
                area="agent",
                run_id=run_id,
                timeline=timeline[old_timeline_len:],
                workspace_id=resolved_workspace,
            )
        if status == "failed":
            self._emit_execution_event(area="agent", event_type="execution_failed", payload={"run_id": run_id, "agent_id": run.get("agent_id"), "status": status}, workspace_id=resolved_workspace)
        elif status == "cancelled":
            self._emit_execution_event(area="agent", event_type="execution_cancelled", payload={"run_id": run_id, "agent_id": run.get("agent_id"), "status": status}, workspace_id=resolved_workspace)
        elif status == "interrupted":
            self._emit_execution_event(area="agent", event_type="execution_interrupted", payload={"run_id": run_id, "agent_id": run.get("agent_id"), "status": status}, workspace_id=resolved_workspace)
        self.record_timeline_event("agent", "agent_run_update", {"run_id": run_id, "agent_id": run.get("agent_id"), "status": status}, workspace_id=resolved_workspace)
        return run

    def get_agent_run(self, run_id: str, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        run = next((item for item in _listify(self.load_state().get("agent_runs")) if item.get("id") == run_id), None)
        if not run or (workspace_id and self._record_workspace(run) != str(workspace_id)):
            raise FileNotFoundError(run_id)
        return run

    def list_handoffs(self, workspace_id: Optional[str] = None, run_id: Optional[str] = None) -> Dict[str, Any]:
        handoffs = self._scoped(_listify(self.load_state().get("handoffs")), workspace_id)
        if run_id:
            handoffs = [item for item in handoffs if item.get("run_id") == run_id]
        return {"handoffs": list(reversed(handoffs[-200:]))}

    def create_workflow(
        self,
        *,
        name: str,
        steps: List[Dict[str, Any]],
        user_email: Optional[str],
        metadata: Optional[Dict[str, Any]] = None,
        graph: Any = None,
        workspace_id: Optional[str] = None,
        nodes: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        state = self.load_state()
        workflow = {
            "id": f"workflow-{_json_hash([name, steps, user_email, _now()])[:16]}",
            "name": name or "Untitled workflow",
            "steps": steps,
            "user_email": user_email,
            "workspace_id": self._resolve_scope(workspace_id, state),
            "metadata": metadata or {},
            "events": [{"type": "created", "timestamp": _now()}],
            "created_at": _now(),
            "updated_at": _now(),
        }
        # Workflow Designer stores a typed-node graph alongside the legacy
        # ``steps`` list so older history keeps working and new editors get nodes.
        if nodes is not None:
            workflow["nodes"] = nodes
        if graph is not None:
            try:
                ingested = graph.ingest_event(
                    "Workflow",
                    workflow["name"],
                    user_email=user_email,
                    source="workspace_os",
                    metadata={"workflow_id": workflow["id"], "steps": steps},
                )
                workflow["graph_node_id"] = ingested.get("node_id")
            except Exception as exc:
                workflow["graph_error"] = str(exc)
        state.setdefault("workflows", []).append(workflow)
        self.save_state(state)
        self.record_timeline_event("workflow", "workflow_created", {"workflow_id": workflow["id"], "name": workflow["name"]})
        return workflow

    def record_workflow_run(
        self,
        *,
        workflow_id: Optional[str],
        name: str,
        status: str,
        timeline: List[Dict[str, Any]],
        outputs: Optional[Dict[str, Any]] = None,
        user_email: Optional[str] = None,
        graph: Any = None,
        workspace_id: Optional[str] = None,
        mode: str = "simulation",
        pause: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Persist a Workflow Designer execution into local-first run history."""
        state = self.load_state()
        resolved_workspace = self._resolve_scope(workspace_id, state)
        run = {
            "id": f"workflow-run-{_json_hash([workflow_id, name, status, _now()])[:16]}",
            "record_schema_version": 2,
            "workflow_id": workflow_id,
            "name": name or "workflow",
            "mode": mode,
            "status": status,
            "timeline": timeline or [],
            "outputs": outputs or {},
            "user_email": user_email,
            "workspace_id": resolved_workspace,
            "created_at": _now(),
        }
        if pause:
            run["pause"] = pause
        if mode == "simulation":
            # Record-only node runners do no real work; their runs must not be
            # written into the knowledge graph as if they were real executions.
            run["graph_node_id"] = None
            run["graph_skipped"] = "simulation runs are not recorded in the knowledge graph"
        elif graph is not None:
            try:
                ingested = graph.ingest_event(
                    "WorkflowRun",
                    f"{run['name']} {status}",
                    user_email=user_email,
                    source="workspace_os",
                    metadata={"run_id": run["id"], "workflow_id": workflow_id, "status": status, "mode": mode},
                )
                run["graph_node_id"] = ingested.get("node_id")
            except Exception as exc:
                run["graph_error"] = str(exc)
        state.setdefault("workflow_runs", []).append(run)
        # Attach the run id to the workflow's event log for cross-linking.
        for wf in _listify(state.get("workflows")):
            if wf.get("id") == workflow_id:
                wf.setdefault("events", []).append({"type": "run", "timestamp": _now(), "payload": {"run_id": run["id"], "status": status}})
                wf["updated_at"] = _now()
                break
        self.save_state(state)
        self._emit_execution_event(area="workflow", event_type="workflow_started", payload={"run_id": run["id"], "workflow_id": workflow_id, "name": name}, workspace_id=resolved_workspace)
        self._emit_replayable_timeline_events(area="workflow", run_id=run["id"], timeline=run["timeline"], workspace_id=resolved_workspace)
        if status == "failed":
            self._emit_execution_event(area="workflow", event_type="execution_failed", payload={"run_id": run["id"], "workflow_id": workflow_id, "status": status}, workspace_id=resolved_workspace)
        elif status in {"ok", "partial"}:
            self._emit_execution_event(area="workflow", event_type="workflow_completed", payload={"run_id": run["id"], "workflow_id": workflow_id, "status": status}, workspace_id=resolved_workspace)
        self.record_timeline_event("workflow", "workflow_run", {"run_id": run["id"], "workflow_id": workflow_id, "status": status}, workspace_id=resolved_workspace)
        run["contract"] = workflow_run_contract(run)
        state = self.load_state()
        for item in _listify(state.get("workflow_runs")):
            if item.get("id") == run["id"]:
                item["contract"] = run["contract"]
                break
        self.save_state(state)
        return run

    def update_workflow_run(
        self,
        run_id: str,
        *,
        workspace_id: Optional[str] = None,
        graph: Any = None,
        patch: Optional[Dict[str, Any]] = None,
        **fields: Any,
    ) -> Dict[str, Any]:
        """Patch a persisted workflow run in place for async execution."""
        updates = {**(patch or {}), **fields}
        state = self.load_state()
        run = next((item for item in _listify(state.get("workflow_runs")) if item.get("id") == run_id), None)
        if run is None or (workspace_id and self._record_workspace(run) != str(workspace_id)):
            raise FileNotFoundError(run_id)
        resolved_workspace = self._record_workspace(run)
        old_timeline_len = len(run.get("timeline") or [])

        for key, value in updates.items():
            if value is None and key == "pause":
                run.pop("pause", None)
            else:
                run[key] = value
        status = str(run.get("status") or "")
        run["updated_at"] = _now()
        if status in RUN_TERMINAL_STATUSES:
            run.setdefault("completed_at", _now())

        workflow_id = run.get("workflow_id")
        for wf in _listify(state.get("workflows")):
            if wf.get("id") == workflow_id:
                wf.setdefault("events", []).append({"type": "run_update", "timestamp": _now(), "payload": {"run_id": run_id, "status": status}})
                wf["updated_at"] = _now()
                break

        if (
            status in RUN_TERMINAL_STATUSES
            and run.get("mode") != "simulation"
            and graph is not None
            and not run.get("graph_node_id")
        ):
            try:
                ingested = graph.ingest_event(
                    "WorkflowRun",
                    f"{run.get('name')} {status}",
                    user_email=run.get("user_email"),
                    source="workspace_os",
                    metadata={
                        "run_id": run_id,
                        "workflow_id": workflow_id,
                        "status": status,
                        "mode": run.get("mode"),
                    },
                )
                run["graph_node_id"] = ingested.get("node_id")
            except Exception as exc:
                run["graph_error"] = str(exc)

        self.save_state(state)
        run["contract"] = workflow_run_contract(run)
        state = self.load_state()
        for item in _listify(state.get("workflow_runs")):
            if item.get("id") == run_id:
                item["contract"] = run["contract"]
                break
        self.save_state(state)

        timeline = run.get("timeline") or []
        if len(timeline) > old_timeline_len:
            self._emit_replayable_timeline_events(
                area="workflow",
                run_id=run_id,
                timeline=timeline[old_timeline_len:],
                workspace_id=resolved_workspace,
            )
        if status == "failed":
            self._emit_execution_event(area="workflow", event_type="execution_failed", payload={"run_id": run_id, "workflow_id": workflow_id, "status": status}, workspace_id=resolved_workspace)
        elif status in {"ok", "partial"}:
            self._emit_execution_event(area="workflow", event_type="workflow_completed", payload={"run_id": run_id, "workflow_id": workflow_id, "status": status}, workspace_id=resolved_workspace)
        elif status == "cancelled":
            self._emit_execution_event(area="workflow", event_type="execution_cancelled", payload={"run_id": run_id, "workflow_id": workflow_id, "status": status}, workspace_id=resolved_workspace)
        elif status == "interrupted":
            self._emit_execution_event(area="workflow", event_type="execution_interrupted", payload={"run_id": run_id, "workflow_id": workflow_id, "status": status}, workspace_id=resolved_workspace)
        self.record_timeline_event("workflow", "workflow_run_update", {"run_id": run_id, "workflow_id": workflow_id, "status": status}, workspace_id=resolved_workspace)
        return run

    def list_workflow_runs(self, workflow_id: Optional[str] = None, limit: int = 50, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        runs = self._scoped(_listify(self.load_state().get("workflow_runs")), workspace_id)
        if workflow_id:
            runs = [run for run in runs if run.get("workflow_id") == workflow_id]
        return {"runs": list(reversed(runs[-max(1, min(limit, 300)):]))}

    def mark_workflow_run_resolved(
        self, run_id: str, *, resumed_run_id: str, approved: bool,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Close out a paused run after its approval decision (one decision only)."""
        state = self.load_state()
        run = next((item for item in _listify(state.get("workflow_runs")) if item.get("id") == run_id), None)
        if run is None or (workspace_id and self._record_workspace(run) != str(workspace_id)):
            raise FileNotFoundError(run_id)
        run["status"] = "resumed" if approved else "denied"
        run["resolved_at"] = _now()
        run["resumed_run_id"] = resumed_run_id
        self.save_state(state)
        return run

    def get_workflow_run(self, run_id: str, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        run = next((item for item in _listify(self.load_state().get("workflow_runs")) if item.get("id") == run_id), None)
        if not run or (workspace_id and self._record_workspace(run) != str(workspace_id)):
            raise FileNotFoundError(run_id)
        return run

    # ── review queue (5.6.0) ─────────────────────────────────────────────
    # Workspace-scoped suggestion inbox. Automation/trigger runs write drafts
    # here for the user to approve/dismiss/snooze. Persistence only; the
    # transition policy lives in ReviewQueueService (services/review_queue.py).

    def create_review_item(
        self,
        *,
        title: str,
        summary: str = "",
        source: str = "workflow_run",
        kind: str = "suggestion",
        payload: Optional[Dict[str, Any]] = None,
        provenance: Optional[Dict[str, Any]] = None,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not str(title or "").strip():
            raise ValueError("title is required")
        state = self.load_state()
        resolved_workspace = self._resolve_scope(workspace_id, state)
        now = _now()
        item = {
            "id": f"review-{_json_hash([title, source, kind, user_email, now])[:16]}",
            "status": "pending",
            "title": title,
            "summary": summary or "",
            "source": source or "workflow_run",
            "kind": kind or "suggestion",
            "payload": dict(payload or {}),
            "provenance": dict(provenance or {}),
            "snoozed_until": None,
            "user_email": user_email,
            "workspace_id": resolved_workspace,
            "created_at": now,
            "updated_at": now,
        }
        state.setdefault("review_items", []).append(item)
        self.save_state(state)
        self.record_timeline_event(
            "review", "review_item_created",
            {"item_id": item["id"], "source": item["source"], "kind": item["kind"]},
            workspace_id=resolved_workspace,
        )
        return item

    def list_review_items(
        self, *, workspace_id: Optional[str] = None, user_email: Optional[str] = None,
        source: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        items = self._scoped(_listify(self.load_state().get("review_items")), workspace_id)
        if user_email:
            items = [item for item in items if item.get("user_email") in {None, user_email}]
        if source:
            items = [item for item in items if item.get("source") == source]
        return list(reversed(items))

    def get_review_item(self, item_id: str, *, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        item = next(
            (it for it in _listify(self.load_state().get("review_items")) if it.get("id") == item_id),
            None,
        )
        if item is None or (workspace_id and self._record_workspace(item) != str(workspace_id)):
            raise FileNotFoundError(item_id)
        return item

    def update_review_item(
        self, item_id: str, *, workspace_id: Optional[str] = None, **fields: Any,
    ) -> Dict[str, Any]:
        state = self.load_state()
        item = next((it for it in _listify(state.get("review_items")) if it.get("id") == item_id), None)
        if item is None or (workspace_id and self._record_workspace(item) != str(workspace_id)):
            raise FileNotFoundError(item_id)
        for key, value in fields.items():
            item[key] = value
        item["updated_at"] = _now()
        self.save_state(state)
        self.record_timeline_event(
            "review", "review_item_updated",
            {"item_id": item_id, "status": item.get("status")},
            workspace_id=self._record_workspace(item),
        )
        return item

    def reconcile_interrupted_runs(self, *, reason: str = "server_startup") -> Dict[str, Any]:
        """Mark durable active runs as interrupted after a process restart.

        Queued/running/cancelling rows cannot have an owning asyncio task after
        startup. Paused approval runs are intentionally left untouched so their
        durable human decision cursor remains resumable.
        """
        state = self.load_state()
        interrupted: List[Dict[str, Any]] = []
        now = _now()
        collections = (("agent_runs", "agent"), ("workflow_runs", "workflow"))
        for key, area in collections:
            for run in _listify(state.get(key)):
                status = str(run.get("status") or "")
                if status not in RUN_ACTIVE_STATUSES:
                    continue
                run["status"] = "interrupted"
                run["interrupted_at"] = now
                run["interrupt_reason"] = reason
                run["updated_at"] = now
                run.setdefault("timeline", []).append({
                    "event": "execution_interrupted",
                    "status": "interrupted",
                    "reason": reason,
                    "timestamp": now,
                })
                interrupted.append({
                    "kind": area,
                    "run_id": run.get("id"),
                    "workspace_id": self._record_workspace(run),
                    "previous_status": status,
                })
        if not interrupted:
            return {"count": 0, "interrupted": []}
        self.save_state(state)
        for item in interrupted:
            area = item["kind"]
            run_id = item["run_id"]
            workspace = item.get("workspace_id")
            self._emit_execution_event(
                area=area,
                event_type="execution_interrupted",
                payload={"run_id": run_id, "reason": reason, "previous_status": item.get("previous_status")},
                workspace_id=workspace,
            )
        self.record_timeline_event(
            "system",
            "startup_reconciliation",
            {"interrupted_runs": len(interrupted), "reason": reason},
        )
        return {"count": len(interrupted), "interrupted": interrupted}

    @staticmethod
    def _replay_frames(run: Dict[str, Any], *, kind: str) -> List[Dict[str, Any]]:
        frames = []
        for index, item in enumerate(run.get("timeline") or []):
            event = item.get("event") or item.get("event_type") or item.get("type") or "event"
            actor = (
                item.get("agent_id")
                or item.get("role")
                or item.get("source_agent")
                or item.get("target_agent")
                or item.get("node")
                or kind
            )
            result = item.get("result") if "result" in item else item.get("output")
            decision = item.get("outcome") or item.get("verdict") or item.get("status")
            frames.append({
                "index": index,
                "event": event,
                "actor": actor,
                "when": item.get("timestamp") or item.get("started_at") or run.get("created_at"),
                "why": item.get("reason") or item.get("note") or item.get("name") or "",
                "input": item.get("context_packet") or item.get("trigger") or run.get("input"),
                "output": result,
                "decision": decision,
                "raw": item,
            })
        return frames

    def replay_agent_run(self, run_id: str, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        run = self.get_agent_run(run_id, workspace_id=workspace_id)
        return {
            "kind": "agent",
            "run_id": run_id,
            "status": run.get("status"),
            "workspace_id": self._record_workspace(run),
            "contract": run.get("contract") or run_record_contract(run),
            "replayable": True,
            "frames": self._replay_frames(run, kind="agent"),
            "handoffs": run.get("handoffs") or [],
            "context_packets": run.get("context_packets") or [],
            "review_history": run.get("review_history") or [],
            "retry_history": run.get("retry_history") or [],
        }

    def replay_workflow_run(self, run_id: str, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        run = self.get_workflow_run(run_id, workspace_id=workspace_id)
        return {
            "kind": "workflow",
            "run_id": run_id,
            "status": run.get("status"),
            "workspace_id": self._record_workspace(run),
            "contract": run.get("contract") or workflow_run_contract(run),
            "replayable": True,
            "frames": self._replay_frames(run, kind="workflow"),
            "outputs": run.get("outputs") or {},
        }

    def get_workflow(self, workflow_id: str, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        workflow = next((wf for wf in _listify(self.load_state().get("workflows")) if wf.get("id") == workflow_id), None)
        if not workflow or (workspace_id and self._record_workspace(workflow) != str(workspace_id)):
            raise FileNotFoundError(workflow_id)
        return workflow

    def update_workflow_definition(
        self,
        workflow_id: str,
        *,
        name: Optional[str] = None,
        nodes: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Edit a stored workflow's node graph / name without losing its history."""
        state = self.load_state()
        workflow = next((wf for wf in _listify(state.get("workflows")) if wf.get("id") == workflow_id), None)
        if not workflow or (workspace_id and self._record_workspace(workflow) != str(workspace_id)):
            raise FileNotFoundError(workflow_id)
        if name is not None and str(name).strip():
            workflow["name"] = str(name).strip()
        if nodes is not None:
            workflow["nodes"] = nodes
        if metadata is not None:
            workflow["metadata"] = {**(workflow.get("metadata") or {}), **metadata}
        workflow.setdefault("events", []).append({"type": "edited", "timestamp": _now()})
        workflow["updated_at"] = _now()
        self.save_state(state)
        self.record_timeline_event("workflow", "workflow_edited", {"workflow_id": workflow_id})
        return workflow

    def list_workflows(self, query: str = "", workspace_id: Optional[str] = None) -> Dict[str, Any]:
        workflows = list(reversed(self._scoped(_listify(self.load_state().get("workflows")), workspace_id)))
        q = str(query or "").lower().strip()
        if q:
            workflows = [
                wf for wf in workflows
                if q in str(wf.get("name") or "").lower()
                or q in json.dumps(wf.get("steps") or [], ensure_ascii=False).lower()
            ]
        return {"workflows": workflows}

    def record_workflow_event(self, workflow_id: str, event_type: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        state = self.load_state()
        workflows = _listify(state.get("workflows"))
        workflow = next((item for item in workflows if item.get("id") == workflow_id), None)
        if not workflow:
            raise FileNotFoundError(workflow_id)
        event = {"type": event_type, "timestamp": _now(), "payload": payload or {}}
        workflow.setdefault("events", []).append(event)
        workflow["updated_at"] = _now()
        self.save_state(state)
        self.record_timeline_event("workflow", "workflow_event", {"workflow_id": workflow_id, "event_type": event_type})
        return workflow

    # ------------------------------------------------------------------
    # Relationship explorer
    # ------------------------------------------------------------------

    def relationship_explorer(self, graph: Any, node_id: str, target_id: Optional[str] = None, limit: int = 500) -> Dict[str, Any]:
        if graph is None:
            return {"node_id": node_id, "inbound": [], "outbound": [], "related_entities": [], "shortest_path": []}
        data = graph.graph(limit=limit)
        nodes = {node.get("id"): node for node in data.get("nodes") or [] if node.get("id")}
        edges = data.get("edges") or []
        inbound = [edge for edge in edges if edge.get("to") == node_id]
        outbound = [edge for edge in edges if edge.get("from") == node_id]
        if node_id not in nodes:
            try:
                neighbors = graph.neighbors(node_id)
                for node in neighbors.get("neighbors") or []:
                    nodes[node.get("id")] = node
                edges.extend(neighbors.get("edges") or [])
                inbound = [edge for edge in edges if edge.get("to") == node_id]
                outbound = [edge for edge in edges if edge.get("from") == node_id]
            except Exception:
                pass

        related_ids = []
        for edge in inbound + outbound:
            other = edge.get("from") if edge.get("to") == node_id else edge.get("to")
            if other:
                related_ids.append(other)
        related = [nodes.get(rid, {"id": rid}) for rid in dict.fromkeys(related_ids)]
        shortest_path = self._shortest_path(edges, node_id, target_id) if target_id else []
        return {
            "node_id": node_id,
            "node": nodes.get(node_id, {"id": node_id}),
            "inbound": inbound,
            "outbound": outbound,
            "related_entities": related,
            "shortest_path": shortest_path,
        }

    @staticmethod
    def _shortest_path(edges: List[Dict[str, Any]], start: str, target: Optional[str]) -> List[str]:
        if not start or not target:
            return []
        adjacency: Dict[str, List[str]] = {}
        for edge in edges:
            src = edge.get("from")
            dst = edge.get("to")
            if src and dst:
                adjacency.setdefault(src, []).append(dst)
                adjacency.setdefault(dst, []).append(src)
        queue: deque[List[str]] = deque([[start]])
        seen = {start}
        while queue:
            path = queue.popleft()
            node = path[-1]
            if node == target:
                return path
            for neighbor in adjacency.get(node, []):
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(path + [neighbor])
        return []

    # ------------------------------------------------------------------
    # Local Computer Memory
    # ------------------------------------------------------------------

    def configure_computer_memory(
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
        state = self.load_state()
        config = state.setdefault("computer_memory", {})
        config.update({
            "enabled": bool(enabled),
            "approved": bool(enabled),
            "approved_at": _now() if enabled else config.get("approved_at"),
            "approved_by": approved_by if enabled else config.get("approved_by"),
            "scopes": scopes or config.get("scopes") or ["Downloads", "Documents", "Repositories"],
            "consent": consent,
        })
        state.setdefault("feature_flags", {})["local_computer_memory"] = bool(enabled)
        self.save_state(state)
        self.record_timeline_event("memory", "computer_memory_configured", {"enabled": bool(enabled), "approved_by": approved_by})
        return config

    def record_computer_activity(self, activity: Dict[str, Any], graph: Any = None) -> Dict[str, Any]:
        state = self.load_state()
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
            try:
                graph.ingest_event(
                    "ComputerActivity",
                    str(activity.get("summary") or activity.get("path") or "Computer activity")[:120],
                    source="workspace_os",
                    metadata=record,
                )
            except Exception as exc:
                record["graph_error"] = str(exc)
        self.save_state(state)
        self.record_timeline_event("memory", "computer_activity", {"activity_id": record["id"]})
        return {"status": "ok", "activity": record}

    # ------------------------------------------------------------------
    # Skills
    # ------------------------------------------------------------------

    def list_skill_registry(self, skills_dir: Path, marketplace: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        state = self.load_state()
        registry = state.setdefault("skill_registry", {})
        installed = []
        if skills_dir.exists():
            for skill_dir in sorted(skills_dir.iterdir()):
                if not skill_dir.is_dir():
                    continue
                skill_md = skill_dir / "SKILL.md"
                schema = skill_dir / "schema.json"
                if not skill_md.exists():
                    continue
                desc = ""
                try:
                    for line in skill_md.read_text(encoding="utf-8").splitlines():
                        if line.startswith("description:"):
                            desc = line.split(":", 1)[1].strip()
                            break
                except Exception:
                    desc = ""
                version = "local"
                if schema.exists():
                    try:
                        version = str((json.loads(schema.read_text(encoding="utf-8")) or {}).get("version") or "local")
                    except Exception:
                        version = "local"
                entry = registry.setdefault(skill_dir.name, {})
                entry.setdefault("enabled", True)
                entry.update({
                    "name": skill_dir.name,
                    "description": desc,
                    "version": version,
                    "installed": True,
                    "install_status": entry.get("install_status") or "ready",
                    "validation_status": "ready" if skill_md.exists() else "missing_manifest",
                    "source": entry.get("source") or "local",
                    "path": str(skill_dir),
                    "updated_at": entry.get("updated_at") or _now(),
                })
                installed.append(entry)
        available = []
        for item in marketplace or []:
            name = item.get("skill") or item.get("name")
            if not name:
                continue
            state_entry = registry.get(name, {})
            available.append({
                **item,
                "enabled": bool(state_entry.get("enabled", True)),
                "installed": bool(state_entry.get("installed")),
                "install_status": state_entry.get("install_status") or ("ready" if state_entry.get("installed") else "available"),
                "validation_status": state_entry.get("validation_status") or item.get("validation_status") or ("ready" if state_entry.get("installed") else "not_installed"),
                "source": state_entry.get("source") or item.get("source") or item.get("plugin") or "marketplace",
                "version": state_entry.get("version") or item.get("version") or "remote",
            })
        self.save_state(state)
        return {
            "installed": installed,
            "available": available,
            "registry": registry,
            "total_installed": len(installed),
            "total_available": len(available),
        }

    def set_skill_enabled(self, skill: str, enabled: bool) -> Dict[str, Any]:
        state = self.load_state()
        entry = state.setdefault("skill_registry", {}).setdefault(skill, {"name": skill})
        entry["enabled"] = bool(enabled)
        entry["updated_at"] = _now()
        self.save_state(state)
        self.record_timeline_event("skills", "skill_enabled" if enabled else "skill_disabled", {"skill": skill})
        return entry

    def mark_skill_installed(self, skill: str, *, version: str = "local", metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        state = self.load_state()
        entry = state.setdefault("skill_registry", {}).setdefault(skill, {"name": skill})
        entry.update({
            "installed": True,
            "enabled": entry.get("enabled", True),
            "version": version,
            "install_status": "ready",
            "validation_status": "ready",
            "source": (metadata or {}).get("source") or entry.get("source") or "marketplace",
            "metadata": metadata or entry.get("metadata") or {},
            "updated_at": _now(),
        })
        self.save_state(state)
        self.record_timeline_event("skills", "skill_installed", {"skill": skill, "version": version})
        return entry

    def mark_skill_uninstalled(self, skill: str) -> Dict[str, Any]:
        state = self.load_state()
        entry = state.setdefault("skill_registry", {}).setdefault(skill, {"name": skill})
        entry.update({"installed": False, "enabled": False, "updated_at": _now()})
        self.save_state(state)
        self.record_timeline_event("skills", "skill_uninstalled", {"skill": skill})
        return entry

    # ------------------------------------------------------------------
    # Plugin SDK registry — mirrors the skill registry contract.
    # ------------------------------------------------------------------

    def list_plugin_registry(self) -> Dict[str, Any]:
        return dict(self.load_state().get("plugin_registry") or {})

    def set_plugin_enabled(self, plugin_id: str, enabled: bool) -> Dict[str, Any]:
        state = self.load_state()
        entry = state.setdefault("plugin_registry", {}).setdefault(plugin_id, {"id": plugin_id})
        entry["enabled"] = bool(enabled)
        entry["updated_at"] = _now()
        self.save_state(state)
        self.record_timeline_event("plugins", "plugin_enabled" if enabled else "plugin_disabled", {"plugin": plugin_id})
        return entry

    def mark_plugin_installed(self, plugin_id: str, *, version: str = "0.0.0", metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        state = self.load_state()
        entry = state.setdefault("plugin_registry", {}).setdefault(plugin_id, {"id": plugin_id})
        entry.update({
            "id": plugin_id,
            "installed": True,
            "enabled": entry.get("enabled", True),
            "version": version,
            "install_status": "ready",
            "validation_status": "valid",
            "metadata": metadata or entry.get("metadata") or {},
            "updated_at": _now(),
        })
        self.save_state(state)
        self.record_timeline_event("plugins", "plugin_installed", {"plugin": plugin_id, "version": version})
        return entry

    def mark_plugin_uninstalled(self, plugin_id: str) -> Dict[str, Any]:
        state = self.load_state()
        entry = state.setdefault("plugin_registry", {}).setdefault(plugin_id, {"id": plugin_id})
        entry.update({"installed": False, "enabled": False, "updated_at": _now()})
        self.save_state(state)
        self.record_timeline_event("plugins", "plugin_uninstalled", {"plugin": plugin_id})
        return {"status": "ok", "plugin_id": plugin_id, "registry": entry}

    # ------------------------------------------------------------------
    # Marketplace template registry (v2.1 foundation)
    # ------------------------------------------------------------------

    @staticmethod
    def _template_registry_key(kind: str, template_id: str, workspace_id: str) -> str:
        base = f"{kind}:{template_id}"
        return base if workspace_id == DEFAULT_WORKSPACE_ID else f"{workspace_id}:{base}"

    def list_template_registry(self, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        state = self.load_state()
        registry = dict(state.get("template_registry") or {})
        if workspace_id is None:
            return registry
        scope = self._resolve_scope(workspace_id, state)
        return {
            key: value
            for key, value in registry.items()
            if self._record_workspace(value) == scope
        }

    def mark_template_installed(
        self,
        *,
        kind: str,
        template_id: str,
        version: str = "1.0.0",
        metadata: Optional[Dict[str, Any]] = None,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        state = self.load_state()
        scope = self._resolve_scope(workspace_id, state)
        key = self._template_registry_key(kind, template_id, scope)
        entry = state.setdefault("template_registry", {}).setdefault(key, {"id": template_id, "kind": kind})
        entry.update({
            "id": template_id,
            "kind": kind,
            "version": version,
            "installed": True,
            "workspace_id": scope,
            "metadata": metadata or entry.get("metadata") or {},
            "updated_at": _now(),
        })
        self.save_state(state)
        self.record_timeline_event("marketplace", "template_installed", {"kind": kind, "template_id": template_id}, workspace_id=scope)
        return entry

    # ------------------------------------------------------------------
    # Audit timeline
    # ------------------------------------------------------------------

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
            if model and model.lower() not in json.dumps(event, ensure_ascii=False).lower():
                continue
            if since_dt and stamp and stamp < since_dt:
                continue
            if until_dt and stamp and stamp > until_dt:
                continue
            filtered.append({
                **event,
                "category": self._audit_category(event),
            })
        filtered.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
        return {"events": filtered[: max(1, min(limit, 1000))], "total": len(filtered)}

    @staticmethod
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
