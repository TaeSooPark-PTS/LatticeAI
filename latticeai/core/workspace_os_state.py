"""Workspace OS state: the shape of a new file, and upgrades to old ones.

Split out of ``workspace_os.py`` in 10.2.0. Neither concern belongs to the
store: ``default_state`` describes a brand-new workspace file, ``new_workspace_record``
builds one workspace entry, and ``migrate_workspaces`` upgrades a file written
by an older version. All three were already free of instance state — two were
``@staticmethod`` in all but name — so they move without behaviour change.

``WorkspaceOSStore`` keeps thin delegating methods, so its public surface and
every existing call site are unchanged.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .timeutil import now_iso as _now
from .workspace_os_constants import (
    DEFAULT_AGENTS,
    DEFAULT_WORKSPACE_ID,
    ONBOARDING_STEPS,
    ROLE_PERMISSIONS,
    WORKSPACE_AREAS,
    WORKSPACE_OS_VERSION,
    WORKSPACE_TYPES,
)


def new_workspace_record(
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


def migrate_workspaces(state: Dict[str, Any]) -> Dict[str, Any]:
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
        base = new_workspace_record(
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
        migrated[DEFAULT_WORKSPACE_ID] = new_workspace_record(
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


def default_state() -> Dict[str, Any]:
    return {
        "version": WORKSPACE_OS_VERSION,
        "identity": "AI Workspace OS",
        "created_at": _now(),
        "updated_at": _now(),
        "active_workspace": DEFAULT_WORKSPACE_ID,
        "workspaces": {
            DEFAULT_WORKSPACE_ID: new_workspace_record(
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
