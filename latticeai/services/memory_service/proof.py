"""Brain Proof: the durable evidence behind "your Brain outlives the model".

Backend-owned on purpose. The first-run Brain screen must show the stores it
can actually recall from, and the model-continuity claim has to be independent
of whichever LLM happens to be loaded — a UI-derived version of this would be
an assertion, not a proof.

The two are deliberately separated: ``capability`` is always true (the design
is model-independent), while ``proven`` stays false until there is durable
evidence on disk to recall from.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from latticeai.core.timeutil import now_iso as _now

from ._contract import MemoryCore as _Core


class MemoryProofMixin(_Core):
    """The proof points and the query they are demonstrated with."""

    def brain_proof(
        self,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
        active_model: Optional[str] = None,
        recall_query: str = "",
        limit: int = 3,
    ) -> Dict[str, Any]:
        """Return the proof points that make Brain feel model-independent.

        This is intentionally backend-owned instead of UI-derived: the first-run
        Brain screen needs to show the durable stores it can actually recall
        from, and the model continuity claim must be independent from whichever
        LLM is currently loaded.
        """
        manager = self.manager(user_email=user_email, workspace_id=workspace_id)
        sources = {str(source.get("id")): source for source in manager.get("sources", []) if isinstance(source, dict)}
        readiness = manager.get("brain_readiness") or {}
        conversation_count = int(sources.get("conversation", {}).get("count") or 0)
        workspace_count = int(sources.get("workspace", {}).get("count") or 0)
        graph_count = int(sources.get("graph", {}).get("count") or 0)
        vector_count = int(sources.get("vector", {}).get("count") or 0)
        query = recall_query.strip() or self._latest_recall_query(user_email=user_email, workspace_id=workspace_id)
        recall: Dict[str, Any] = (
            self.recall(query, user_email=user_email, workspace_id=workspace_id, limit=limit)
            if query
            else {"query": "", "results": [], "count": 0, "source": "live"}
        )
        recall_items = [
            {
                "id": item.get("id"),
                "source": item.get("source"),
                "title": item.get("title"),
                "snippet": item.get("snippet"),
                "score": item.get("score", 0),
                # Evidence explainability: why this item was recalled, so the
                # citation UI can show the matched terms instead of a bare score.
                "matched_terms": item.get("matched_terms") or [],
                "confidence": item.get("confidence") or "low",
                # v11.1.0: what kind of memory this is, and — for a picture —
                # the caption and inline thumbnail the Evidence panel renders.
                "kind": item.get("kind") or "",
                **({"caption": item["caption"]} if item.get("caption") else {}),
                **({"thumbnail": item["thumbnail"]} if item.get("thumbnail") else {}),
            }
            for item in list(recall.get("results", []))[: max(1, min(limit, 8))]
        ]
        durable_items = workspace_count + conversation_count + graph_count
        # Capability and proof are deliberately separated. The brain is
        # architecturally model-independent, so the capability is always true.
        # The proof stays false until there is durable evidence on disk.
        has_durable_evidence = durable_items > 0
        proven_continuity = has_durable_evidence
        return {
            "status": readiness.get("state") or "quiet",
            "readiness": readiness,
            "model_continuity": {
                "active_model": active_model or "",
                "brain_owner": "lattice_brain",
                # Design capability: the brain is built to outlive any model.
                "capability": True,
                # Proof: only true when durable evidence exists to recall.
                "survives_model_switch": proven_continuity,
                "proven": proven_continuity,
                "context_store": "workspace + conversation + graph + vector",
            },
            "proofs": {
                "durable_items": durable_items,
                "has_durable_evidence": has_durable_evidence,
                "workspace_memories": workspace_count,
                "conversations": conversation_count,
                "graph_concepts": graph_count,
                "vector_items": vector_count,
                "healthy_sources": readiness.get("signals", {}).get("healthy_sources", 0),
            },
            "recall": {
                "query": recall.get("query") or query,
                "items": recall_items,
                "count": recall.get("count", 0),
            },
            "claims": {
                "can_recall_user_context": bool(recall_items or durable_items > 0),
                # Proven, not asserted: no durable evidence means no continuity claim.
                "keeps_context_across_models": proven_continuity,
                "is_knowledge_store": bool(graph_count or vector_count or workspace_count or conversation_count),
            },
            "generated_at": _now(),
        }

    def _latest_recall_query(self, *, user_email: Optional[str], workspace_id: Optional[str]) -> str:
        for memory in self._workspace_memories(user_email=user_email, workspace_id=workspace_id or "personal"):
            content = str(memory.get("content") or "").strip()
            if content:
                return content[:96]
        for conversation in self._conversations():
            messages = conversation.get("messages") or []
            if not isinstance(messages, list):
                continue
            for message in reversed(messages[-8:]):
                if not isinstance(message, dict):
                    continue
                if user_email and message.get("user_email") != user_email:
                    continue
                message_workspace = message.get("workspace_id") or "personal"
                target_workspace = workspace_id or "personal"
                if message_workspace != target_workspace:
                    continue
                content = str(message.get("content") or "").strip()
                if content:
                    return content[:96]
        return ""
