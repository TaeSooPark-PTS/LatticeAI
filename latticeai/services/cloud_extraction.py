"""Richer candidate extraction from cloud answers (Phase 2).

Deterministic, dependency-free heuristics that turn a cloud answer into
candidate Concept / Decision / Task nodes. These are always staged with
provenance and never auto-committed in Phase 2.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from latticeai.services.cloud_streaming import (
    CloudTurnResult,
    KGExpansionPlan,
    plan_kg_expansion,
)

_DECISION_PATTERNS = (
    re.compile(r"(?im)^(?:decision|결정)\s*[:：-]\s*(.+)$"),
    re.compile(r"(?im)we (?:decided|agreed|chose) to (.+?)(?:\.|$)"),
    re.compile(r"(?im)(?:결정(?:했|하)|합의)(?:습니다|다)?\s*[:：]?\s*(.+)"),
)
_TASK_PATTERNS = (
    re.compile(r"(?im)^(?:todo|task|할\s*일|다음)\s*[:：-]\s*(.+)$"),
    re.compile(r"(?im)^[-*]\s+\[(?: |x|X)\]\s*(.+)$"),
    re.compile(r"(?im)^\d+[.)]\s+(.+)$"),
)
_CONCEPT_PATTERNS = (
    re.compile(r"(?im)^(?:concept|개념|용어)\s*[:：-]\s*(.+)$"),
    re.compile(r"\*\*([^*]{2,80})\*\*"),
)


def _clean(text: str, limit: int = 200) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())[:limit]


def extract_candidates(answer: str, *, limit: int = 8) -> List[Dict[str, Any]]:
    """Return lightweight candidate nodes derived from the answer text."""
    text = str(answer or "")
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def add(node_type: str, title: str, summary: str = "") -> None:
        title = _clean(title, 120)
        if not title:
            return
        key = f"{node_type}:{title.lower()}"
        if key in seen:
            return
        seen.add(key)
        out.append(
            {
                "type": node_type,
                "title": title,
                "summary": _clean(summary or title, 400),
                "metadata": {
                    "derived_from_cloud": True,
                    "extraction": "heuristic_v1",
                    "confidence": 0.55,
                },
            }
        )

    for pattern in _DECISION_PATTERNS:
        for match in pattern.finditer(text):
            add("Decision", match.group(1))
            if len(out) >= limit:
                return out

    for pattern in _TASK_PATTERNS:
        for match in pattern.finditer(text):
            add("Task", match.group(1))
            if len(out) >= limit:
                return out

    for pattern in _CONCEPT_PATTERNS:
        for match in pattern.finditer(text):
            add("Concept", match.group(1))
            if len(out) >= limit:
                return out

    return out


def plan_kg_expansion_rich(result: CloudTurnResult) -> KGExpansionPlan:
    """Base conversation plan + heuristic Concept/Decision/Task candidates."""
    plan = plan_kg_expansion(result)
    candidates = extract_candidates(result.answer_text)
    turn_id = plan.new_nodes[0]["id"] if plan.new_nodes else "cloud_turn:unknown"

    for idx, cand in enumerate(candidates):
        node_id = f"{turn_id}:cand:{idx}"
        node = {
            "id": node_id,
            **cand,
        }
        plan.new_nodes.append(node)
        plan.new_edges.append(
            {
                "from": turn_id,
                "to": node_id,
                "type": "implies",
                "weight": 0.6,
                "metadata": {"provenance": "cloud_extraction"},
            }
        )
        for src in result.sent_node_ids:
            plan.new_edges.append(
                {
                    "from": node_id,
                    "to": src,
                    "type": "grounded_on",
                    "weight": 0.5,
                    "metadata": {"provenance": "cloud_extraction"},
                }
            )

    plan.provenance = {
        **plan.provenance,
        "candidate_count": len(candidates),
        "extraction": "heuristic_v1",
    }
    plan.auto_commit = False
    return plan


__all__ = ["extract_candidates", "plan_kg_expansion_rich"]
