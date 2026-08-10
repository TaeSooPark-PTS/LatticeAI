"""Durable pending-embed queue: ingest now, embed a moment later.

The write path already degrades honestly — when the incremental vector sync
fails, ``IngestionResult.indexing_status`` becomes ``"pending"`` and
``index_status()`` keeps the node visible as backlog. What was missing is
anyone whose job it is to *come back for it*. Until a human ran a rebuild,
"pending" meant "unsearchable, indefinitely".

This is that worker's memory, and it is on disk for the same reason
``ingestion_jobs`` is: a queue that lives in one process's heap forgets its
backlog on restart, which is exactly when a backlog exists. The table is
created lazily inside whichever SQLite file the brain already uses, so the
existing backup/restore covers it with no manifest change.

The queue is a pure service — schedule + tick, no threads, no server, no
event loop. :meth:`VectorEmbedQueue.tick_async` exists for callers that live
on the event loop and hands the blocking work to a thread.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from ...utils import utc_now_iso

#: Job lifecycle. ``failed`` is terminal — the retry budget is exhausted.
VECTOR_JOB_STATUSES = ("pending", "running", "done", "failed")
#: How many times one node may fail before the queue stops retrying it.
DEFAULT_MAX_ATTEMPTS = 3
#: Nodes claimed per :meth:`VectorEmbedQueue.tick`.
DEFAULT_TICK_LIMIT = 25

LOGGER = logging.getLogger(__name__)


class VectorJobStore:
    """SQLite persistence for the pending-embed backlog.

    Own connection per operation, always closed — ``with sqlite3.connect(...)``
    commits but never closes, which is how 70 leaks accumulated before 10.2.0.
    """

    def __init__(self, db_path: Any) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS vector_jobs (
                  node_id TEXT PRIMARY KEY,
                  status TEXT NOT NULL DEFAULT 'pending',
                  attempts INTEGER NOT NULL DEFAULT 0,
                  detail TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_vector_jobs_status
                  ON vector_jobs(status, created_at);
                """
            )

    def enqueue(self, node_id: str, *, detail: Optional[str] = None) -> bool:
        """Mark ``node_id`` pending. Re-queuing an existing row is expected."""
        node_id = str(node_id or "").strip()
        if not node_id:
            return False
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO vector_jobs(node_id, status, attempts, detail,
                                        created_at, updated_at)
                VALUES (?, 'pending', 0, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                  status='pending',
                  detail=excluded.detail,
                  updated_at=excluded.updated_at
                """,
                (node_id, detail, now, now),
            )
        return True

    def claim(self, limit: int) -> List[str]:
        """Take up to ``limit`` pending node ids and mark them running."""
        limit = max(1, int(limit))
        now = utc_now_iso()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT node_id FROM vector_jobs
                WHERE status='pending'
                ORDER BY created_at ASC, node_id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            claimed = [str(row["node_id"]) for row in rows]
            for node_id in claimed:
                conn.execute(
                    """
                    UPDATE vector_jobs
                    SET status='running', attempts=attempts+1, updated_at=?
                    WHERE node_id=?
                    """,
                    (now, node_id),
                )
        return claimed

    def finish(self, node_id: str, *, status: str, detail: Optional[str]) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE vector_jobs SET status=?, detail=?, updated_at=? "
                "WHERE node_id=?",
                (status, detail, utc_now_iso(), str(node_id)),
            )

    def attempts_for(self, node_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT attempts FROM vector_jobs WHERE node_id=?", (str(node_id),)
            ).fetchone()
        return int(row["attempts"]) if row is not None else 0

    def counts(self) -> Dict[str, int]:
        """``{status: count}`` with every known status present (zero-filled)."""
        counts = dict.fromkeys(VECTOR_JOB_STATUSES, 0)
        with self._connect() as conn:
            for row in conn.execute(
                "SELECT status, COUNT(*) AS total FROM vector_jobs GROUP BY status"
            ):
                counts[str(row["status"])] = int(row["total"])
        return counts


class VectorEmbedQueue:
    """Schedule nodes for background embedding, then drain them a tick at a time.

    ``indexer`` is normally ``KnowledgeGraphStore.index_node_incremental``: it
    returns a status dict and never raises. A queue built without a usable
    database is *disabled*, not silently in-memory — :meth:`describe` says so
    and :meth:`schedule` returns False, because a backlog that evaporates on
    restart is worse than an honest "not tracked".
    """

    def __init__(
        self,
        *,
        db_path: Optional[Any] = None,
        indexer: Optional[Callable[[str], Any]] = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        self._indexer = indexer
        self._max_attempts = max(1, int(max_attempts))
        self._store: Optional[VectorJobStore] = None
        self._detail: Optional[str] = None
        if db_path is None:
            self._detail = "no database configured; the embed backlog is not tracked"
        else:
            try:
                self._store = VectorJobStore(db_path)
            except Exception as exc:  # noqa: BLE001 — never block ingestion
                self._detail = f"vector job persistence unavailable: {exc}"
                LOGGER.warning("vector job persistence unavailable: %s", exc)

    @property
    def available(self) -> bool:
        return self._store is not None

    def describe(self) -> Dict[str, Any]:
        """Whether the backlog survives a restart, and where it lives."""
        return {
            "persistent": self._store is not None,
            "db_path": str(self._store.db_path) if self._store is not None else None,
            "detail": self._detail,
        }

    def schedule(self, node_id: str, *, detail: Optional[str] = None) -> bool:
        if self._store is None:
            return False
        try:
            return self._store.enqueue(node_id, detail=detail)
        except Exception as exc:  # noqa: BLE001 — queueing is best-effort
            LOGGER.warning("vector job %s could not be queued: %s", node_id, exc)
            return False

    def snapshot(self) -> Dict[str, int]:
        """Backlog counts by status (all zero when the queue is disabled)."""
        if self._store is None:
            return dict.fromkeys(VECTOR_JOB_STATUSES, 0)
        try:
            return self._store.counts()
        except Exception:  # noqa: BLE001 — a status read must never raise
            return dict.fromkeys(VECTOR_JOB_STATUSES, 0)

    def pending_count(self) -> int:
        """Nodes still owed an embedding (queued plus in flight)."""
        counts = self.snapshot()
        return int(counts["pending"]) + int(counts["running"])

    def tick(self, limit: int = DEFAULT_TICK_LIMIT) -> Dict[str, Any]:
        """Drain up to ``limit`` queued nodes. Never raises.

        A node whose indexing fails goes back to ``pending`` until its retry
        budget runs out, then stays ``failed`` with the last reason attached —
        visible backlog, not a silent retry loop.
        """
        summary: Dict[str, Any] = {
            "claimed": 0,
            "indexed": 0,
            "retried": 0,
            "failed": 0,
            "detail": self._detail,
        }
        store = self._store
        if store is None:
            return summary
        indexer = self._indexer
        if indexer is None:
            summary["detail"] = "no indexer configured; queued nodes were left pending"
            return summary
        claimed = store.claim(limit)
        summary["claimed"] = len(claimed)
        for node_id in claimed:
            status, detail = self._run_one(store, indexer, node_id)
            if status == "done":
                summary["indexed"] += 1
            elif status == "pending":
                summary["retried"] += 1
            else:
                summary["failed"] += 1
            store.finish(node_id, status=status, detail=detail)
        return summary

    async def tick_async(self, limit: int = DEFAULT_TICK_LIMIT) -> Dict[str, Any]:
        """:meth:`tick` off the event loop — embedding and SQLite both block."""
        return await asyncio.to_thread(self.tick, limit)

    def _run_one(
        self,
        store: VectorJobStore,
        indexer: Callable[[str], Any],
        node_id: str,
    ) -> Tuple[str, Optional[str]]:
        """Index one node → the status to persist and the reason for it."""
        try:
            outcome = indexer(node_id) or {}
        except Exception as exc:  # noqa: BLE001 — one bad node must not stop the drain
            return self._retry_or_fail(store, node_id, str(exc))
        if str(outcome.get("status") or "") == "failed":
            detail = str(outcome.get("detail") or "unknown error")
            return self._retry_or_fail(store, node_id, detail)
        return "done", None

    def _retry_or_fail(
        self, store: VectorJobStore, node_id: str, detail: str
    ) -> Tuple[str, Optional[str]]:
        attempts = store.attempts_for(node_id)
        if attempts >= self._max_attempts:
            return "failed", f"{detail} (gave up after {attempts} attempts)"
        return "pending", detail


__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_TICK_LIMIT",
    "VECTOR_JOB_STATUSES",
    "VectorEmbedQueue",
    "VectorJobStore",
]
