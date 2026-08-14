"""The multi-modal capability probe.

This module was the local-file surface: the home-sandbox browser, the folder
ingest door, the watch registry, the Obsidian and interop bridges, the
background-job views and ``/local/serve``. Every one of those either writes or
reads state ``lattice-host`` owns now, so v11.6.0 keeps a single route.

``GET /api/ingestion/multimodal`` answers what this machine will actually do
with a picture, a recording or a video — the gates, the ports that resolved, and
which of the three reasons a video would be refused for. It reports capability,
not backlog: nothing here ingests, and the pipeline it asks holds no store.
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Request


def create_local_files_router(
    *,
    require_user: Callable[[Request], Any],
    ingestion_pipeline: Any,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/ingestion/multimodal")
    async def ingestion_multimodal_status(request: Request):
        """What this install will do with a picture, a recording, or a video.

        FEATURE_STATUS has described this answer — including which of the three
        reasons a video would be refused for — since 11.2.0, but the pipeline
        method that produces it had no route, so no surface could ever show it
        and "why was my video not indexed?" had no answer short of reading the
        server's environment.
        """
        require_user(request)
        return ingestion_pipeline.multimodal_status()

    return router


__all__ = ["create_local_files_router"]
