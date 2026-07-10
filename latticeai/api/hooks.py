"""Hooks platform API router (v3.2.0).

Exposes the lifecycle :class:`~lattice_brain.runtime.hooks.HooksRegistry` over HTTP so
the /app Hooks view can list, inspect, enable/disable, reorder, and register
hooks. Full paths live in the decorators (no ``prefix=``), matching the rest of
the API.
"""

from __future__ import annotations

from typing import Callable, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from lattice_brain.runtime.hooks import HooksRegistry


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


class HookRunRequest(BaseModel):
    kind: Optional[str] = None
    hook_id: Optional[str] = None
    event: str = ""
    payload: dict = {}


def create_hooks_router(
    *,
    registry: HooksRegistry,
    require_user: Callable[[Request], str],
    require_admin: Callable[[Request], tuple],
    append_audit_event: Callable[..., None],
) -> APIRouter:
    router = APIRouter()

    def _require_admin_email(request: Request) -> str:
        """Authorize a hook mutation and return the acting administrator."""
        result = require_admin(request)
        if isinstance(result, tuple):
            return str(result[0] or "")
        # Keep the router tolerant of small test/runtime adapters that return
        # only the identity while the production access runtime returns
        # ``(email, users)``.
        return str(result or "")

    @router.get("/api/hooks")
    async def list_hooks(request: Request, kind: Optional[str] = None):
        require_user(request)
        return registry.list(kind=kind)

    # NOTE: declared before the ``/{hook_id:path}`` catch-all so "runs" is not
    # captured as a hook id.
    @router.get("/api/hooks/runs")
    async def hook_runs(request: Request, limit: int = 50, kind: Optional[str] = None):
        require_user(request)
        return registry.recent_runs(limit=limit, kind=kind)

    @router.post("/api/hooks/run")
    async def run_hooks(req: HookRunRequest, request: Request):
        """Execute hooks now — by ``kind`` (all enabled hooks of that kind) or a
        single ``hook_id``. Returns the dispatch record so callers can see what
        ran, in what order, and whether the action was blocked."""
        user = _require_admin_email(request)
        try:
            if req.hook_id:
                result = registry.run_hook(req.hook_id, event=req.event or None, payload=req.payload, user_email=user)
            elif req.kind:
                result = registry.run_hooks(req.kind, event=req.event or None, payload=req.payload, user_email=user)
            else:
                raise HTTPException(status_code=400, detail="Provide a 'kind' or a 'hook_id' to run.")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Hook not found: {exc}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        append_audit_event("hook_run", user_email=user, hook_id=req.hook_id, kind=req.kind, event=req.event)
        return result

    # Alias for the same dispatch action (fire == run).
    @router.post("/api/hooks/fire")
    async def fire_hooks(req: HookRunRequest, request: Request):
        return await run_hooks(req, request)

    @router.get("/api/hooks/{hook_id:path}")
    async def inspect_hook(hook_id: str, request: Request):
        require_user(request)
        try:
            return {"hook": registry.inspect(hook_id)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Hook not found: {hook_id}") from exc

    @router.post("/api/hooks/enable")
    async def enable_hook(req: HookToggleRequest, request: Request):
        user = _require_admin_email(request)
        try:
            hook = registry.set_enabled(req.hook_id, req.enabled)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Hook not found: {req.hook_id}") from exc
        append_audit_event("hook_toggle", user_email=user, hook_id=req.hook_id, enabled=req.enabled)
        return {"hook": hook}

    @router.post("/api/hooks/disable")
    async def disable_hook(req: HookToggleRequest, request: Request):
        user = _require_admin_email(request)
        try:
            hook = registry.set_enabled(req.hook_id, False)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Hook not found: {req.hook_id}") from exc
        append_audit_event("hook_toggle", user_email=user, hook_id=req.hook_id, enabled=False)
        return {"hook": hook}

    @router.post("/api/hooks/reorder")
    async def reorder_hooks(req: HookReorderRequest, request: Request):
        _require_admin_email(request)
        return registry.reorder(req.kind, req.ordered_ids)

    @router.post("/api/hooks/register")
    async def register_hook(req: HookRegisterRequest, request: Request):
        user = _require_admin_email(request)
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
        user = _require_admin_email(request)
        try:
            result = registry.remove(hook_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Hook not found: {hook_id}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        append_audit_event("hook_remove", user_email=user, hook_id=hook_id)
        return result

    return router
