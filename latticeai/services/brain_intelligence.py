"""Proactive Brain Intelligence service (v9.3.0).

The Brain graduates from a passive store to an active steward of its own
knowledge. This service wires the previously dormant quality layer
(:mod:`lattice_brain.quality` — dedupe, merge, conflict and temporal
contradiction detection, retention) into router-facing capabilities:

* **health_report** — scored diagnosis of the Brain across freshness,
  connectivity, embedding coverage, and contradiction pressure, with
  recommended next actions. Every number is read from the live stores;
  a missing store degrades the dimension to ``unavailable``, never a guess.
* **insights** — a proactive digest: recent knowledge growth, most active
  types, stale knowledge, orphan (disconnected) nodes, and suggested
  questions grounded in real node titles.
* **contradictions** — surfaced conflicts across workspace memories
  (negation/preference conflicts, temporal contradictions) plus explicit
  CONTRADICTS edges already recorded in the graph.
* **consolidate** — duplicate-memory and duplicate-edge detection. Dry-run
  by default (consent-first, like every Brain automation); ``apply=True``
  prunes only exact duplicate workspace memories through the audited
  MemoryService path and never touches graph content.

Pure service: no FastAPI, no globals. Collaborators are injected.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from lattice_brain.quality import GraphEdgeQualityManager, MemoryQualityManager
from latticeai.core.timeutil import now_iso as _now

LOGGER = logging.getLogger(__name__)

_STALE_DAYS = 45
_RECENT_DAYS = 7
_GRAPH_SAMPLE_LIMIT = 800


def _parse_ts(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class BrainIntelligenceService:
    def __init__(
        self,
        *,
        knowledge_graph: Any = None,
        memory_service: Any = None,
        enable_graph: bool = True,
    ) -> None:
        self._kg = knowledge_graph
        self._memory = memory_service
        self._enable_graph = bool(enable_graph and knowledge_graph is not None)
        self._memory_quality = MemoryQualityManager()
        self._edge_quality = GraphEdgeQualityManager()
        self._proactive_brain: Any = None

    def _proactive(self) -> Any:
        """Lazy graph-layer ProactiveBrain over the injected store (or None)."""
        if not self._enable_graph:
            return None
        if self._proactive_brain is None:
            try:
                from lattice_brain.graph.proactive import ProactiveBrain

                self._proactive_brain = ProactiveBrain(
                    self._kg, sample_limit=_GRAPH_SAMPLE_LIMIT
                )
            except Exception:
                LOGGER.exception("proactive brain initialization failed")
                return None
        return self._proactive_brain

    # ── shared graph sampling ─────────────────────────────────────────────

    def _graph_sample(self, *, workspace_id: Optional[str]) -> Dict[str, Any]:
        """Recent graph slice with scoping applied. Empty when graph is off."""
        if not self._enable_graph:
            return {"nodes": [], "edges": [], "available": False}
        try:
            kwargs: Dict[str, Any] = {}
            if workspace_id is not None:
                kwargs["allowed_workspaces"] = {workspace_id}
            data = self._kg.graph(_GRAPH_SAMPLE_LIMIT, **kwargs)
            edges = []
            for edge in data.get("edges") or []:
                # Store emits "from"/"to"; the quality layer expects
                # "source"/"target". Normalize once here.
                normalized = dict(edge)
                normalized.setdefault("source", edge.get("from"))
                normalized.setdefault("target", edge.get("to"))
                edges.append(normalized)
            return {
                "nodes": list(data.get("nodes") or []),
                "edges": edges,
                "available": True,
            }
        except Exception as exc:
            LOGGER.exception("brain intelligence graph sample failed")
            return {"nodes": [], "edges": [], "available": False, "error": str(exc)}

    def _workspace_memories(
        self, *, user_email: Optional[str], workspace_id: Optional[str]
    ) -> List[Dict[str, Any]]:
        if self._memory is None:
            return []
        try:
            inspected = self._memory.inspect(
                "workspace",
                user_email=user_email,
                workspace_id=workspace_id or "personal",
                limit=500,
            )
            return list(inspected.get("items") or [])
        except Exception:
            LOGGER.exception("brain intelligence memory read failed")
            return []

    # ── health report ─────────────────────────────────────────────────────

    def health_report(
        self, *, user_email: Optional[str] = None, workspace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        sample = self._graph_sample(workspace_id=workspace_id)
        nodes, edges = sample["nodes"], sample["edges"]
        now = datetime.now(timezone.utc)
        dimensions: Dict[str, Dict[str, Any]] = {}

        # Freshness — how much of the sampled knowledge saw recent updates.
        if sample["available"] and nodes:
            stale_cutoff = now - timedelta(days=_STALE_DAYS)
            dated = [(_parse_ts(n.get("updated_at")), n) for n in nodes]
            known = [pair for pair in dated if pair[0] is not None]
            stale = [n for ts, n in known if ts < stale_cutoff]
            fresh_ratio = 1.0 - (len(stale) / len(known)) if known else 0.0
            dimensions["freshness"] = {
                "status": "ok",
                "score": round(fresh_ratio * 100),
                "sampled": len(nodes),
                "stale_nodes": len(stale),
                "stale_threshold_days": _STALE_DAYS,
            }
        else:
            dimensions["freshness"] = {"status": "unavailable", "score": None}

        # Connectivity — orphan nodes are knowledge the Brain cannot reason
        # across; a well-tended graph keeps them rare.
        if sample["available"] and nodes:
            connected = set()
            for edge in edges:
                connected.add(str(edge.get("source") or edge.get("from_node") or ""))
                connected.add(str(edge.get("target") or edge.get("to_node") or ""))
            orphans = [n for n in nodes if str(n.get("id")) not in connected]
            ratio = 1.0 - (len(orphans) / len(nodes))
            dimensions["connectivity"] = {
                "status": "ok",
                "score": round(ratio * 100),
                "sampled": len(nodes),
                "orphan_nodes": len(orphans),
                "edges": len(edges),
            }
        else:
            dimensions["connectivity"] = {"status": "unavailable", "score": None}

        # Embedding coverage — semantic recall only works for indexed items.
        index_status: Dict[str, Any] = {}
        if self._enable_graph and hasattr(self._kg, "index_status"):
            try:
                index_status = self._kg.index_status()
            except Exception as exc:
                LOGGER.exception("brain intelligence index status failed")
                index_status = {"error": str(exc)}
        scale = index_status.get("scale") or {}
        if "coverage_ratio" in scale:
            dimensions["embedding_coverage"] = {
                "status": "ok",
                "score": round(float(scale["coverage_ratio"]) * 100),
                "ready_items": scale.get("ready_items"),
                "pending_items": scale.get("pending_items"),
                "needs_reindex": index_status.get("status") == "needs_reindex",
            }
        else:
            dimensions["embedding_coverage"] = {"status": "unavailable", "score": None}

        # Edge quality + contradiction pressure — reuses the quality layer.
        if sample["available"] and edges:
            metrics = self._edge_quality.compute_quality_metrics(edges)
            contradiction_edges = [
                e for e in edges if "CONTRADICT" in str(e.get("type") or "").upper()
            ]
            pressure = min(1.0, metrics.get("dup_rate", 0.0) + len(contradiction_edges) / max(len(edges), 1))
            dimensions["consistency"] = {
                "status": "ok",
                "score": round((1.0 - pressure) * 100),
                "edge_metrics": metrics,
                "contradiction_edges": len(contradiction_edges),
            }
        else:
            dimensions["consistency"] = {"status": "unavailable", "score": None}

        scores = [d["score"] for d in dimensions.values() if d.get("score") is not None]
        overall = round(sum(scores) / len(scores)) if scores else None
        grade = (
            None if overall is None
            else "excellent" if overall >= 85
            else "good" if overall >= 70
            else "attention" if overall >= 50
            else "critical"
        )

        actions: List[Dict[str, str]] = []
        emb = dimensions["embedding_coverage"]
        if emb.get("needs_reindex"):
            actions.append({
                "id": "rebuild_vector_index",
                "reason": f"{emb.get('pending_items', 0)} items are missing or stale in the vector index.",
            })
        conn_dim = dimensions["connectivity"]
        conn_score = conn_dim.get("score")
        if conn_score is not None and conn_score < 70:
            actions.append({
                "id": "review_orphans",
                "reason": f"{conn_dim.get('orphan_nodes', 0)} nodes have no relationships.",
            })
        fresh_dim = dimensions["freshness"]
        fresh_score = fresh_dim.get("score")
        if fresh_score is not None and fresh_score < 60:
            actions.append({
                "id": "refresh_stale_knowledge",
                "reason": f"{fresh_dim.get('stale_nodes', 0)} nodes untouched for over {_STALE_DAYS} days.",
            })
        cons_dim = dimensions["consistency"]
        if cons_dim.get("contradiction_edges"):
            actions.append({
                "id": "resolve_contradictions",
                "reason": f"{cons_dim['contradiction_edges']} contradiction edges recorded in the graph.",
            })

        return {
            "overall_score": overall,
            "grade": grade,
            "dimensions": dimensions,
            "recommended_actions": actions,
            "graph_available": sample["available"],
            "generated_at": _now(),
        }

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
        result["generated_at"] = _now()
        return result

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
                other = next((r for r in memory_rows if r["id"] == other_id), None)
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


__all__ = ["BrainIntelligenceService"]
