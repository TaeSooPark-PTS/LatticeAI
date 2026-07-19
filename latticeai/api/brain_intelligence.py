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

    @router.post("/api/brain/consolidate")
    async def brain_consolidate(req: ConsolidateRequest, request: Request):
        user = require_user(request)
        scope = gate_write(request) if req.apply else gate_read(request)
        result = service.consolidate(apply=req.apply, user_email=user, workspace_id=scope)
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
