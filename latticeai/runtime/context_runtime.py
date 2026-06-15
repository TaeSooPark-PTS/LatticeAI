"""Retrieval/context runtime assembly for app startup."""

from __future__ import annotations

from typing import Any, Callable, Dict


def build_context_runtime(
    *,
    graph_store: Any,
    ingestion_pipeline: Any,
    memory_service: Any,
    gardener: Any,
    require_auth: bool,
    allowed_scopes_for_user: Callable[[Any], Any],
) -> Dict[str, Any]:
    """Construct search, brain memory, and context assembly services."""

    from lattice_brain.context import ContextAssembler
    from lattice_brain.memory import BrainMemory
    from latticeai.services.search_service import SearchService

    search_service = SearchService(graph_store=graph_store)
    brain_memory = BrainMemory(ingestion_pipeline)

    def scoped_hybrid_search(q, user_email=None, **kw):
        allowed = None
        if require_auth and user_email:
            allowed = allowed_scopes_for_user(user_email)
        return search_service.hybrid_search(q, allowed_workspaces=allowed, **kw)

    context_assembler = ContextAssembler(
        memory_recall=memory_service.recall,
        hybrid_search=scoped_hybrid_search,
        notes_context=gardener.get_relevant_context,
    )

    return {
        "SEARCH_SERVICE": search_service,
        "BRAIN_MEMORY": brain_memory,
        "CONTEXT_ASSEMBLER": context_assembler,
        "_scoped_hybrid_search": scoped_hybrid_search,
    }
