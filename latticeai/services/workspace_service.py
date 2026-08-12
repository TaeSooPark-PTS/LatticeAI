"""Workspace service layer: scope resolution, permission guardrails, and a
single seam in front of :class:`WorkspaceOSStore`.

This module centralizes the workspace_id resolution and role/permission checks
that were previously scattered across the FastAPI handlers. It depends only on
``workspace_os`` (and indirectly ``enterprise``), never on the FastAPI app, so
both the app assembly and the API routers can import it freely.

Guardrail summary (v1.2.0):

* **Explicit workspace targeting is gated.** When a caller names a workspace
  (``X-Workspace-Id`` header / ``workspace_id``), reads require ``read`` and
  writes require ``write`` on that workspace; non-members are rejected.
* **Backward compatible default.** With no workspace named, the scope resolves
  to the *active* workspace (Personal by default). Pre-1.1 clients that never
  send a header keep operating on Personal data exactly as before.
* **Personal workspace** always grants its single local user owner rights.
* **No-auth local mode** keeps the owner fallback for *ownerless* org
  workspaces (the anonymous local user owns what they create), but a *named*
  stranger never bypasses membership.
* **Graph and installed Skills are intentionally machine-global shared state**
  (the local knowledge graph and on-disk skills are not partitioned per
  workspace). Skill enable/disable and other actions still record
  workspace-scoped timeline events via the active workspace.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from latticeai.core.workspace_os import WorkspaceOSStore


class WorkspaceService:
    """Permission-aware façade over :class:`WorkspaceOSStore`."""

    # Graph / installed-skill state that is shared machine-wide rather than
    # partitioned per workspace. Surfaced so the UI / docs can be explicit.
    SHARED_GLOBAL_AREAS = ("graph", "skills")

    def __init__(self, store: WorkspaceOSStore, *, resolve_user_id: Optional[Callable[[Optional[str]], Optional[str]]] = None):
        self.store = store
        self._resolve_user_id = resolve_user_id or (lambda user_id: user_id)

    def _identity(self, user_id: Optional[str]) -> Optional[str]:
        if isinstance(user_id, str) and user_id.startswith("user:"):
            return user_id
        return self._resolve_user_id(user_id)

    # ── scope resolution + gating ────────────────────────────────────────

    def _ensure_permission(self, workspace_id: str, user_id: Optional[str], permission: str) -> None:
        resolved_user = self._identity(user_id)
        if not self.store.has_permission(workspace_id, resolved_user, permission):
            raise PermissionError(
                f"'{user_id or 'anonymous'}' lacks '{permission}' on workspace '{workspace_id}'"
            )

    def resolve_read_scope(self, requested: Optional[str], user_id: Optional[str]) -> str:
        """Resolve + authorize the workspace a read should target.

        ``None`` falls back to the active workspace (Personal by default), which
        preserves pre-1.1 behaviour. An explicitly named workspace is gated on
        ``read`` so non-members cannot read organization data.
        """
        workspace_id = requested or self.store._active_workspace_id()
        self._ensure_permission(workspace_id, user_id, "read")
        return workspace_id

    def resolve_write_scope(self, requested: Optional[str], user_id: Optional[str]) -> str:
        """Resolve + authorize the workspace a write should target (gated on ``write``)."""
        workspace_id = requested or self.store._active_workspace_id()
        self._ensure_permission(workspace_id, user_id, "write")
        return workspace_id

    def readable_workspaces(self, user_id: Optional[str]) -> list[str]:
        """Return workspace ids the caller can read.

        This keeps scoped read APIs from each reconstructing membership logic
        from raw workspace state. The personal workspace remains readable via
        the store's normal permission rules.
        """
        resolved_user = self._identity(user_id)
        workspaces = (self.store.load_state().get("workspaces") or {})
        return [
            str(workspace_id)
            for workspace_id in workspaces
            if self.store.has_permission(str(workspace_id), resolved_user, "read")
        ]

    # ── record-level authorization (by-id access must not bypass gating) ──

    def authorize_record_read(self, record: Dict[str, Any], user_id: Optional[str]) -> None:
        """Authorize reading a record against ITS OWN workspace.

        Records predating workspace scoping carry no workspace_id and remain
        readable (legacy-global compatibility); a scoped record requires read
        permission on its workspace regardless of any caller-supplied header.
        """
        workspace_id = (record or {}).get("workspace_id")
        if workspace_id:
            self._ensure_permission(workspace_id, user_id, "read")

    def authorize_memory_delete(self, record: Dict[str, Any], user_id: Optional[str]) -> None:
        """Delete requires owning the memory or write access to its workspace.

        Ownerless records with no workspace keep their pre-v4 behaviour
        (deletable by any authenticated local user).
        """
        owner = (record or {}).get("user_email")
        workspace_id = (record or {}).get("workspace_id")
        resolved_user = self._identity(user_id)
        if owner and owner in {user_id, resolved_user}:
            return
        if workspace_id:
            self._ensure_permission(workspace_id, resolved_user, "write")
            return
        if owner and owner not in {user_id, resolved_user}:
            raise PermissionError(
                f"'{user_id or 'anonymous'}' is not the owner of memory '{record.get('id')}'"
            )

    # ── workspace registry / summary ─────────────────────────────────────

    def summary(self, user_id: Optional[str]) -> Dict[str, Any]:
        data = self.store.summary()
        data["workspace_registry"] = self.store.list_workspaces(user_id=self._identity(user_id))
        data["shared_global_areas"] = list(self.SHARED_GLOBAL_AREAS)
        return data

    def list_workspaces(self, user_id: Optional[str]) -> Dict[str, Any]:
        return self.store.list_workspaces(user_id=self._identity(user_id))

    def get_workspace(self, workspace_id: str, user_id: Optional[str]) -> Dict[str, Any]:
        # Reading workspace metadata requires read access to that workspace.
        self._ensure_permission(workspace_id, user_id, "read")
        return self.store.get_workspace(workspace_id, user_id=self._identity(user_id))

    def workspace_summary(self, workspace_id: str, user_id: Optional[str]) -> Dict[str, Any]:
        self._ensure_permission(workspace_id, user_id, "read")
        return self.store.workspace_summary(workspace_id, user_id=self._identity(user_id))

    # ── organization workspace management (delegates with actor) ─────────

    def create_organization_workspace(self, *, name: str, owner_user_id: Optional[str], settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self.store.create_organization_workspace(name=name, owner_user_id=self._identity(owner_user_id), settings=settings)

    def update_workspace(self, workspace_id: str, *, name=None, settings=None, actor=None) -> Dict[str, Any]:
        return self.store.update_workspace(workspace_id, name=name, settings=settings, actor=self._identity(actor))

    def archive_workspace(self, workspace_id: str, *, actor=None) -> Dict[str, Any]:
        return self.store.archive_workspace(workspace_id, actor=self._identity(actor))

    def add_member(self, workspace_id: str, *, user_id: str, role: str = "member", actor=None) -> Dict[str, Any]:
        return self.store.add_member(workspace_id, user_id=self._identity(user_id) or user_id, role=role, actor=self._identity(actor))

    def update_member_role(self, workspace_id: str, *, user_id: str, role: str, actor=None) -> Dict[str, Any]:
        return self.store.update_member_role(workspace_id, user_id=self._identity(user_id) or user_id, role=role, actor=self._identity(actor))

    def remove_member(self, workspace_id: str, *, user_id: str, actor=None) -> Dict[str, Any]:
        return self.store.remove_member(workspace_id, user_id=self._identity(user_id) or user_id, actor=self._identity(actor))

    def set_active_workspace(self, workspace_id: str, user_id: Optional[str]) -> Dict[str, Any]:
        # Membership is enforced inside the store for organization workspaces.
        return self.store.set_active_workspace(workspace_id, user_id=self._identity(user_id))
