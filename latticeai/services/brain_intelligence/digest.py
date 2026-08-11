"""What the Brain would tell you if you asked "what's going on in there?"

Three read-only views over the same graph slice, each answering a different
shape of that question:

* :meth:`insights` — the proactive digest: recent growth, trending types, stale
  knowledge, orphans, and questions grounded in real node titles;
* :meth:`garden_overview` — the same knowledge as four beds a gardener tends
  (new growth, weeds, neglect, load-bearing), because "how healthy is my
  knowledge?" in aggregate is not a question anyone acts on;
* :meth:`graph_duplicates` / :meth:`quality_report` — the graph layer's own
  proactive scans, surfaced without applying anything.

Honest when empty: an unavailable graph produces empty beds and
``available: False``, never invented plants.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from latticeai.core.timeutil import now_iso as _now

from ._contract import BrainIntelligenceCore as _Core
from .constants import _RECENT_DAYS, _STALE_DAYS, LOGGER, _parse_ts


class BrainDigestMixin(_Core):
    """Insights, garden, and the graph-layer quality reads."""

    # ── insights digest ──────────────────────────────────────────────────

    def insights(
        self, *, user_email: Optional[str] = None, workspace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        sample = self._graph_sample(workspace_id=workspace_id)
        nodes, edges = sample["nodes"], sample["edges"]
        now = datetime.now(timezone.utc)
        recent_cutoff = now - timedelta(days=_RECENT_DAYS)
        stale_cutoff = now - timedelta(days=_STALE_DAYS)

        recent_nodes: List[Dict[str, Any]] = []
        stale_nodes: List[Dict[str, Any]] = []
        type_counts: Dict[str, int] = {}
        for node in nodes:
            node_type = str(node.get("type") or "node")
            ts = _parse_ts(node.get("updated_at"))
            if ts is not None and ts >= recent_cutoff:
                recent_nodes.append(node)
                type_counts[node_type] = type_counts.get(node_type, 0) + 1
            elif ts is not None and ts < stale_cutoff:
                stale_nodes.append(node)

        connected = set()
        for edge in edges:
            connected.add(str(edge.get("source") or edge.get("from_node") or ""))
            connected.add(str(edge.get("target") or edge.get("to_node") or ""))
        orphans = [n for n in nodes if str(n.get("id")) not in connected]

        def _slim(node: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "id": node.get("id"),
                "type": node.get("type"),
                "title": str(node.get("title") or "")[:120],
                "updated_at": node.get("updated_at"),
            }

        trending = sorted(type_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
        suggested_questions = [
            f"{str(node.get('title') or '').strip()[:60]}에 대해 지금까지 알고 있는 것을 정리해줘"
            for node in recent_nodes[:3]
            if str(node.get("title") or "").strip()
        ]

        return {
            "window_days": _RECENT_DAYS,
            "activity": {
                "recent_nodes": len(recent_nodes),
                "recent_samples": [_slim(n) for n in recent_nodes[:8]],
                "trending_types": [{"type": t, "count": c} for t, c in trending],
            },
            "attention": {
                "stale_nodes": len(stale_nodes),
                "stale_samples": [_slim(n) for n in stale_nodes[:8]],
                "orphan_nodes": len(orphans),
                "orphan_samples": [_slim(n) for n in orphans[:8]],
            },
            "suggested_questions": suggested_questions,
            "graph_available": sample["available"],
            "generated_at": _now(),
        }

    def garden_overview(
        self, *, user_email: Optional[str] = None, workspace_id: Optional[str] = None,
        limit: int = 8,
    ) -> Dict[str, Any]:
        """The knowledge garden in four beds (v9.9.7).

        Living Brain answers "how healthy is my knowledge?" in aggregate.
        A gardener asks four concrete questions instead, and this answers all
        four from one workspace-scoped graph sample plus the memory tier:

        * **recent** — what came in lately (the garden's new growth);
        * **contradictions** — what disagrees with itself (needs weeding);
        * **stale** — what has not been touched in a long time;
        * **frequent** — what the rest of the graph leans on most (by degree).

        Read-only and honest: when the graph is unavailable every bed is empty
        and ``available`` is false — the view never invents plants.
        """
        # Explicit 0 clamps to 1 — `limit or 8` would silently re-expand it.
        try:
            limit = max(1, min(int(limit), 50))
        except (TypeError, ValueError):
            limit = 8
        sample = self._graph_sample(workspace_id=workspace_id)
        nodes, edges = sample["nodes"], sample["edges"]
        now = datetime.now(timezone.utc)
        recent_cutoff = now - timedelta(days=_RECENT_DAYS)
        stale_cutoff = now - timedelta(days=_STALE_DAYS)

        def _slim(node: Dict[str, Any], **extra: Any) -> Dict[str, Any]:
            return {
                "id": node.get("id"),
                "type": node.get("type"),
                "title": str(node.get("title") or "")[:120],
                "updated_at": node.get("updated_at"),
                **extra,
            }

        recent: List[Dict[str, Any]] = []
        stale: List[Dict[str, Any]] = []
        for node in nodes:
            # Chunks are retrieval plumbing, not knowledge a gardener tends.
            if str(node.get("type") or "") == "Chunk":
                continue
            ts = _parse_ts(node.get("updated_at"))
            if ts is None:
                continue
            if ts >= recent_cutoff:
                recent.append(node)
            elif ts < stale_cutoff:
                stale.append(node)
        recent.sort(key=lambda n: str(n.get("updated_at") or ""), reverse=True)
        stale.sort(key=lambda n: str(n.get("updated_at") or ""))

        # "Frequent" is degree, not a guess: how many relations actually point
        # at a node. Chunks are retrieval plumbing, never garden plants.
        degree: Dict[str, int] = {}
        for edge in edges:
            for key in ("source", "target"):
                node_id = str(edge.get(key) or "")
                if node_id:
                    degree[node_id] = degree.get(node_id, 0) + 1
        by_id = {str(node.get("id")): node for node in nodes}
        frequent = [
            _slim(by_id[node_id], degree=count)
            for node_id, count in sorted(degree.items(), key=lambda kv: kv[1], reverse=True)
            if node_id in by_id and str(by_id[node_id].get("type") or "") != "Chunk"
        ][:limit]

        contradiction_items: List[Dict[str, Any]] = []
        contradiction_count = 0
        try:
            found = self.contradictions(user_email=user_email, workspace_id=workspace_id)
            items = found.get("items") if isinstance(found, dict) else None
            if isinstance(items, list):
                contradiction_count = len(items)
                contradiction_items = items[:limit]
        except Exception:  # noqa: BLE001 — one empty bed, never a broken view
            LOGGER.exception("garden overview contradictions failed")

        return {
            "available": sample["available"],
            "window_days": _RECENT_DAYS,
            "stale_threshold_days": _STALE_DAYS,
            "beds": {
                "recent": {
                    "count": len(recent),
                    "items": [_slim(node) for node in recent[:limit]],
                },
                "contradictions": {
                    "count": contradiction_count,
                    "items": contradiction_items,
                },
                "stale": {
                    "count": len(stale),
                    "items": [_slim(node) for node in stale[:limit]],
                },
                "frequent": {
                    "count": len(frequent),
                    "items": frequent,
                },
            },
            "generated_at": _now(),
        }

    # ── graph-layer proactive quality (v9.6.x) ───────────────────────────

    def graph_duplicates(
        self, *, user_email: Optional[str] = None, workspace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Duplicate graph nodes (exact groups + near pairs) — read only."""
        proactive = self._proactive()
        if proactive is None:
            return {
                "available": False,
                "exact_groups": [],
                "near_pairs": [],
                "exact_duplicate_nodes": 0,
                "nodes_scanned": 0,
                "generated_at": _now(),
            }
        try:
            result = dict(proactive.find_duplicates(workspace_id=workspace_id))
        except Exception as exc:
            LOGGER.exception("graph duplicates scan failed")
            return {
                "available": False,
                "error": str(exc),
                "exact_groups": [],
                "near_pairs": [],
                "exact_duplicate_nodes": 0,
                "nodes_scanned": 0,
                "generated_at": _now(),
            }
        result["available"] = True
        result["generated_at"] = _now()
        return result

    def quality_report(
        self, *, user_email: Optional[str] = None, workspace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Combined graph quality report: duplicates, contradictions, stale
        nodes, edge quality — one workspace-scoped graph sample."""
        proactive = self._proactive()
        if proactive is None:
            return {"available": False, "generated_at": _now()}
        try:
            result = dict(proactive.quality_report(workspace_id=workspace_id))
        except Exception as exc:
            LOGGER.exception("graph quality report failed")
            return {"available": False, "error": str(exc), "generated_at": _now()}
        result["available"] = True
        # v11.1.0: decay is part of quality, and "the Brain is tidying up" is a
        # state the user is entitled to see rather than a background surprise.
        importance = self.importance_report(
            user_email=user_email, workspace_id=workspace_id
        )
        candidates = len(importance.get("candidates") or [])
        result["importance"] = importance
        result["tidying"] = bool(importance.get("available")) and candidates > 0
        summary = dict(result.get("summary") or {})
        summary["consolidation_candidates"] = candidates
        result["summary"] = summary
        result["generated_at"] = _now()
        return result
