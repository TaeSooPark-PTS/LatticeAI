"""Brain Chronicle API router (v11.3.0, track B).

Read-only surface over :class:`~latticeai.services.chronicle.ChronicleService`
— the first HTTP exposure of the bitemporal data v11.1.0 started recording:

* ``GET /api/chronicle/overview`` — the growth curve: totals, the first and
  last thing the Brain ever saw, and one bucket per day.
* ``GET /api/chronicle/day/{date}`` — one day's story, grouped into what came
  in, what was learned, what was talked about, and what changed.
* ``GET /api/chronicle/as-of?ts=…`` — the Brain as it stood at an instant.

All three follow the same require_user/gate_read contract as the command
centre, so a workspace member never reads another workspace's timeline. A
malformed date or timestamp answers 422 through the message catalog rather
than a 500 or a silently empty day.
"""

from __future__ import annotations

from typing import Callable, Optional

from fastapi import APIRouter, Query, Request

from latticeai.core.messages import http_error, resolve_language
from latticeai.services.chronicle import ChronicleService


def create_chronicle_router(
    *,
    service: ChronicleService,
    require_user: Callable[[Request], str],
    gate_read: Callable[[Request], Optional[str]],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/chronicle/overview")
    async def chronicle_overview(request: Request):
        user = require_user(request)
        scope = gate_read(request)
        return service.overview(user_email=user, workspace_id=scope)

    @router.get("/api/chronicle/day/{date}")
    async def chronicle_day(date: str, request: Request):
        user = require_user(request)
        scope = gate_read(request)
        try:
            return service.day(date, user_email=user, workspace_id=scope)
        except ValueError:
            raise http_error(422, "chronicle.bad_date", resolve_language(request))

    @router.get("/api/chronicle/as-of")
    async def chronicle_as_of(request: Request, ts: str = Query(..., max_length=64)):
        require_user(request)
        scope = gate_read(request)
        try:
            return service.as_of(ts, workspace_id=scope)
        except ValueError:
            raise http_error(422, "chronicle.bad_timestamp", resolve_language(request))

    return router


__all__ = ["create_chronicle_router"]
