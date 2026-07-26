"""Background ingestion jobs — queue + per-job progress (v9.9.6 extraction).

Split out of :mod:`lattice_brain.ingestion` (behaviour-preserving move, review
2026-07-27 P2 #8 "ingestion job/watch 분리"). The pipeline owns *what it means
to ingest one item*; this module owns *scheduling many of them and reporting
progress* — a genuinely separate concern with its own frozen wire schema
(``/api/ingestion/jobs*``) and its own resume semantics.

The seam is also where a real scheduler (thread pool, rq, celery) would plug
in without touching the pipeline.

``IngestionItem`` is imported only for type checking: the pipeline module owns
that dataclass, and a runtime import here would be circular.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

from .utils import utc_now_iso

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .ingestion import IngestionItem


JOB_ERRORS_CAP = 50  # per-job error records kept (failed count keeps counting)


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


class BackgroundIngestionQueue:
    """Simple in-memory queue for background incremental ingestion.

    For large corpus: this is the seam where a real scheduler / worker pool
    (celery, rq, or internal thread) can be plugged later without changing callers.
    Supports incremental (skip duplicates) vs force reindex.
    """
    def __init__(self) -> None:
        self._jobs: Dict[str, BackgroundIngestionJob] = {}
        self._counter = 0
        self._lock = threading.Lock()

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


__all__ = ["JOB_ERRORS_CAP", "BackgroundIngestionJob", "BackgroundIngestionQueue"]
