"""Admin-only UX funnel metrics endpoint (backlog #16).

Exposes :class:`~latticeai.services.funnel_metrics.FunnelMetricsService`
snapshots at ``GET /api/admin/funnel-metrics``. Follows the admin router
convention: gate through the injected ``require_admin`` callable, read-only,
no side effects.
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Request


def create_funnel_metrics_router(
    *,
    service: Any,
    require_admin: Callable[[Request], Any],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/admin/funnel-metrics")
    async def funnel_metrics(request: Request):
        require_admin(request)
        return service.snapshot()

    return router


__all__ = ["create_funnel_metrics_router"]
