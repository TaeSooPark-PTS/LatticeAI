"""Network boundary API — local_only / cloud_allowed dial + Phase 3 policy/UI."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from latticeai.core.messages import http_error, resolve_language, translate
from latticeai.core.network_boundary import network_mode_catalog, normalize_network_mode
from latticeai.services.cloud_egress_audit import record_cloud_egress
from latticeai.services.cloud_token_guard import budget_for
from latticeai.services.hybrid_context import build_minimal_context
from latticeai.services.hybrid_policy import HybridPolicyService
from latticeai.services.network_boundary_service import NetworkBoundaryService


class SetNetworkBoundaryRequest(BaseModel):
    mode: str = Field(..., description="local_only | cloud_allowed")
    workspace_id: Optional[str] = None
    acknowledge_risk: bool = False


class PreviewRequest(BaseModel):
    message: str
    workspace_id: Optional[str] = None
    top_k: int = 6


class SetNodeSensitivityRequest(BaseModel):
    node_id: str
    local_only: bool = True
    reason: Optional[str] = None
    workspace_id: Optional[str] = None


class SetHybridPolicyRequest(BaseModel):
    workspace_id: Optional[str] = None
    blocked_node_types: Optional[list[str]] = None
    blocked_metadata_flags: Optional[list[str]] = None
    auto_commit: Optional[bool] = None
    allow_multimodal: Optional[bool] = None
    min_extraction_confidence: Optional[float] = None


def create_network_boundary_router(
    *,
    service: NetworkBoundaryService,
    require_user: Callable[..., str],
    knowledge_graph: Any = None,
    policy_service: Optional[HybridPolicyService] = None,
) -> APIRouter:
    router = APIRouter(tags=["network-boundary"])

    def _scope(request: Request, workspace_id: Optional[str] = None) -> tuple[str, Optional[str]]:
        user = require_user(request)
        header_ws = request.headers.get("X-Workspace-Id")
        scope = workspace_id or (header_ws.strip() if header_ws else None)
        return user, scope

    @router.get("/api/network-boundary")
    async def get_network_boundary(
        request: Request,
        workspace_id: Optional[str] = None,
    ):
        user, scope = _scope(request, workspace_id)
        payload = service.get(user_email=user, workspace_id=scope)
        scope_key = f"{user or 'anon'}|{scope or 'global'}"
        payload["token_budget"] = budget_for(scope_key).snapshot()
        if policy_service is not None:
            payload["policy"] = policy_service.resolve(
                user_email=user, workspace_id=scope
            )
        return payload

    @router.get("/api/network-boundary/catalog")
    async def network_boundary_catalog(request: Request):
        require_user(request)
        return {"modes": network_mode_catalog()}

    @router.get("/api/network-boundary/ui-state")
    async def network_boundary_ui_state(
        request: Request,
        workspace_id: Optional[str] = None,
    ):
        """Compact payload for the progressive-enhancement toggle panel."""
        user, scope = _scope(request, workspace_id)
        mode_payload = service.get(user_email=user, workspace_id=scope)
        policy = (
            policy_service.resolve(user_email=user, workspace_id=scope)
            if policy_service is not None
            else {}
        )
        scope_key = f"{user or 'anon'}|{scope or 'global'}"
        return {
            "mode": mode_payload.get("mode"),
            "label": mode_payload.get("label"),
            "label_ko": mode_payload.get("label_ko"),
            "allows_cloud": mode_payload.get("allows_cloud"),
            "requires_ack": mode_payload.get("requires_ack"),
            "warning_ko": next(
                (
                    m.get("warning_ko")
                    for m in (mode_payload.get("catalog") or [])
                    if m.get("id") == mode_payload.get("mode")
                ),
                None,
            ),
            "policy": policy,
            "token_budget": budget_for(scope_key).snapshot(),
            "catalog": network_mode_catalog(),
        }

    @router.post("/api/network-boundary")
    async def set_network_boundary(body: SetNetworkBoundaryRequest, request: Request):
        user, scope = _scope(request, body.workspace_id)
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
        user, scope = _scope(request, body.workspace_id)
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

    @router.post("/api/network-boundary/node-sensitivity")
    async def set_node_sensitivity(body: SetNodeSensitivityRequest, request: Request):
        """Mark one memory as never-leaving (or clear the mark).

        The cloud filter has always looked for this flag. Until 10.2.0 nothing
        in the product could set it, so the guard could not fire. Ingestion
        stamps secret-bearing paths automatically; this covers what a path
        cannot tell you — a note whose *content* is private.
        """
        user, scope = _scope(request, body.workspace_id)
        if knowledge_graph is None or not hasattr(knowledge_graph, "set_node_sensitivity"):
            raise http_error(501, "common.graph_unavailable", resolve_language(request))
        result = knowledge_graph.set_node_sensitivity(
            body.node_id, local_only=bool(body.local_only), reason=body.reason
        )
        if not result.get("ok"):
            raise HTTPException(
                status_code=404,
                detail=result.get("reason")
                or translate("graph.node_not_found", resolve_language(request)),
            )
        record_cloud_egress(
            node_ids=[body.node_id], token_estimate=0, mode="(policy)",
            provider="(local)", user_email=user, workspace_id=scope,
            outcome="marked_local_only" if body.local_only else "cleared_local_only",
            detail=body.reason,
        )
        return result

    @router.get("/api/network-boundary/policy")
    async def get_hybrid_policy(
        request: Request,
        workspace_id: Optional[str] = None,
    ):
        if policy_service is None:
            raise http_error(501, "boundary.policy_not_configured", resolve_language(request))
        user, scope = _scope(request, workspace_id)
        return policy_service.resolve(user_email=user, workspace_id=scope)

    @router.post("/api/network-boundary/policy")
    async def set_hybrid_policy(body: SetHybridPolicyRequest, request: Request):
        if policy_service is None:
            raise http_error(501, "boundary.policy_not_configured", resolve_language(request))
        user, scope = _scope(request, body.workspace_id)
        patch: Dict[str, Any] = {}
        for key in (
            "blocked_node_types",
            "blocked_metadata_flags",
            "auto_commit",
            "allow_multimodal",
            "min_extraction_confidence",
        ):
            val = getattr(body, key, None)
            if val is not None:
                patch[key] = val
        return policy_service.set_policy(
            patch, user_email=user, workspace_id=scope, source="api"
        )

    return router


__all__ = [
    "create_network_boundary_router",
    "SetNetworkBoundaryRequest",
    "PreviewRequest",
    "SetHybridPolicyRequest",
    "SetNodeSensitivityRequest",
]
