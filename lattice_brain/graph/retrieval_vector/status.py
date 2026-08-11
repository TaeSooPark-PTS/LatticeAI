"""What the vector index currently knows, and what it still owes.

``index_status`` / ``vector_freshness`` / ``vector_queue`` — the honesty
surface that reports coverage, staleness, and pending work rather than
letting a half-built index look complete. Moved verbatim out of
``retrieval_vector.py`` (v11.3.0 decomposition).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# ruff: noqa: F403,F405
from .._kg_common import *  # noqa: F403,F401
from ..vector_index import VectorEmbedQueue

# Typing-only base (runtime value is `object`, so the store's MRO is
# unchanged). Reporting on the index means asking the halves that build and
# query it — source items and incremental indexing from .indexing, the
# fingerprint it reaches through, and the backend selection from .search. They
# are named as bases rather than re-declared, so the signatures cannot drift.
if TYPE_CHECKING:
    from .indexing import _VectorIndexingMixin
    from .search import _VectorSearchMixin

    class _Core(_VectorIndexingMixin, _VectorSearchMixin):
        """The sibling halves this one reaches through ``self``."""
else:
    _Core = object


class _VectorStatusMixin(_Core):
    """Vector index status/freshness. Composed into the public mixin."""

    def index_status(self) -> Dict[str, Any]:
        storage_capabilities = None
        try:
            storage_capabilities = self.storage_engine.capabilities().as_dict()
        except Exception as exc:
            storage_capabilities = {
                "engine": "sqlite",
                "available": False,
                "reason": str(exc),
            }
        with self._connect() as conn:
            vector_counts = {
                row["item_type"]: row["count"]
                for row in conn.execute(
                    "SELECT item_type, COUNT(*) AS count FROM vector_embeddings GROUP BY item_type"
                )
            }
            # Materialised on purpose, unlike the rebuild path: status walks
            # this set twice (once for ids, once to classify each item) and
            # reports a count, none of which a one-shot iterator can serve.
            source_items = list(self._iter_vector_source_items(conn))
            vector_rows = {
                row["item_id"]: row
                for row in conn.execute(
                    """
                        SELECT item_id, text_hash, embedding_dim, embedding_model, indexed_at
                        FROM vector_embeddings
                        """
                ).fetchall()
            }
            latest_rows = conn.execute(
                """
                    SELECT id, operation, status, requested_at, started_at, completed_at,
                           items_total, items_indexed, items_skipped, error_message, metadata_json
                    FROM vector_index_operations
                    ORDER BY requested_at DESC, id DESC
                    LIMIT 5
                    """
            ).fetchall()
        missing = stale = ready = 0
        source_item_ids = {str(item["item_id"]) for item in source_items}
        backlog_by_type: Dict[str, int] = {}
        backlog_reasons: Dict[str, int] = {}
        backlog_samples: List[Dict[str, Any]] = []

        def add_backlog(item: Dict[str, Any], reason: str) -> None:
            item_type = str(item.get("item_type") or "unknown")
            backlog_by_type[item_type] = backlog_by_type.get(item_type, 0) + 1
            backlog_reasons[reason] = backlog_reasons.get(reason, 0) + 1
            if len(backlog_samples) >= 20:
                return
            backlog_samples.append(
                {
                    "item_id": item.get("item_id"),
                    "item_type": item_type,
                    "source_node": item.get("source_node"),
                    "reason": reason,
                    "metadata": {
                        key: value
                        for key, value in dict(item.get("metadata") or {}).items()
                        if key in {"node_type", "source", "conversation_id", "parent_source_node"}
                    },
                }
            )

        for item in source_items:
            vector_row = vector_rows.get(item["item_id"])
            expected_hash = _sha256_text(_clean_text(item["text"]))
            if not vector_row:
                missing += 1
                add_backlog(item, "missing_vector")
            elif (
                vector_row["text_hash"] != expected_hash
                or vector_row["embedding_dim"] != self._embedding_model.dim
                or vector_row["embedding_model"] != self._embedding_model.model_id
            ):
                stale += 1
                reason = "text_changed"
                if vector_row["embedding_model"] != self._embedding_model.model_id:
                    reason = "model_changed"
                elif vector_row["embedding_dim"] != self._embedding_model.dim:
                    reason = "dimension_changed"
                add_backlog(item, reason)
            else:
                ready += 1
        pending = missing + stale
        orphaned_items = max(0, len(set(vector_rows) - source_item_ids))
        coverage_ratio = round(ready / len(source_items), 6) if source_items else 1.0
        latest_completed = None
        for row in latest_rows:
            if row["status"] == "completed":
                latest_completed = row
                break
        latency_budget: Dict[str, Any] = {
            "target_rebuild_ms": 10_000,
            "last_rebuild_duration_ms": None,
            "last_items_per_second": None,
            "within_target": None,
        }
        if latest_completed is not None:
            metadata = _safe_loads(latest_completed["metadata_json"])
            duration_ms = metadata.get("duration_ms")
            items_total = int(latest_completed["items_total"] or 0)
            if isinstance(duration_ms, (int, float)) and duration_ms > 0:
                latency_budget.update(
                    {
                        "last_rebuild_duration_ms": round(float(duration_ms), 2),
                        "last_items_per_second": round(items_total / (float(duration_ms) / 1000.0), 2),
                        "within_target": float(duration_ms) <= 10_000,
                    }
                )
        embedder_status = self.embedder_fingerprint_status()
        return {
            "status": "ready" if pending == 0 else "needs_reindex",
            "embedder": embedder_status,
            "storage": {
                "db_path": str(self.db_path),
                "backend": "sqlite",
                "embedding_model": self._embedding_model.model_id,
                "embedding_dim": self._embedding_model.dim,
                # Honest capability report: trigram FTS5 keyword index, or
                # LIKE-scan fallback when this SQLite build lacks it.
                "fts_enabled": bool(getattr(self, "_fts_enabled", False)),
                "engine": storage_capabilities,
                "vector_search_backend": (
                    storage_capabilities.get("vector_backend")
                    if isinstance(storage_capabilities, dict)
                    else "bruteforce-cosine"
                ),
                "vector_search_mode": (
                    (storage_capabilities.get("metadata") or {}).get("vector_mode")
                    if isinstance(storage_capabilities, dict)
                    else "fallback"
                ),
                "sqlite_vec_ann_available": (
                    bool((storage_capabilities.get("metadata") or {}).get("sqlite_vec_ann_available"))
                    if isinstance(storage_capabilities, dict)
                    else False
                ),
                # v11.1.0: which in-process index scores a search, and — when
                # the configured one could not be used — the reason it was
                # substituted, so an unavailable optional extra is visible
                # here instead of only showing up as "search feels slow".
                "vector_index": self._vector_index_selection().as_dict(),
            },
            "source_items": len(source_items),
            "indexed_items": sum(vector_counts.values()),
            "ready_items": ready,
            "missing_items": missing,
            "stale_items": stale,
            "pending_items": pending,
            "by_item_type": vector_counts,
            "scale": {
                "version": 1,
                "coverage_ratio": coverage_ratio,
                "coverage_percent": round(coverage_ratio * 100.0, 2),
                "source_items": len(source_items),
                "ready_items": ready,
                "pending_items": pending,
                "missing_items": missing,
                "stale_items": stale,
                "orphaned_items": orphaned_items,
                "backlog_by_item_type": backlog_by_type,
                "backlog_reasons": backlog_reasons,
                "backlog_samples": backlog_samples,
                "incremental_reindex_recommended": pending > 0,
                # A stale embedder means every old-model row must be re-embedded;
                # only a full rebuild (which re-records the fingerprint) heals it.
                "full_rebuild_recommended": bool(
                    orphaned_items > 0 or embedder_status["stale_embedder"]
                ),
                "latency_budget": latency_budget,
            },
            "operations": [
                {
                    "id": row["id"],
                    "operation": row["operation"],
                    "status": row["status"],
                    "requested_at": row["requested_at"],
                    "started_at": row["started_at"],
                    "completed_at": row["completed_at"],
                    "items_total": row["items_total"],
                    "items_indexed": row["items_indexed"],
                    "items_skipped": row["items_skipped"],
                    "error_message": row["error_message"],
                    "metadata": _safe_loads(row["metadata_json"]),
                }
                for row in latest_rows
            ],
        }

    def vector_freshness(self) -> Dict[str, Any]:
        """Compact vector-index freshness summary for API surfaces (v9.8.0).

        Reduces :meth:`index_status` (``pending = missing + stale``) to the
        fixed contract ``{"status", "pending_items", "total_items", "detail"}``
        with ``status`` in ``ready`` / ``pending`` / ``stale_embedder`` /
        ``unavailable``. ``stale_embedder`` (review Wave 2.2) is reported only
        when the recorded embedder fingerprint differs from the current
        embedder AND rows indexed under the old model still exist — the index
        needs a full rebuild, not an incremental sync.

        Never raises: environments where the embedding provider or index
        storage cannot be used report ``"unavailable"`` with the cause in
        ``detail`` instead of surfacing an exception to the API layer.
        """
        try:
            status = self.index_status()
        except Exception as exc:  # noqa: BLE001 — freshness must degrade, not fail
            return {
                "status": "unavailable",
                "pending_items": 0,
                "total_items": 0,
                "detail": f"vector index status unavailable: {exc}",
            }
        return self._vector_freshness_summary(status)

    def _vector_freshness_summary(self, status: Dict[str, Any]) -> Dict[str, Any]:
        """The freshness reduction of an already-read :meth:`index_status`.

        Split out so :meth:`vector_freshness_breakdown` can report both shapes
        from one index scan; ``index_status`` walks every source item, and
        calling it twice to answer one question about freshness would double
        the most expensive read in this module.
        """
        pending = int(status.get("pending_items") or 0)
        total = int(status.get("source_items") or 0)
        embedder = status.get("embedder") or {}
        if embedder.get("stale_embedder"):
            old_model_rows = 0
            try:
                with self._connect() as conn:
                    old_model_rows = int(
                        conn.execute(
                            "SELECT COUNT(*) AS c FROM vector_embeddings "
                            "WHERE embedding_model<>? OR embedding_dim<>?",
                            (
                                self._embedding_model.model_id,
                                int(self._embedding_model.dim),
                            ),
                        ).fetchone()["c"]
                    )
            except Exception:  # noqa: BLE001 — keep the existing statuses on failure
                old_model_rows = 0
            if old_model_rows > 0:
                recorded = embedder.get("recorded") or {}
                return {
                    "status": "stale_embedder",
                    "pending_items": pending,
                    "total_items": total,
                    "detail": (
                        f"embedding model changed ({recorded.get('model_id')} → "
                        f"{self._embedding_model.model_id}); {old_model_rows} indexed "
                        "rows still use the previous model — run a full vector index rebuild"
                    ),
                }
        if pending > 0:
            return {
                "status": "pending",
                "pending_items": pending,
                "total_items": total,
                "detail": (
                    f"{pending} of {total} items are missing or stale in the vector index"
                ),
            }
        detail = (
            "vector index is up to date"
            if total
            else "vector index is empty (no indexable items yet)"
        )
        return {
            "status": "ready",
            "pending_items": 0,
            "total_items": total,
            "detail": detail,
        }

    @property
    def vector_queue(self) -> VectorEmbedQueue:
        """This store's durable pending-embed backlog (created on demand).

        Built lazily rather than in ``__init__`` so opening a graph never
        creates a table nobody asked for, and hung off the store so the
        ingestion pipeline and the freshness report share one backlog instead
        of each keeping a private view of it.
        """
        queue = getattr(self, "_vector_queue", None)
        if queue is None:
            queue = VectorEmbedQueue(
                db_path=self.db_path, indexer=self.index_node_incremental
            )
            self._vector_queue = queue
        return queue

    def vector_freshness_breakdown(self) -> Dict[str, Any]:
        """The four numbers behind :meth:`vector_freshness` (v11.1.0).

        ``vector_freshness()`` answers one question — *is the index behind?* —
        and its four keys are a frozen wire contract that surfaces already
        read, so this is a sibling rather than an extension of it. The split
        matters because "12 pending" hides two different situations: twelve
        items never embedded (a new import) and twelve items whose text
        changed under an existing embedding (edits). Only the second means
        current answers are quietly wrong.

        ``queued`` counts the durable background backlog
        (:class:`~lattice_brain.graph.vector_index.VectorEmbedQueue`), and is
        ``None`` when that queue has no database to persist to — never ``0``,
        which would claim an empty backlog nobody measured.

        Never raises: an unreadable index reports ``status="unavailable"``
        with the cause in ``detail`` and zeroed counts.
        """
        status: Dict[str, Any] = {}
        summary: Dict[str, Any]
        try:
            status = self.index_status()
        except Exception as exc:  # noqa: BLE001 — freshness must degrade, not fail
            summary = {
                "status": "unavailable",
                "pending_items": 0,
                "total_items": 0,
                "detail": f"vector index status unavailable: {exc}",
            }
        else:
            summary = self._vector_freshness_summary(status)
        breakdown: Dict[str, Any] = {
            "status": summary["status"],
            "detail": summary["detail"],
            "embedded": int(status.get("ready_items") or 0),
            "pending": int(summary["pending_items"]),
            "missing": int(status.get("missing_items") or 0),
            "stale": int(status.get("stale_items") or 0),
            "total": int(summary["total_items"]),
            "queued": None,
        }
        queue = self.vector_queue
        if queue.available:
            breakdown["queued"] = int(queue.pending_count())
        return breakdown
