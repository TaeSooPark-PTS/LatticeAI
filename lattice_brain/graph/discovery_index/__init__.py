"""Local filesystem indexing: a chosen folder becomes graph knowledge.

v11.3.0 turned this module into a package. ``KnowledgeGraphLocalIndexMixin``
is now composed from four cohesive sub-mixins — text extraction, node/index
upserts, graph cleanup, and the folder-scan driver — each moved here
verbatim. Every name this module exported before still resolves from
``lattice_brain.graph.discovery_index``.
"""

from __future__ import annotations

# ruff: noqa: F403,F405
from .._kg_common import *  # noqa: F403,F401
from .cleanup import _LocalCleanupMixin
from .extract import _LocalExtractMixin
from .scan import _LocalScanMixin
from .upsert import _local_scoped_slug, _LocalUpsertMixin  # noqa: F401


# Base order is most-composed-first (the scan driver, then the three halves it
# calls): each half is named as the driver's typing-only base, and C3 needs a
# subclass ahead of the class it extends. The method sets are disjoint, so at
# runtime the order changes nothing.
class KnowledgeGraphLocalIndexMixin(
    _LocalScanMixin,
    _LocalExtractMixin,
    _LocalUpsertMixin,
    _LocalCleanupMixin,
):
    """Local file → graph indexing (text extraction, node/index upserts,
    graph-node deletion, orphan cleanup, and the index_local_folder driver),
    split out of discovery. Composed into KnowledgeGraphStore alongside
    KnowledgeGraphDiscoveryMixin; both share the instance so these methods
    still reach sibling discovery/write helpers through the class MRO.
    """
