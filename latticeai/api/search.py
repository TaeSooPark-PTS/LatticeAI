"""v3 knowledge graph, vector, and hybrid search API contracts."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

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


def create_search_router(
    *,
    service: SearchService,
    require_user: Callable[[Request], str],
) -> APIRouter:
    router = APIRouter()

    def _guarded(request: Request) -> SearchService:
        require_user(request)
        return service

    def _raise_graph_error(exc: Exception) -> None:
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

    return router
