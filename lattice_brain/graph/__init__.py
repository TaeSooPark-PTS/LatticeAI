"""Knowledge graph subsystem of the Brain Core.

Physically hosts the graph schema, store, mixins (write/retrieval/discovery/
documents/ingest/projection/provenance), device identity, brain network, and
the graph curator. Heavy modules are lazy-loaded so importing
``lattice_brain.graph`` stays cheap.
"""

from __future__ import annotations

__all__ = [
    "KnowledgeGraphStore",
    "KGStoreV2",
    "NodeType",
    "EdgeType",
]


def __getattr__(name: str):
    if name == "KnowledgeGraphStore":
        from .store import KnowledgeGraphStore

        return KnowledgeGraphStore
    if name in {"KGStoreV2", "NodeType", "EdgeType"}:
        from . import schema

        return getattr(schema, name)
    raise AttributeError(name)
