"""Compatibility shim for the v4.2 lattice-brain store.

The implementation now lives under :mod:`lattice_brain`. Root imports are
kept for older integrations and tests.
"""

import warnings as _warnings

_warnings.warn(
    "Importing 'knowledge_graph' from the repository root is deprecated; "
    "use 'from lattice_brain.graph.store import KnowledgeGraphStore' instead. "
    "The root shim will be removed in a future major release.",
    DeprecationWarning,
    stacklevel=2,
)

from lattice_brain.graph._kg_common import (  # noqa: F401,E402
    EDGE_VERB,
    GRAPH_SCHEMA_VERSION,
    LOCAL_CODE_EXTENSIONS,
    LOCAL_DOCUMENT_EXTENSIONS,
    LOCAL_IMAGE_EXTENSIONS,
    LOCAL_SIZE_LIMITS,
    LOCAL_SLIDE_EXTENSIONS,
    LOCAL_SPREADSHEET_EXTENSIONS,
    LOCAL_SUPPORTED_EXTENSIONS,
    LOCAL_TEXT_EXTENSIONS,
    _KG_DB_FORMAT_VERSION,
    _PROJECTION_VERSION,
    _extract_concepts,
    _extract_concepts_rules,
    _extract_triples,
    _extract_triples_rules,
    _slug,
    set_llm_router,
)
from lattice_brain.graph.store import KnowledgeGraphStore  # noqa: E402

__all__ = [
    "KnowledgeGraphStore",
    "GRAPH_SCHEMA_VERSION",
    "EDGE_VERB",
    "_PROJECTION_VERSION",
    "_KG_DB_FORMAT_VERSION",
    "set_llm_router",
    "_slug",
    "_extract_concepts",
    "_extract_concepts_rules",
    "_extract_triples",
    "_extract_triples_rules",
]
