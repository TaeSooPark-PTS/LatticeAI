"""Knowledge graph page and API routes.

Relocated from the root ``knowledge_graph_api.py`` in v4 (T2); the root module
remains as a deprecation shim. Route paths and response shapes are frozen by
``tests/unit/test_knowledge_graph_router_parity.py`` — the ``/knowledge-graph/*``
data endpoints back the v3 SPA (Files, Hybrid Search, graph explorer).
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from lattice_brain.ingestion import IngestionItem
from latticeai.api.ui_redirects import app_redirect
from latticeai.api.workspace_scope import (
    resolve_workspace_scope,
    workspace_scope_from_request,
)
from latticeai.core.messages import http_error, resolve_language
from latticeai.core.quiet import quiet


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


# Kept as a module-level name because callers import it from here; the
# implementation now lives in the shared resolver.
_workspace_scope_from_request = workspace_scope_from_request


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


def _pipeline_stage_view(*, count: int, pending: int) -> Dict[str, Any]:
    """Derive a coherent journey-ribbon stage from count + pending.

    Invariants (enforced for layout-rebuild Capture screen 11):
    - ``pending > 0`` → ``working``
    - ``pending == 0`` and ``count > 0`` → ``done`` (never ``waiting``)
    - ``pending == 0`` and ``count == 0`` → ``waiting`` (nothing arrived yet)
    """
    safe_count = max(0, int(count))
    safe_pending = max(0, int(pending))
    if safe_pending > 0:
        status = "working"
    elif safe_count > 0:
        status = "done"
    else:
        status = "waiting"
    return {"count": safe_count, "pending": safe_pending, "status": status}


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
        return resolve_workspace_scope(
            request,
            user=user,
            workspace_service=workspace_service,
            write=True,
        )

    def _scoped_stats(request: Request) -> Dict[str, Any]:
        """Store statistics restricted to what the caller may read.

        ``stats()`` counted every row in the database, so a member of one
        organization workspace could read another's node/edge/document volume
        off a "harmless" metrics endpoint. Unscoped mode (single-user / no
        auth) still gets the whole-store counts, which is the same number it
        always was.
        """
        kg, allowed = _scoped(request)
        if allowed is None:
            return dict(kg.stats())
        try:
            return dict(
                kg.stats(allowed_workspaces=allowed, include_legacy_global=False)
            )
        except TypeError:
            # A store predating scoped stats cannot answer the scoped
            # question. Keep the response shape and empty the aggregates it
            # could not restrict, rather than leaking whole-store totals.
            payload = dict(kg.stats())
            empty: Dict[str, Any] = {
                "nodes": {},
                "edges": {},
                "local_sources": 0,
                "local_file_status": {},
            }
            payload.update(
                {key: value for key, value in empty.items() if key in payload}
            )
            return payload

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
        return _scoped_stats(request)

    @router.get("/knowledge-graph/pipeline/status")
    async def knowledge_graph_pipeline_status(request: Request):
        """Per-stage counts + status for the Capture 3-step journey ribbon.

        Aggregates existing store data only — no schema change. Top-level
        ``received`` / ``extracted`` / ``connected`` stay for simple clients;
        ``stages`` is the single source of truth for count, pending, and
        status (``done`` / ``working`` / ``waiting``). Keys are omitted when
        a value cannot be computed (never faked as 0 when the underlying
        source is unavailable).
        """
        kg, allowed = _scoped(request)
        received: Optional[int] = None
        extracted: Optional[int] = None
        connected: Optional[int] = None
        index_pending: Optional[int] = None

        try:
            docs_payload = kg.list_documents(10_000)
            documents = list(docs_payload.get("documents") or [])
            if allowed is not None:
                documents = _filter_scoped(kg, documents, allowed)
            received = len(documents)
            extracted = sum(
                1
                for doc in documents
                if doc.get("indexed") or int(doc.get("chunks") or 0) > 0
            )
        except Exception:
            quiet()

        try:
            stats = _scoped_stats(request)
            edges = stats.get("edges")
            if isinstance(edges, dict):
                connected = sum(int(value or 0) for value in edges.values())
            elif isinstance(edges, (int, float)) and not isinstance(edges, bool):
                connected = int(edges)
            elif isinstance(stats.get("v2"), dict):
                v2_edges = stats["v2"].get("edges")
                if isinstance(v2_edges, (int, float)) and not isinstance(v2_edges, bool):
                    connected = int(v2_edges)
            if received is None:
                nodes = stats.get("nodes")
                if isinstance(nodes, dict):
                    received = sum(
                        int(value or 0)
                        for key, value in nodes.items()
                        if str(key).lower() != "chunk"
                    )
                elif isinstance(nodes, (int, float)) and not isinstance(nodes, bool):
                    received = int(nodes)
        except Exception:
            quiet()

        index_fn = getattr(kg, "index_status", None)
        if callable(index_fn):
            try:
                index = index_fn() or {}
                if received is None and index.get("source_items") is not None:
                    received = int(index.get("source_items") or 0)
                if extracted is None and index.get("ready_items") is not None:
                    extracted = int(index.get("ready_items") or 0)
                if index.get("pending_items") is not None:
                    index_pending = max(0, int(index.get("pending_items") or 0))
                elif index.get("pending") is not None:
                    index_pending = max(0, int(index.get("pending") or 0))
            except Exception:
                quiet()

        payload: Dict[str, Any] = {}
        if received is not None:
            payload["received"] = max(0, int(received))
        if extracted is not None:
            payload["extracted"] = max(0, int(extracted))
        if connected is not None:
            payload["connected"] = max(0, int(connected))

        if payload:
            stages: Dict[str, Dict[str, Any]] = {}
            if "received" in payload:
                # Once listed as a document it has been received — no backlog.
                stages["received"] = _pipeline_stage_view(
                    count=payload["received"], pending=0
                )
            if "extracted" in payload:
                # Prefer vector-index backlog; fall back to docs not yet chunked.
                if index_pending is not None:
                    extract_pending = index_pending
                elif "received" in payload:
                    extract_pending = max(
                        0, int(payload["received"]) - int(payload["extracted"])
                    )
                else:
                    extract_pending = 0
                stages["extracted"] = _pipeline_stage_view(
                    count=payload["extracted"], pending=extract_pending
                )
            if "connected" in payload:
                # Connections are graph edges; backlog only when index reports
                # pending items that still need linking (reuse extract backlog
                # only when we have no better signal — 0 when graph is ready).
                connect_pending = 0
                if index_pending is not None and payload["connected"] == 0:
                    connect_pending = index_pending
                stages["connected"] = _pipeline_stage_view(
                    count=payload["connected"], pending=connect_pending
                )
            payload["stages"] = stages
            payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
        return payload

    @router.get("/knowledge-graph/schema")
    async def knowledge_graph_schema(request: Request):
        stats = _scoped_stats(request)
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
            raise http_error(400, "graph.node_id_required", resolve_language(request))
        if allowed is not None and not _filter_scoped(kg, [{"id": node_id}], allowed):
            raise http_error(404, "graph.node_not_found", resolve_language(request))
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
                raise http_error(403, "common.user_mismatch", resolve_language(request))
        effective_user = current_user or req.user_email or None
        kg = graph()
        workspace_id = _write_workspace(request, current_user)
        event_type = (req.type or "").strip().lower()
        if event_type not in {"message", "ai_response", "note"}:
            raise http_error(400, "graph.unsupported_type", resolve_language(request))
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
