"""Memory platform + Memory Manager API router (v3.2.0).

Exposes :class:`~latticeai.services.memory_service.MemoryService` so the /app
Memory view can inspect every memory tier, recall across them, and run manager
operations (prune / compact / rebuild / clear). Full paths in decorators.
"""

from __future__ import annotations

from typing import Callable, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from latticeai.services.memory_service import MemoryService


class RecallRequest(BaseModel):
    query: str = ""
    limit: int = 20


class PruneRequest(BaseModel):
    ids: List[str] = []
    kind: Optional[str] = None


class RebuildRequest(BaseModel):
    target: str = "vector"


class ClearRequest(BaseModel):
    scope: str
    confirm: bool = False


def create_memory_router(
    *,
    service: MemoryService,
    require_user: Callable[[Request], str],
    get_current_user: Callable[[Request], Optional[str]],
    gate_read: Callable[[Request], Optional[str]],
    gate_write: Callable[[Request], Optional[str]],
    append_audit_event: Callable[..., None],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/memory/manager")
    async def memory_manager(request: Request):
        user = require_user(request)
        scope = gate_read(request)
        return service.manager(user_email=user, workspace_id=scope)

    @router.get("/api/memory/tiers")
    async def memory_tiers(request: Request):
        require_user(request)
        return service.tiers()

    @router.get("/api/memory/inspect")
    async def memory_inspect(request: Request, source: str, limit: int = 50):
        user = require_user(request)
        scope = gate_read(request)
        try:
            return service.inspect(source, user_email=user, workspace_id=scope, limit=limit)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Unknown memory source: {source}") from exc

    @router.post("/api/memory/recall")
    async def memory_recall(req: RecallRequest, request: Request):
        user = require_user(request)
        scope = gate_read(request)
        return service.recall(req.query, user_email=user, workspace_id=scope, limit=req.limit)

    @router.post("/api/memory/prune")
    async def memory_prune(req: PruneRequest, request: Request):
        user = require_user(request)
        gate_write(request)
        result = service.prune(ids=req.ids, kind=req.kind, user_email=user)
        append_audit_event("memory_prune", user_email=user, count=result.get("count", 0))
        return result

    @router.post("/api/memory/compact")
    async def memory_compact(request: Request):
        user = require_user(request)
        gate_write(request)
        result = service.compact(user_email=user)
        append_audit_event("memory_compact", user_email=user, compacted=result.get("compacted", 0))
        return result

    @router.post("/api/memory/rebuild")
    async def memory_rebuild(req: RebuildRequest, request: Request):
        user = require_user(request)
        gate_write(request)
        result = service.rebuild(req.target)
        append_audit_event("memory_rebuild", user_email=user, target=req.target, status=result.get("status"))
        return result

    @router.post("/api/memory/clear")
    async def memory_clear(req: ClearRequest, request: Request):
        user = require_user(request)
        gate_write(request)
        try:
            result = service.clear(scope=req.scope, confirm=req.confirm, user_email=user)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        append_audit_event("memory_clear", user_email=user, scope=req.scope)
        return result

    return router
