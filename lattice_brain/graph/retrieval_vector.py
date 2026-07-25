from __future__ import annotations

# ruff: noqa: F403,F405

from ._kg_common import *  # noqa: F403,F401


class KnowledgeGraphVectorMixin:
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

    def _iter_vector_source_items(
        self,
        conn: sqlite3.Connection,
        *,
        include_nodes: bool = True,
        include_chunks: bool = True,
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
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
                    items.append(
                        {
                            "item_id": row["id"],
                            "item_type": "node",
                            "source_node": row["id"],
                            "text": text,
                            "metadata": {"node_type": row["type"], **metadata},
                        }
                    )
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
                    items.append(
                        {
                            "item_id": row["id"],
                            "item_type": "chunk",
                            "source_node": row["id"],
                            "text": text,
                            "metadata": {
                                **metadata,
                                "parent_source_node": row["parent_source_node"],
                            },
                        }
                    )
        return items

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
                items = self._iter_vector_source_items(
                    conn,
                    include_nodes=include_nodes,
                    include_chunks=include_chunks,
                )
                indexed = skipped = 0
                for item in items:
                    changed = self._upsert_vector_item(conn, **item)
                    if changed:
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
                        len(items),
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
                "items_total": len(items),
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
            source_items = self._iter_vector_source_items(conn)
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

    def vector_search(
        self,
        query: str,
        *,
        limit: int = 30,
        min_score: float = 0.0,
        max_candidates: int = 10_000,
    ) -> Dict[str, Any]:
        query = str(query or "").strip()
        limit = max(1, min(int(limit or 30), 100))
        min_score = float(min_score or 0.0)
        if not query:
            return {"query": query, "matches": []}
        query_vector = self._embedding_model.embed(query)
        max_candidates = max(limit, min(int(max_candidates or 10_000), 50_000))
        with self._connect() as conn:
            rows = conn.execute(
                """
                    SELECT
                      ve.item_id, ve.item_type, ve.source_node, ve.embedding,
                      ve.embedding_dim, ve.embedding_model, ve.metadata_json AS vector_metadata,
                      n.type AS node_type, n.title AS node_title, n.summary AS node_summary,
                      n.metadata_json AS node_metadata, n.updated_at AS node_updated_at,
                      c.text AS chunk_text, c.source_node AS parent_node_id,
                      pn.type AS parent_type, pn.title AS parent_title,
                      pn.summary AS parent_summary, pn.metadata_json AS parent_metadata,
                      pn.updated_at AS parent_updated_at
                    FROM vector_embeddings ve
                    LEFT JOIN nodes n ON n.id=ve.source_node
                    LEFT JOIN chunks c ON c.id=ve.item_id
                    LEFT JOIN nodes pn ON pn.id=c.source_node
                    WHERE ve.embedding_model=? AND ve.embedding_dim=?
                    ORDER BY ve.indexed_at DESC
                    LIMIT ?
                    """,
                (
                    self._embedding_model.model_id,
                    self._embedding_model.dim,
                    max_candidates,
                ),
            ).fetchall()
        scored = []
        for row in rows:
            vector = self._embedding_model.decode(
                row["embedding"], row["embedding_dim"]
            )
            score = self._embedding_model.similarity(query_vector, vector)
            if score < min_score:
                continue
            is_chunk = row["item_type"] == "chunk"
            summary = (
                row["chunk_text"]
                if is_chunk and row["chunk_text"]
                else row["node_summary"]
            )
            parent_metadata = _safe_loads(row["parent_metadata"])
            node_metadata = _safe_loads(row["node_metadata"])
            scored.append(
                {
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
                    },
                    "updated_at": row["parent_updated_at"]
                    if is_chunk and row["parent_updated_at"]
                    else row["node_updated_at"],
                }
            )
        scored.sort(
            key=lambda item: (item["score"], item.get("updated_at") or ""), reverse=True
        )
        return {
            "query": query,
            "embedding_model": self._embedding_model.model_id,
            "embedding_dim": self._embedding_model.dim,
            "matches": scored[:limit],
        }
