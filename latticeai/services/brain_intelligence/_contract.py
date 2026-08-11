"""The seam the Brain Intelligence mixins share.

``BrainIntelligenceService`` is assembled from the proposal, sampling, health,
digest and consistency mixins. Each reads constructor state it does not own,
and several call methods another mixin implements — ``_graph_sample`` feeds
almost everything, ``_proactive`` gates the graph-layer surfaces, and the
garden view composes the contradiction scan. That contract existed as an
unwritten convention inside one class; this module writes it down.

Typing-only, exactly like :mod:`lattice_brain.portability._contract`: every
body below is a bare ``raise NotImplementedError`` that the composed class
always overrides, so the MRO and every method resolution stay what the
single-file class had. Adding a cross-mixin call without declaring it here is a
type error.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class BrainIntelligenceCore:
    """What any Brain Intelligence mixin may assume about ``self``.

    Members are declared, not implemented: the implementation lives in
    ``service.py`` (constructor state) or in whichever mixin owns the method.
    """

    # ── State owned by BrainIntelligenceService.__init__ ─────────────────────
    _kg: Any
    _memory: Any
    _enable_graph: bool
    _memory_quality: Any
    _edge_quality: Any
    _proactive_brain: Any
    _review_queue_service: Any
    _synthesizer: Any

    # ── proposals.py: the review-queue seam every write door goes through ────
    def _review_queue(self) -> Any:
        raise NotImplementedError

    def _synthesis(self) -> Any:
        raise NotImplementedError

    @staticmethod
    def _no_queue(detail: str) -> Dict[str, Any]:
        raise NotImplementedError

    def importance_report(
        self, *, user_email: Optional[str] = None, workspace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        raise NotImplementedError

    # ── sampling.py: the graph slice and the memory tier ─────────────────────
    def _proactive(self) -> Any:
        raise NotImplementedError

    def _graph_sample(self, *, workspace_id: Optional[str]) -> Dict[str, Any]:
        raise NotImplementedError

    def _workspace_memories(
        self, *, user_email: Optional[str], workspace_id: Optional[str]
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError

    # ── consistency.py: what the garden view composes ────────────────────────
    def contradictions(
        self, *, user_email: Optional[str] = None, workspace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        raise NotImplementedError
