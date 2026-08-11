"""What the Brain disagrees with itself about, and what it says twice.

Two scans that wire the previously dormant quality layer
(:mod:`lattice_brain.quality`) to real stores:

* :meth:`contradictions` — four independent signals in one list: negation and
  preference conflicts between workspace memories, temporal contradictions,
  explicit ``CONTRADICTS`` edges already recorded in the graph, and the graph
  layer's own node-content detector. Each item carries the signal that found
  it, so a reviewer can weigh them differently.
* :meth:`consolidate` — duplicate memories and duplicate edges. Dry-run by
  default, consent-first like every Brain automation. ``apply=True`` prunes
  only exact duplicate *workspace memories*, through the audited MemoryService
  path; graph content is never mutated here — the node merge plan is surfaced
  for a governed apply path to adopt.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from latticeai.core.timeutil import now_iso as _now

from ._contract import BrainIntelligenceCore as _Core
from .constants import LOGGER


class BrainConsistencyMixin(_Core):
    """Contradiction and consolidation scans. Mixed into the service."""

    # ── contradictions ───────────────────────────────────────────────────

    def contradictions(
        self, *, user_email: Optional[str] = None, workspace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        memories = self._workspace_memories(user_email=user_email, workspace_id=workspace_id)
        memory_rows = [
            {
                "id": str(m.get("id") or f"mem-{i}"),
                "content": str(m.get("content") or ""),
                "score": 0.6,
                "source": "workspace",
                "timestamp": m.get("created_at") or m.get("timestamp") or 0,
            }
            for i, m in enumerate(memories)
            if str(m.get("content") or "").strip()
        ]

        conflicts: List[Dict[str, Any]] = []
        candidates = self._memory_quality.extract_candidates(memory_rows)
        for candidate in self._memory_quality.detect_conflicts(candidates):
            pair_conflicts = [c for c in candidate.conflicts if c.startswith("conflict:contradicts:")]
            for marker in pair_conflicts:
                other_id = marker.rsplit(":", 1)[-1]
                if any(
                    c["kind"] == "memory_pair"
                    and {c["left_id"], c["right_id"]} == {candidate.id, other_id}
                    for c in conflicts
                ):
                    continue
                other: Dict[str, Any] | None = next(
                    (r for r in memory_rows if r["id"] == other_id), None
                )
                conflicts.append({
                    "kind": "memory_pair",
                    "left_id": candidate.id,
                    "left_content": candidate.content[:200],
                    "right_id": other_id,
                    "right_content": (other or {}).get("content", "")[:200],
                    "signal": "preference_negation",
                })

        temporal = self._memory_quality.detect_temporal_contradictions(memory_rows)
        temporal_items = [
            {
                "kind": "temporal",
                "id": item.get("id"),
                "content": str(item.get("content") or "")[:200],
                "signal": item.get("proactive_flag"),
            }
            for item in temporal
        ]

        edge_items: List[Dict[str, Any]] = []
        sample = self._graph_sample(workspace_id=workspace_id)
        for edge in sample["edges"]:
            if "CONTRADICT" in str(edge.get("type") or "").upper():
                edge_items.append({
                    "kind": "graph_edge",
                    "id": edge.get("id"),
                    "source": edge.get("source") or edge.get("from_node"),
                    "target": edge.get("target") or edge.get("to_node"),
                    "signal": "contradicts_edge",
                })

        # v9.6.x additive: graph-layer node-content contradictions (proactive
        # detector over node title/summary), on top of the memory + edge scans.
        graph_pair_items: List[Dict[str, Any]] = []
        proactive = self._proactive()
        if proactive is not None:
            try:
                graph_result = proactive.detect_contradictions(workspace_id=workspace_id)
                graph_pair_items = [
                    {"kind": "graph_node_pair", **pair}
                    for pair in graph_result.get("node_pairs") or []
                ]
            except Exception:
                LOGGER.exception("graph contradiction scan failed")

        items = conflicts + temporal_items + edge_items + graph_pair_items
        return {
            "items": items,
            "count": len(items),
            "sources": {
                "memory_pairs": len(conflicts),
                "temporal": len(temporal_items),
                "graph_edges": len(edge_items),
                "graph_node_pairs": len(graph_pair_items),
            },
            "memories_scanned": len(memory_rows),
            "generated_at": _now(),
        }

    # ── consolidation ────────────────────────────────────────────────────

    def consolidate(
        self,
        *,
        apply: bool = False,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        memories = self._workspace_memories(user_email=user_email, workspace_id=workspace_id)
        memory_rows = [
            {
                "id": str(m.get("id") or f"mem-{i}"),
                "content": str(m.get("content") or ""),
                "score": 0.6,
                "source": "workspace",
            }
            for i, m in enumerate(memories)
            if str(m.get("content") or "").strip()
        ]
        candidates = self._memory_quality.extract_candidates(memory_rows)
        kept = self._memory_quality.dedupe(candidates)
        kept_ids = {c.id for c in kept}
        duplicate_memory_ids = [c.id for c in candidates if c.id not in kept_ids]

        sample = self._graph_sample(workspace_id=workspace_id)
        duplicate_edge_ids = [
            edge_id
            for edge_id in self._edge_quality.detect_duplicate_edges(sample["edges"])
            if edge_id
        ]

        pruned = 0
        if apply and duplicate_memory_ids and self._memory is not None:
            try:
                result = self._memory.prune(
                    ids=duplicate_memory_ids,
                    user_email=user_email,
                    workspace_id=workspace_id,
                )
                pruned = int(result.get("count") or 0)
            except Exception:
                LOGGER.exception("consolidation prune failed")

        # v9.6.x additive: graph-layer node merge plan. Always dry-run from
        # this service — graph content changes stay proposal-first; the plan
        # is surfaced so a governed apply path can adopt it later.
        graph_consolidation: Optional[Dict[str, Any]] = None
        proactive = self._proactive()
        if proactive is not None:
            try:
                graph_consolidation = proactive.consolidate_duplicates(
                    workspace_id=workspace_id, dry_run=True
                )
            except Exception:
                LOGGER.exception("graph consolidation plan failed")

        return {
            "mode": "applied" if apply else "dry_run",
            "memories_scanned": len(memory_rows),
            "duplicate_memories": duplicate_memory_ids,
            "duplicate_memory_count": len(duplicate_memory_ids),
            "pruned": pruned,
            # Graph edges are reported for review only; consolidation never
            # mutates graph content directly.
            "duplicate_edges": duplicate_edge_ids[:50],
            "duplicate_edge_count": len(duplicate_edge_ids),
            "graph_consolidation": graph_consolidation,
            "generated_at": _now(),
        }
