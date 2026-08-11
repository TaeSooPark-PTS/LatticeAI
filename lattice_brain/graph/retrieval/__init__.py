"""Retrieval: the read path from a query to grounded context.

v11.3.0 turned this module into a package. ``KnowledgeGraphRetrievalMixin`` is
now composed from four cohesive sub-mixins — graph view + lexical search,
the hybrid pipeline, context assembly, and destructive maintenance — each of
which moved here verbatim. Every name this module exported before still
resolves from ``lattice_brain.graph.retrieval``.
"""

from __future__ import annotations

# ruff: noqa: F403,F405
from .._kg_common import *  # noqa: F403,F401
from ..fusion import (  # noqa: F401
    DEFAULT_EXPANSION_CAP,
    DEFAULT_EXPANSION_SEEDS,
    expand_with_neighbors,
    graph_expansion_enabled,
    rrf_fuse,
)

# --- Compat seam (v9.9.5 decomposition) -------------------------------------
# The non-search read surface (list_documents / workspaces_of /
# filter_scoped_nodes / neighbors / get_node / relationship_search /
# traverse / stats) moved byte-identically to .retrieval_reads as
# KnowledgeGraphReadsMixin. Re-exported here so any legacy
# ``from lattice_brain.graph.retrieval import ...`` site keeps resolving.
from ..retrieval_reads import KnowledgeGraphReadsMixin  # noqa: F401
from .context import _ContextMixin
from .graph_view import _GraphViewMixin
from .hybrid import _HybridSearchMixin
from .maintenance import _MaintenanceMixin
from .signals import (  # noqa: F401
    MULTIMODAL_NODE_TYPES,
    context_quality_signal,
    multimodal_signal,
)


class KnowledgeGraphRetrievalMixin(
    _ContextMixin,
    _HybridSearchMixin,
    _GraphViewMixin,
    _MaintenanceMixin,
):
    """The graph read surface, composed from its four cohesive halves.

    The sub-mixins define disjoint method sets, so resolution order changes
    nothing at runtime: this class exposes exactly the methods it exposed when
    they all lived in one 1,120-line module. The order is written
    most-composed-first (context → hybrid → graph view) because each half
    names the one below it as its typing-only base, and C3 needs a subclass
    ahead of the class it extends.
    """
