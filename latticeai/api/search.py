from __future__ import annotations

from typing import Any, Callable, Dict, NoReturn, Optional

from fastapi import APIRouter, HTTPException, Request

from latticeai.core.embedding_providers.profiles import (
    RECOMMENDED_EMBEDDING_MODEL,
    RECOMMENDED_EMBEDDING_PROFILE,
)
from latticeai.services.search_service import SearchService


def create_search_router(
    *,
    service: SearchService,
    require_user: Callable[[Request], str],
    embedding_info: Optional[Callable[[], Dict[str, Any]]] = None,
) -> APIRouter:
    router = APIRouter()

    def _raise_embedder_error(exc: Exception) -> NoReturn:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/api/embeddings/status")
    async def embeddings_status(
        request: Request, refresh: bool = False
    ) -> Dict[str, Any]:
        require_user(request)
        resolved = embedding_info() if embedding_info else {}
        try:
            payload = dict(
                service.embeddings_status(resolved=resolved, refresh=refresh)
            )
        except ValueError as exc:
            _raise_embedder_error(exc)
        payload.setdefault("recommended_model", RECOMMENDED_EMBEDDING_MODEL)
        payload.setdefault("recommended_profile", RECOMMENDED_EMBEDDING_PROFILE)
        return payload

    return router
