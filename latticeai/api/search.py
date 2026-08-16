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

It is now the module's only route. ``GET /api/embeddings/providers`` — the
static catalogue of provider ids and the env vars each needs — was removed in
v11.8.0: no surface in the tree asked for it, and a catalogue nothing reads is
a second place for the provider list to go stale.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, NoReturn, Optional

from fastapi import APIRouter, HTTPException, Request

from latticeai.services.search_service import SearchService


def create_search_router(
    *,
    service: SearchService,
    require_user: Callable[[Request], str],
    embedding_info: Optional[Callable[[], Dict[str, Any]]] = None,
) -> APIRouter:
    router = APIRouter()

    def _raise_embedder_error(exc: Exception) -> NoReturn:
        # NoReturn, not None: the handler ends with this in its except branch,
        # and without it the function reads as a missing return.
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/api/embeddings/status")
    async def embeddings_status(request: Request, refresh: bool = False) -> Dict[str, Any]:
        require_user(request)
        resolved = embedding_info() if embedding_info else {}
        try:
            return service.embeddings_status(resolved=resolved, refresh=refresh)
        except ValueError as exc:
            _raise_embedder_error(exc)

    return router
