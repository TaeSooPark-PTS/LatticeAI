"""Background ingestion jobs — queue + per-job progress (v9.9.6 extraction).

Split out of :mod:`lattice_brain.ingestion` (behaviour-preserving move, review
2026-07-27 P2 #8 "ingestion job/watch 분리"). The pipeline owns *what it means
to ingest one item*; this module owns *scheduling many of them and reporting
progress* — a genuinely separate concern with its own frozen wire schema
(``/api/ingestion/jobs*``) and its own resume semantics.

The seam is also where a real scheduler (thread pool, rq, celery) would plug
in without touching the pipeline.

Job state is **durable** (review 2026-08 P1 #3): ``done_indices`` used to live
only in this process's heap, so a restart mid-import silently lost the resume
point and re-ingesting meant replaying every item. :class:`IngestionJobStore`
persists the queue to SQLite — by default the same database file the knowledge
graph and :mod:`lattice_brain.conversations` use, so the existing
backup/restore covers it with no manifest change. A queue built without a
``db_path`` stays purely in-memory and says so through :meth:`describe`.

``IngestionItem`` is imported only for type checking: the pipeline module owns
that dataclass, and a runtime import here would be circular.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Optional, Set

from .utils import utc_now_iso

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .ingestion import IngestionItem


JOB_ERRORS_CAP = 50  # per-job error records kept (failed count keeps counting)

#: Statuses a job may hold on disk. Frozen wire schema of ``/api/ingestion/jobs*``.
JOB_STATUSES = ("queued", "running", "completed", "failed", "partial")


@dataclass
class BackgroundIngestionJob:
    """Job descriptor + progress state for background/incremental indexing.

    ``done_indices`` tracks per-item completion so an interrupted or partially
    failed job can be *resumed* from the remaining items instead of restarting.
    ``errors`` is capped at ``max_errors`` records; ``failed`` keeps counting.
    """
    job_id: str
    items: List[IngestionItem]
    status: str = "queued"  # queued | running | completed | failed | partial
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    processed: int = 0
    failed: int = 0
    total: int = 0
    errors: List[Dict[str, Any]] = field(default_factory=list)
    incremental: bool = True
    user_email: Optional[str] = None
    max_errors: int = JOB_ERRORS_CAP
    done_indices: Set[int] = field(default_factory=set)

    def touch(self) -> None:
        self.updated_at = utc_now_iso()

    def record_error(self, index: int, item: IngestionItem, detail: Any) -> None:
        self.failed += 1
        if len(self.errors) < self.max_errors:
            self.errors.append({
                "index": index,
                "source": item.source_uri or item.path or item.title or item.source_type,
                "detail": str(detail)[:500],
            })

    def remaining_indices(self) -> List[int]:
        return [i for i in range(len(self.items)) if i not in self.done_indices]

    def as_dict(self) -> Dict[str, Any]:
        """Frozen job schema consumed by ``/api/ingestion/jobs*``."""
        return {
            "job_id": self.job_id,
            "status": self.status,
            "total": self.total,
            "processed": self.processed,
            "failed": self.failed,
            "errors": list(self.errors),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _item_payload(item: IngestionItem) -> Dict[str, Any]:
    """One ingestion item as JSON-safe data (dataclass fields only)."""
    return dataclasses.asdict(item)


def _item_from_payload(payload: Dict[str, Any]) -> IngestionItem:
    """Rebuild an ``IngestionItem`` from persisted data.

    Unknown keys are dropped rather than raising: a job written by an older
    build must still resume on a newer one. Imported here (not at module
    scope) because ``lattice_brain.ingestion`` imports *this* module.
    """
    from .ingestion import IngestionItem as _Item

    known = {f.name for f in dataclasses.fields(_Item)}
    kwargs = {key: value for key, value in payload.items() if key in known}
    metadata = kwargs.get("metadata")
    if not isinstance(metadata, dict):
        kwargs["metadata"] = {}
    return _Item(**kwargs)


class IngestionJobStore:
    """SQLite persistence for :class:`BackgroundIngestionJob`.

    Own connection per operation, always closed — ``with sqlite3.connect(...)``
    commits but never closes (same note as
    :meth:`lattice_brain.conversations.ConversationStore._connect`).
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS ingestion_jobs (
                  job_id TEXT PRIMARY KEY,
                  status TEXT NOT NULL,
                  total INTEGER NOT NULL DEFAULT 0,
                  processed INTEGER NOT NULL DEFAULT 0,
                  failed INTEGER NOT NULL DEFAULT 0,
                  incremental INTEGER NOT NULL DEFAULT 1,
                  user_email TEXT,
                  max_errors INTEGER NOT NULL DEFAULT 50,
                  items_json TEXT NOT NULL DEFAULT '[]',
                  done_indices_json TEXT NOT NULL DEFAULT '[]',
                  errors_json TEXT NOT NULL DEFAULT '[]',
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_status
                  ON ingestion_jobs(status);
                CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_created
                  ON ingestion_jobs(created_at);
                """
            )

    def save(self, job: BackgroundIngestionJob) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO ingestion_jobs(
                  job_id, status, total, processed, failed, incremental, user_email,
                  max_errors, items_json, done_indices_json, errors_json,
                  created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                  status=excluded.status,
                  total=excluded.total,
                  processed=excluded.processed,
                  failed=excluded.failed,
                  incremental=excluded.incremental,
                  user_email=excluded.user_email,
                  max_errors=excluded.max_errors,
                  items_json=excluded.items_json,
                  done_indices_json=excluded.done_indices_json,
                  errors_json=excluded.errors_json,
                  updated_at=excluded.updated_at
                """,
                (
                    job.job_id,
                    job.status,
                    int(job.total),
                    int(job.processed),
                    int(job.failed),
                    1 if job.incremental else 0,
                    job.user_email,
                    int(job.max_errors),
                    json.dumps(
                        [_item_payload(item) for item in job.items],
                        ensure_ascii=False,
                        default=str,
                    ),
                    json.dumps(sorted(job.done_indices)),
                    json.dumps(list(job.errors), ensure_ascii=False, default=str),
                    job.created_at,
                    job.updated_at,
                ),
            )

    def load_all(self) -> List[BackgroundIngestionJob]:
        """Every persisted job, oldest first.

        A row still marked ``running`` means the process died mid-run. It is
        reported as ``partial``/``queued`` (whichever the recorded progress
        supports) so it is honestly *not* running and so
        ``run_background_job`` will pick it up instead of refusing.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ingestion_jobs ORDER BY created_at ASC, job_id ASC"
            ).fetchall()
        jobs: List[BackgroundIngestionJob] = []
        for row in rows:
            try:
                jobs.append(self._row_to_job(row))
            except Exception as exc:  # noqa: BLE001 — one bad row must not hide the rest
                logging.warning(
                    "ingestion job %s could not be restored: %s", row["job_id"], exc
                )
        return jobs

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> BackgroundIngestionJob:
        items = [
            _item_from_payload(payload)
            for payload in json.loads(row["items_json"] or "[]")
        ]
        done = {int(i) for i in json.loads(row["done_indices_json"] or "[]")}
        status = str(row["status"] or "queued")
        if status == "running":
            status = "partial" if done else "queued"
        return BackgroundIngestionJob(
            job_id=str(row["job_id"]),
            items=items,
            status=status,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            processed=len(done),
            failed=int(row["failed"] or 0),
            total=int(row["total"] or 0),
            errors=list(json.loads(row["errors_json"] or "[]")),
            incremental=bool(row["incremental"]),
            user_email=row["user_email"],
            max_errors=int(row["max_errors"] or JOB_ERRORS_CAP),
            done_indices=done,
        )


class BackgroundIngestionQueue:
    """Queue for background incremental ingestion, durable when given a db.

    For large corpus: this is the seam where a real scheduler / worker pool
    (celery, rq, or internal thread) can be plugged later without changing callers.
    Supports incremental (skip duplicates) vs force reindex.

    ``db_path`` makes job state survive a restart. The in-process dict stays
    the authority *within* a process (callers hold job references and mutate
    them), and every mutation is mirrored to SQLite through :meth:`save`.
    Without ``db_path`` — or when the database cannot be opened — the queue is
    memory-only and :meth:`describe` reports that instead of implying
    durability it does not have.
    """

    def __init__(self, db_path: Optional[Any] = None) -> None:
        self._jobs: Dict[str, BackgroundIngestionJob] = {}
        self._counter = 0
        self._lock = threading.RLock()
        self._store: Optional[IngestionJobStore] = None
        self._persistence_detail: Optional[str] = None
        if db_path is None:
            self._persistence_detail = "no database configured; job state is in-memory only"
        elif not isinstance(db_path, (str, Path)):
            self._persistence_detail = (
                f"unusable db_path {type(db_path).__name__}; job state is in-memory only"
            )
        else:
            try:
                self._store = IngestionJobStore(Path(db_path))
                for job in self._store.load_all():
                    self._jobs[job.job_id] = job
                    self._counter = max(self._counter, _job_sequence(job.job_id))
            except Exception as exc:  # noqa: BLE001 — degrade to memory, never block ingestion
                self._store = None
                self._persistence_detail = f"job persistence unavailable: {exc}"
                logging.warning("ingestion job persistence unavailable: %s", exc)

    # ── honesty surface ──────────────────────────────────────────────────────
    def describe(self) -> Dict[str, Any]:
        """Whether resume state actually survives a restart, and where."""
        return {
            "persistent": self._store is not None,
            "db_path": str(self._store.db_path) if self._store is not None else None,
            "jobs": len(self._jobs),
            "detail": self._persistence_detail,
        }

    def save(self, job: BackgroundIngestionJob) -> None:
        """Mirror a job's current state to disk (no-op when memory-only).

        A persistence failure degrades durability, never the run in progress:
        the item work already succeeded and must not be rolled back by a
        bookkeeping error.
        """
        if self._store is None:
            return
        try:
            self._store.save(job)
        except Exception as exc:  # noqa: BLE001 — durability is best-effort mid-run
            logging.warning("ingestion job %s could not be persisted: %s", job.job_id, exc)

    def schedule(
        self,
        items: List[IngestionItem],
        *,
        incremental: bool = True,
        user_email: Optional[str] = None,
    ) -> BackgroundIngestionJob:
        with self._lock:
            self._counter += 1
            job_id = f"bg_ingest_{self._counter:04d}"
        job = BackgroundIngestionJob(
            job_id=job_id,
            items=items,
            total=len(items),
            incremental=incremental,
            user_email=user_email,
        )
        # annotate items for downstream
        for it in job.items:
            # attach flag without breaking dataclass defaults (use metadata)
            it.metadata = {**it.metadata, "incremental": incremental, "bg_job": job_id}
        with self._lock:
            self._jobs[job_id] = job
        self.save(job)
        return job

    def get(self, job_id: str) -> Optional[BackgroundIngestionJob]:
        return self._jobs.get(job_id)

    def list_pending(self) -> List[BackgroundIngestionJob]:
        return [j for j in self._jobs.values() if j.status == "queued"]

    def list_recent(self, limit: int = 20) -> List[BackgroundIngestionJob]:
        """Most recent jobs first (insertion order is schedule order)."""
        limit = max(1, int(limit))
        with self._lock:
            jobs = list(self._jobs.values())
        return list(reversed(jobs))[:limit]


def _job_sequence(job_id: str) -> int:
    """The numeric suffix of ``bg_ingest_0007`` → 7 (0 when unparseable).

    Restored jobs must not have their ids handed out again after a restart.
    """
    _, _, suffix = str(job_id or "").rpartition("_")
    try:
        return int(suffix)
    except ValueError:
        return 0


__all__ = [
    "JOB_ERRORS_CAP",
    "JOB_STATUSES",
    "BackgroundIngestionJob",
    "BackgroundIngestionQueue",
    "IngestionJobStore",
]
