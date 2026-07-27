"""Network boundary API — local_only / cloud_allowed dial (hybrid Phase 1–2)."""

from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from latticeai.core.network_boundary import network_mode_catalog, normalize_network_mode
from latticeai.services.hybrid_context import build_minimal_context
from latticeai.services.network_boundary_service import NetworkBoundaryService
from latticeai.services.cloud_token_guard import budget_for


class SetNetworkBoundaryRequest(BaseModel):
    mode: str = Field(..., description="local_only | cloud_allowed")
    workspace_id: Optional[str] = None
    acknowledge_risk: bool = False


class PreviewRequest(BaseModel):
    message: str
    workspace_id: Optional[str] = None
    top_k: int = 6


def create_network_boundary_router(
    *,
    service: NetworkBoundaryService,
    require_user: Callable[..., str],
    knowledge_graph: Any = None,
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
        payload = service.get(user_email=user, workspace_id=scope)
        scope_key = f"{user or 'anon'}|{scope or 'global'}"
        payload["token_budget"] = budget_for(scope_key).snapshot()
        return payload

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

    @router.post("/api/network-boundary/preview")
    async def preview_cloud_context(body: PreviewRequest, request: Request):
        """Transparency panel: which local nodes would leave the machine."""
        user = require_user(request)
        header_ws = request.headers.get("X-Workspace-Id")
        scope = body.workspace_id or (header_ws.strip() if header_ws else None)
        mode = service.resolve(user_email=user, workspace_id=scope)
        minimal = build_minimal_context(
            body.message,
            store=knowledge_graph,
            mode=mode,
            top_k=max(1, min(int(body.top_k or 6), 12)),
            allowed_workspaces={scope} if scope else None,
        )
        scope_key = f"{user or 'anon'}|{scope or 'global'}"
        budget = budget_for(scope_key)
        refusal = budget.check_turn(minimal.token_estimate)
        return {
            "mode": mode.value,
            "allows_cloud": mode.value == "cloud_allowed",
            "node_ids": minimal.node_ids,
            "keywords": minimal.keywords,
            "titles": [str(n.get("title") or n.get("id") or "") for n in minimal.nodes],
            "types": [str(n.get("type") or "") for n in minimal.nodes],
            "token_estimate": minimal.token_estimate,
            "quality": minimal.quality,
            "compact_preview": minimal.compact_text[:1200],
            "token_budget": budget.snapshot(),
            "would_block": refusal,
        }

    return router


__all__ = [
    "create_network_boundary_router",
    "SetNetworkBoundaryRequest",
    "PreviewRequest",
]
