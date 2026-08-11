"""Scheduling many items, and running (or resuming) what was scheduled.

The pipeline owns "ingest one item"; ``BackgroundIngestionQueue`` owns
"schedule many and report progress". This mixin is the seam between them:
per-item errors are recorded and never abort a job, progress is checkpointed
after every item, and the same method powers both the first run and a resume
because already-completed items are simply skipped.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..ingestion_jobs import BackgroundIngestionJob
from ._contract import IngestionCore as _Core
from .models import IngestionItem


class IngestionJobsMixin(_Core):
    """Background job scheduling. Mixed into ``IngestionPipeline``."""

    # --- Large candidate #1: background / incremental scheduling (slice) ---
    def schedule_background(
        self,
        items: List[IngestionItem],
        *,
        incremental: bool = True,
        user_email: Optional[str] = None,
    ) -> BackgroundIngestionJob:
        """Schedule items for background incremental indexing.

        Returns a job handle. Actual execution can be driven by caller
        (or future worker) calling pipeline.ingest on each — or through
        :meth:`run_background_job`. This seam enables large-corpus scale
        without blocking user requests.
        """
        job = self._bg_queue.schedule(items, incremental=incremental, user_email=user_email)
        # mark initial status on results concept (jobs track)
        return job

    def get_background_job(self, job_id: str) -> Optional[BackgroundIngestionJob]:
        return self._bg_queue.get(job_id)

    def list_background_jobs(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Recent jobs (newest first) in the frozen ``/api/ingestion`` schema."""
        return [job.as_dict() for job in self._bg_queue.list_recent(limit=limit)]

    def run_background_job(
        self, job_id: str, *, user_email: Optional[str] = None
    ) -> Dict[str, Any]:
        """Execute a queued/interrupted job's remaining items.

        Per-item errors are recorded (capped) and never abort the job. The
        final status is ``completed`` (all done), ``partial`` (some done),
        or ``failed`` (nothing done). Already-completed items are skipped, so
        the same method safely powers both first-run and resume.
        """
        job = self._bg_queue.get(job_id)
        if job is None:
            return {"status": "not_found", "job_id": job_id}
        if job.status == "running":
            return job.as_dict()
        return self._execute_background_job(job, user_email=user_email)

    def resume_background_job(
        self, job_id: str, *, user_email: Optional[str] = None
    ) -> Dict[str, Any]:
        """Resume an interrupted/partial/failed job from its remaining items."""
        return self.run_background_job(job_id, user_email=user_email)

    def _execute_background_job(
        self, job: BackgroundIngestionJob, *, user_email: Optional[str] = None
    ) -> Dict[str, Any]:
        job.status = "running"
        # Retried items get a fresh verdict: reset failure state for this run.
        job.failed = 0
        job.errors = []
        job.touch()
        self._bg_queue.save(job)
        runner_email = user_email or job.user_email
        for index in job.remaining_indices():
            item = job.items[index]
            try:
                result = self.ingest(item, user_email=runner_email or item.owner)
                status, detail = result.status, result.detail
            except Exception as exc:  # noqa: BLE001 — per-item isolation: keep going
                status, detail = "failed", str(exc)
            if status == "ok":
                job.done_indices.add(index)
            else:
                job.record_error(index, item, detail or status)
            job.processed = len(job.done_indices)
            job.touch()
            # Checkpoint per item: a crash here must cost at most the item in
            # flight, never the whole job's progress. One small UPDATE against
            # an ingest (parse + chunk + embed) is noise.
            self._bg_queue.save(job)
        job.processed = len(job.done_indices)
        if job.total == 0 or job.processed >= job.total:
            job.status = "completed"
        elif job.processed > 0:
            job.status = "partial"
        else:
            job.status = "failed"
        job.touch()
        self._bg_queue.save(job)
        return job.as_dict()
