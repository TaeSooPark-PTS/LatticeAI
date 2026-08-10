from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

# ruff: noqa: F403,F405
from ._kg_common import *  # noqa: F403,F401
from .vector_index import (
    DEFAULT_VECTOR_INDEX,
    HNSW_BACKEND,
    VECTOR_INDEX_ENV,
    BackendSelection,
    HnswIndex,
    IndexItem,
    VectorEmbedQueue,
    VectorIndex,
    build_index,
    resolve_vector_index,
)

# The cross-mixin surface (`_connect`, `_upsert_node`, …) is declared in
# `_kg_contract.KnowledgeGraphCore`. It is a typing-only base: at runtime this
# is `object`, so the MRO of `KnowledgeGraphStore` is unchanged.
if TYPE_CHECKING:
    from ._kg_contract import KnowledgeGraphCore as _Core
else:
    _Core = object


# ── brute-force recall cap (review 2026-08 P1 #2) ────────────────────────────
# There is no ANN index in the default build: sqlite-vec is an optional
# dependency and, when it is absent, `index_status()["storage"]` honestly
# reports ``vector_search_backend: "bruteforce-cosine"``. Brute force scores
# every candidate in Python, so *some* cap is unavoidable on a large graph.
#
# What is not acceptable is a SILENT cap. The pre-10.7 code took the 10 000
# most recently indexed rows — ordered by ``indexed_at``, i.e. by recency, not
# by similarity — and returned them as if they were the whole index, so recall
# on a 200 000-row brain quietly became "the newest 5%". The cap is now
# explicit, configurable, and reported back to the caller in ``recall``.
#
# ``LATTICEAI_VECTOR_MAX_CANDIDATES`` overrides the default; ``0`` means "no
# cap — scan the whole index" (exact recall, paid for in latency).
VECTOR_MAX_CANDIDATES_ENV = "LATTICEAI_VECTOR_MAX_CANDIDATES"
DEFAULT_VECTOR_MAX_CANDIDATES = 10_000
#: Upper bound for a configured cap; ``0``/``None`` still means uncapped.
VECTOR_MAX_CANDIDATES_CEILING = 500_000

# ── scan batching (v11.1.0) ──────────────────────────────────────────────────
# The exact scan hands its candidates to a VectorIndex, which by definition
# holds what it is given. Handing it the whole result set would make peak
# memory O(rows × dim) floats, so the scan feeds it in fixed batches instead:
# exhaustive backends score every batch independently, so the union is
# identical to one big pass, at O(batch × dim) resident cost.
VECTOR_SCAN_BATCH = 512


