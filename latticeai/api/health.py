"""The health router — ``GET /health``, and nothing else.

Extracted from ``server_app.py`` in v1.2.0 with three companions
(``/mode``, ``/runtime_features``, ``GET /engines``). Those three are operator
status views of the *product*, and v11.6.0 serves them from ``lattice-host``;
what is left here is the one route the supervisor gates a worker's readiness on.
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
    externally_reachable: bool,
    app_version: str,
    app_mode: str,
) -> APIRouter:
    router = APIRouter()
    svc = model_service

    def _access_posture() -> dict:
        """The two facts ``trusted_local_owner`` is computed from.

        Stated on the *unauthenticated* part of ``/health`` on purpose: the Rust
        front door polls this endpoint already, and it gates its own native
        ``/rust/*`` lanes on the answer.  Those lanes read the store with no
        credential at all, so without this they had no way to tell an
        optional-auth loopback worker (where that is exactly right) from a
        ``LATTICEAI_REQUIRE_AUTH=true`` one (where it is a hole).  Neither field
        is a secret: an unauthenticated caller discovers both by being refused.
        """
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
