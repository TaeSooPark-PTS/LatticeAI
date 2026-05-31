"""Realtime Collaboration API router (v2.0).

Server-Sent-Events stream + presence + activity feed over
:class:`latticeai.core.realtime.RealtimeBus`. Workspace isolation is enforced by
resolving each caller's allowed workspace scope before subscribing; single-user
local mode works with no scope restriction.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Set

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel


class PresenceRequest(BaseModel):
    client_id: Optional[str] = None
    workspace_id: Optional[str] = None


def create_realtime_router(
    *,
    bus,
    require_user: Callable[[Request], str],
    get_current_user: Callable[[Request], Optional[str]],
    allowed_scopes: Callable[[Optional[str]], Optional[Set[str]]],
    ui_file_response: Optional[Callable[[Path], Any]] = None,
    static_dir: Optional[Path] = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/activity")
    async def activity_page(request: Request):
        require_user(request)
        if ui_file_response is None or static_dir is None:
            raise HTTPException(status_code=404, detail="Activity UI not available.")
        page = static_dir / "activity.html"
        if not page.exists():
            raise HTTPException(status_code=404, detail="Activity UI not found.")
        return ui_file_response(page)

    @router.get("/realtime/stream")
    async def realtime_stream(request: Request):
        user = require_user(request)
        scope = allowed_scopes(user or None)
        sub_id = secrets.token_urlsafe(12)
        sub = bus.add_subscriber(sub_id, workspace_scope=scope, user=user or None)

        async def event_gen():
            async for frame in bus.stream(sub):
                if await request.is_disconnected():
                    break
                yield frame

        return StreamingResponse(
            event_gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
        )

    @router.get("/realtime/feed")
    async def realtime_feed(request: Request, limit: int = 50):
        user = require_user(request)
        scope = allowed_scopes(user or None)
        return {"events": bus.recent(limit=limit, workspace_scope=scope), "stats": bus.stats()}

    @router.get("/realtime/presence")
    async def realtime_presence(request: Request):
        user = require_user(request)
        scope = allowed_scopes(user or None)
        return {"presence": bus.presence(workspace_scope=scope), "stats": bus.stats()}

    @router.post("/realtime/presence/join")
    async def realtime_join(req: PresenceRequest, request: Request):
        user = require_user(request)
        client_id = req.client_id or secrets.token_urlsafe(8)
        record = bus.join(client_id, user=user or None, workspace_id=req.workspace_id)
        return {"presence": record}

    @router.post("/realtime/presence/leave")
    async def realtime_leave(req: PresenceRequest, request: Request):
        require_user(request)
        if req.client_id:
            bus.leave(req.client_id)
        return {"status": "ok"}

    return router
