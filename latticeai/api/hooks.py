"""Hooks platform API router (v3.2.0).

Exposes the lifecycle :class:`~latticeai.core.hooks.HooksRegistry` over HTTP so
the /app Hooks view can list, inspect, enable/disable, reorder, and register
hooks. Full paths live in the decorators (no ``prefix=``), matching the rest of
the API.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from latticeai.core.hooks import HooksRegistry


class HookToggleRequest(BaseModel):
    hook_id: str
    enabled: bool = True


class HookReorderRequest(BaseModel):
    kind: str
    ordered_ids: List[str] = []


class HookRegisterRequest(BaseModel):
    name: str
    kind: str
    description: str = ""
    command: str = ""
    order: Optional[int] = None
    enabled: bool = True


def create_hooks_router(
    *,
    registry: HooksRegistry,
    require_user: Callable[[Request], str],
    append_audit_event: Callable[..., None],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/hooks")
    async def list_hooks(request: Request, kind: Optional[str] = None):
        require_user(request)
        return registry.list(kind=kind)

    @router.get("/api/hooks/{hook_id:path}")
    async def inspect_hook(hook_id: str, request: Request):
        require_user(request)
        try:
            return {"hook": registry.inspect(hook_id)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Hook not found: {hook_id}") from exc

    @router.post("/api/hooks/enable")
    async def enable_hook(req: HookToggleRequest, request: Request):
        user = require_user(request)
        try:
            hook = registry.set_enabled(req.hook_id, req.enabled)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Hook not found: {req.hook_id}") from exc
        append_audit_event("hook_toggle", user_email=user, hook_id=req.hook_id, enabled=req.enabled)
        return {"hook": hook}

    @router.post("/api/hooks/disable")
    async def disable_hook(req: HookToggleRequest, request: Request):
        user = require_user(request)
        try:
            hook = registry.set_enabled(req.hook_id, False)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Hook not found: {req.hook_id}") from exc
        append_audit_event("hook_toggle", user_email=user, hook_id=req.hook_id, enabled=False)
        return {"hook": hook}

    @router.post("/api/hooks/reorder")
    async def reorder_hooks(req: HookReorderRequest, request: Request):
        require_user(request)
        return registry.reorder(req.kind, req.ordered_ids)

    @router.post("/api/hooks/register")
    async def register_hook(req: HookRegisterRequest, request: Request):
        user = require_user(request)
        try:
            entry = registry.register(
                name=req.name,
                kind=req.kind,
                description=req.description,
                command=req.command,
                order=req.order,
                enabled=req.enabled,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        append_audit_event("hook_register", user_email=user, hook_id=entry["id"], kind=entry["kind"])
        return {"hook": entry}

    @router.delete("/api/hooks/{hook_id:path}")
    async def remove_hook(hook_id: str, request: Request):
        user = require_user(request)
        try:
            result = registry.remove(hook_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Hook not found: {hook_id}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        append_audit_event("hook_remove", user_email=user, hook_id=hook_id)
        return result

    return router
