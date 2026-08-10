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


def _no_graph_reason(graph_available: bool) -> str:
    """Why a graph-derived health dimension has nothing to say.

    Two different situations that both end in ``status: "unavailable"``: the
    graph could not be read at all, and the graph read fine but holds nothing
    yet. Telling them apart is the difference between "something is broken"
    and "you have not saved anything yet".
    """
    return (
        "no knowledge saved yet"
        if graph_available
        else "the knowledge graph could not be read"
    )


class BrainIntelligenceService:
    def __init__(
        self,
        *,
        knowledge_graph: Any = None,
        memory_service: Any = None,
        enable_graph: bool = True,
        review_queue: Any = None,
    ) -> None:
        self._kg = knowledge_graph
        self._memory = memory_service
        self._enable_graph = bool(enable_graph and knowledge_graph is not None)
        self._memory_quality = MemoryQualityManager()
        self._edge_quality = GraphEdgeQualityManager()
        self._proactive_brain: Any = None
        self._review_queue_service = review_queue
        self._synthesizer: Any = None

    # ── proposal path (v11.1.0) ───────────────────────────────────────────
    #
    # Synthesis never writes: it proposes, and the Review Center decides. That
    # makes the review queue a hard dependency of every method below — when it
    # is absent they report ``available: False`` rather than falling back to a
    # direct write.

    def attach_review_queue(self, review_queue: Any) -> None:
        """Bind the review queue proposals are written to (composition root)."""
        self._review_queue_service = review_queue
        self._synthesizer = None

    def _review_queue(self) -> Any:
        """The queue to propose into, or ``None`` when there is none.

        Prefers an explicitly injected service. Otherwise it builds one over
        the workspace store the memory service already holds — the same
        ``WorkspaceOSStore`` every other ``ReviewQueueService`` in the process
        is constructed over, so proposals land in the one inbox the user reads
        even before the composition root injects the service directly.
        """
        if self._review_queue_service is not None:
            return self._review_queue_service
        store = getattr(self._memory, "_store", None)
        if store is None or not hasattr(store, "create_review_item"):
            return None
        from latticeai.services.review_queue import ReviewQueueService

        self._review_queue_service = ReviewQueueService(store=store)
        return self._review_queue_service

    def _synthesis(self) -> Any:
        """Lazy :class:`BrainSynthesizer` over the graph + review queue."""
        if self._synthesizer is not None:
            return self._synthesizer
        queue = self._review_queue()
        if not self._enable_graph or queue is None:
            return None
        from lattice_brain.synthesis import BrainSynthesizer

        self._synthesizer = BrainSynthesizer(self._kg, queue)
        return self._synthesizer

    @staticmethod
    def _no_queue(detail: str) -> Dict[str, Any]:
        return {"available": False, "detail": detail, "generated_at": _now()}

    def synthesize(
        self, *, user_email: Optional[str] = None, workspace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Run one synthesis pass; every finding becomes a review proposal."""
        synthesizer = self._synthesis()
        if synthesizer is None:
            return self._no_queue(
                "synthesis needs both the knowledge graph and the review queue"
            )
        try:
            result = dict(
                synthesizer.run(workspace_id=workspace_id, user_email=user_email)
            )
        except Exception as exc:  # noqa: BLE001 — a failed pass is reported, not raised
            LOGGER.exception("brain synthesis failed")
            return {"available": False, "error": str(exc), "generated_at": _now()}
        result["available"] = True
        return result

    def note_ingest(
        self,
        result: Any,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Ingest-driven trigger seam.

        Hand every ingest result here; synthesis runs only once the configured
        number of genuinely new nodes has accumulated. Returns the run result
        when one fired, ``None`` otherwise — so an ingest path can call this
        unconditionally and cheaply.
        """
        synthesizer = self._synthesis()
        if synthesizer is None:
            return None
        try:
            return synthesizer.run_if_due(
                result, workspace_id=workspace_id, user_email=user_email
            )
        except Exception:  # noqa: BLE001 — synthesis must never break an ingest
            LOGGER.exception("ingest-triggered synthesis failed")
            return None

    def propose_contradictions(
        self, *, user_email: Optional[str] = None, workspace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Raise a review proposal for every contradicting pair of memories."""
        queue = self._review_queue()
        if not self._enable_graph or queue is None:
            return self._no_queue(
                "contradiction proposals need both the knowledge graph and the review queue"
            )
        from lattice_brain.synthesis import propose_contradictions

        try:
            return propose_contradictions(
                self._kg, queue, workspace_id=workspace_id, user_email=user_email
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("contradiction proposal pass failed")
            return {"available": False, "error": str(exc), "generated_at": _now()}

    def resolve_contradiction(
        self,
        item_id: str,
        *,
        resolution: str,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Approve a contradiction proposal and apply its temporal stamps.

        Raises ``ContradictionResolutionError`` (a ``ValueError``) for an
        unknown resolution or a review item that is not a contradiction — the
        router turns those into 400s.
        """
        queue = self._review_queue()
        if not self._enable_graph or queue is None:
            return self._no_queue(
                "resolving a contradiction needs both the knowledge graph and the review queue"
            )
        from lattice_brain.synthesis import resolve_contradiction

        result = dict(
            resolve_contradiction(
                self._kg,
                queue,
                item_id,
                resolution=resolution,
                workspace_id=workspace_id,
            )
        )
        result["available"] = True
        return result

    def importance_report(
        self, *, user_email: Optional[str] = None, workspace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Which memories are load-bearing, and which have decayed into noise."""
        proactive = self._proactive()
        if proactive is None:
            return {"available": False, "candidates": [], "generated_at": _now()}
        try:
            report = dict(proactive.importance_report(workspace_id=workspace_id))
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("importance report failed")
            return {
                "available": False,
                "error": str(exc),
                "candidates": [],
                "generated_at": _now(),
            }
        report["available"] = True
        return report

    def proactive_brief(
        self, *, user_email: Optional[str] = None, workspace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """The Brain Brief's proactive section: what the Brain noticed on its own.

        Read-only. It counts the proposals already waiting in the Review Center
        rather than raising new ones, so opening the home screen never mutates
        anything — and reports honestly when there is no queue to read.
        """
        section: Dict[str, Any] = {
            "available": False,
            "pending": {"total": 0, "by_kind": {}},
            "tidying": False,
            "headline": "",
            "lines": [],
            "generated_at": _now(),
        }
        queue = self._review_queue()
        if queue is None:
            section["detail"] = "the review queue is not available on this deployment"
            return section
        pending = self._pending_synthesis_items(queue, workspace_id=workspace_id)
        by_kind: Dict[str, int] = {}
        for item in pending:
            kind = str(item.get("kind") or "suggestion")
            by_kind[kind] = by_kind.get(kind, 0) + 1
        synthesizer = self._synthesis()
        brief: Dict[str, Any] = {}
        if synthesizer is not None:
            try:
                brief = dict(
                    synthesizer.brief_section(
                        counts=by_kind, workspace_id=workspace_id
                    )
                )
            except Exception:  # noqa: BLE001 — the section degrades, never raises
                LOGGER.exception("synthesis brief section failed")
        section.update(
            {
                "available": True,
                "pending": {"total": len(pending), "by_kind": by_kind},
                "items": [
                    {
                        "id": item.get("id"),
                        "kind": item.get("kind"),
                        "title": item.get("title"),
                        "summary": item.get("summary"),
                    }
                    for item in pending[:5]
                ],
                "tidying": by_kind.get("consolidation", 0) > 0,
                "headline": str(brief.get("headline") or ""),
                "lines": list(brief.get("lines") or []),
                "recent_nodes": brief.get("recent_nodes"),
            }
        )
        return section

    @staticmethod
    def _pending_synthesis_items(
        queue: Any, *, workspace_id: Optional[str]
    ) -> List[Dict[str, Any]]:
        from lattice_brain.synthesis import SYNTHESIS_REVIEW_SOURCE

        try:
            listing = queue.list(
                workspace_id=workspace_id, source=SYNTHESIS_REVIEW_SOURCE
            )
        except Exception:  # noqa: BLE001 — an unreadable inbox reads as empty
            LOGGER.exception("review queue listing failed")
            return []
        return [
            item
            for item in listing.get("items") or []
            if str(item.get("effective_status") or item.get("status")) == "pending"
        ]

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


__all__ = ["BrainIntelligenceService"]
