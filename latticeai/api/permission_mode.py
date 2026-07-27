"""Permission mode API — strict / trusted / bypass dial (v9.9.8)."""

from __future__ import annotations

from typing import Callable, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from latticeai.core.permission_mode import mode_catalog, normalize_mode
from latticeai.services.permission_mode_service import PermissionModeService


class SetPermissionModeRequest(BaseModel):
    mode: str = Field(..., description="strict | trusted | bypass")
    workspace_id: Optional[str] = None
    acknowledge_risk: bool = False


def create_permission_mode_router(
    *,
    service: PermissionModeService,
    require_user: Callable[..., str],
) -> APIRouter:
    router = APIRouter(tags=["permission-mode"])

    @router.get("/api/permission-mode")
    async def get_permission_mode(
        request: Request,
        workspace_id: Optional[str] = None,
    ):
        user = require_user(request)
        header_ws = request.headers.get("X-Workspace-Id")
        scope = workspace_id or (header_ws.strip() if header_ws else None)
        return service.get(user_email=user, workspace_id=scope)

    @router.get("/api/permission-mode/catalog")
    async def permission_mode_catalog(request: Request):
        require_user(request)
        return {"modes": mode_catalog()}

    @router.post("/api/permission-mode")
    async def set_permission_mode(body: SetPermissionModeRequest, request: Request):
        user = require_user(request)
        header_ws = request.headers.get("X-Workspace-Id")
        scope = body.workspace_id or (header_ws.strip() if header_ws else None)
        try:
            return service.set_mode(
                normalize_mode(body.mode),
                user_email=user,
                workspace_id=scope,
                acknowledge_risk=body.acknowledge_risk,
                source="api",
            )
        except PermissionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router


__all__ = ["create_permission_mode_router", "SetPermissionModeRequest"]
