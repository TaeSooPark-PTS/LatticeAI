"""v3 knowledge graph, vector, and hybrid search API contracts."""

from __future__ import annotations

from typing import Any, Callable, Dict, NoReturn, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from latticeai.core.embedding_providers import embedding_provider_profiles
from latticeai.services.search_service import DEFAULT_HYBRID_WEIGHTS, SearchService


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int = 30


class VectorSearchRequest(SearchRequest):
    min_score: float = 0.0


class HybridSearchRequest(SearchRequest):
    keyword_limit: int = 30
    vector_limit: int = 30
    graph_limit: int = 30
    weights: Optional[Dict[str, float]] = None


class GraphNodeRequest(BaseModel):
    node_id: str = Field(..., min_length=1)
    include_neighbors: bool = True
    depth: int = 1
    limit: int = 100


class RelationshipSearchRequest(BaseModel):
    query: str = ""
    node_id: str = ""
    relationship_type: str = ""
    limit: int = 30


class IndexRebuildRequest(BaseModel):
    full: bool = False
    include_nodes: bool = True
    include_chunks: bool = True


class _ScopedSearchService:
    """Injects the caller's workspace scope into every search call —
    enforcement lives at this one chokepoint, not in each handler."""

    _SCOPED = {
        "keyword_search",
        "vector_search",
        "graph_search",
        "hybrid_search",
        "graph",
        "node",
        "relationships",
    }

    def __init__(self, service: SearchService, allowed):
        self._service = service
        self._allowed = allowed

    def __getattr__(self, name):
        attr = getattr(self._service, name)
        if name in self._SCOPED:
            def scoped(*args, **kwargs):
                kwargs.setdefault("allowed_workspaces", self._allowed)
                return attr(*args, **kwargs)
            return scoped
        return attr


def create_search_router(
    *,
    service: SearchService,
    require_user: Callable[[Request], str],
    embedding_info: Optional[Callable[[], Dict[str, Any]]] = None,
    allowed_workspaces_for: Optional[Callable[[Optional[str]], Any]] = None,
) -> APIRouter:
    router = APIRouter()

    def _guarded(request: Request) -> "_ScopedSearchService":
        user = require_user(request)
        allowed = None
        if allowed_workspaces_for is not None and user:
            allowed = allowed_workspaces_for(user)
        return _ScopedSearchService(service, allowed)

    def _raise_graph_error(exc: Exception) -> NoReturn:
        # NoReturn, not None: every handler ends with this in its except
        # branch, and without it each one reads as a missing return.
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/api/search/hybrid")
    async def hybrid_search(req: HybridSearchRequest, request: Request) -> Dict[str, Any]:
        try:
            return _guarded(request).hybrid_search(
                req.query,
                limit=req.limit,
                keyword_limit=req.keyword_limit,
                vector_limit=req.vector_limit,
                graph_limit=req.graph_limit,
                weights=req.weights or DEFAULT_HYBRID_WEIGHTS,
            )
        except ValueError as exc:
            _raise_graph_error(exc)

    @router.get("/api/search/hybrid")
    async def hybrid_search_get(q: str, request: Request, limit: int = 30) -> Dict[str, Any]:
        try:
            return _guarded(request).hybrid_search(q, limit=limit)
        except ValueError as exc:
            _raise_graph_error(exc)

    @router.post("/api/search/keyword")
    async def keyword_search(req: SearchRequest, request: Request) -> Dict[str, Any]:
        try:
            return _guarded(request).keyword_search(req.query, limit=req.limit)
        except ValueError as exc:
            _raise_graph_error(exc)

    @router.get("/api/search/keyword")
    async def keyword_search_get(q: str, request: Request, limit: int = 30) -> Dict[str, Any]:
        try:
            return _guarded(request).keyword_search(q, limit=limit)
        except ValueError as exc:
            _raise_graph_error(exc)

    @router.post("/api/search/vector")
    async def vector_search(req: VectorSearchRequest, request: Request) -> Dict[str, Any]:
        try:
            return _guarded(request).vector_search(req.query, limit=req.limit, min_score=req.min_score)
        except ValueError as exc:
            _raise_graph_error(exc)

    @router.get("/api/search/vector")
    async def vector_search_get(
        q: str,
        request: Request,
        limit: int = 30,
        min_score: float = 0.0,
    ) -> Dict[str, Any]:
        try:
            return _guarded(request).vector_search(q, limit=limit, min_score=min_score)
        except ValueError as exc:
            _raise_graph_error(exc)

    @router.get("/api/graph")
    async def graph(request: Request, limit: int = 300) -> Dict[str, Any]:
        try:
            return _guarded(request).graph(limit=limit)
        except ValueError as exc:
            _raise_graph_error(exc)

    @router.get("/api/graph/node")
    async def graph_node(
        node_id: str,
        request: Request,
        include_neighbors: bool = True,
        depth: int = 1,
        limit: int = 100,
    ) -> Dict[str, Any]:
        try:
            return _guarded(request).node(
                node_id,
                include_neighbors=include_neighbors,
                depth=depth,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/api/graph/node")
    async def graph_node_post(req: GraphNodeRequest, request: Request) -> Dict[str, Any]:
        try:
            return _guarded(request).node(
                req.node_id,
                include_neighbors=req.include_neighbors,
                depth=req.depth,
                limit=req.limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/api/graph/relationship")
    async def graph_relationship(
        request: Request,
        q: str = "",
        node_id: str = "",
        relationship_type: str = "",
        limit: int = 30,
    ) -> Dict[str, Any]:
        try:
            return _guarded(request).relationships(
                query=q,
                node_id=node_id,
                relationship_type=relationship_type,
                limit=limit,
            )
        except ValueError as exc:
            _raise_graph_error(exc)

    @router.post("/api/graph/relationship")
    async def graph_relationship_post(req: RelationshipSearchRequest, request: Request) -> Dict[str, Any]:
        try:
            return _guarded(request).relationships(
                query=req.query,
                node_id=req.node_id,
                relationship_type=req.relationship_type,
                limit=req.limit,
            )
        except ValueError as exc:
            _raise_graph_error(exc)

    @router.get("/api/index/status")
    async def index_status(request: Request) -> Dict[str, Any]:
        try:
            return _guarded(request).index_status()
        except ValueError as exc:
            _raise_graph_error(exc)

    @router.post("/api/index/rebuild")
    async def index_rebuild(req: IndexRebuildRequest, request: Request) -> Dict[str, Any]:
        try:
            return _guarded(request).rebuild_index(
                full=req.full,
                include_nodes=req.include_nodes,
                include_chunks=req.include_chunks,
            )
        except ValueError as exc:
            _raise_graph_error(exc)

    @router.get("/api/embeddings/status")
    async def embeddings_status(request: Request, refresh: bool = False) -> Dict[str, Any]:
        require_user(request)
        resolved = embedding_info() if embedding_info else {}
        try:
            return service.embeddings_status(resolved=resolved, refresh=refresh)
        except ValueError as exc:
            _raise_graph_error(exc)

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
