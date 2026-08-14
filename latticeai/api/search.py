"""The embedder report — what this worker will turn text into.

v3 shipped this module as the knowledge-graph / vector / hybrid search API: 15
routes over the store, the ANN index and the fusion ranker. v11.6.0 moved
retrieval into ``lattice-retrieval`` and the index write door into
``lattice-jobs``, and what stays in Python is the half that is a fact about
*this process*: which embedding provider resolved, at what width, and whether
it is the one that was asked for.

``GET /api/embeddings/status`` therefore reports the **embedder**, not the
index. Index completeness is a native jobs route now, and reporting it from
here would have meant re-opening a store the worker no longer holds.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, NoReturn, Optional

from fastapi import APIRouter, HTTPException, Request

from latticeai.core.embedding_providers import embedding_provider_profiles
from latticeai.services.search_service import SearchService


def create_search_router(
    *,
    service: SearchService,
    require_user: Callable[[Request], str],
    embedding_info: Optional[Callable[[], Dict[str, Any]]] = None,
) -> APIRouter:
    router = APIRouter()

    def _raise_embedder_error(exc: Exception) -> NoReturn:
        # NoReturn, not None: both handlers end with this in their except
        # branch, and without it each one reads as a missing return.
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/api/embeddings/status")
    async def embeddings_status(request: Request, refresh: bool = False) -> Dict[str, Any]:
        require_user(request)
        resolved = embedding_info() if embedding_info else {}
        try:
            return service.embeddings_status(resolved=resolved, refresh=refresh)
        except ValueError as exc:
            _raise_embedder_error(exc)

    @router.get("/api/embeddings/providers")
    async def embeddings_providers(request: Request) -> Dict[str, Any]:
        require_user(request)
        resolved = embedding_info() if embedding_info else {}
        profiles = resolved.get("profiles") or embedding_provider_profiles()
        return {
            "active": resolved.get("active_provider"),
            "requested": resolved.get("requested_provider"),
            "profile": resolved.get("profile") or "",
            "profiles": profiles,
            "providers": [
                {"id": "hash", "label": "Local hash (fallback)", "grade": "fallback",
                 "requires": [], "detail": "Deterministic offline vectors — always available."},
                {"id": "mlx", "label": "MLX (Apple Silicon)", "grade": "production",
                 "requires": ["LATTICEAI_EMBEDDING_MODEL"], "detail": "Local embedding model via MLX."},
                {"id": "ollama", "label": "Ollama", "grade": "production",
                 "requires": ["LATTICEAI_EMBEDDING_MODEL", "LATTICEAI_EMBEDDING_BASE_URL"],
                 "detail": "Local/remote Ollama embedding server."},
                {"id": "openai", "label": "OpenAI-compatible", "grade": "production",
                 "requires": ["LATTICEAI_EMBEDDING_MODEL", "LATTICEAI_EMBEDDING_BASE_URL", "LATTICEAI_EMBEDDING_API_KEY"],
                 "detail": "Any /v1/embeddings endpoint (OpenAI, LM Studio, vLLM, …)."},
                {"id": "custom", "label": "Custom callable", "grade": "production",
                 "requires": ["LATTICEAI_EMBEDDING_CUSTOM_TARGET"],
                 "detail": "User-supplied module:callable returning vectors."},
            ],
        }

    return router
