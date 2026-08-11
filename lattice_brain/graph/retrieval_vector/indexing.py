"""Building the vector index: incremental upserts and full rebuilds.

Moved verbatim out of ``retrieval_vector.py`` (v11.3.0 decomposition).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# ruff: noqa: F403,F405
from .._kg_common import *  # noqa: F403,F401

# Typing-only base (runtime value is `object`, so the store's MRO is
# unchanged). Every build path records which embedder produced the rows, so
# this half calls the fingerprint half through `self`; naming it as the base
# states that assumption and carries the store contract
# (`_connect`, `_upsert_node`, …) along with it.
if TYPE_CHECKING:
    from .fingerprint import _VectorFingerprintMixin as _Core
else:
    _Core = object


class _VectorIndexingMixin(_Core):
    """Vector index build/refresh. Composed into the public mixin."""

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
