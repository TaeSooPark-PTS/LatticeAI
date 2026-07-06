from __future__ import annotations

# ruff: noqa: F403,F405

from ._kg_common import *  # noqa: F403,F401


class KnowledgeGraphVectorMixin:
    """Vector-embedding index build/status/search, split out of retrieval.

    Composed into KnowledgeGraphStore alongside KnowledgeGraphRetrievalMixin;
    both mixins share the same instance, so vector methods still reach sibling
    retrieval/write helpers (e.g. self._vector_text_for_node) through the MRO.
    """

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
        for item in source_items:
            vector_row = vector_rows.get(item["item_id"])
            expected_hash = _sha256_text(_clean_text(item["text"]))
            if not vector_row:
                missing += 1
            elif (
                vector_row["text_hash"] != expected_hash
                or vector_row["embedding_dim"] != self._embedding_model.dim
                or vector_row["embedding_model"] != self._embedding_model.model_id
            ):
                stale += 1
            else:
                ready += 1
        pending = missing + stale
        return {
            "status": "ready" if pending == 0 else "needs_reindex",
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
