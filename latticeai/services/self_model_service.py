"""Self-Model service — the Brain's profile of its owner (v11.1.0 Track 4).

Router-facing façade over :mod:`lattice_brain.self_model`. It owns two things
the Brain Core deliberately does not:

* **where the collaborators come from.** The knowledge graph and the review
  queue are read off the ``MemoryService`` the memory router already holds
  (the same ``WorkspaceOSStore`` every other ``ReviewQueueService`` in the
  process is built over), so the Self-Model routes need no extra wiring at the
  composition root and proposals land in the one inbox the user reads.
* **what "unavailable" means.** A Brain with the graph disabled reports
  ``available: False`` with a reason instead of pretending it has no profile,
  and a write attempted without a graph raises so the API answers 409 rather
  than silently doing nothing.

The consent model is the product's: extraction proposes, the user approves,
and only the *user's own* edits write directly.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from lattice_brain.self_model import (
    DEFAULT_SUMMARY_TOKENS,
    KIND_ORDER,
    SelfModelError,
    apply_self_model_proposal,
    delete_self_model_fact,
    list_self_model,
    propose_self_model,
    self_model_summary,
    upsert_self_model_fact,
)
from latticeai.core.timeutil import now_iso as _now

GRAPH_UNAVAILABLE = "self-model needs the knowledge graph"
QUEUE_UNAVAILABLE = "self-model proposals need the review queue"


class SelfModelService:
    """Read/propose/apply/edit the Self-Model subgraph."""

    def __init__(
        self,
        *,
        memory_service: Any = None,
        knowledge_graph: Any = None,
        review_queue: Any = None,
        enable_graph: bool = True,
        summary_tokens: int = DEFAULT_SUMMARY_TOKENS,
    ) -> None:
        self._memory = memory_service
        self._graph = knowledge_graph
        self._queue = review_queue
        self._enable_graph = bool(enable_graph)
        self._summary_tokens = int(summary_tokens)

    # ── collaborators ────────────────────────────────────────────────────
    def _kg(self) -> Any:
        """The knowledge graph, or ``None`` when this Brain has none."""
        if not self._enable_graph:
            return None
        if self._graph is None:
            self._graph = getattr(self._memory, "_kg", None)
        return self._graph

    def _review_queue(self) -> Any:
        """The review queue proposals are written to, or ``None``."""
        if self._queue is not None:
            return self._queue
        store = getattr(self._memory, "_store", None)
        if store is None or not hasattr(store, "create_review_item"):
            return None
        from latticeai.services.review_queue import ReviewQueueService

        self._queue = ReviewQueueService(store=store)
        return self._queue

    @staticmethod
    def _unavailable(detail: str) -> Dict[str, Any]:
        return {"available": False, "detail": detail, "generated_at": _now()}

    def _require_graph(self) -> Any:
        graph = self._kg()
        if graph is None:
            raise SelfModelError(GRAPH_UNAVAILABLE, code="graph_unavailable")
        return graph

    # ── reads ────────────────────────────────────────────────────────────
    def profile(
        self, *, user_email: Optional[str] = None, workspace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Everything the Brain holds about its owner, plus the injected text."""
        graph = self._kg()
        if graph is None:
            return {**self._unavailable(GRAPH_UNAVAILABLE), "facts": [], "summary": ""}
        listing = dict(list_self_model(graph, workspace_id=workspace_id))
        listing["summary"] = self_model_summary(
            graph, limit_tokens=self._summary_tokens, workspace_id=workspace_id
        )
        listing["summary_tokens"] = self._summary_tokens
        listing["user_email"] = user_email
        listing["kind_options"] = list(KIND_ORDER)
        return listing

    def summary(self, *, workspace_id: Optional[str] = None) -> str:
        """Injection-ready summary (empty string when there is nothing to say)."""
        graph = self._kg()
        if graph is None:
            return ""
        return self_model_summary(
            graph, limit_tokens=self._summary_tokens, workspace_id=workspace_id
        )

    # ── proposal path (agents / background) ──────────────────────────────
    def propose(
        self,
        text: str,
        *,
        texts: Optional[List[str]] = None,
        source: Optional[str] = None,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
        max_proposals: int = 5,
    ) -> Dict[str, Any]:
        """Read text and raise a review proposal per new fact. Never writes."""
        graph = self._kg()
        if graph is None:
            return self._unavailable(GRAPH_UNAVAILABLE)
        queue = self._review_queue()
        if queue is None:
            return self._unavailable(QUEUE_UNAVAILABLE)
        return propose_self_model(
            graph,
            queue,
            text=text,
            texts=texts,
            source=source,
            workspace_id=workspace_id,
            user_email=user_email,
            max_proposals=max_proposals,
        )

    def apply(
        self, item_id: str, *, workspace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Approve one Self-Model proposal and write the fact into the graph."""
        graph = self._require_graph()
        queue = self._review_queue()
        if queue is None:
            raise SelfModelError(QUEUE_UNAVAILABLE, code="queue_unavailable")
        return apply_self_model_proposal(
            graph, queue, item_id, workspace_id=workspace_id
        )

    # ── user-initiated edits (direct) ────────────────────────────────────
    def upsert(
        self, *, kind: str, text: str, workspace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """A person editing their own profile writes straight through."""
        return upsert_self_model_fact(
            self._require_graph(), kind=kind, text=text, workspace_id=workspace_id
        )

    def delete(self, node_id: str) -> Dict[str, Any]:
        """Forget one fact about the user, permanently."""
        return delete_self_model_fact(self._require_graph(), node_id)


__all__ = ["GRAPH_UNAVAILABLE", "QUEUE_UNAVAILABLE", "SelfModelService"]
