"""Minimal context extraction for hybrid cloud LLM turns.

Given a user message and a local Knowledge Graph store, select only the
smallest useful set of related nodes, assemble a compact text payload, and
estimate token cost. This is the only knowledge that is ever allowed to leave
the machine when NetworkBoundaryMode is CLOUD_ALLOWED.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Sequence

from latticeai.core.network_boundary import (
    NetworkBoundaryMode,
    is_node_blocked_for_cloud,
    normalize_network_mode,
)
from latticeai.core.security import redact_secret_text

logger = logging.getLogger(__name__)


@dataclass
class MinimalContext:
    """What may be sent to a cloud LLM."""

    query: str
    keywords: List[str] = field(default_factory=list)
    node_ids: List[str] = field(default_factory=list)
    compact_text: str = ""
    nodes: List[Dict[str, Any]] = field(default_factory=list)
    token_estimate: int = 0
    quality: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "keywords": list(self.keywords),
            "node_ids": list(self.node_ids),
            "compact_text": self.compact_text,
            "nodes": list(self.nodes),
            "token_estimate": self.token_estimate,
            "quality": dict(self.quality),
        }


class SupportsHybridSearch(Protocol):
    """Minimal surface required from a KnowledgeGraph store."""

    def hybrid_search(
        self,
        query: str,
        *,
        top_k: int = 20,
        allowed_workspaces: Any = None,
        include_legacy_global: bool = False,
    ) -> Dict[str, Any]:
        ...

    def context_for_query_with_meta(
        self,
        query: str,
        limit: int = 6,
        *,
        allowed_workspaces: Any = None,
        include_legacy_global: bool = False,
        use_hybrid: bool = True,
    ) -> Dict[str, Any]:
        ...


def _rough_token_estimate(text: str) -> int:
    """Cheap heuristic: ~4 characters per token for mixed EN/KO."""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def _extract_keywords(message: str, limit: int = 12) -> List[str]:
    """Very lightweight keyword / entity candidates.

    Real extraction can later be replaced by a small local model or the
    existing topic candidate helpers inside lattice_brain. For scaffolding we
    keep this deterministic and dependency-free.
    """
    import re

    text = str(message or "").strip()
    if not text:
        return []
    # Split on whitespace and common punctuation; keep tokens of length >= 2.
    tokens = re.findall(r"[\w가-힣]{2,}", text, flags=re.UNICODE)
    seen: set[str] = set()
    out: List[str] = []
    for tok in tokens:
        key = tok.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tok)
        if len(out) >= limit:
            break
    return out


def build_minimal_context(
    message: str,
    *,
    store: Optional[SupportsHybridSearch] = None,
    mode: NetworkBoundaryMode | str = NetworkBoundaryMode.LOCAL_ONLY,
    top_k: int = 6,
    allowed_workspaces: Any = None,
    include_legacy_global: bool = False,
    preferred_types: Optional[Sequence[str]] = None,
) -> MinimalContext:
    """Select the smallest useful set of local nodes for a cloud turn.

    When mode is LOCAL_ONLY this still returns a MinimalContext (for local
    RAG) but callers must not transmit it.
    """
    mode = normalize_network_mode(mode)
    query = str(message or "").strip()
    keywords = _extract_keywords(query)

    empty = MinimalContext(
        query=query,
        keywords=keywords,
        quality={"mode": "none", "nodes": 0, "limited": True, "reason": "no store or empty query"},
    )
    if not query or store is None:
        return empty

    preferred = set(
        preferred_types
        or (
            "Decision",
            "Concept",
            "Task",
            "Document",
            "File",
            "CodeFile",
            "Person",
            "Feature",
        )
    )

    # One retrieval, not two. This used to call
    # ``context_for_query_with_meta`` first — a full retrieval — and throw away
    # everything but its ``quality`` dict, then run ``hybrid_search`` for the
    # matches it actually needed. Every cloud turn paid for retrieval twice.
    # ``context_quality_signal`` derives the same signal from the match list we
    # already have.
    matches: List[Dict[str, Any]] = []
    quality: Dict[str, Any] = {}

    try:
        hybrid = store.hybrid_search(
            query,
            top_k=max(top_k * 2, top_k),
            allowed_workspaces=allowed_workspaces,
            include_legacy_global=include_legacy_global,
        )
        matches = list(hybrid.get("matches") or [])
        from lattice_brain.graph.retrieval import context_quality_signal

        quality = context_quality_signal(
            str(hybrid.get("mode") or "hybrid"),
            len(matches),
        )
    except Exception:  # noqa: BLE001
        # Fail closed: an unusable retrieval sends nothing rather than
        # falling back to a broader, unfiltered context.
        logger.warning("hybrid context retrieval failed; sending no context", exc_info=True)
        matches = []

    # Filter blocked + prefer useful types, then cut to top_k.
    selected: List[Dict[str, Any]] = []
    for match in matches:
        if is_node_blocked_for_cloud(match):
            continue
        # Soft preference: keep preferred types first by stable partition later.
        selected.append(match)

    selected.sort(
        key=lambda m: (
            0 if str(m.get("type") or "") in preferred else 1,
            -float(m.get("score") or 0.0),
            str(m.get("node_id") or m.get("id") or ""),
        )
    )
    selected = selected[: max(1, min(int(top_k), 12))]

    lines: List[str] = []
    node_ids: List[str] = []
    for match in selected:
        nid = str(match.get("node_id") or match.get("id") or "")
        if not nid:
            continue
        node_ids.append(nid)
        title = str(match.get("title") or nid)
        summary = str(match.get("summary") or "")[:400]
        ntype = str(match.get("type") or "Node")
        lines.append(f"- [{ntype}] {title}: {summary}".rstrip(": "))

    # Last gate before the text can leave the machine. `redact_secret_text` was
    # already applied to logs, audit records, and API previews — everywhere
    # except the one path where bytes actually go to a third party. A token
    # pasted into a note is not marked sensitive by anyone, so pattern
    # redaction is the only thing standing between it and the provider.
    compact = redact_secret_text("\n".join(lines))
    return MinimalContext(
        query=query,
        keywords=keywords,
        node_ids=node_ids,
        compact_text=compact,
        nodes=selected,
        token_estimate=_rough_token_estimate(compact),
        quality=quality or {"mode": "none", "nodes": len(selected), "limited": len(selected) <= 1},
    )


__all__ = [
    "MinimalContext",
    "SupportsHybridSearch",
    "build_minimal_context",
]
