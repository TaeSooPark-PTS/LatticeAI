"""Knowledge graph page and API routes.

Relocated from the root ``knowledge_graph_api.py`` in v4 (T2); the root module
remains as a deprecation shim. Route paths and response shapes are frozen by
``tests/unit/test_knowledge_graph_router_parity.py`` — the ``/knowledge-graph/*``
data endpoints back the v3 SPA (Files, Hybrid Search, graph explorer).
"""

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from latticeai.api.ui_redirects import app_redirect
from lattice_brain.ingestion import IngestionItem


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


class CurateNoiseRequest(BaseModel):
    # Dry-run by default: the job *reports* removals until explicitly applied.
    dry_run: bool = True
    max_df_ratio: float = 0.8
    min_doc_frequency: int = 1
    min_corpus_docs: int = 5
    normalize_verbs: bool = True
    max_removals: int = 200


class PromotionActionRequest(BaseModel):
    # None applies/rejects every pending promotion; otherwise only these ids.
    ids: Optional[List[str]] = None


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
    require_admin: Optional[Callable[[Request], Any]] = None,
    allowed_workspaces_for: Optional[Callable[[Optional[str]], Any]] = None,
    ingestion_pipeline: Any = None,
    workspace_service: Any = None,
) -> APIRouter:
    router = APIRouter()

    def graph():
        require_graph()
        return get_graph()

    def _scoped(request: Request):
        """Authenticate the caller and resolve their allowed workspace set.

        Returns ``(graph, allowed)``. ``allowed is None`` means no scoping
        (single-user / no-auth mode); otherwise it is the set of workspace ids
        the caller may read. Legacy-global rows are private by default in this
        multi-user path; maintenance callers can opt in only through the store
        API's explicit ``include_legacy_global=True`` argument.
        """
        user = require_user(request)
        allowed = None
        if allowed_workspaces_for is not None and user:
            allowed = allowed_workspaces_for(user)
        return graph(), allowed

    def _filter_scoped(kg: Any, items: Any, allowed: Any) -> list:
        """Apply the fail-closed v2 scope contract, including to old stores."""

        candidates = list(items)
        try:
            return kg.filter_scoped_nodes(
                candidates,
                allowed,
                include_legacy_global=False,
            )
        except TypeError:
            # A pre-hardening store may still interpret missing ids as global.
            # Resolve authoritative scopes directly and keep only known rows in
            # an allowed workspace instead of invoking that legacy behavior.
            scopes = kg.workspaces_of([item.get("id") for item in candidates])
            allowed_ids = {str(item) for item in allowed if item}
            return [
                item
                for item in candidates
                if str(item.get("id") or "") in scopes
                and scopes[str(item.get("id") or "")] is not None
                and str(scopes[str(item.get("id") or "")]) in allowed_ids
            ]

    def _write_workspace(request: Request, user: str) -> Optional[str]:
        requested = _workspace_scope_from_request(request)
        if workspace_service is None:
            return requested
        try:
            return workspace_service.resolve_write_scope(requested, user or None)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

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
        # Curation rewrites the shared graph and is therefore an administrative
        # operation whenever role-based authentication is configured.  The
        # fallback preserves the standalone/local router contract used by
        # embedders that only provide ``require_user``.
        (require_admin or require_user)(request)
        return graph().curate()

    @router.post("/knowledge-graph/curate/noise")
    async def knowledge_graph_curate_noise(req: CurateNoiseRequest, request: Request):
        """Noise-reduction curation job (backlog #10).

        Dry-run by default: reports which heuristic concept nodes would be
        removed (low IDF / below the frequency floor) and which relation
        verbs would be normalized, without changing the graph. Set
        ``dry_run=false`` to apply. User-created nodes are never removed.
        Administrative like ``/knowledge-graph/curate`` when roles exist.
        """
        (require_admin or require_user)(request)
        return graph().curate_noise(
            dry_run=req.dry_run,
            max_df_ratio=req.max_df_ratio,
            min_doc_frequency=req.min_doc_frequency,
            min_corpus_docs=req.min_corpus_docs,
            normalize_verbs=req.normalize_verbs,
            max_removals=req.max_removals,
        )

    @router.get("/knowledge-graph/promotions")
    async def knowledge_graph_promotions(request: Request):
        """Pending curator promotions awaiting human review (review Wave 4).

        Populated when ``curate()`` runs in review mode (explicit
        ``review_mode=True`` or the LATTICEAI_GRAPH_PROMOTION_REVIEW env
        opt-in). Administrative like ``/knowledge-graph/curate``: the queue
        governs the shared graph.
        """
        (require_admin or require_user)(request)
        pending = graph().pending_promotions()
        return {"pending": pending, "total": len(pending)}

    @router.post("/knowledge-graph/promotions/apply")
    async def knowledge_graph_promotions_apply(
        req: PromotionActionRequest, request: Request
    ):
        """Apply pending promotions (all when ``ids`` is omitted)."""
        (require_admin or require_user)(request)
        return graph().apply_pending_promotions(ids=req.ids)

    @router.post("/knowledge-graph/promotions/reject")
    async def knowledge_graph_promotions_reject(
        req: PromotionActionRequest, request: Request
    ):
        """Reject pending promotions without writing (all when ``ids`` omitted)."""
        (require_admin or require_user)(request)
        return graph().reject_pending_promotions(ids=req.ids)

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
        try:
            return kg.graph(
                limit,
                allowed_workspaces=allowed,
                include_legacy_global=False,
            )
        except TypeError:
            payload = kg.graph(limit)
            nodes = _filter_scoped(kg, payload.get("nodes", []), allowed)
            kept = {node.get("id") for node in nodes}
            return {
                **payload,
                "nodes": nodes,
                "edges": [
                    edge
                    for edge in payload.get("edges", [])
                    if edge.get("from") in kept and edge.get("to") in kept
                ],
            }

    @router.get("/knowledge-graph/documents")
    async def knowledge_graph_documents(request: Request, limit: int = 200):
        """Ingested documents (uploads + indexed local docs) with index state.

        Backs the Files view so uploaded content is visible end-to-end:
        upload → Files → Knowledge Graph → Hybrid Search → Chat.
        """
        kg, allowed = _scoped(request)
        payload = kg.list_documents(limit)
        if allowed is not None:
            documents = _filter_scoped(kg, payload.get("documents", []), allowed)
            payload = {**payload, "documents": documents, "total": len(documents)}
        return payload

    @router.get("/knowledge-graph/search")
    async def knowledge_graph_search(q: str, request: Request, limit: int = 30):
        kg, allowed = _scoped(request)
        if not q or not q.strip():
            return {"query": q, "matches": []}
        payload = kg.search(q, limit)
        if allowed is not None:
            payload = {
                **payload,
                "matches": _filter_scoped(kg, payload.get("matches", []), allowed),
            }
        return payload

    @router.get("/knowledge-graph/context")
    async def knowledge_graph_context(q: str, request: Request, limit: int = 6):
        kg, allowed = _scoped(request)
        if allowed is None:
            return {"query": q, "context": kg.context_for_query(q, limit)}
        # Scoped mode: derive context from scope-filtered search matches so the
        # RAG context never carries content from workspaces the caller can't read.
        matches = _filter_scoped(kg, kg.search(q, limit).get("matches", []), allowed)
        return {"query": q, "context": _format_context(matches, limit)}

    @router.get("/knowledge-graph/neighbors/{node_id:path}")
    async def knowledge_graph_neighbors(node_id: str, request: Request):
        kg, allowed = _scoped(request)
        if not node_id:
            raise HTTPException(status_code=400, detail="node_id required")
        if allowed is not None and not _filter_scoped(kg, [{"id": node_id}], allowed):
            raise HTTPException(status_code=404, detail="node not found")
        payload = kg.neighbors(node_id)
        if allowed is not None:
            neighbors = _filter_scoped(kg, payload.get("neighbors", []), allowed)
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
        if current_user and req.user_email:
            if current_user.strip().lower() != req.user_email.strip().lower():
                raise HTTPException(status_code=403, detail="user_email must match the authenticated user.")
        effective_user = current_user or req.user_email or None
        kg = graph()
        workspace_id = _write_workspace(request, current_user)
        event_type = (req.type or "").strip().lower()
        if event_type not in {"message", "ai_response", "note"}:
            raise HTTPException(status_code=400, detail="지원하는 type: message, ai_response, note")
        role = req.role or ("assistant" if event_type == "ai_response" else "user")
        if ingestion_pipeline is not None:
            source_type = "chat_message" if event_type in {"message", "ai_response"} else "note"
            result = ingestion_pipeline.ingest(
                IngestionItem(
                    source_type=source_type,
                    title=req.title,
                    text=req.content,
                    source_uri=req.source,
                    owner=effective_user,
                    workspace_id=workspace_id,
                    conversation_id=req.conversation_id,
                    metadata={
                        "type": req.type,
                        "role": role,
                        "source": req.source or "mcp",
                        "user_nickname": req.user_nickname,
                        "raw": {
                            "type": req.type,
                            "title": req.title,
                            "content": req.content,
                            "workspace_id": workspace_id,
                            "metadata": req.metadata or {},
                        },
                        **(req.metadata or {}),
                    },
                ),
                user_email=effective_user,
            )
            if result.status != "ok":
                raise HTTPException(status_code=500, detail=result.detail or result.status)
            return result.as_dict()
        return kg.ingest_message(
            role,
            req.content,
            user_email=effective_user,
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
