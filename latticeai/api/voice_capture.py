"""Voice capability router (v9.9.7).

``GET /api/capture/voice/status`` — what this machine can actually do with a
voice memo. Capture and storage are native (``POST /api/capture/voice`` moved
to ``lattice-platform`` in §W3b, and calls ``POST /worker/asr`` for the
transcript); whether a local transcriber exists at all is a fact about this
process, so the probe stays here.
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Request


def create_voice_capture_router(
    *,
    service: Any,
    require_user: Callable[[Request], Any],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/capture/voice/status")
    async def voice_status(request: Request):
        require_user(request)
        return service.status()

    return router


__all__ = ["create_voice_capture_router"]
