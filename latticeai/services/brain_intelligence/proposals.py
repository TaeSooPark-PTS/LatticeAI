"""Synthesis never writes: it proposes, and the Review Center decides.

Everything the Brain notices on its own arrives as a review proposal — new
connections, contradictions, consolidation candidates, decayed importance. That
makes the review queue a hard dependency of every method here: when it is
absent they report ``available: False`` rather than falling back to a direct
write.

:meth:`proactive_brief` is the read-only counterpart. It counts what is already
waiting rather than raising anything new, so opening the home screen never
mutates the Brain.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from latticeai.core.timeutil import now_iso as _now

from ._contract import BrainIntelligenceCore as _Core
from .constants import LOGGER


class BrainProposalsMixin(_Core):
    """The proposal path and the proactive brief. Mixed into the service."""

    # ── proposal path (v11.1.0) ───────────────────────────────────────────
    #
    # Synthesis never writes: it proposes, and the Review Center decides. That
    # makes the review queue a hard dependency of every method below — when it
    # is absent they report ``available: False`` rather than falling back to a
    # direct write.

    def _review_queue(self) -> Any:
        """The queue to propose into, or ``None`` when there is none.

        Prefers an explicitly injected service. Otherwise it builds one over
        the workspace store the memory service already holds — the same
        ``WorkspaceOSStore`` every other ``ReviewQueueService`` in the process
        is constructed over, so proposals land in the one inbox the user reads
        even before the composition root injects the service directly.
        """
        if self._review_queue_service is not None:
            return self._review_queue_service
        store = getattr(self._memory, "_store", None)
        if store is None or not hasattr(store, "create_review_item"):
            return None
        from latticeai.services.review_queue import ReviewQueueService

        self._review_queue_service = ReviewQueueService(store=store)
        return self._review_queue_service

    def _synthesis(self) -> Any:
        """Lazy :class:`BrainSynthesizer` over the graph + review queue."""
        if self._synthesizer is not None:
            return self._synthesizer
        queue = self._review_queue()
        if not self._enable_graph or queue is None:
            return None
        from lattice_brain.synthesis import BrainSynthesizer

        self._synthesizer = BrainSynthesizer(self._kg, queue)
        return self._synthesizer

    @staticmethod
    def _no_queue(detail: str) -> Dict[str, Any]:
        return {"available": False, "detail": detail, "generated_at": _now()}

    def synthesize(
        self, *, user_email: Optional[str] = None, workspace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Run one synthesis pass; every finding becomes a review proposal."""
        synthesizer = self._synthesis()
        if synthesizer is None:
            return self._no_queue(
                "synthesis needs both the knowledge graph and the review queue"
            )
        try:
            result = dict(
                synthesizer.run(workspace_id=workspace_id, user_email=user_email)
            )
        except Exception as exc:  # noqa: BLE001 — a failed pass is reported, not raised
            LOGGER.exception("brain synthesis failed")
            return {"available": False, "error": str(exc), "generated_at": _now()}
        result["available"] = True
        return result

    def note_ingest(
        self,
        result: Any,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Ingest-driven trigger seam.

        Hand every ingest result here; synthesis runs only once the configured
        number of genuinely new nodes has accumulated. Returns the run result
        when one fired, ``None`` otherwise — so an ingest path can call this
        unconditionally and cheaply.
        """
        synthesizer = self._synthesis()
        if synthesizer is None:
            return None
        try:
            return synthesizer.run_if_due(
                result, workspace_id=workspace_id, user_email=user_email
            )
        except Exception:  # noqa: BLE001 — synthesis must never break an ingest
            LOGGER.exception("ingest-triggered synthesis failed")
            return None

    def propose_contradictions(
        self, *, user_email: Optional[str] = None, workspace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Raise a review proposal for every contradicting pair of memories."""
        queue = self._review_queue()
        if not self._enable_graph or queue is None:
            return self._no_queue(
                "contradiction proposals need both the knowledge graph and the review queue"
            )
        from lattice_brain.synthesis import propose_contradictions

        try:
            return propose_contradictions(
                self._kg, queue, workspace_id=workspace_id, user_email=user_email
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("contradiction proposal pass failed")
            return {"available": False, "error": str(exc), "generated_at": _now()}

    def resolve_contradiction(
        self,
        item_id: str,
        *,
        resolution: str,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Approve a contradiction proposal and apply its temporal stamps.

        Raises ``ContradictionResolutionError`` (a ``ValueError``) for an
        unknown resolution or a review item that is not a contradiction — the
        router turns those into 400s.
        """
        queue = self._review_queue()
        if not self._enable_graph or queue is None:
            return self._no_queue(
                "resolving a contradiction needs both the knowledge graph and the review queue"
            )
        from lattice_brain.synthesis import resolve_contradiction

        result = dict(
            resolve_contradiction(
                self._kg,
                queue,
                item_id,
                resolution=resolution,
                workspace_id=workspace_id,
            )
        )
        result["available"] = True
        return result

    def importance_report(
        self, *, user_email: Optional[str] = None, workspace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Which memories are load-bearing, and which have decayed into noise."""
        proactive = self._proactive()
        if proactive is None:
            return {"available": False, "candidates": [], "generated_at": _now()}
        try:
            report = dict(proactive.importance_report(workspace_id=workspace_id))
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("importance report failed")
            return {
                "available": False,
                "error": str(exc),
                "candidates": [],
                "generated_at": _now(),
            }
        report["available"] = True
        return report

    def proactive_brief(
        self, *, user_email: Optional[str] = None, workspace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """The Brain Brief's proactive section: what the Brain noticed on its own.

        Read-only. It counts the proposals already waiting in the Review Center
        rather than raising new ones, so opening the home screen never mutates
        anything — and reports honestly when there is no queue to read.
        """
        section: Dict[str, Any] = {
            "available": False,
            "pending": {"total": 0, "by_kind": {}},
            "tidying": False,
            "headline": "",
            "lines": [],
            "generated_at": _now(),
        }
        queue = self._review_queue()
        if queue is None:
            section["detail"] = "the review queue is not available on this deployment"
            return section
        pending = self._pending_synthesis_items(queue, workspace_id=workspace_id)
        by_kind: Dict[str, int] = {}
        for item in pending:
            kind = str(item.get("kind") or "suggestion")
            by_kind[kind] = by_kind.get(kind, 0) + 1
        synthesizer = self._synthesis()
        brief: Dict[str, Any] = {}
        if synthesizer is not None:
            try:
                brief = dict(
                    synthesizer.brief_section(
                        counts=by_kind, workspace_id=workspace_id
                    )
                )
            except Exception:  # noqa: BLE001 — the section degrades, never raises
                LOGGER.exception("synthesis brief section failed")
        section.update(
            {
                "available": True,
                "pending": {"total": len(pending), "by_kind": by_kind},
                "items": [
                    {
                        "id": item.get("id"),
                        "kind": item.get("kind"),
                        "title": item.get("title"),
                        "summary": item.get("summary"),
                    }
                    for item in pending[:5]
                ],
                "tidying": by_kind.get("consolidation", 0) > 0,
                "headline": str(brief.get("headline") or ""),
                "lines": list(brief.get("lines") or []),
                "recent_nodes": brief.get("recent_nodes"),
            }
        )
        return section

    @staticmethod
    def _pending_synthesis_items(
        queue: Any, *, workspace_id: Optional[str]
    ) -> List[Dict[str, Any]]:
        from lattice_brain.synthesis import SYNTHESIS_REVIEW_SOURCE

        try:
            listing = queue.list(
                workspace_id=workspace_id, source=SYNTHESIS_REVIEW_SOURCE
            )
        except Exception:  # noqa: BLE001 — an unreadable inbox reads as empty
            LOGGER.exception("review queue listing failed")
            return []
        return [
            item
            for item in listing.get("items") or []
            if str(item.get("effective_status") or item.get("status")) == "pending"
        ]
