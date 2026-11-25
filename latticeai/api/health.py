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
    externally_reachable: bool,
    app_version: str,
    app_mode: str,
) -> APIRouter:
    router = APIRouter()
    svc = model_service

    def _access_posture() -> dict:
        return {
            "require_auth": bool(require_auth),
            "externally_reachable": bool(externally_reachable),
        }

    @router.get("/health")
    async def health(request: Request):
        base = {
            **svc.health_base(version=app_version, mode=app_mode),
            "access": _access_posture(),
        }
        if not get_current_user(request) and require_auth:
            return base
        engines = await asyncio.to_thread(engine_status)
        return svc.health_full(base, engines)

    return router
