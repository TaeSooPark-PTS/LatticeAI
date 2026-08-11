"""The scored diagnosis, and the vector index's freshness.

:meth:`health_report` grades four dimensions — freshness, connectivity,
embedding coverage, consistency — from the live stores, and is deliberate about
what it refuses to grade. A dimension it cannot measure scores ``None`` with a
stated reason rather than a flattering number (an empty index covers 100% of
nothing; scoring that as a perfect 100 is how a brand-new Brain used to grade
itself "excellent"). The composite reports its own ``coverage``, and when
nothing could be measured it says why — the 9.9.7 rule that a "—" always states
its reason.

:meth:`vector_freshness` is a fixed four-key contract that never raises, plus
an additive ``breakdown`` when the store can split "never embedded" from
"embedded but stale".
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from latticeai.core.timeutil import now_iso as _now

from ._contract import BrainIntelligenceCore as _Core
from .constants import _STALE_DAYS, LOGGER, _no_graph_reason, _parse_ts


class BrainHealthMixin(_Core):
    """Health report + vector freshness. Mixed into the service."""

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
            stale = [n for ts, n in known if ts is not None and ts < stale_cutoff]
            fresh_ratio = 1.0 - (len(stale) / len(known)) if known else 0.0
            dimensions["freshness"] = {
                "status": "ok",
                "score": round(fresh_ratio * 100),
                "sampled": len(nodes),
                "stale_nodes": len(stale),
                "stale_threshold_days": _STALE_DAYS,
            }
        else:
            dimensions["freshness"] = {
                "status": "unavailable",
                "score": None,
                "reason": _no_graph_reason(sample["available"]),
            }

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
            dimensions["connectivity"] = {
                "status": "unavailable",
                "score": None,
                "reason": _no_graph_reason(sample["available"]),
            }

        # Embedding coverage — semantic recall only works for indexed items.
        index_status: Dict[str, Any] = {}
        if self._enable_graph and hasattr(self._kg, "index_status"):
            try:
                index_status = self._kg.index_status()
            except Exception as exc:
                LOGGER.exception("brain intelligence index status failed")
                index_status = {"error": str(exc)}
        scale = index_status.get("scale") or {}
        indexable = scale.get("source_items", index_status.get("source_items"))
        if "coverage_ratio" not in scale:
            dimensions["embedding_coverage"] = {
                "status": "unavailable",
                "score": None,
                "reason": "this knowledge store does not report vector index coverage",
            }
        elif indexable == 0:
            # An empty index covers 100% of nothing. Scoring that as a perfect
            # 100 is how a brand-new Brain used to grade itself "excellent"
            # off its only measurable dimension (audit v11.2.0, Finding 3).
            dimensions["embedding_coverage"] = {
                "status": "unavailable",
                "score": None,
                "reason": "no indexable items yet",
            }
        else:
            dimensions["embedding_coverage"] = {
                "status": "ok",
                "score": round(float(scale["coverage_ratio"]) * 100),
                "ready_items": scale.get("ready_items"),
                "pending_items": scale.get("pending_items"),
                "needs_reindex": index_status.get("status") == "needs_reindex",
            }

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
            dimensions["consistency"] = {
                "status": "unavailable",
                "score": None,
                "reason": (
                    _no_graph_reason(sample["available"])
                    if not (sample["available"] and nodes)
                    else "no relationships recorded yet"
                ),
            }

        scores = [d["score"] for d in dimensions.values() if d.get("score") is not None]
        overall = round(sum(scores) / len(scores)) if scores else None
        grade = (
            None if overall is None
            else "excellent" if overall >= 85
            else "good" if overall >= 70
            else "attention" if overall >= 50
            else "critical"
        )
        # What the verdict rests on. A composite averages only what could be
        # measured, so the count of measured dimensions is part of the answer
        # rather than a footnote — and when nothing could be measured the
        # report says so instead of leaving a bare null (the 9.9.7 rule: a
        # "—" always states why).
        unmeasured = sorted(
            name for name, dim in dimensions.items() if dim.get("score") is None
        )
        coverage: Dict[str, Any] = {
            "measured": len(scores),
            "total": len(dimensions),
            "unavailable": unmeasured,
            "partial": bool(unmeasured),
        }
        reason: Optional[str] = None
        if overall is None:
            reason = (
                "no health dimension could be measured yet — "
                + "; ".join(
                    f"{name}: {dimensions[name].get('reason') or 'unavailable'}"
                    for name in unmeasured
                )
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

        report: Dict[str, Any] = {
            "overall_score": overall,
            "grade": grade,
            "dimensions": dimensions,
            "coverage": coverage,
            "recommended_actions": actions,
            "graph_available": sample["available"],
            "generated_at": _now(),
        }
        if reason is not None:
            report["reason"] = reason
        return report

    # ── vector freshness (v9.8.0) ────────────────────────────────────────

    def vector_freshness(
        self, *, user_email: Optional[str] = None, workspace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Fixed-contract vector index freshness for ``/api/brain/vector-freshness``.

        Always returns ``{"status": "ready"|"pending"|"unavailable",
        "pending_items": int, "total_items": int, "detail": str}`` and never
        raises. The vector index is store-global (not workspace-partitioned);
        scope arguments are accepted for router symmetry but do not narrow
        the report.

        Since v11.2.0 a store that can split its backlog also gets a
        ``breakdown`` key (see :meth:`_freshness_breakdown`). It is additive:
        the four keys above keep their meaning and their types, so the
        freshness chip that reads ``pending_items`` is untouched, and a store
        without the split simply has no ``breakdown``.
        """
        payload = self._vector_freshness_contract()
        breakdown = self._freshness_breakdown()
        if breakdown is not None:
            payload["breakdown"] = breakdown
        return payload

    def _freshness_breakdown(self) -> Optional[Dict[str, Any]]:
        """``vector_freshness_breakdown()`` from the store, or ``None``.

        "12 pending" hides two different situations — twelve items never
        embedded (a new import) and twelve whose text changed under an
        existing embedding (edits, where current answers are quietly wrong).
        The store has always known the difference; until now nothing asked it.

        ``None`` covers every reason the split is not available (graph off,
        an older store, an unreadable index), because a caller can only act on
        numbers that were really measured — never on zeros standing in for
        them.
        """
        if not self._enable_graph or self._kg is None:
            return None
        breakdown_fn = getattr(self._kg, "vector_freshness_breakdown", None)
        if not callable(breakdown_fn):
            return None
        try:
            raw = breakdown_fn()
        except Exception:
            LOGGER.exception("vector freshness breakdown read failed")
            return None
        # An empty or unrecognisable answer is "not measured", which is what
        # ``None`` already means here — publishing an empty block would claim
        # a split nobody computed.
        if not isinstance(raw, dict) or not raw:
            return None
        return dict(raw)

    def _vector_freshness_contract(self) -> Dict[str, Any]:
        """The four-key freshness payload, unchanged since v9.8.0."""

        def _unavailable(detail: str) -> Dict[str, Any]:
            return {
                "status": "unavailable",
                "pending_items": 0,
                "total_items": 0,
                "detail": detail,
            }

        if not self._enable_graph or self._kg is None:
            return _unavailable("knowledge graph is disabled; no vector index is configured")

        freshness_fn = getattr(self._kg, "vector_freshness", None)
        if callable(freshness_fn):
            try:
                raw = freshness_fn() or {}
            except Exception as exc:
                LOGGER.exception("vector freshness read failed")
                return _unavailable(f"vector freshness read failed: {exc}")
            status = str(raw.get("status") or "unavailable")
            if status == "needs_reindex":
                status = "pending"
            if status not in {"ready", "pending", "unavailable"}:
                status = "unavailable"
            return {
                "status": status,
                "pending_items": int(raw.get("pending_items") or 0),
                "total_items": int(raw.get("total_items") or 0),
                "detail": str(raw.get("detail") or ""),
            }

        # Older/lighter stores: summarize index_status directly.
        status_fn = getattr(self._kg, "index_status", None)
        if callable(status_fn):
            try:
                raw = status_fn() or {}
            except Exception as exc:
                LOGGER.exception("vector index status read failed")
                return _unavailable(f"vector index status unavailable: {exc}")
            pending = int(raw.get("pending_items") or 0)
            total = int(raw.get("source_items") or 0)
            if pending > 0:
                return {
                    "status": "pending",
                    "pending_items": pending,
                    "total_items": total,
                    "detail": (
                        f"{pending} of {total} items are missing or stale in the vector index"
                    ),
                }
            return {
                "status": "ready",
                "pending_items": 0,
                "total_items": total,
                "detail": "vector index is up to date",
            }

        return _unavailable("this knowledge store does not expose a vector index")
