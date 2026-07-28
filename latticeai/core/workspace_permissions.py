"""Workspace permission and role logic extracted from WorkspaceOSStore.

Provides pure functions + a small PermissionManager for composition.
All behavior preserved; main store continues to expose the same public methods.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .timeutil import now_iso as _now
from .workspace_os_utils import _listify


# Avoid circular at import time: pull constants lazily from parent module.
def _get_role_permissions():
    from .workspace_os import ROLE_PERMISSIONS
    return ROLE_PERMISSIONS

def _get_workspace_roles():
    from .workspace_os import WORKSPACE_ROLES
    return WORKSPACE_ROLES


def _member_role(ws: Dict[str, Any], user_id: Optional[str]) -> Optional[str]:
    if ws.get("type") == "personal":
        return "owner"
    owner = ws.get("owner_user_id")
    if not owner and not user_id:
        return "owner"
    if user_id and user_id == owner:
        return "owner"
    for member in _listify(ws.get("members")):
        if member.get("user_id") == user_id:
            return member.get("role")
    return None


def has_permission(ws_or_store: Any, workspace_id: str, user_id: Optional[str], permission: str) -> bool:
    """Works both as standalone or delegated."""
    if hasattr(ws_or_store, "load_state"):
        # called as method on store
        ws = (ws_or_store.load_state().get("workspaces") or {}).get(workspace_id)
    else:
        ws = ws_or_store
    if not ws:
        return False
    role = _member_role(ws, user_id)
    if role is None:
        return False
    return permission in _get_role_permissions().get(role, set())


class WorkspacePermissionManager:
    """Composable permission manager."""

    def __init__(self, store: Any):
        self._store = store

    def get_member_role(self, workspace_id: str, user_id: Optional[str]) -> Optional[str]:
        ws = (self._store.load_state().get("workspaces") or {}).get(workspace_id)
        if not ws:
            raise FileNotFoundError(workspace_id)
        return _member_role(ws, user_id)

    def has_permission(self, workspace_id: str, user_id: Optional[str], permission: str) -> bool:
        try:
            role = self.get_member_role(workspace_id, user_id)
        except FileNotFoundError:
            return False
        if role is None:
            return False
        return permission in _get_role_permissions().get(role, set())

    def require_permission(self, ws: Dict[str, Any], actor: Optional[str], permission: str) -> None:
        role = _member_role(ws, actor)
        if role is None or permission not in _get_role_permissions().get(role, set()):
            raise PermissionError(
                f"'{actor or 'anonymous'}' lacks '{permission}' on workspace '{ws.get('workspace_id')}'"
            )

    def add_member(self, workspace_id: str, *, user_id: str, role: str = "member", actor: Optional[str] = None) -> Dict[str, Any]:
        if role not in _get_workspace_roles():
            raise ValueError(f"unknown role: {role}")
        if not str(user_id or "").strip():
            raise ValueError("user_id is required")
        state = self._store.load_state()
        ws = self._store._load_org(state, workspace_id)
        self.require_permission(ws, actor, "manage_members")
        members = ws.setdefault("members", [])
        existing = next((m for m in members if m.get("user_id") == user_id), None)
        if existing:
            existing["role"] = role
            existing["updated_at"] = _now()
        else:
            members.append({"user_id": user_id, "role": role, "added_at": _now()})
        ws["updated_at"] = _now()
        self._store.save_state(state)
        self._store.record_timeline_event("workspace", "member_added", {"workspace_id": workspace_id, "user_id": user_id, "role": role})
        return self._store._workspace_public(ws, actor)

    # add thin wrappers for update/remove if needed by store
