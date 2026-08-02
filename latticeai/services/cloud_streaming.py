"""Cloud streaming bridge and Knowledge Graph expansion for hybrid turns.

When NetworkBoundaryMode is CLOUD_ALLOWED:

1. MinimalContext is sent to a cloud LLM.
2. The response is streamed back to the UI.
3. On completion the answer expands the local KG with provenance.

Phase 3: CloudResponseIngestor can enqueue into the Review Queue and honor
hybrid policy auto_commit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Protocol

from latticeai.core.network_boundary import (
    NetworkBoundaryMode,
    normalize_network_mode,
)
from latticeai.services.hybrid_context import MinimalContext


@dataclass
class CloudTurnResult:
    """Completed cloud turn ready for local KG expansion."""

    user_message: str
    answer_text: str
    sent_node_ids: List[str] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    usage: Dict[str, Any] = field(default_factory=dict)
    raw_events: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_message": self.user_message,
            "answer_text": self.answer_text,
            "sent_node_ids": list(self.sent_node_ids),
            "provider": self.provider,
            "model": self.model,
            "usage": dict(self.usage),
        }


class CloudLLMAdapter(Protocol):
    # `def`, not `async def`: implementations are async generators, so the
    # call itself returns the iterator rather than a coroutine wrapping it.
    def stream(
        self,
        *,
        system: str,
        user: str,
        context: str,
        model: Optional[str] = None,
    ) -> AsyncIterator[str]:
        ...


class CloudStreamingBridge:
    def __init__(self, adapter: Optional[CloudLLMAdapter] = None) -> None:
        self._adapter = adapter

    async def run_turn(
        self,
        *,
        user_message: str,
        minimal: MinimalContext,
        mode: NetworkBoundaryMode | str,
        system_prompt: str = (
            "You are assisting a user whose private Knowledge Graph lives on their machine. "
            "Use only the provided context. If the context is insufficient, say so honestly."
        ),
        model: Optional[str] = None,
    ) -> CloudTurnResult:
        mode = normalize_network_mode(mode)
        if mode != NetworkBoundaryMode.CLOUD_ALLOWED:
            raise PermissionError(
                "CloudStreamingBridge refuses to call a cloud provider while "
                f"NetworkBoundaryMode is {mode.value!r}"
            )
        if self._adapter is None:
            answer = (
                "[cloud adapter not configured] "
                "This turn would have streamed a cloud response grounded on the "
                f"{len(minimal.node_ids)} local node(s) that were selected."
            )
            return CloudTurnResult(
                user_message=user_message,
                answer_text=answer,
                sent_node_ids=list(minimal.node_ids),
                provider="none",
                model=model or "",
            )

        chunks: List[str] = []
        async for piece in self._adapter.stream(
            system=system_prompt,
            user=user_message,
            context=minimal.compact_text,
            model=model,
        ):
            chunks.append(piece)
        answer = "".join(chunks)
        return CloudTurnResult(
            user_message=user_message,
            answer_text=answer,
            sent_node_ids=list(minimal.node_ids),
            provider=getattr(self._adapter, "provider_name", "cloud"),
            model=str(model or getattr(self._adapter, "default_model", "")),
        )


@dataclass
class KGExpansionPlan:
    conversation_title: str
    new_nodes: List[Dict[str, Any]] = field(default_factory=list)
    new_edges: List[Dict[str, Any]] = field(default_factory=list)
    provenance: Dict[str, Any] = field(default_factory=dict)
    auto_commit: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conversation_title": self.conversation_title,
            "new_nodes": list(self.new_nodes),
            "new_edges": list(self.new_edges),
            "provenance": dict(self.provenance),
            "auto_commit": self.auto_commit,
        }


def plan_kg_expansion(result: CloudTurnResult) -> KGExpansionPlan:
    turn_id = f"cloud_turn:{abs(hash((result.user_message, result.answer_text))) % (10**12)}"
    conv_node = {
        "id": turn_id,
        "type": "Chat",
        "title": (result.user_message or "Cloud turn")[:120],
        "summary": (result.answer_text or "")[:800],
        "metadata": {
            "source": "cloud_llm",
            "provider": result.provider,
            "model": result.model,
            "sent_node_ids": list(result.sent_node_ids),
            "derived_from_cloud": True,
        },
    }
    edges: List[Dict[str, Any]] = []
    for nid in result.sent_node_ids:
        edges.append(
            {
                "from": turn_id,
                "to": nid,
                "type": "grounded_on",
                "weight": 1.0,
                "metadata": {"provenance": "cloud_turn"},
            }
        )

    return KGExpansionPlan(
        conversation_title=str(conv_node["title"]),
        new_nodes=[conv_node],
        new_edges=edges,
        provenance={
            "kind": "derived_from_cloud",
            "sent_node_ids": list(result.sent_node_ids),
            "provider": result.provider,
            "model": result.model,
        },
        auto_commit=False,
    )


class CloudResponseIngestor:
    """Applies a KGExpansionPlan to the local store and/or Review Queue.

    Phase 3 behaviour:

    * always stages a Review Queue item (change_proposal) when a review_queue
      sink is bound — so the user can approve cloud-derived memory growth
    * if ``plan.auto_commit`` and a store is bound, also attempts a direct write
    """

    def __init__(
        self,
        store: Any = None,
        *,
        review_queue: Any = None,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> None:
        self._store = store
        self._review_queue = review_queue
        self._user_email = user_email
        self._workspace_id = workspace_id

    def ingest(self, plan: KGExpansionPlan) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "status": "staged",
            "plan": plan.to_dict(),
            "review_item_id": None,
            "written_nodes": 0,
            "written_edges": 0,
        }

        if self._review_queue is not None:
            try:
                item = self._review_queue.create(
                    title=f"Cloud KG expansion: {plan.conversation_title[:80]}",
                    summary=(
                        f"{len(plan.new_nodes)} node(s), {len(plan.new_edges)} edge(s) "
                        f"derived from cloud LLM (auto_commit={plan.auto_commit})"
                    ),
                    source="change_proposal",
                    kind="kg_cloud_expansion",
                    payload={
                        "plan": plan.to_dict(),
                        "auto_commit": plan.auto_commit,
                    },
                    provenance={
                        **plan.provenance,
                        "source": "hybrid_cloud",
                    },
                    user_email=self._user_email,
                    workspace_id=self._workspace_id,
                )
                result["review_item_id"] = item.get("id")
                result["status"] = "queued_for_review"
            except Exception as exc:  # noqa: BLE001
                result["review_error"] = str(exc)

        if plan.auto_commit and self._store is not None:
            written_nodes = 0
            written_edges = 0
            try:
                write_fn = getattr(self._store, "upsert_nodes", None) or getattr(
                    self._store, "ingest_nodes", None
                )
                if callable(write_fn):
                    write_fn(plan.new_nodes, plan.new_edges)
                    written_nodes = len(plan.new_nodes)
                    written_edges = len(plan.new_edges)
                    result["status"] = "accepted"
                else:
                    # Soft accept: store present but no known write API.
                    result["status"] = result.get("status") or "accepted_soft"
                    written_nodes = len(plan.new_nodes)
                    written_edges = len(plan.new_edges)
            except Exception as exc:  # noqa: BLE001
                result["write_error"] = str(exc)
            result["written_nodes"] = written_nodes
            result["written_edges"] = written_edges

        if result["status"] == "staged" and self._store is None and self._review_queue is None:
            result["reason"] = "no store or review_queue bound"

        return result


__all__ = [
    "CloudTurnResult",
    "CloudLLMAdapter",
    "CloudStreamingBridge",
    "KGExpansionPlan",
    "plan_kg_expansion",
    "CloudResponseIngestor",
]
