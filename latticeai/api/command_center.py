"""Command Center API router (v9.5.0).

Read-only surface over :class:`~latticeai.services.command_center.CommandCenterService`:

* ``GET /api/command/briefing`` — the daily briefing: recent knowledge,
  conversation activity, automation state, pending reviews, health snapshot,
  top suggestions, and state-derived quick actions.
* ``GET /api/command/search?q=…`` — universal search across knowledge nodes,
  the user's conversations, and installed automations. Powers Cmd+K.

Both endpoints are scoped to the requesting user and workspace via the same
require_user/gate_read contract as the rest of the platform.
"""

from __future__ import annotations

from typing import Callable, Optional

from fastapi import APIRouter, Query, Request

from latticeai.services.command_center import CommandCenterService


def create_command_center_router(
    *,
    service: CommandCenterService,
    require_user: Callable[[Request], str],
    gate_read: Callable[[Request], Optional[str]],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/command/briefing")
    async def command_briefing(request: Request):
        user = require_user(request)
        scope = gate_read(request)
        return service.briefing(user_email=user, workspace_id=scope)

    @router.get("/api/command/search")
    async def command_search(
        request: Request,
        q: str = Query("", max_length=300),
        limit: int = Query(8, ge=1, le=20),
    ):
        user = require_user(request)
        scope = gate_read(request)
        return service.search(q, user_email=user, workspace_id=scope, limit=limit)

    return router


__all__ = ["create_command_center_router"]
