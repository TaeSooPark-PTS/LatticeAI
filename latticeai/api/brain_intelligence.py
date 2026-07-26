"""Proactive Brain Intelligence API router (v9.3.0).

Exposes :class:`~latticeai.services.brain_intelligence.BrainIntelligenceService`:
health diagnosis, proactive insights, contradiction surfacing, and consent-first
consolidation. Read endpoints use the workspace read gate; consolidation with
``apply=true`` is a write and is audited.
"""

from __future__ import annotations

from typing import Callable, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from latticeai.services.brain_intelligence import BrainIntelligenceService


class ConsolidateRequest(BaseModel):
    apply: bool = False
    # v9.6.x additive alias: dry_run=true means apply=false. When provided it
    # takes precedence over ``apply`` (explicit intent wins); omitted keeps the
    # v9.3.0 contract unchanged. Default behaviour is always a dry run.
    dry_run: Optional[bool] = None

    def effective_apply(self) -> bool:
        if self.dry_run is not None:
            return not self.dry_run
        return self.apply


def create_brain_intelligence_router(
    *,
    service: BrainIntelligenceService,
    require_user: Callable[[Request], str],
    gate_read: Callable[[Request], Optional[str]],
    gate_write: Callable[[Request], Optional[str]],
    append_audit_event: Callable[..., None],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/brain/health")
    async def brain_health(request: Request):
        user = require_user(request)
        scope = gate_read(request)
        return service.health_report(user_email=user, workspace_id=scope)

    @router.get("/api/brain/insights")
    async def brain_insights(request: Request):
        user = require_user(request)
        scope = gate_read(request)
        return service.insights(user_email=user, workspace_id=scope)

    @router.get("/api/brain/contradictions")
    async def brain_contradictions(request: Request):
        user = require_user(request)
        scope = gate_read(request)
        return service.contradictions(user_email=user, workspace_id=scope)

    @router.get("/api/brain/garden")
    async def brain_garden(request: Request, limit: int = 8):
        """Knowledge garden overview (v9.9.7): recent / contradictions /
        stale / frequent, read-only and workspace-scoped."""
        user = require_user(request)
        scope = gate_read(request)
        return service.garden_overview(user_email=user, workspace_id=scope, limit=limit)

    @router.get("/api/brain/vector-freshness")
    async def brain_vector_freshness(request: Request):
        """Vector index freshness summary (read-only, never raises).

        Fixed contract consumed by the frontend:
        ``{"status": "ready"|"pending"|"unavailable", "pending_items": int,
        "total_items": int, "detail": str}``.
        """
        user = require_user(request)
        scope = gate_read(request)
        return service.vector_freshness(user_email=user, workspace_id=scope)

    @router.get("/api/brain/duplicates")
    async def brain_duplicates(request: Request):
        """Graph-layer duplicate node candidates (read-only)."""
        user = require_user(request)
        scope = gate_read(request)
        return service.graph_duplicates(user_email=user, workspace_id=scope)

    @router.get("/api/brain/quality-report")
    async def brain_quality_report(request: Request):
        """Combined graph quality report: duplicates, contradictions, stale
        nodes, edge quality (read-only)."""
        user = require_user(request)
        scope = gate_read(request)
        return service.quality_report(user_email=user, workspace_id=scope)

    @router.post("/api/brain/consolidate")
    async def brain_consolidate(req: ConsolidateRequest, request: Request):
        user = require_user(request)
        apply = req.effective_apply()
        scope = gate_write(request) if apply else gate_read(request)
        result = service.consolidate(apply=apply, user_email=user, workspace_id=scope)
        append_audit_event(
            "brain_consolidate",
            user_email=user,
            mode=result.get("mode"),
            duplicate_memories=result.get("duplicate_memory_count", 0),
            pruned=result.get("pruned", 0),
        )
        return result

    return router


__all__ = ["create_brain_intelligence_router"]
