"""Graph-layer Proactive Brain intelligence (v9.6.x).

Moves the "Proactive Brain Intelligence" operations (duplicate discovery,
contradiction detection, quality reporting, consolidation planning) down into
the graph layer, operating directly on a :class:`KnowledgeGraphStore` through
its *public read APIs* only (``graph(limit, allowed_workspaces=...)``).

Design contracts
----------------
* **Read-only by default.** Every method is a pure read over the store sample.
  ``consolidate_duplicates`` produces a merge *plan*; it mutates the store only
  when ``dry_run=False`` **and** the store exposes a safe ``merge_nodes``
  primitive (the current store does not — the plan is returned for review /
  proposal-first governance instead; no raw SQL is ever issued from here).
* **Workspace scoped.** ``workspace_id`` is forwarded as
  ``allowed_workspaces={workspace_id}`` so multi-user scoping rules of the
  store apply unchanged. ``workspace_id=None`` means unscoped (single-user).
* **Shared dedupe semantics.** Duplicate keys and token signatures reuse
  :func:`lattice_brain.quality.dedupe_key` / ``content_signature`` — the exact
  logic ``MemoryQualityManager.dedupe`` applies to memories — so memory-level
  and graph-level dedupe agree by construction.
* **Edge-dict pitfall.** The store emits edges with ``from``/``to`` keys.
  They are normalized once to ``source``/``target`` here. Scores/weights of
  ``0`` are meaningful — no ``or`` defaults on numeric fields.

``gate_ingest_candidate`` is a pure function seam for the ingestion pipeline
(owned elsewhere): it scores new content against existing graph content and
recommends ``ingest`` / ``skip_duplicate`` / ``review`` without side effects.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from lattice_brain.quality import (
    GraphEdgeQualityManager,
    MemoryQualityManager,
    content_signature,
    dedupe_key,
)

logger = logging.getLogger(__name__)

_DEFAULT_SAMPLE_LIMIT = 800
_DEFAULT_CONTRADICTION_NODES = 300
_DEFAULT_NEAR_THRESHOLD = 0.75
_DEFAULT_MAX_PAIRS = 200
_DEFAULT_STALE_DAYS = 90  # mirrors MemoryQualityManager.apply_retention
_DEFAULT_HALF_LIFE_DAYS = 30.0
# Node types that record *what happened* rather than *what is known*. Only
# these are offered for consolidation: folding a Decision or a Document into a
# summary would lose the thing the user actually keeps a Brain for.
_EPISODIC_TYPES = frozenset(
    {
        "chat",
        "conversation",
        "message",
        "airesponse",
        "ai_response",
        "event",
        "chunk",
    }
)


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


def _jaccard(left: Set[str], right: Set[str]) -> float:
    if not left or not right:
        return 0.0
    union = len(left | right)
    if union == 0:
        return 0.0
    return len(left & right) / union


def _node_text(node: Dict[str, Any]) -> str:
    title = str(node.get("title") or "").strip()
    summary = str(node.get("summary") or "").strip()
    return f"{title} {summary}".strip()


def _is_episodic(node: Dict[str, Any]) -> bool:
    return str(node.get("type") or "").strip().lower() in _EPISODIC_TYPES


def _access_count(node: Dict[str, Any], stored: Optional[Dict[str, Any]]) -> float:
    """Access count for a node: ingested metadata first, then the store counter.

    A surface that already tracks reads (``metadata.access_count``) is more
    accurate than our own read-path counter, so it wins; ``0`` from metadata is
    a real answer and is not treated as "missing".
    """
    metadata = node.get("metadata")
    if isinstance(metadata, dict):
        for key in ("access_count", "accesses", "access"):
            value = metadata.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
    if stored is None:
        return 0.0
    return float(stored.get("accesses") or 0.0)


def _slim(node: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": node.get("id"),
        "type": node.get("type"),
        "title": str(node.get("title") or "")[:120],
        "updated_at": node.get("updated_at"),
    }


class ProactiveBrain:
    """Proactive quality operations over a KnowledgeGraphStore sample.

    ``store`` only needs the public read API
    ``graph(limit, *, allowed_workspaces=None)`` (as implemented by
    :class:`~lattice_brain.graph.store.KnowledgeGraphStore`); optional write
    support is discovered via a ``merge_nodes`` attribute, never assumed.
    """

    def __init__(self, store: Any, *, sample_limit: int = _DEFAULT_SAMPLE_LIMIT):
        if store is None:
            raise ValueError("ProactiveBrain requires a graph store instance")
        self._store = store
        self._sample_limit = max(1, int(sample_limit))
        self._memory_quality = MemoryQualityManager()
        self._edge_quality = GraphEdgeQualityManager()

    # ── sampling ─────────────────────────────────────────────────────────

    def _sample(
        self, *, workspace_id: Optional[str] = None, limit: Optional[int] = None
    ) -> Dict[str, List[Dict[str, Any]]]:
        kwargs: Dict[str, Any] = {}
        if workspace_id is not None:
            kwargs["allowed_workspaces"] = {workspace_id}
        data = self._store.graph(int(limit or self._sample_limit), **kwargs)
        nodes = [dict(n) for n in (data.get("nodes") or [])]
        edges: List[Dict[str, Any]] = []
        for edge in data.get("edges") or []:
            normalized = dict(edge)
            # Store emits "from"/"to" (older projections: from_node/to_node).
            if normalized.get("source") is None:
                normalized["source"] = (
                    edge.get("from") if edge.get("from") is not None else edge.get("from_node")
                )
            if normalized.get("target") is None:
                normalized["target"] = (
                    edge.get("to") if edge.get("to") is not None else edge.get("to_node")
                )
            edges.append(normalized)
        return {"nodes": nodes, "edges": edges}

    def sample(
        self, *, workspace_id: Optional[str] = None, limit: Optional[int] = None
    ) -> Dict[str, List[Dict[str, Any]]]:
        """One normalized graph sample (``source``/``target`` edge keys).

        Public seam for :mod:`lattice_brain.synthesis`, which needs the *same*
        sample for several passes; taking it once keeps a synthesis run to one
        graph read and keeps every pass looking at identical data.
        """
        return self._sample(workspace_id=workspace_id, limit=limit)

    # ── duplicates ───────────────────────────────────────────────────────

    def find_duplicates(
        self,
        *,
        workspace_id: Optional[str] = None,
        limit: Optional[int] = None,
        near_threshold: float = _DEFAULT_NEAR_THRESHOLD,
        max_pairs: int = _DEFAULT_MAX_PAIRS,
    ) -> Dict[str, Any]:
        """Candidate duplicate nodes: exact signature groups + near pairs."""
        sample = self._sample(workspace_id=workspace_id, limit=limit)
        return self._find_duplicates_in(
            sample["nodes"], near_threshold=near_threshold, max_pairs=max_pairs
        )

    def _find_duplicates_in(
        self,
        nodes: List[Dict[str, Any]],
        *,
        near_threshold: float = _DEFAULT_NEAR_THRESHOLD,
        max_pairs: int = _DEFAULT_MAX_PAIRS,
    ) -> Dict[str, Any]:
        by_key: Dict[str, List[Dict[str, Any]]] = {}
        signatures: List[Tuple[Dict[str, Any], Set[str]]] = []
        for node in nodes:
            text = _node_text(node)
            if len(text) < 3:
                continue
            by_key.setdefault(dedupe_key(text), []).append(node)
            signatures.append((node, content_signature(text)))

        exact_groups = []
        exact_ids: Set[str] = set()
        grouped_together: Set[Tuple[str, str]] = set()
        for key, members in by_key.items():
            if len(members) < 2:
                continue
            ids = [str(m.get("id")) for m in members]
            exact_groups.append(
                {
                    "signature": key,
                    "count": len(members),
                    "node_ids": ids,
                    "nodes": [_slim(m) for m in members],
                }
            )
            exact_ids.update(ids[1:])
            for i, left in enumerate(ids):
                for right in ids[i + 1:]:
                    grouped_together.add((min(left, right), max(left, right)))

        # Near-duplicate pairs via token-signature blocking (co-occurrence >= 3
        # shared tokens) to avoid a full O(n^2) scan on large samples.
        token_index: Dict[str, List[int]] = {}
        for idx, (_node, sig) in enumerate(signatures):
            for token in sig:
                token_index.setdefault(token, []).append(idx)
        cooccur: Dict[Tuple[int, int], int] = {}
        for indices in token_index.values():
            if len(indices) < 2 or len(indices) > 50:  # skip too-common tokens
                continue
            for i, left_idx in enumerate(indices):
                for right_idx in indices[i + 1:]:
                    cooccur[(left_idx, right_idx)] = (
                        cooccur.get((left_idx, right_idx), 0) + 1
                    )

        near_pairs: List[Dict[str, Any]] = []
        for (li, ri), shared in sorted(cooccur.items(), key=lambda kv: -kv[1]):
            if shared < 3:
                continue
            left_node, left_sig = signatures[li]
            right_node, right_sig = signatures[ri]
            pair_key = (
                min(str(left_node.get("id")), str(right_node.get("id"))),
                max(str(left_node.get("id")), str(right_node.get("id"))),
            )
            if pair_key in grouped_together:
                continue  # already reported as exact duplicates
            similarity = _jaccard(left_sig, right_sig)
            if similarity < near_threshold:
                continue
            near_pairs.append(
                {
                    "left": _slim(left_node),
                    "right": _slim(right_node),
                    "similarity": round(similarity, 4),
                }
            )
            if len(near_pairs) >= max_pairs:
                break
        near_pairs.sort(key=lambda p: float(p["similarity"]), reverse=True)

        return {
            "nodes_scanned": len(nodes),
            "exact_groups": exact_groups,
            "exact_duplicate_nodes": len(exact_ids),
            "near_pairs": near_pairs,
            "near_threshold": near_threshold,
        }

    # ── contradictions ───────────────────────────────────────────────────

    def detect_contradictions(
        self,
        *,
        workspace_id: Optional[str] = None,
        limit: Optional[int] = None,
        max_nodes: int = _DEFAULT_CONTRADICTION_NODES,
    ) -> Dict[str, Any]:
        """Contradiction signals over graph node contents + CONTRADICTS edges."""
        sample = self._sample(workspace_id=workspace_id, limit=limit)
        return self._detect_contradictions_in(
            sample["nodes"], sample["edges"], max_nodes=max_nodes
        )

    def contradictions_in(
        self,
        nodes: List[Dict[str, Any]],
        edges: List[Dict[str, Any]],
        *,
        max_nodes: int = _DEFAULT_CONTRADICTION_NODES,
    ) -> Dict[str, Any]:
        """Contradiction signals over an already-taken :meth:`sample`."""
        return self._detect_contradictions_in(nodes, edges, max_nodes=max_nodes)

    def _detect_contradictions_in(
        self,
        nodes: List[Dict[str, Any]],
        edges: List[Dict[str, Any]],
        *,
        max_nodes: int = _DEFAULT_CONTRADICTION_NODES,
    ) -> Dict[str, Any]:
        rows = []
        for i, node in enumerate(nodes[: max(1, int(max_nodes))]):
            text = _node_text(node)
            if not text:
                continue
            rows.append(
                {
                    "id": str(node.get("id") or f"node-{i}"),
                    "content": text,
                    "score": 0.6,
                    "source": "graph",
                    "timestamp": node.get("updated_at") or 0,
                }
            )
        by_id = {row["id"]: row for row in rows}

        node_pairs: List[Dict[str, Any]] = []
        candidates = self._memory_quality.extract_candidates(rows)
        for candidate in self._memory_quality.detect_conflicts(candidates):
            for marker in candidate.conflicts:
                if not marker.startswith("conflict:contradicts:"):
                    continue
                other_id = marker.rsplit(":", 1)[-1]
                if any(
                    {p["left_id"], p["right_id"]} == {candidate.id, other_id}
                    for p in node_pairs
                ):
                    continue
                other = by_id.get(other_id) or {}
                node_pairs.append(
                    {
                        "left_id": candidate.id,
                        "left_content": candidate.content[:200],
                        "right_id": other_id,
                        "right_content": str(other.get("content") or "")[:200],
                        "signal": "preference_negation",
                    }
                )

        temporal = [
            {
                "id": item.get("id"),
                "content": str(item.get("content") or "")[:200],
                "signal": item.get("proactive_flag"),
            }
            for item in self._memory_quality.detect_temporal_contradictions(rows)
        ]

        contradiction_edges = [
            {
                "id": edge.get("id"),
                "source": edge.get("source"),
                "target": edge.get("target"),
                "type": edge.get("type"),
                "signal": "contradicts_edge",
            }
            for edge in edges
            if "CONTRADICT" in str(edge.get("type") or "").upper()
        ]

        return {
            "nodes_scanned": len(rows),
            "node_pairs": node_pairs,
            "temporal": temporal,
            "contradiction_edges": contradiction_edges,
            "count": len(node_pairs) + len(temporal) + len(contradiction_edges),
        }

    # ── quality report ───────────────────────────────────────────────────

    def quality_report(
        self,
        *,
        workspace_id: Optional[str] = None,
        limit: Optional[int] = None,
        max_age_days: int = _DEFAULT_STALE_DAYS,
        near_threshold: float = _DEFAULT_NEAR_THRESHOLD,
    ) -> Dict[str, Any]:
        """Combined JSON-safe report: duplicates, contradictions, stale nodes,
        edge quality. One graph sample feeds every section."""
        sample = self._sample(workspace_id=workspace_id, limit=limit)
        nodes, edges = sample["nodes"], sample["edges"]

        duplicates = self._find_duplicates_in(nodes, near_threshold=near_threshold)
        contradictions = self._detect_contradictions_in(nodes, edges)

        # Stale nodes — apply_retention semantics (age > max_age_days).
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(max_age_days)))
        stale = []
        dated = 0
        for node in nodes:
            ts = _parse_ts(node.get("updated_at"))
            if ts is None:
                continue
            dated += 1
            if ts < cutoff:
                stale.append(node)
        stale_report = {
            "count": len(stale),
            "dated_nodes": dated,
            "threshold_days": int(max_age_days),
            "samples": [_slim(n) for n in stale[:10]],
        }

        # Edge quality — confidence/evidence read explicitly (0.0 is a valid
        # confidence; never `or`-defaulted away).
        quality_edges = []
        for edge in edges:
            meta = edge.get("metadata") or {}
            entry: Dict[str, Any] = {
                "id": edge.get("id"),
                "source": edge.get("source"),
                "target": edge.get("target"),
                "type": edge.get("type"),
            }
            confidence = meta.get("confidence")
            if confidence is None:
                confidence = edge.get("confidence")
            if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
                entry["confidence"] = float(confidence)
            evidence = meta.get("evidence")
            if evidence is None:
                evidence = edge.get("evidence")
            entry["evidence"] = list(evidence) if isinstance(evidence, (list, tuple)) else []
            quality_edges.append(entry)
        edge_metrics = self._edge_quality.compute_quality_metrics(quality_edges)
        duplicate_edge_ids = [
            eid for eid in self._edge_quality.detect_duplicate_edges(quality_edges) if eid
        ]

        return {
            "nodes_scanned": len(nodes),
            "edges_scanned": len(edges),
            "duplicates": duplicates,
            "contradictions": contradictions,
            "stale_nodes": stale_report,
            "edge_quality": {
                "metrics": edge_metrics,
                "duplicate_edge_ids": duplicate_edge_ids[:50],
                "duplicate_edge_count": len(duplicate_edge_ids),
            },
            "summary": {
                "exact_duplicate_nodes": duplicates["exact_duplicate_nodes"],
                "near_duplicate_pairs": len(duplicates["near_pairs"]),
                "contradiction_signals": contradictions["count"],
                "stale_nodes": stale_report["count"],
            },
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    # ── importance & decay (v11.1.0) ─────────────────────────────────────

    def importance_report(
        self,
        *,
        workspace_id: Optional[str] = None,
        limit: Optional[int] = None,
        half_life_days: float = _DEFAULT_HALF_LIFE_DAYS,
        max_candidates: int = 20,
        sample: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    ) -> Dict[str, Any]:
        """Score every sampled node by use, then name the weakest episodic ones.

        The score is deliberately boring and reproducible — no model, no
        randomness::

            score = (1 + accesses + degree) * 0.5 ** (age_days / half_life)

        *accesses* prefers a real counter: ``metadata.access_count`` when the
        ingesting surface recorded one, otherwise the store's own read-path
        counter (``access_stats``), otherwise zero. *Episodic* types (chats,
        messages, events, chunks) are the only consolidation candidates —
        a decayed Document or Decision is stale knowledge to review, not
        noise to fold away.
        """
        data = sample if sample is not None else self._sample(
            workspace_id=workspace_id, limit=limit
        )
        nodes, edges = data["nodes"], data["edges"]
        degree: Dict[str, int] = {}
        for edge in edges:
            for key in ("source", "target"):
                node_id = str(edge.get(key) or "")
                if node_id:
                    degree[node_id] = degree.get(node_id, 0) + 1

        stats_fn = getattr(self._store, "access_stats", None)
        stored: Dict[str, Any] = {}
        if callable(stats_fn):
            try:
                stored = dict(stats_fn([n.get("id") for n in nodes]) or {})
            except Exception:  # noqa: BLE001 — the report degrades, never fails
                logger.exception("access stats read failed")

        now = datetime.now(timezone.utc)
        half_life = max(0.5, float(half_life_days))
        scored: List[Dict[str, Any]] = []
        for node in nodes:
            node_id = str(node.get("id") or "")
            accesses = _access_count(node, stored.get(node_id))
            ts = _parse_ts(node.get("updated_at"))
            age_days = 0.0 if ts is None else max(
                0.0, (now - ts).total_seconds() / 86400.0
            )
            decay = 0.5 ** (age_days / half_life)
            scored.append(
                {
                    **_slim(node),
                    "accesses": accesses,
                    "degree": degree.get(node_id, 0),
                    "age_days": round(age_days, 2),
                    "score": round((1.0 + accesses + degree.get(node_id, 0)) * decay, 4),
                    "episodic": _is_episodic(node),
                }
            )
        scored.sort(key=lambda item: (item["score"], str(item.get("id") or "")))
        candidates = [item for item in scored if item["episodic"]][
            : max(1, int(max_candidates))
        ]
        return {
            "nodes_scanned": len(nodes),
            "half_life_days": half_life,
            "access_source": "store" if stored else "metadata",
            "candidates": candidates,
            "candidate_count": len(candidates),
            "strongest": list(reversed(scored[-5:])),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    # ── consolidation ────────────────────────────────────────────────────

    def consolidate_duplicates(
        self,
        *,
        workspace_id: Optional[str] = None,
        limit: Optional[int] = None,
        dry_run: bool = True,
        near_threshold: float = _DEFAULT_NEAR_THRESHOLD,
    ) -> Dict[str, Any]:
        """Merge plan for exact-duplicate node groups.

        Canonical node per group = most recently updated (ties: smallest id,
        deterministic). Near-duplicate pairs are *reported for review only* —
        they are never auto-merged.

        Writes happen only when ``dry_run=False`` **and** the store exposes a
        safe ``merge_nodes(keep_id, remove_ids)`` primitive. The current
        KnowledgeGraphStore does not, so apply requests degrade to
        ``mode="plan_only"`` with an actionable plan instead of issuing raw
        SQL against store internals.
        """
        sample = self._sample(workspace_id=workspace_id, limit=limit)
        nodes, edges = sample["nodes"], sample["edges"]
        duplicates = self._find_duplicates_in(nodes, near_threshold=near_threshold)

        def _keep_rank(node: Dict[str, Any]):
            ts = _parse_ts(node.get("updated_at")) or datetime.fromtimestamp(
                0, tz=timezone.utc
            )
            # Latest update first; ties broken by smallest id — deterministic.
            return (-ts.timestamp(), str(node.get("id") or ""))

        groups = []
        for group in duplicates["exact_groups"]:
            members = group["nodes"]
            keep = sorted(members, key=_keep_rank)[0]
            keep_id = str(keep.get("id"))
            remove_ids = [str(m.get("id")) for m in members if str(m.get("id")) != keep_id]
            touched_edges = sum(
                1
                for e in edges
                if str(e.get("source")) in remove_ids or str(e.get("target")) in remove_ids
            )
            groups.append(
                {
                    "signature": group["signature"],
                    "keep": keep_id,
                    "keep_title": keep.get("title"),
                    "remove": remove_ids,
                    "edges_to_redirect": touched_edges,
                    "reason": "exact_duplicate",
                }
            )

        merge_fn = getattr(self._store, "merge_nodes", None)
        apply_supported = callable(merge_fn)
        applied: List[Dict[str, Any]] = []
        if dry_run:
            mode = "dry_run"
        elif not apply_supported:
            mode = "plan_only"
        else:
            mode = "applied"
            for group in groups:
                try:
                    if merge_fn is None:  # guarded by apply_supported above
                        raise RuntimeError("store has no merge_nodes")  # pragma: no cover — unreachable: callable(merge_fn) above proves it is not None
                    result = merge_fn(group["keep"], group["remove"])
                    applied.append({"keep": group["keep"], "result": result})
                except Exception as exc:  # keep going; report per-group failure
                    logger.exception("proactive merge failed for %s", group["keep"])
                    applied.append({"keep": group["keep"], "error": str(exc)})

        return {
            "mode": mode,
            "apply_supported": apply_supported,
            "nodes_scanned": len(nodes),
            "groups": groups,
            "group_count": len(groups),
            "applied": applied,
            "review_only_near_pairs": duplicates["near_pairs"][:20],
            "note": (
                None
                if apply_supported or dry_run
                else "Store exposes no safe merge primitive (merge_nodes); "
                "returning the merge plan for review instead of mutating the graph."
            ),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }


# ── ingestion quality gate (pure function seam) ──────────────────────────


def gate_ingest_candidate(
    text: str,
    existing_search_fn: Callable[[str], Any],
    *,
    near_threshold: float = 0.6,
) -> Dict[str, Any]:
    """Score whether new content should be ingested into the graph.

    Pure function — no store access, no side effects. Intended adoption point
    for the ingestion pipeline (``services/ingestion.py`` /
    ``lattice_brain.graph.ingest``), which passes a closure over its own
    scoped search, e.g.::

        gate = gate_ingest_candidate(
            text,
            lambda q: store.search(q, 20, allowed_workspaces={ws}),
        )
        if gate["action"] == "skip_duplicate": ...

    ``existing_search_fn(text)`` may return either the store's
    ``{"matches": [...]}`` shape or a plain list of node dicts
    (``id``/``title``/``summary`` or ``content``).

    Returns ``{"action": "ingest"|"skip_duplicate"|"review", "reason": str,
    "match_id": Optional[str], "similarity": float}``. A search failure
    returns ``review`` (fail-open to a human, never silent data loss).
    """
    text = str(text or "").strip()
    if len(text) < 3:
        return {
            "action": "review",
            "reason": "empty_or_too_short",
            "match_id": None,
            "similarity": 0.0,
        }
    try:
        raw = existing_search_fn(text)
    except Exception as exc:
        return {
            "action": "review",
            "reason": f"search_failed: {exc}",
            "match_id": None,
            "similarity": 0.0,
        }
    matches = raw.get("matches") if isinstance(raw, dict) else raw
    key = dedupe_key(text)
    signature = content_signature(text)
    best_similarity = 0.0
    best_id: Optional[str] = None
    for match in list(matches or []):
        if not isinstance(match, dict):
            continue
        parts = [
            str(match.get("title") or ""),
            str(match.get("summary") or ""),
            str(match.get("content") or ""),
        ]
        match_text = " ".join(p for p in parts if p).strip()
        if not match_text:
            continue
        if dedupe_key(match_text) == key:
            return {
                "action": "skip_duplicate",
                "reason": "exact_signature_match",
                "match_id": match.get("id"),
                "similarity": 1.0,
            }
        similarity = _jaccard(signature, content_signature(match_text))
        if similarity > best_similarity:
            best_similarity = similarity
            best_id = match.get("id")
    if best_similarity >= near_threshold:
        return {
            "action": "review",
            "reason": "near_duplicate",
            "match_id": best_id,
            "similarity": round(best_similarity, 4),
        }
    return {
        "action": "ingest",
        "reason": "novel_content",
        "match_id": best_id,
        "similarity": round(best_similarity, 4),
    }


__all__ = ["ProactiveBrain", "gate_ingest_candidate"]
