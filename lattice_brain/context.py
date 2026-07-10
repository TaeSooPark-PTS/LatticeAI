"""Context System — one budgeted, provenance-carrying assembly pipeline.

Replaces the ad-hoc string concatenation that built chat context (language
hint + vault scan + KG LIKE search + recent chat appended in fixed order
with no size control). Every section the assembler emits records WHY it is
in the prompt (source, ids, scores), so "why is this in my context?" is
answerable, and the whole assembly respects a token budget.

Token counts are an explicit approximation: ``approx_tokens = ceil(len/4)``
(the stack ships no model-agnostic tokenizer; the field name says so —
design-review amendment T5).
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


def approx_tokens(text: str) -> int:
    """Documented chars/4 approximation — NOT a real tokenizer count."""
    return max(0, (len(text or "") + 3) // 4)


def _call_context_seam(callback: Callable[..., Any], query: str, **context: Any) -> Any:
    """Pass identity/scope only when a legacy seam declares support for it.

    Signature inspection preserves old one-argument adapters without catching
    a callback's own ``TypeError`` and retrying it with less restrictive scope.
    If a callable cannot be inspected, the secure behavior is to pass every
    context field and let the caller's failure isolation omit that section.
    """
    try:
        parameters = inspect.signature(callback).parameters
    except (TypeError, ValueError):
        return callback(query, **context)
    accepts_kwargs = any(
        item.kind is inspect.Parameter.VAR_KEYWORD
        for item in parameters.values()
    )
    supported = context if accepts_kwargs else {
        key: value for key, value in context.items()
        if key in parameters
    }
    return callback(query, **supported)


def _call_keyword_seam(callback: Callable[..., Any], **context: Any) -> Any:
    """Invoke a keyword-only seam while preserving legacy narrow signatures."""
    try:
        parameters = inspect.signature(callback).parameters
    except (TypeError, ValueError):
        return callback(**context)
    accepts_kwargs = any(
        item.kind is inspect.Parameter.VAR_KEYWORD
        for item in parameters.values()
    )
    supported = context if accepts_kwargs else {
        key: value for key, value in context.items()
        if key in parameters
    }
    return callback(**supported)


@dataclass
class ContextSection:
    name: str
    content: str
    source: str                      # memory | knowledge | notes | recent_chat | system
    provenance: List[Dict[str, Any]] = field(default_factory=list)
    truncated: bool = False

    @property
    def approx_tokens(self) -> int:
        return approx_tokens(self.content)

    def as_trace(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "approx_tokens": self.approx_tokens,
            "truncated": self.truncated,
            "provenance": self.provenance,
        }


@dataclass
class AssembledContext:
    sections: List[ContextSection]
    budget_approx_tokens: int

    @property
    def text(self) -> str:
        parts = []
        for section in self.sections:
            if section.content.strip():
                parts.append(f"[{section.name}]\n{section.content.strip()}")
        return "\n\n".join(parts)

    @property
    def approx_tokens(self) -> int:
        return sum(s.approx_tokens for s in self.sections)

    def trace(self) -> Dict[str, Any]:
        return {
            "budget_approx_tokens": self.budget_approx_tokens,
            "used_approx_tokens": self.approx_tokens,
            "sections": [s.as_trace() for s in self.sections],
        }


class ContextAssembler:
    """Ordered, budgeted context assembly over injected retrieval seams.

    Section priority (kept under budget in this order — semantic memories
    first because they are cheap and durable; recency last):
      1. memories  — workspace semantic memories (preferences/decisions/…)
      2. knowledge — hybrid search over the brain (the product's own engine)
      3. notes     — garden-note context
      4. recent    — the user's recent exchange
    Every seam is optional; an absent seam contributes nothing (honest
    absence), never a fabricated section.
    """

    def __init__(
        self,
        *,
        memory_recall: Optional[Callable[..., Dict[str, Any]]] = None,
        hybrid_search: Optional[Callable[..., Dict[str, Any]]] = None,
        notes_context: Optional[Callable[..., str]] = None,
        recent_chat: Optional[Callable[..., str]] = None,
    ) -> None:
        self._memory_recall = memory_recall
        self._hybrid_search = hybrid_search
        self._notes_context = notes_context
        self._recent_chat = recent_chat

    def assemble(
        self,
        query: str,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        budget: int = 2000,
        memory_limit: int = 5,
        knowledge_limit: int = 5,
    ) -> AssembledContext:
        sections: List[ContextSection] = []

        if self._memory_recall is not None:
            sections.append(self._memories_section(query, user_email, workspace_id, memory_limit))
        if self._hybrid_search is not None:
            sections.append(self._knowledge_section(query, knowledge_limit, user_email, workspace_id))
        if self._notes_context is not None:
            sections.append(self._notes_section(query, user_email, workspace_id))
        if self._recent_chat is not None:
            sections.append(self._recent_section(user_email, conversation_id, workspace_id))

        sections = [s for s in sections if s.content.strip()]
        self._apply_budget(sections, budget)
        return AssembledContext(sections=sections, budget_approx_tokens=budget)

    # ── section builders (each failure-isolated and honest) ───────────────
    def _memories_section(self, query, user_email, workspace_id, limit) -> ContextSection:
        try:
            recall = self._memory_recall(query, user_email=user_email, workspace_id=workspace_id, limit=limit)
            results = [r for r in recall.get("results", []) if r.get("source") == "workspace"][:limit]
        except Exception as exc:
            logging.debug("context: memory recall failed: %s", exc)
            results = []
        lines = [f"- ({r.get('kind') or 'memory'}) {r.get('snippet') or ''}" for r in results]
        return ContextSection(
            name="User memories",
            content="\n".join(lines),
            source="memory",
            provenance=[
                {"id": r.get("id"), "kind": r.get("kind"), "score": r.get("score")}
                for r in results
            ],
        )

    def _knowledge_section(self, query, limit, user_email=None, workspace_id=None) -> ContextSection:
        try:
            hybrid = _call_context_seam(
                self._hybrid_search,
                query,
                limit=limit,
                user_email=user_email,
                workspace_id=workspace_id,
            )
            matches = hybrid.get("matches", [])[:limit]
        except Exception as exc:
            logging.debug("context: hybrid search failed: %s", exc)
            matches = []
        lines = []
        provenance = []
        for m in matches:
            title = m.get("title") or m.get("id") or "item"
            body = (m.get("summary") or m.get("snippet") or "")[:400]
            lines.append(f"- {title}: {body}" if body else f"- {title}")
            provenance.append({
                "id": m.get("id"),
                "score": m.get("score"),
                "sources": m.get("sources"),
            })
        return ContextSection(
            name="Knowledge",
            content="\n".join(lines),
            source="knowledge",
            provenance=provenance,
        )

    def _notes_section(self, query, user_email=None, workspace_id=None) -> ContextSection:
        try:
            content = _call_context_seam(
                self._notes_context,
                query,
                user_email=user_email,
                workspace_id=workspace_id,
            ) or ""
        except Exception as exc:
            logging.debug("context: notes context failed: %s", exc)
            content = ""
        return ContextSection(
            name="Garden notes",
            content=content,
            source="notes",
            provenance=[{"source": "garden", "included": bool(content)}],
        )

    def _recent_section(self, user_email, conversation_id, workspace_id=None) -> ContextSection:
        try:
            content = _call_keyword_seam(
                self._recent_chat,
                user_email=user_email,
                conversation_id=conversation_id,
                workspace_id=workspace_id,
            ) or ""
        except Exception as exc:
            logging.debug("context: recent chat failed: %s", exc)
            content = ""
        return ContextSection(
            name="Recent conversation",
            content=content,
            source="recent_chat",
            provenance=[{"conversation_id": conversation_id, "user_email": user_email}],
        )

    # ── budget ─────────────────────────────────────────────────────────────
    @staticmethod
    def _apply_budget(sections: List[ContextSection], budget: int) -> None:
        """Trim from the END (lowest priority) until under budget."""
        budget = max(1, int(budget))
        used = 0
        for section in sections:
            remaining = budget - used
            if remaining <= 0:
                section.content = ""
                section.truncated = True
                continue
            if section.approx_tokens > remaining:
                section.content = section.content[: remaining * 4]
                section.truncated = True
            used += section.approx_tokens


__all__ = ["ContextAssembler", "ContextSection", "AssembledContext", "approx_tokens"]
