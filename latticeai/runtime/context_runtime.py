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
    artifact_ledger: Any = None,
) -> Dict[str, Any]:
    """Construct search, brain memory, and context assembly services."""

    from lattice_brain.context import ContextAssembler
    from lattice_brain.memory import BrainMemory
    from latticeai.services.search_service import SearchService

    search_service = SearchService(graph_store=graph_store)
    brain_memory = BrainMemory(ingestion_pipeline)

    def scoped_hybrid_search(q, user_email=None, workspace_id=None, **kw):
        allowed = None
        if require_auth:
            if workspace_id is not None:
                allowed = {workspace_id}
            elif user_email:
                allowed = allowed_scopes_for_user(user_email)
        return search_service.hybrid_search(q, allowed_workspaces=allowed, **kw)

    def scoped_notes_context(q, user_email=None, workspace_id=None, **kw):
        allowed = None
        if require_auth:
            if workspace_id is not None:
                allowed = {workspace_id}
            elif user_email:
                allowed = allowed_scopes_for_user(user_email)
        return gardener.get_relevant_context(q, allowed_workspaces=allowed, **kw)

    # Re-search loop (v9.9.6): files this conversation just produced are a
    # deterministic context section, so "그 파일에 다크모드 넣어줘" works before
    # asynchronous indexing catches up. Optional — absent ledger, absent section.
    if artifact_ledger is None:
        from latticeai.core.artifact_ledger import ArtifactLedger

        artifact_ledger = ArtifactLedger()

    context_assembler = ContextAssembler(
        memory_recall=memory_service.recall,
        hybrid_search=scoped_hybrid_search,
        notes_context=scoped_notes_context,
        recent_artifacts=artifact_ledger.recent,
    )

    return {
        "SEARCH_SERVICE": search_service,
        "BRAIN_MEMORY": brain_memory,
        "CONTEXT_ASSEMBLER": context_assembler,
        "ARTIFACT_LEDGER": artifact_ledger,
        "_scoped_hybrid_search": scoped_hybrid_search,
    }
