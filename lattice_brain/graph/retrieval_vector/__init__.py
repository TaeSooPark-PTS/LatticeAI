"""The derived vector index: build it, report on it, search it.

v11.3.0 turned this module into a package. ``KnowledgeGraphVectorMixin`` is
now composed from four cohesive sub-mixins — embedder fingerprint, index
build, index status, and search — each moved here verbatim. Every name this
module exported before still resolves from
``lattice_brain.graph.retrieval_vector``.
"""

from __future__ import annotations

# ruff: noqa: F403,F405
from .._kg_common import *  # noqa: F403,F401
from .fingerprint import _VectorFingerprintMixin
from .indexing import _VectorIndexingMixin
from .search import (  # noqa: F401
    DEFAULT_VECTOR_MAX_CANDIDATES,
    VECTOR_MAX_CANDIDATES_CEILING,
    VECTOR_MAX_CANDIDATES_ENV,
    VECTOR_SCAN_BATCH,
    _configured_vector_max_candidates,
    _VectorSearchMixin,
)
from .status import _VectorStatusMixin


# Base order is most-composed-first (status → indexing → fingerprint, then
# search): each half names the ones it calls as its typing-only base, and C3
# needs a subclass ahead of the class it extends. The method sets are disjoint,
# so at runtime the order changes nothing.
class KnowledgeGraphVectorMixin(
    _VectorStatusMixin,
    _VectorIndexingMixin,
    _VectorFingerprintMixin,
    _VectorSearchMixin,
):
    """Vector-embedding index build/status/search, split out of retrieval.

    Composed into KnowledgeGraphStore alongside KnowledgeGraphRetrievalMixin;
    both mixins share the same instance, so vector methods still reach sibling
    retrieval/write helpers (e.g. self._vector_text_for_node) through the MRO.
    """
