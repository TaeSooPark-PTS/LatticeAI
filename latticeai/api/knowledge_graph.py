"""Knowledge graph page and API routes.

Relocated from the root ``knowledge_graph_api.py`` in v4 (T2); the root module
remains as a deprecation shim. Route paths and response shapes are frozen by
``tests/unit/test_knowledge_graph_router_parity.py`` — the ``/knowledge-graph/*``
data endpoints back the v3 SPA (Files, Hybrid Search, graph explorer).
"""

from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from latticeai.api.ui_redirects import app_redirect


class KnowledgeGraphIngestRequest(BaseModel):
    type: str
    content: str = ""
    role: Optional[str] = None
    title: Optional[str] = None
    source: Optional[str] = None
    conversation_id: Optional[str] = None
    user_email: Optional[str] = None
    user_nickname: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


def _workspace_scope_from_request(request: Request) -> Optional[str]:
    header = request.headers.get("X-Workspace-Id")
    if header and header.strip():
        return header.strip()
    query = request.query_params.get("workspace_id")
    return query.strip() if query and query.strip() else None


def _format_context(matches: list, limit: int) -> str:
    """Mirror ``KnowledgeGraphRetrievalMixin.context_for_query`` formatting for a
    pre-filtered match list, so scoped callers get identical context lines minus
    the rows they are not allowed to see."""
    lines = []
    for match in matches[:limit]:
        meta = match.get("metadata") or {}
        source = (
            meta.get("relative_path")
            or meta.get("filename")
            or meta.get("conversation_id")
            or meta.get("source")
            or match.get("id")
        )
        summary = " ".join(str(match.get("summary") or "").split())[:700]
        lines.append(f"- [{match.get('type')}] {match.get('title')} | source={source} | {summary}")
    return "\n".join(lines)


def create_knowledge_graph_router(
    *,
    get_graph: Callable[[], Any],
    require_graph: Callable[[], None],
    require_user: Callable[[Request], str],
    static_dir: Path,
    allowed_workspaces_for: Optional[Callable[[Optional[str]], Any]] = None,
) -> APIRouter:
    router = APIRouter()

    def graph():
        require_graph()
        return get_graph()

    def _scoped(request: Request):
        """Authenticate the caller and resolve their allowed workspace set.

        Returns ``(graph, allowed)``. ``allowed is None`` means no scoping
        (single-user / no-auth mode); otherwise it is the set of workspace ids
        the caller may read. Legacy-global rows (no workspace) stay visible —
        the documented pre-v4 compatibility behavior enforced by
        ``filter_scoped_nodes``.
        """
        user = require_user(request)
        allowed = None
        if allowed_workspaces_for is not None and user:
            allowed = allowed_workspaces_for(user)
        return graph(), allowed

    @router.get("/graph")
    async def knowledge_graph_page(request: Request):
        """Serve the interactive knowledge graph canvas UI."""
        graph()
        require_user(request)
        return app_redirect("knowledge-graph", request)

    @router.get("/knowledge-graph")
    async def knowledge_graph_legacy_page(request: Request):
        """Backward-compatible route for the graph page."""
        graph()
        require_user(request)
        return app_redirect("knowledge-graph", request)

    @router.post("/knowledge-graph/curate")
    async def knowledge_graph_curate(request: Request):
        require_user(request)
        return graph().curate()

    @router.get("/knowledge-graph/provenance/coverage")
    async def knowledge_graph_provenance_coverage(request: Request):
        require_user(request)
        return graph().provenance_coverage()

    @router.get("/knowledge-graph/stats")
    async def knowledge_graph_stats(request: Request):
        require_user(request)
        return graph().stats()

    @router.get("/knowledge-graph/schema")
    async def knowledge_graph_schema(request: Request):
        require_user(request)
        stats = graph().stats()
        return {
            "legacy_schema_version": stats.get("schema_version"),
            "v2_schema_available": stats.get("v2_schema_available"),
            "v2": stats.get("v2"),
        }

    @router.get("/knowledge-graph/graph")
    async def knowledge_graph_data(request: Request, limit: int = 300):
        kg, allowed = _scoped(request)
        if allowed is None:
            return kg.graph(limit)
        return kg.graph(limit, allowed_workspaces=allowed)

    @router.get("/knowledge-graph/documents")
    async def knowledge_graph_documents(request: Request, limit: int = 200):
        """Ingested documents (uploads + indexed local docs) with index state.

        Backs the Files view so uploaded content is visible end-to-end:
        upload → Files → Knowledge Graph → Hybrid Search → Chat.
        """
        kg, allowed = _scoped(request)
        payload = kg.list_documents(limit)
        if allowed is not None:
            documents = kg.filter_scoped_nodes(payload.get("documents", []), allowed)
            payload = {**payload, "documents": documents, "total": len(documents)}
        return payload

    @router.get("/knowledge-graph/search")
    async def knowledge_graph_search(q: str, request: Request, limit: int = 30):
        kg, allowed = _scoped(request)
        if not q or not q.strip():
            return {"query": q, "matches": []}
        payload = kg.search(q, limit)
        if allowed is not None:
            payload = {**payload, "matches": kg.filter_scoped_nodes(payload.get("matches", []), allowed)}
        return payload

    @router.get("/knowledge-graph/context")
    async def knowledge_graph_context(q: str, request: Request, limit: int = 6):
        kg, allowed = _scoped(request)
        if allowed is None:
            return {"query": q, "context": kg.context_for_query(q, limit)}
        # Scoped mode: derive context from scope-filtered search matches so the
        # RAG context never carries content from workspaces the caller can't read.
        matches = kg.filter_scoped_nodes(kg.search(q, limit).get("matches", []), allowed)
        return {"query": q, "context": _format_context(matches, limit)}

    @router.get("/knowledge-graph/neighbors/{node_id:path}")
    async def knowledge_graph_neighbors(node_id: str, request: Request):
        kg, allowed = _scoped(request)
        if not node_id:
            raise HTTPException(status_code=400, detail="node_id required")
        if allowed is not None and not kg.filter_scoped_nodes([{"id": node_id}], allowed):
            raise HTTPException(status_code=404, detail="node not found")
        payload = kg.neighbors(node_id)
        if allowed is not None:
            neighbors = kg.filter_scoped_nodes(payload.get("neighbors", []), allowed)
            kept = {n.get("id") for n in neighbors}
            edges = [
                e for e in payload.get("edges", [])
                if (e.get("from") == node_id or e.get("from") in kept)
                and (e.get("to") == node_id or e.get("to") in kept)
            ]
            payload = {**payload, "neighbors": neighbors, "edges": edges}
        return payload

    @router.post("/knowledge-graph/ingest")
    async def knowledge_graph_ingest(req: KnowledgeGraphIngestRequest, request: Request):
        current_user = require_user(request)
        kg = graph()
        workspace_id = _workspace_scope_from_request(request)
        event_type = (req.type or "").strip().lower()
        if event_type not in {"message", "ai_response", "note"}:
            raise HTTPException(status_code=400, detail="지원하는 type: message, ai_response, note")
        role = req.role or ("assistant" if event_type == "ai_response" else "user")
        return kg.ingest_message(
            role,
            req.content,
            user_email=req.user_email or current_user,
            user_nickname=req.user_nickname,
            source=req.source or "mcp",
            conversation_id=req.conversation_id,
            workspace_id=workspace_id,
            raw={
                "type": req.type,
                "title": req.title,
                "content": req.content,
                "workspace_id": workspace_id,
                "metadata": req.metadata or {},
            },
        )

    return router
