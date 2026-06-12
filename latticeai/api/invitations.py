"""Invitation API: create, list, and accept workspace invitations."""

from __future__ import annotations

from typing import Callable, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel


class InvitationCreateRequest(BaseModel):
    email: Optional[str] = None
    workspace_id: Optional[str] = None
    role: str = "member"
    expires_hours: int = 168


def create_invitations_router(
    *,
    invitation_store,
    workspace_service,
    require_admin: Callable,
    require_user: Callable[[Request], str],
    user_id_for_email: Callable[[Optional[str]], Optional[str]],
    append_audit_event: Callable[..., None],
) -> APIRouter:
    router = APIRouter()

    @router.get("/invitations")
    async def list_invitations(request: Request):
        require_admin(request)
        return {"invitations": invitation_store.list()}

    @router.post("/invitations")
    async def create_invitation(req: InvitationCreateRequest, request: Request):
        admin_email, _ = require_admin(request)
        actor_id = user_id_for_email(admin_email)
        if req.workspace_id:
            try:
                workspace_service.store._require_permission(
                    workspace_service.store._load_org(workspace_service.store.load_state(), req.workspace_id),
                    actor_id,
                    "manage_members",
                )
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail=f"Workspace not found: {req.workspace_id}") from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
        if req.role not in {"owner", "admin", "member", "viewer"}:
            raise HTTPException(status_code=400, detail="unknown invitation role")
        invitation = invitation_store.create(
            email=req.email,
            workspace_id=req.workspace_id,
            role=req.role,
            created_by=actor_id,
            expires_hours=req.expires_hours,
        )
        append_audit_event(
            "invitation_created",
            user_email=admin_email,
            invitation_id=invitation.get("id"),
            workspace_id=req.workspace_id,
            role=req.role,
        )
        return {"invitation": invitation}

    @router.post("/invitations/{token}/accept")
    async def accept_invitation(token: str, request: Request):
        email = require_user(request)
        user_id = user_id_for_email(email)
        if not user_id:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            invitation = invitation_store.accept(token, accepted_by=user_id, email=email or None)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Invitation not found") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        workspace_id = invitation.get("workspace_id")
        if workspace_id:
            try:
                workspace_service.add_member(
                    workspace_id,
                    user_id=user_id,
                    role=invitation.get("role") or "member",
                    actor=invitation.get("created_by"),
                )
            except Exception as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        append_audit_event(
            "invitation_accepted",
            user_email=email,
            invitation_id=invitation.get("id"),
            workspace_id=workspace_id,
        )
        return {"invitation": invitation}

    return router
