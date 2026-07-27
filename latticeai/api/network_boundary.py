"""Network boundary API — local_only / cloud_allowed dial (hybrid Phase 1)."""

from __future__ import annotations

from typing import Callable, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from latticeai.core.network_boundary import network_mode_catalog, normalize_network_mode
from latticeai.services.network_boundary_service import NetworkBoundaryService


class SetNetworkBoundaryRequest(BaseModel):
    mode: str = Field(..., description="local_only | cloud_allowed")
    workspace_id: Optional[str] = None
    acknowledge_risk: bool = False


def create_network_boundary_router(
    *,
    service: NetworkBoundaryService,
    require_user: Callable[..., str],
) -> APIRouter:
    router = APIRouter(tags=["network-boundary"])

    @router.get("/api/network-boundary")
    async def get_network_boundary(
        request: Request,
        workspace_id: Optional[str] = None,
    ):
        user = require_user(request)
        header_ws = request.headers.get("X-Workspace-Id")
        scope = workspace_id or (header_ws.strip() if header_ws else None)
        return service.get(user_email=user, workspace_id=scope)

    @router.get("/api/network-boundary/catalog")
    async def network_boundary_catalog(request: Request):
        require_user(request)
        return {"modes": network_mode_catalog()}

    @router.post("/api/network-boundary")
    async def set_network_boundary(body: SetNetworkBoundaryRequest, request: Request):
        user = require_user(request)
        header_ws = request.headers.get("X-Workspace-Id")
        scope = body.workspace_id or (header_ws.strip() if header_ws else None)
        try:
            return service.set_mode(
                normalize_network_mode(body.mode),
                user_email=user,
                workspace_id=scope,
                acknowledge_risk=body.acknowledge_risk,
                source="api",
            )
        except PermissionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router


__all__ = ["create_network_boundary_router", "SetNetworkBoundaryRequest"]
