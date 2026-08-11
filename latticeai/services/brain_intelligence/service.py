"""The composed Brain Intelligence service — injected collaborators, five surfaces.

``BrainIntelligenceService`` owns the state every mixin reads (the Knowledge
Graph, the memory service, the two quality managers, and the lazily built
proactive brain / synthesizer) and inherits its behaviour from the proposal,
sampling, health, digest and consistency mixins. Same public surface, same
method resolution, in six readable files instead of one eleven-hundred-line
one.

Pure service: no FastAPI, no globals. Collaborators are injected.
"""

from __future__ import annotations

from typing import Any

from lattice_brain.quality import GraphEdgeQualityManager, MemoryQualityManager

from .consistency import BrainConsistencyMixin
from .digest import BrainDigestMixin
from .health import BrainHealthMixin
from .proposals import BrainProposalsMixin
from .sampling import BrainSamplingMixin


class BrainIntelligenceService(
    BrainProposalsMixin,
    BrainSamplingMixin,
    BrainHealthMixin,
    BrainDigestMixin,
    BrainConsistencyMixin,
):
    def __init__(
        self,
        *,
        knowledge_graph: Any = None,
        memory_service: Any = None,
        enable_graph: bool = True,
        review_queue: Any = None,
    ) -> None:
        self._kg = knowledge_graph
        self._memory = memory_service
        self._enable_graph = bool(enable_graph and knowledge_graph is not None)
        self._memory_quality = MemoryQualityManager()
        self._edge_quality = GraphEdgeQualityManager()
        self._proactive_brain: Any = None
        self._review_queue_service = review_queue
        self._synthesizer: Any = None
