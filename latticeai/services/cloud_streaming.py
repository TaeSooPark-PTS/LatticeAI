"""Cloud streaming bridge and Knowledge Graph expansion for hybrid turns.

When NetworkBoundaryMode is CLOUD_ALLOWED:

1. MinimalContext (already selected) is sent to a cloud LLM.
2. The response is streamed back to the UI.
3. On completion the answer is turned into local KG growth with provenance.

This module defines the contracts and a safe no-op / scaffold implementation.
Concrete provider adapters (OpenAI-compatible, Anthropic, etc.) plug in later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Mapping, Optional, Protocol

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
    """Provider-specific streaming adapter."""

    async def stream(
        self,
        *,
        system: str,
        user: str,
        context: str,
        model: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """Yield text chunks."""
        ...


class CloudStreamingBridge:
    """Orchestrates a single hybrid cloud turn.

    Refuses to call any adapter unless the network mode is CLOUD_ALLOWED.
    """

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
            # Scaffold: no real provider wired yet.
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
            model=model or getattr(self._adapter, "default_model", ""),
        )


@dataclass
class KGExpansionPlan:
    """What the ingestor proposes to write into the local Knowledge Graph."""

    conversation_title: str
    new_nodes: List[Dict[str, Any]] = field(default_factory=list)
    new_edges: List[Dict[str, Any]] = field(default_factory=list)
    provenance: Dict[str, Any] = field(default_factory=dict)
    auto_commit: bool = False  # v1 default: stage for review

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conversation_title": self.conversation_title,
            "new_nodes": list(self.new_nodes),
            "new_edges": list(self.new_edges),
            "provenance": dict(self.provenance),
            "auto_commit": self.auto_commit,
        }


def plan_kg_expansion(result: CloudTurnResult) -> KGExpansionPlan:
    """Build a conservative expansion plan from a completed cloud turn.

    v1 only records the conversation turn and provenance links back to the
    local nodes that were sent. Richer concept/decision extraction can be
    layered on later and still go through the same plan object.
    """
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
        conversation_title=conv_node["title"],
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
    """Applies a KGExpansionPlan to a local store (or stages it for review)."""

    def __init__(self, store: Any = None) -> None:
        self._store = store

    def ingest(self, plan: KGExpansionPlan) -> Dict[str, Any]:
        """v1 scaffold: return the plan without writing when no store is bound.

        Real implementation will call the graph write_master / proposal path
        so that cloud-derived knowledge goes through the same quality gates
        as other mutations.
        """
        if self._store is None or not plan.auto_commit:
            return {
                "status": "staged",
                "reason": "auto_commit is false or store is not bound",
                "plan": plan.to_dict(),
            }
        # Placeholder for actual write path.
        return {
            "status": "accepted",
            "plan": plan.to_dict(),
            "written_nodes": len(plan.new_nodes),
            "written_edges": len(plan.new_edges),
        }


__all__ = [
    "CloudTurnResult",
    "CloudLLMAdapter",
    "CloudStreamingBridge",
    "KGExpansionPlan",
    "plan_kg_expansion",
    "CloudResponseIngestor",
]
