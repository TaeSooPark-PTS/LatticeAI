"""Knowledge graph page and API routes.

Relocated from the root ``knowledge_graph_api.py`` in v4 (T2); the root module
remains as a deprecation shim. Route paths and response shapes are frozen by
``tests/unit/test_knowledge_graph_router_parity.py`` — the ``/knowledge-graph/*``
data endpoints back the v3 SPA (Files, Hybrid Search, graph explorer).
"""

from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel


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


def create_knowledge_graph_router(
    *,
    get_graph: Callable[[], Any],
    require_graph: Callable[[], None],
    require_user: Callable[[Request], str],
    static_dir: Path,
) -> APIRouter:
    router = APIRouter()

    def graph():
        require_graph()
        return get_graph()

    @router.get("/graph")
    async def knowledge_graph_page(request: Request):
        """Serve the interactive knowledge graph canvas UI."""
        graph()
        require_user(request)
        response = FileResponse(static_dir / "graph.html")
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    @router.get("/knowledge-graph")
    async def knowledge_graph_legacy_page(request: Request):
        """Backward-compatible route for the graph page."""
        graph()
        require_user(request)
        response = FileResponse(static_dir / "graph.html")
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

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
        require_user(request)
        return graph().graph(limit)

    @router.get("/knowledge-graph/documents")
    async def knowledge_graph_documents(request: Request, limit: int = 200):
        """Ingested documents (uploads + indexed local docs) with index state.

        Backs the Files view so uploaded content is visible end-to-end:
        upload → Files → Knowledge Graph → Hybrid Search → Chat.
        """
        require_user(request)
        return graph().list_documents(limit)

    @router.get("/knowledge-graph/search")
    async def knowledge_graph_search(q: str, request: Request, limit: int = 30):
        require_user(request)
        if not q or not q.strip():
            return {"query": q, "matches": []}
        return graph().search(q, limit)

    @router.get("/knowledge-graph/context")
    async def knowledge_graph_context(q: str, request: Request, limit: int = 6):
        require_user(request)
        return {"query": q, "context": graph().context_for_query(q, limit)}

    @router.get("/knowledge-graph/neighbors/{node_id:path}")
    async def knowledge_graph_neighbors(node_id: str, request: Request):
        require_user(request)
        if not node_id:
            raise HTTPException(status_code=400, detail="node_id required")
        return graph().neighbors(node_id)

    @router.post("/knowledge-graph/ingest")
    async def knowledge_graph_ingest(req: KnowledgeGraphIngestRequest, request: Request):
        current_user = require_user(request)
        kg = graph()
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
            raw={
                "type": req.type,
                "title": req.title,
                "content": req.content,
                "metadata": req.metadata or {},
            },
        )

    return router
