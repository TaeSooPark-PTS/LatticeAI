"""Legacy ↔ v2 projection, the keyword index, and curation over the result.

Split into two cohesive submodules in v11.3.0 (no behaviour change):
``v2_schema`` (the derived projection, its migration, and the FTS index) and
``curation`` (topic promotion, the review queue, noise reduction).
:class:`KnowledgeGraphProjectionMixin` composes both under its original name, so
``KnowledgeGraphStore``'s MRO and every method resolution are what they were,
and every name the single module exposed is re-exported here.
"""

# The single module had no ``__all__``: its surface was "every module global",
# star-imported ``_kg_common`` vocabulary included. That is reproduced verbatim
# below, so the waiver the star import needs applies to this file too.
# ruff: noqa: F403,F405
from __future__ import annotations

from .._kg_common import *  # noqa: F401

# Module-level names of the single file, re-exported with the redundant-alias
# form so each is marked deliberate rather than a leftover import.
#
# Stubbing note: rebinding one of these *here* changes only this module's name.
# ``curate`` reads ``_promotion_review_default`` as ``curation``'s own global,
# so a test standing in for it patches ``…graph.projection.curation``.
from .curation import _LAST_NOISE_CURATE_KEY as _LAST_NOISE_CURATE_KEY
from .curation import _PENDING_PROMOTIONS_CAP as _PENDING_PROMOTIONS_CAP
from .curation import _PENDING_PROMOTIONS_KEY as _PENDING_PROMOTIONS_KEY
from .curation import _PROMOTION_REVIEW_ENV as _PROMOTION_REVIEW_ENV
from .curation import KnowledgeGraphCurationMixin
from .curation import _promotion_review_default as _promotion_review_default
from .v2_schema import KnowledgeGraphV2SchemaMixin


class KnowledgeGraphProjectionMixin(
    KnowledgeGraphV2SchemaMixin, KnowledgeGraphCurationMixin
):
    """Every projection and curation method, under the name the store mixes in.

    Composition only: the two mixins own disjoint halves of what used to be one
    class body, and this subclass adds nothing. ``KnowledgeGraphStore`` keeps
    listing exactly this name among its bases.
    """
