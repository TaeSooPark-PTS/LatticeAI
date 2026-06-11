"""Deprecation shim — the knowledge graph router moved in v4.

The ``/knowledge-graph/*`` data router (and legacy ``/graph`` page routes)
now live in :mod:`latticeai.api.knowledge_graph`. This root module remains
importable for the deprecation window and will be removed in a future major
release.
"""

from latticeai.api.knowledge_graph import (  # noqa: F401
    KnowledgeGraphIngestRequest,
    create_knowledge_graph_router,
)

__all__ = ["KnowledgeGraphIngestRequest", "create_knowledge_graph_router"]
