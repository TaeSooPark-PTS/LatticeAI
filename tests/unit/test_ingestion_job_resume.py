"""An interrupted import must resume, not restart or vanish.

Review 2026-08: the ingestion queue lived only in this process's heap, so a
restart mid-import silently lost the resume state — the user saw a job simply
disappear, and re-running redid work already done. These tests pin the
persistence contract that replaced it: what survives a restart, what a job
that died mid-run reports itself as, and that a corrupt row cannot hide the
healthy ones.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from lattice_brain.ingestion import IngestionItem
from lattice_brain.ingestion_jobs import (
    BackgroundIngestionJob,
    IngestionJobStore,
    _item_from_payload,
    _item_payload,
)


def _item(title: str) -> IngestionItem:
    return IngestionItem(source_type="note", title=title, text=f"body of {title}")


def _job(job_id: str = "job-1", count: int = 3) -> BackgroundIngestionJob:
    items = [_item(f"note-{i}") for i in range(count)]
    return BackgroundIngestionJob(job_id=job_id, items=items, total=len(items))


@pytest.fixture()
def store(tmp_path) -> IngestionJobStore:
    return IngestionJobStore(tmp_path / "jobs.sqlite")


class TestRoundTrip:
    def test_saved_job_survives_a_new_store_on_the_same_file(self, tmp_path):
        """The point of persistence: a *different* process sees the job."""
        path = tmp_path / "jobs.sqlite"
        job = _job()
        job.processed = 1
        job.done_indices = {0}
        IngestionJobStore(path).save(job)

        # A fresh store stands in for a restarted process.
        loaded = IngestionJobStore(path).load_all()
        assert [j.job_id for j in loaded] == ["job-1"]
        assert loaded[0].processed == 1
        assert loaded[0].done_indices == {0}

    def test_items_survive_so_the_remaining_work_is_known(self, store):
        job = _job(count=3)
        job.done_indices = {0, 2}
        store.save(job)

        loaded = store.load_all()[0]
        assert len(loaded.items) == 3
        # This is the whole reason items are persisted: knowing what is left.
        assert loaded.remaining_indices() == [1]
        assert [i.title for i in loaded.items] == ["note-0", "note-1", "note-2"]

    def test_errors_and_counters_survive(self, store):
        job = _job()
        job.record_error(1, job.items[1], "boom")
        job.failed = 1
        store.save(job)

        loaded = store.load_all()[0]
        assert loaded.failed == 1
        assert len(loaded.errors) == 1
        assert loaded.errors[0]["index"] == 1
        assert "boom" in loaded.errors[0]["detail"]

    def test_save_is_idempotent_on_job_id(self, store):
        job = _job()
        store.save(job)
        job.processed = 2
        job.done_indices = {0, 1}
        store.save(job)

        loaded = store.load_all()
        assert len(loaded) == 1, "same job_id must update, not duplicate"
        assert loaded[0].processed == 2


class TestCrashRecovery:
    def test_job_left_running_does_not_come_back_as_running(self, store):
        """A process that died mid-run leaves `running` in the table.

        Reporting that verbatim would be a lie — nothing is running — and it
        would also make the runner refuse to touch the job.
        """
        job = _job()
        job.status = "running"
        job.processed = 1
        job.done_indices = {0}
        store.save(job)

        loaded = store.load_all()[0]
        assert loaded.status != "running"
        assert loaded.status in ("partial", "queued")
        assert loaded.remaining_indices() == [1, 2], "resume point must be preserved"

    def test_job_that_died_before_any_progress_is_queued_not_partial(self, store):
        job = _job()
        job.status = "running"
        store.save(job)

        loaded = store.load_all()[0]
        assert loaded.status == "queued"

    def test_terminal_statuses_are_left_alone(self, store):
        for status in ("completed", "failed", "partial"):
            job = _job(job_id=f"job-{status}")
            job.status = status
            store.save(job)

        by_id = {j.job_id: j for j in store.load_all()}
        assert by_id["job-completed"].status == "completed"
        assert by_id["job-failed"].status == "failed"
        assert by_id["job-partial"].status == "partial"


class TestDurability:
    def test_one_corrupt_row_does_not_hide_the_healthy_ones(self, store):
        store.save(_job(job_id="good-1"))
        store.save(_job(job_id="good-2"))
        with sqlite3.connect(str(store.db_path)) as conn:
            conn.execute(
                "UPDATE ingestion_jobs SET items_json = ? WHERE job_id = ?",
                ("{not json at all", "good-1"),
            )

        loaded = store.load_all()
        assert "good-2" in {j.job_id for j in loaded}

    def test_jobs_come_back_oldest_first(self, store):
        first = _job(job_id="older")
        first.created_at = "2026-01-01T00:00:00Z"
        second = _job(job_id="newer")
        second.created_at = "2026-06-01T00:00:00Z"
        store.save(second)
        store.save(first)

        assert [j.job_id for j in store.load_all()] == ["older", "newer"]

    def test_store_creates_its_parent_directory(self, tmp_path):
        nested = tmp_path / "does" / "not" / "exist" / "jobs.sqlite"
        IngestionJobStore(nested)
        assert nested.parent.is_dir()


class TestItemPayloadCompatibility:
    def test_item_round_trips(self):
        item = _item("hello")
        assert _item_from_payload(_item_payload(item)).title == "hello"

    def test_unknown_keys_from_an_older_build_are_dropped(self):
        """A job written by an older build must still resume on a newer one."""
        payload = _item_payload(_item("hello"))
        payload["a_field_that_no_longer_exists"] = "whatever"
        assert _item_from_payload(payload).title == "hello"

    def test_non_dict_metadata_is_normalised(self):
        payload = _item_payload(_item("hello"))
        payload["metadata"] = "not-a-dict"
        assert _item_from_payload(payload).metadata == {}

    def test_payload_is_json_serialisable(self):
        """It is stored as JSON — a non-serialisable field would fail at save."""
        json.dumps(_item_payload(_item("hello")), ensure_ascii=False, default=str)