def _configured_vector_max_candidates() -> Optional[int]:
    """Resolve the candidate cap from the environment (None = uncapped).

    Never raises: an unparseable value falls back to the documented default
    rather than breaking every search.
    """
    raw = os.getenv(VECTOR_MAX_CANDIDATES_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_VECTOR_MAX_CANDIDATES
    try:
        value = int(raw.strip())
    except ValueError:
        logging.warning(
            "%s=%r is not an integer — using the default cap of %d",
            VECTOR_MAX_CANDIDATES_ENV, raw, DEFAULT_VECTOR_MAX_CANDIDATES,
        )
        return DEFAULT_VECTOR_MAX_CANDIDATES
    if value <= 0:
        return None  # explicit opt-in to an exhaustive scan
    return min(value, VECTOR_MAX_CANDIDATES_CEILING)


class KnowledgeGraphVectorMixin(_Core):
    """Vector-embedding index build/status/search, split out of retrieval.

    Composed into KnowledgeGraphStore alongside KnowledgeGraphRetrievalMixin;
    both mixins share the same instance, so vector methods still reach sibling
    retrieval/write helpers (e.g. self._vector_text_for_node) through the MRO.
    """

    # ── embedder fingerprint (review Wave 2.2 — stale_embedder) ──────────────
    # vector_search filters on the CURRENT model/dim, so swapping the embedder
    # silently yields zero vector rows. The fingerprint persisted in graph_meta
    # records which embedder actually built the index; a mismatch is surfaced
    # as the honest ``stale_embedder`` signal instead of a silent degradation.

    _EMBEDDER_FINGERPRINT_KEY = "embedder_fingerprint"

    def _embedder_fingerprint_record(
        self, conn: sqlite3.Connection
    ) -> Optional[Dict[str, Any]]:
        """Read the recorded embedder fingerprint from graph_meta (or None)."""
        row = conn.execute(
            "SELECT value FROM graph_meta WHERE key=?",
            (self._EMBEDDER_FINGERPRINT_KEY,),
        ).fetchone()
        if not row:
            return None
        payload = _safe_loads(row["value"])
        if not isinstance(payload, dict) or not payload.get("model_id"):
            return None
        try:
            dim = int(payload.get("dim") or 0)
        except (TypeError, ValueError):
            dim = 0
        return {"model_id": str(payload["model_id"]), "dim": dim}

    def _write_embedder_fingerprint(self, conn: sqlite3.Connection) -> Dict[str, Any]:
        """Persist the CURRENT embedder identity (same transaction as caller)."""
        fingerprint = {
            "model_id": self._embedding_model.model_id,
            "dim": int(self._embedding_model.dim),
        }
        conn.execute(
            "INSERT OR REPLACE INTO graph_meta(key, value) VALUES (?, ?)",
            (self._EMBEDDER_FINGERPRINT_KEY, _json(fingerprint)),
        )
        return fingerprint

    def record_embedder_fingerprint(self) -> Dict[str, Any]:
        """Record the current embedder (model_id + dim) as the index builder."""
        with self._connect() as conn:
            return self._write_embedder_fingerprint(conn)

    def embedder_fingerprint_status(self) -> Dict[str, Any]:
        """Compare the current embedder against the recorded index fingerprint.

        Returns ``{"current": {model_id, dim}, "recorded": {...} | None,
        "stale_embedder": bool}``. ``stale_embedder`` is True only when a
        fingerprint was recorded AND it differs from the current embedder —
        an unrecorded index (legacy DBs, nothing indexed yet) is honestly
        "unknown", never reported stale. Never raises.
        """
        current = {
            "model_id": self._embedding_model.model_id,
            "dim": int(self._embedding_model.dim),
        }
        recorded: Optional[Dict[str, Any]] = None
        try:
            with self._connect() as conn:
                recorded = self._embedder_fingerprint_record(conn)
        except Exception:  # noqa: BLE001 — status must degrade, never raise
            recorded = None
        stale = bool(
            recorded is not None
            and (
                recorded.get("model_id") != current["model_id"]
                or recorded.get("dim") != current["dim"]
            )
        )
        return {"current": current, "recorded": recorded, "stale_embedder": stale}

    def _vector_text_hashes(self, conn: sqlite3.Connection) -> Dict[str, str]:
        """``item_id -> text_hash`` for rows already embedded by *this* embedder.

        The incremental rebuild's job is mostly deciding what it does *not*
        have to do, and it used to ask that question with one ``SELECT`` per
        candidate item — a round trip per node and per chunk on every run,
        almost all of which answer "unchanged". One query returning two short
        columns replaces all of them.

        Rows written by a different embedder are left out, so they compare as
        missing and get re-embedded, which is what an embedder swap requires.
        """
        return {
            row["item_id"]: row["text_hash"]
            for row in conn.execute(
                """
                    SELECT item_id, text_hash
                    FROM vector_embeddings
                    WHERE embedding_model=? AND embedding_dim=?
                    """,
                (self._embedding_model.model_id, self._embedding_model.dim),
            ).fetchall()
        }

    def _iter_vector_source_items(
        self,
        conn: sqlite3.Connection,
        *,
        include_nodes: bool = True,
        include_chunks: bool = True,
    ) -> Iterator[Dict[str, Any]]:
        """Stream the graph's embeddable text, one item at a time.

        Yields rather than returns a list. Every caller consumes this exactly
        once in a ``for``, and building the list first meant a rebuild held
        the full text of every node and chunk in memory simultaneously — the
        one shape guaranteed to fail on precisely the large graph that most
        needs the index.
        """
        if include_nodes:
            for row in conn.execute(
                """
                    SELECT id, type, title, summary, metadata_json
                    FROM nodes
                    WHERE type <> 'Chunk'
                    ORDER BY updated_at DESC, id ASC
                    """
            ).fetchall():
                metadata = _safe_loads(row["metadata_json"])
                text = self._vector_text_for_node(
                    title=row["title"],
                    summary=row["summary"] or "",
                    metadata=metadata,
                )
                if text:
                    yield {
                        "item_id": row["id"],
                        "item_type": "node",
                        "source_node": row["id"],
                        "text": text,
                        "metadata": {"node_type": row["type"], **metadata},
                    }
        if include_chunks:
            for row in conn.execute(
                """
                    SELECT c.id, c.source_node AS parent_source_node, c.text, c.metadata_json
                    FROM chunks c
                    JOIN nodes n ON n.id=c.id
                    ORDER BY c.created_at DESC, c.id ASC
                    """
            ).fetchall():
                metadata = _safe_loads(row["metadata_json"])
                text = _clean_text(row["text"] or "")
                if text:
                    yield {
                        "item_id": row["id"],
                        "item_type": "chunk",
                        "source_node": row["id"],
                        "text": text,
                        "metadata": {
                            **metadata,
                            "parent_source_node": row["parent_source_node"],
                        },
                    }

    def index_node_incremental(self, node_id: str) -> Dict[str, Any]:
        """Embed/index only ``node_id`` and its chunks (incremental sync).

        The item construction mirrors :meth:`_iter_vector_source_items` exactly
        (same ids, same ``source_node``/``parent_source_node`` semantics), so
        anything this method indexes is indistinguishable from a full
        :meth:`rebuild_vector_index` pass — and anything it *fails* to index
        stays visible as ``missing``/``stale`` backlog in :meth:`index_status`,
        where a later rebuild picks it up.

        Never raises: embedding-provider or storage failures are reported as
        ``{"status": "failed", ...}`` so ingestion callers can degrade instead
        of losing an already-persisted write.
        """
        node_id = str(node_id or "").strip()
        started = time.perf_counter()
        summary: Dict[str, Any] = {
            "node_id": node_id,
            "items_total": 0,
            "items_indexed": 0,
            "items_skipped": 0,
        }
        if not node_id:
            return {**summary, "status": "skipped", "detail": "node_id required"}
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT id, type, title, summary, metadata_json FROM nodes WHERE id=?",
                    (node_id,),
                ).fetchone()
                if row is None:
                    return {**summary, "status": "skipped", "detail": "node not found"}
                items: List[Dict[str, Any]] = []
                if row["type"] != "Chunk":
                    metadata = _safe_loads(row["metadata_json"])
                    text = self._vector_text_for_node(
                        title=row["title"],
                        summary=row["summary"] or "",
                        metadata=metadata,
                    )
                    if text:
                        items.append(
                            {
                                "item_id": row["id"],
                                "item_type": "node",
                                "source_node": row["id"],
                                "text": text,
                                "metadata": {"node_type": row["type"], **metadata},
                            }
                        )
                for chunk_row in conn.execute(
                    """
                        SELECT c.id, c.source_node AS parent_source_node, c.text, c.metadata_json
                        FROM chunks c
                        JOIN nodes n ON n.id=c.id
                        WHERE c.source_node=?
                        ORDER BY c.created_at ASC, c.id ASC
                        """,
                    (node_id,),
                ).fetchall():
                    metadata = _safe_loads(chunk_row["metadata_json"])
                    text = _clean_text(chunk_row["text"] or "")
                    if text:
                        items.append(
                            {
                                "item_id": chunk_row["id"],
                                "item_type": "chunk",
                                "source_node": chunk_row["id"],
                                "text": text,
                                "metadata": {
                                    **metadata,
                                    "parent_source_node": chunk_row["parent_source_node"],
                                },
                            }
                        )
                indexed = skipped = 0
                for item in items:
                    if self._upsert_vector_item(conn, **item):
                        indexed += 1
                    else:
                        skipped += 1
                if indexed and self._embedder_fingerprint_record(conn) is None:
                    # First successful vector write establishes the fingerprint;
                    # later incremental writes never overwrite it (only a full
                    # rebuild may flip it after an embedder swap).
                    self._write_embedder_fingerprint(conn)
            summary.update(
                {
                    "items_total": len(items),
                    "items_indexed": indexed,
                    "items_skipped": skipped,
                }
            )
            return {
                **summary,
                "status": "indexed" if indexed else "noop",
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "embedding_model": self._embedding_model.model_id,
            }
        except Exception as exc:  # noqa: BLE001 — incremental sync must never raise
            return {
                **summary,
                "status": "failed",
                "detail": str(exc),
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            }

    def rebuild_vector_index(
        self,
        *,
        full: bool = False,
        include_nodes: bool = True,
        include_chunks: bool = True,
    ) -> Dict[str, Any]:
        """Rebuild the derived vector index without mutating graph content."""
        op_id = f"vector-op:{_sha256_text(f'{time.time()}:{os.getpid()}')[:24]}"
        requested_at = _now()
        started = time.perf_counter()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                        INSERT INTO vector_index_operations(
                          id, operation, status, requested_at, started_at, metadata_json
                        )
                        VALUES (?, ?, 'running', ?, ?, ?)
                        """,
                    (
                        op_id,
                        "rebuild_full" if full else "rebuild_incremental",
                        requested_at,
                        requested_at,
                        _json(
                            {
                                "include_nodes": include_nodes,
                                "include_chunks": include_chunks,
                            }
                        ),
                    ),
                )
                if full:
                    filters = []
                    if include_nodes:
                        filters.append("'node'")
                    if include_chunks:
                        filters.append("'chunk'")
                    if filters:
                        conn.execute(
                            f"DELETE FROM vector_embeddings WHERE item_type IN ({','.join(filters)})"
                        )
                # After a full wipe nothing is current by definition, so the
                # prefetch would only be a wasted scan of a table we just
                # emptied. Incremental is where it pays: it turns "one SELECT
                # per item, nearly all of which say unchanged" into one query.
                known = {} if full else self._vector_text_hashes(conn)
                total = indexed = skipped = 0
                for item in self._iter_vector_source_items(
                    conn,
                    include_nodes=include_nodes,
                    include_chunks=include_chunks,
                ):
                    total += 1
                    if known.get(item["item_id"]) == _sha256_text(_clean_text(item["text"])):
                        skipped += 1
                        continue
                    if self._upsert_vector_item(conn, **item):
                        indexed += 1
                    else:
                        skipped += 1
                duration_ms = round((time.perf_counter() - started) * 1000, 2)
                conn.execute(
                    """
                        UPDATE vector_index_operations
                        SET status='completed', completed_at=?, items_total=?,
                            items_indexed=?, items_skipped=?, metadata_json=?
                        WHERE id=?
                        """,
                    (
                        _now(),
                        total,
                        indexed,
                        skipped,
                        _json(
                            {
                                "include_nodes": include_nodes,
                                "include_chunks": include_chunks,
                                "duration_ms": duration_ms,
                                "embedding_model": self._embedding_model.model_id,
                                "embedding_dim": self._embedding_model.dim,
                            }
                        ),
                        op_id,
                    ),
                )
                # A successful rebuild (re)establishes which embedder built
                # the index — this is the only path that may flip a recorded
                # fingerprint after an embedder swap.
                self._write_embedder_fingerprint(conn)
            return {
                "status": "completed",
                "operation_id": op_id,
                "full": bool(full),
                "items_total": total,
                "items_indexed": indexed,
                "items_skipped": skipped,
                "duration_ms": duration_ms,
                "embedding_model": self._embedding_model.model_id,
                "embedding_dim": self._embedding_model.dim,
            }
        except Exception as exc:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            with self._connect() as conn:
                conn.execute(
                    """
                        INSERT INTO vector_index_operations(
                          id, operation, status, requested_at, started_at, completed_at,
                          error_message, metadata_json
                        )
                        VALUES (?, ?, 'failed', ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                          status='failed',
                          completed_at=excluded.completed_at,
                          error_message=excluded.error_message,
                          metadata_json=excluded.metadata_json
                        """,
                    (
                        op_id,
                        "rebuild_full" if full else "rebuild_incremental",
                        requested_at,
                        requested_at,
                        _now(),
                        str(exc),
                        _json({"duration_ms": duration_ms}),
                    ),
                )
            raise

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

    def _vector_index_selection(self) -> BackendSelection:
        """The configured in-process index backend (``LATTICEAI_VECTOR_INDEX``).

        Resolved per call rather than cached: the env var is the whole control
        surface, and a cached selection would make a config change look like
        it had no effect. The only expensive part — importing ``hnswlib`` —
        is already cached by ``sys.modules``.
        """
        return resolve_vector_index()

    def _vector_search_backend(self) -> str:
        """Which backend actually scores the vectors.

        An explicitly selected in-process index (quantized / hnsw) wins,
        because it is the thing that will do the scoring. Otherwise this is
        the storage layer's answer: sqlite-vec exposes an ANN index; without
        it this store scores rows in Python (``bruteforce-cosine``). Never
        raises — a capability probe failure means "we cannot claim ANN",
        which is the brute-force answer.
        """
        selection = self._vector_index_selection()
        if selection.name != DEFAULT_VECTOR_INDEX:
            return selection.backend
        try:
            capabilities = self.storage_engine.capabilities().as_dict()
        except Exception:  # noqa: BLE001 — a probe failure is not an ANN index
            return "bruteforce-cosine"
        backend = (capabilities or {}).get("vector_backend")
        return str(backend) if backend else "bruteforce-cosine"

    @staticmethod
    def _recall_report(
        *,
        backend: str,
        cap: Optional[int],
        candidates_total: int,
        candidates_scanned: int,
        approx_detail: Optional[str] = None,
    ) -> Dict[str, Any]:
        """The honest answer to "did this search see the whole index?".

        ``approx_detail`` covers the second way recall can be incomplete: an
        ANN backend *visits* the whole index but is not guaranteed to return
        its true top-k. "Scanned N of N" with no detail would read as an exact
        answer, so the approximate backends supply their caveat here.
        """
        truncated = candidates_scanned < candidates_total
        detail: Optional[str] = None
        if truncated:
            detail = (
                f"partial recall: scored the {candidates_scanned} most recently "
                f"indexed vectors of {candidates_total}. The cut is by index "
                f"recency, not similarity, so older matches were never compared. "
                f"Raise {VECTOR_MAX_CANDIDATES_ENV} (0 = scan everything), or "
                f"switch to an index that covers the whole set: "
                f"{VECTOR_INDEX_ENV}=hnsw (needs the optional hnsw extra) or "
                f"install sqlite-vec."
            )
        elif approx_detail:
            detail = approx_detail
        return {
            "backend": backend,
            "max_candidates": cap,
            "candidates_total": candidates_total,
            "candidates_scanned": candidates_scanned,
            "truncated": truncated,
            "detail": detail,
        }

    def _vector_candidate_cap(
        self, requested: Optional[int], *, limit: int
    ) -> Optional[int]:
        """Resolve the effective candidate cap (None = scan everything).

        ``requested is None`` uses the configured/default cap; an explicit
        ``<= 0`` is the caller asking for an exhaustive scan. Note the
        ``is None`` test: ``0`` is a meaningful value here, so truthiness
        would silently turn "no cap" into "the default cap".
        """
        if requested is None:
            cap = _configured_vector_max_candidates()
        elif int(requested) <= 0:
            cap = None
        else:
            cap = min(int(requested), VECTOR_MAX_CANDIDATES_CEILING)
        if cap is None:
            return None
        # Never scan fewer rows than the caller intends to receive.
        return max(limit, cap)

    # One row shape feeds every vector match, so both the exact scan and the
    # ANN lookup project exactly the same columns; only the WHERE/ORDER tail
    # differs. Bound values are always parameters — the interpolation below is
    # a placeholder list, never data.
    _VECTOR_ROW_SELECT = """
                    SELECT
                      ve.item_id, ve.item_type, ve.source_node, ve.embedding,
                      ve.embedding_dim, ve.embedding_model, ve.metadata_json AS vector_metadata,
                      n.type AS node_type, n.title AS node_title, n.summary AS node_summary,
                      n.metadata_json AS node_metadata, n.updated_at AS node_updated_at,
                      c.text AS chunk_text, c.source_node AS parent_node_id,
                      c.metadata_json AS chunk_metadata,
                      pn.type AS parent_type, pn.title AS parent_title,
                      pn.summary AS parent_summary, pn.metadata_json AS parent_metadata,
                      pn.updated_at AS parent_updated_at
                    FROM vector_embeddings ve
                    LEFT JOIN nodes n ON n.id=ve.source_node
                    LEFT JOIN chunks c ON c.id=ve.item_id
                    LEFT JOIN nodes pn ON pn.id=c.source_node
                    WHERE ve.embedding_model=? AND ve.embedding_dim=?
                    """

    @staticmethod
    def _vector_match(row: sqlite3.Row, score: float) -> Dict[str, Any]:
        """One scored embedding row → one search match (pure projection)."""
        is_chunk = row["item_type"] == "chunk"
        summary = (
            row["chunk_text"] if is_chunk and row["chunk_text"] else row["node_summary"]
        )
        parent_metadata = _safe_loads(row["parent_metadata"])
        node_metadata = _safe_loads(row["node_metadata"])
        # Citation precision (review 2026-07-27 P1 #4): a chunk hit used to
        # cite only its parent document, so a 200-page PDF answered with
        # "from report.pdf". The chunk's own provenance (section heading,
        # page, offset) now rides along, and `locator` is the one-line
        # human form — absent when the chunk carries no such metadata.
        chunk_metadata = _safe_loads(row["chunk_metadata"]) if is_chunk else {}
        locator = citation_locator(chunk_metadata)
        return {
            "id": row["item_id"],
            "node_id": row["parent_node_id"]
            if is_chunk and row["parent_node_id"]
            else row["source_node"],
            "item_type": row["item_type"],
            "type": "Chunk" if is_chunk else row["node_type"],
            "title": row["parent_title"]
            if is_chunk and row["parent_title"]
            else row["node_title"],
            "summary": _clean_text(summary or "")[:1000],
            "score": round(float(score), 6),
            "metadata": {
                **(parent_metadata if is_chunk else node_metadata),
                "vector": _safe_loads(row["vector_metadata"]),
                "parent_node_id": row["parent_node_id"],
                "parent_type": row["parent_type"],
                **({"chunk": chunk_metadata} if chunk_metadata else {}),
                **({"locator": locator} if locator else {}),
            },
            "updated_at": row["parent_updated_at"]
            if is_chunk and row["parent_updated_at"]
            else row["node_updated_at"],
        }

    @staticmethod
    def _flush_scan_batch(
        index: VectorIndex,
        batch: List[IndexItem],
        query_vector: List[float],
        min_score: float,
        scores: Dict[str, float],
    ) -> None:
        """Score one batch into ``scores`` and empty it."""
        if not batch:
            return
        index.rebuild(batch)
        scores.update(
            index.search(query_vector, len(batch), filter={"min_score": min_score})
        )
        batch.clear()

    def _score_vector_rows(
        self,
        rows: List[sqlite3.Row],
        query_vector: List[float],
        selection: BackendSelection,
        *,
        min_score: float,
    ) -> Dict[str, float]:
        """``item_id -> score`` for every row that clears ``min_score``."""
        index = build_index(
            selection,
            dim=int(self._embedding_model.dim),
            similarity=self._embedding_model.similarity,
        )
        scores: Dict[str, float] = {}
        batch: List[IndexItem] = []
        for row in rows:
            batch.append(
                (
                    str(row["item_id"]),
                    self._embedding_model.decode(
                        row["embedding"], row["embedding_dim"]
                    ),
                    {"item_type": row["item_type"]},
                )
            )
            if len(batch) >= VECTOR_SCAN_BATCH:
                self._flush_scan_batch(index, batch, query_vector, min_score, scores)
        self._flush_scan_batch(index, batch, query_vector, min_score, scores)
        return scores

    def _vector_search_scan(
        self,
        query: str,
        query_vector: List[float],
        selection: BackendSelection,
        *,
        limit: int,
        min_score: float,
        backend: str,
        cap: Optional[int],
    ) -> Dict[str, Any]:
        """Exhaustive scan of (at most ``cap``) rows — the historical path."""
        sql = self._VECTOR_ROW_SELECT + " ORDER BY ve.indexed_at DESC"
        params: List[Any] = [
            self._embedding_model.model_id,
            self._embedding_model.dim,
        ]
        if cap is not None:
            sql += " LIMIT ?"
            params.append(cap)
        with self._connect() as conn:
            # Counted in the same transaction as the scan so "scanned N of M"
            # cannot describe two different index states.
            candidates_total = int(
                conn.execute(
                    "SELECT COUNT(*) AS c FROM vector_embeddings "
                    "WHERE embedding_model=? AND embedding_dim=?",
                    (self._embedding_model.model_id, self._embedding_model.dim),
                ).fetchone()["c"]
            )
            rows = conn.execute(sql, tuple(params)).fetchall()
        recall = self._recall_report(
            backend=backend,
            cap=cap,
            candidates_total=candidates_total,
            candidates_scanned=len(rows),
            approx_detail=(
                "approximate backend: every candidate was compared, but the "
                "scores are estimates, so near-ties can reorder"
                if selection.approx
                else None
            ),
        )
        scores = self._score_vector_rows(
            rows, query_vector, selection, min_score=min_score
        )
        # Rows are walked in index order (not score order) so the sort below
        # sees exactly the input ordering the pre-11.1.0 inline loop produced:
        # a stable sort makes that the tie-break of last resort.
        scored = [
            self._vector_match(row, scores[str(row["item_id"])])
            for row in rows
            if str(row["item_id"]) in scores
        ]
        scored.sort(
            key=lambda item: (item["score"], item.get("updated_at") or ""), reverse=True
        )
        return {
            "query": query,
            "embedding_model": self._embedding_model.model_id,
            "embedding_dim": self._embedding_model.dim,
            "matches": scored[:limit],
            "recall": recall,
            "index": selection.as_dict(),
        }

    def _iter_vector_index_items(
        self, conn: sqlite3.Connection, model_id: str, dim: int
    ) -> Iterator[IndexItem]:
        """Every embedding for ``model_id``/``dim`` as index items."""
        for row in conn.execute(
            "SELECT item_id, embedding, embedding_dim FROM vector_embeddings "
            "WHERE embedding_model=? AND embedding_dim=? ORDER BY item_id ASC",
            (model_id, dim),
        ):
            yield (
                str(row["item_id"]),
                self._embedding_model.decode(row["embedding"], row["embedding_dim"]),
                {},
            )

    def _vector_rows_by_id(
        self, conn: sqlite3.Connection, item_ids: List[str]
    ) -> List[sqlite3.Row]:
        """Full match rows for the ids an ANN lookup returned."""
        if not item_ids:
            return []
        placeholders = ",".join("?" * len(item_ids))
        return conn.execute(
            self._VECTOR_ROW_SELECT + f" AND ve.item_id IN ({placeholders})",
            (self._embedding_model.model_id, self._embedding_model.dim, *item_ids),
        ).fetchall()

    def _hnsw_index(
        self,
        conn: sqlite3.Connection,
        fingerprint: str,
        model_id: str,
        dim: int,
    ) -> HnswIndex:
        """The live ANN graph for ``fingerprint`` — cache, sidecar, or rebuild.

        Held on the store for the process's lifetime, because reading a
        50 000-vector graph off disk costs roughly as much as the search it
        enables: paying it per query gave back most of the speedup (105 ms
        instead of 15 ms at 50k). The fingerprint — model, dimension, row
        count, newest ``indexed_at`` — is what makes the cache safe: any write
        to ``vector_embeddings`` changes it, and a changed fingerprint is
        never served from the cache or from the sidecar.
        """
        cached = getattr(self, "_hnsw_cached", None)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        index = HnswIndex(dim=dim)
        if not index.load(self.db_path, fingerprint=fingerprint):
            index.rebuild(self._iter_vector_index_items(conn, model_id, dim))
            index.save(self.db_path, fingerprint=fingerprint)
        self._hnsw_cached = (fingerprint, index)
        return index

    def _vector_search_ann(
        self,
        query: str,
        query_vector: List[float],
        selection: BackendSelection,
        *,
        limit: int,
        min_score: float,
        backend: str,
    ) -> Optional[Dict[str, Any]]:
        """Approximate top-k via the persisted HNSW sidecar.

        Two phases instead of one: ask the graph for ids, then read only those
        rows. That is where the speed comes from — the exact scan pays to
        decode every embedding on every query, and this pays it once per
        index generation.

        The sidecar is keyed by ``model:dim:rows:newest`` so any write to
        ``vector_embeddings`` invalidates it and the next search rebuilds.
        Returns ``None`` when the index is empty, which the caller answers
        with the ordinary (equally empty, but honestly reported) scan.
        """
        model_id = self._embedding_model.model_id
        dim = int(self._embedding_model.dim)
        with self._connect() as conn:
            head = conn.execute(
                "SELECT COUNT(*) AS c, MAX(indexed_at) AS newest FROM vector_embeddings "
                "WHERE embedding_model=? AND embedding_dim=?",
                (model_id, dim),
            ).fetchone()
            candidates_total = int(head["c"])
            if candidates_total == 0:
                return None
            fingerprint = f"{model_id}:{dim}:{candidates_total}:{head['newest']}"
            index = self._hnsw_index(conn, fingerprint, model_id, dim)
            pairs = index.search(query_vector, limit, filter={"min_score": min_score})
            rows = {
                str(row["item_id"]): row
                for row in self._vector_rows_by_id(
                    conn, [item_id for item_id, _ in pairs]
                )
            }
        scored = [
            self._vector_match(rows[item_id], score)
            for item_id, score in pairs
            if item_id in rows
        ]
        scored.sort(
            key=lambda item: (item["score"], item.get("updated_at") or ""), reverse=True
        )
        return {
            "query": query,
            "embedding_model": model_id,
            "embedding_dim": dim,
            "matches": scored[:limit],
            "recall": self._recall_report(
                backend=backend,
                cap=None,
                candidates_total=candidates_total,
                candidates_scanned=candidates_total,
                approx_detail=(
                    "approximate nearest-neighbour search: the whole index is "
                    "reachable but the true top-k is not guaranteed — compare "
                    "with scripts/bench_vector_index.py"
                ),
            ),
            "index": {**selection.as_dict(), "sidecar": index.loaded_from_sidecar},
        }

    def vector_search(
        self,
        query: str,
        *,
        limit: int = 30,
        min_score: float = 0.0,
        max_candidates: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Cosine search over the vector index (exact by default).

        ``max_candidates`` bounds how many indexed rows are scored; ``None``
        (the default) resolves it from ``LATTICEAI_VECTOR_MAX_CANDIDATES``
        (default 10 000), and ``0`` or a negative value scans the whole index.
        When the cap bites, the rows kept are the most recently indexed ones —
        recency, not similarity — so the result is *partial recall*. That is
        reported in the additive ``recall`` block
        (``{backend, max_candidates, candidates_total, candidates_scanned,
        truncated, detail}``) instead of being hidden, and callers/UIs are
        expected to surface ``recall.truncated``.

        v11.1.0: the scoring itself now lives in
        :mod:`lattice_brain.graph.vector_index`. ``LATTICEAI_VECTOR_INDEX``
        picks the backend — ``brute`` (default, exact, byte-compatible with
        every previous release), ``quantized`` (int8, exhaustive, approximate
        scores) or ``hnsw`` (approximate nearest neighbour, needs the optional
        ``hnsw`` extra). The resolved backend and any fallback reason ride
        along in the additive ``index`` block, whose ``approx`` flag is the
        one bit a caller needs to know whether "not found" is a fact or an
        estimate. The empty-query early return is deliberately unchanged: no
        query means no index was consulted, so there is nothing to report.
        """
        query = str(query or "").strip()
        limit = max(1, min(int(limit or 30), 100))
        min_score = float(min_score or 0.0)
        cap = self._vector_candidate_cap(max_candidates, limit=limit)
        backend = self._vector_search_backend()
        if not query:
            return {
                "query": query,
                "matches": [],
                "recall": {
                    "backend": backend,
                    "max_candidates": cap,
                    "candidates_total": 0,
                    "candidates_scanned": 0,
                    "truncated": False,
                    "detail": None,
                },
            }
        selection = self._vector_index_selection()
        query_vector = self._embedding_model.embed(query)
        if selection.name == HNSW_BACKEND:
            try:
                approximate = self._vector_search_ann(
                    query,
                    query_vector,
                    selection,
                    limit=limit,
                    min_score=min_score,
                    backend=backend,
                )
            except Exception as exc:  # noqa: BLE001 — a broken ANN must not lose the answer
                logging.warning("hnsw vector search failed: %s", exc)
                selection = dataclasses.replace(
                    resolve_vector_index(DEFAULT_VECTOR_INDEX),
                    requested=HNSW_BACKEND,
                    detail=f"hnsw search failed ({exc}); used the exact scan instead",
                )
                backend = selection.backend
            else:
                if approximate is not None:
                    return approximate
        return self._vector_search_scan(
            query,
            query_vector,
            selection,
            limit=limit,
            min_score=min_score,
            backend=backend,
            cap=cap,
        )
