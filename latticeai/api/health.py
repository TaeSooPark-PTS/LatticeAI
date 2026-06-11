"""Health / status / engine-summary router.

Extracted from ``server_app.py`` in v1.2.0. Paths unchanged: ``/health``,
``/mode``, ``/runtime_features``, ``/engines`` (GET). Heavier engine *mutation*
endpoints (install / verify-cloud / pull-model) stay in server_app for now.
"""

from __future__ import annotations

import asyncio
from typing import Callable, List, Optional

from fastapi import APIRouter, Request


def create_health_router(
    *,
    model_service,
    engine_status: Callable[[], List[dict]],
    get_current_user: Callable[[Request], Optional[str]],
    require_auth: bool,
    app_version: str,
    app_mode: str,
) -> APIRouter:
    router = APIRouter()
    svc = model_service

    @router.get("/health")
    async def health(request: Request):
        base = svc.health_base(version=app_version, mode=app_mode)
        if not get_current_user(request) and require_auth:
            return base
        engines = await asyncio.to_thread(engine_status)
        return svc.health_full(base, engines)

    @router.get("/mode")
    @router.get("/runtime_features")
    async def mode():
        return svc.runtime()

    @router.get("/engines")
    async def engines():
        return svc.engines_payload(await asyncio.to_thread(engine_status))

    return router
