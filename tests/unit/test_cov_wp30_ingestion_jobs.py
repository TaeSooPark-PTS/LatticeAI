"""wp30 coverage — background ingestion queue durability honesty.

The queue promises resume-after-restart only when it actually has a database.
Every other case has to say so through ``describe()`` rather than implying a
durability it does not have: no db, an unusable ``db_path`` type, a database
that cannot be opened, and a mid-run persistence failure that must degrade
durability without touching the work already done.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.ingestion import IngestionItem
from lattice_brain.ingestion_jobs import (
    BackgroundIngestionQueue,
    IngestionJobStore,
    _job_sequence,
)


def _items(*titles: str):
    return [IngestionItem(source_type="note", title=title, text=f"{title} body") for title in titles]


def test_memory_only_queue_says_so_and_saving_is_a_no_op():
    queue = BackgroundIngestionQueue()
    job = queue.schedule(_items("A"))

    described = queue.describe()
    assert described == {
        "persistent": False,
        "db_path": None,
        "jobs": 1,
        "detail": "no database configured; job state is in-memory only",
    }
    queue.save(job)  # no store: nothing to mirror, nothing to fail
    assert queue.list_pending() == [job]
    assert queue.list_recent()[0].job_id == job.job_id


def test_an_unusable_db_path_type_degrades_to_memory():
    queue = BackgroundIngestionQueue(db_path=12345)
    described = queue.describe()
    assert described["persistent"] is False
    assert described["db_path"] is None
    assert described["detail"] == "unusable db_path int; job state is in-memory only"


def test_an_unopenable_database_degrades_to_memory_and_warns(tmp_path, caplog):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a directory should be", encoding="utf-8")

    with caplog.at_level("WARNING"):
        queue = BackgroundIngestionQueue(db_path=blocker / "jobs.sqlite")

    assert queue.describe()["persistent"] is False
    assert queue.describe()["detail"].startswith("job persistence unavailable:")
    assert "ingestion job persistence unavailable" in " ".join(
        record.getMessage() for record in caplog.records
    )
    # It still schedules; only durability was lost.
    assert queue.schedule(_items("A")).job_id == "bg_ingest_0001"


def test_jobs_and_the_id_counter_survive_a_restart(tmp_path):
    db_path = tmp_path / "brain" / "kg.sqlite"
    first = BackgroundIngestionQueue(db_path=db_path)
    original = first.schedule(_items("A", "B"), user_email="u@x")
    original.done_indices.add(0)
    original.processed = 1
    original.status = "running"
    first.save(original)

    restarted = BackgroundIngestionQueue(db_path=db_path)
    restored = restarted.get(original.job_id)

    assert restored is not None
    # A row still marked "running" means the process died mid-run.
    assert restored.status == "partial"
    assert restored.remaining_indices() == [1]
    assert [item.title for item in restored.items] == ["A", "B"]
    assert restarted.describe() == {
        "persistent": True,
        "db_path": str(db_path),
        "jobs": 1,
        "detail": None,
    }
    # The restored id must not be handed out again.
    assert restarted.schedule(_items("C")).job_id == "bg_ingest_0002"


def test_a_persistence_failure_mid_run_is_logged_not_raised(tmp_path, caplog):
    queue = BackgroundIngestionQueue(db_path=tmp_path / "kg.sqlite")
    job = queue.schedule(_items("A"))

    def _boom(job):
        raise RuntimeError("database is locked")

    queue._store.save = _boom  # type: ignore[method-assign]
    with caplog.at_level("WARNING"):
        queue.save(job)

    assert f"ingestion job {job.job_id} could not be persisted" in " ".join(
        record.getMessage() for record in caplog.records
    )


def test_one_unrestorable_row_never_hides_the_rest(tmp_path, caplog):
    db_path = tmp_path / "kg.sqlite"
    store = IngestionJobStore(db_path)
    good = BackgroundIngestionQueue(db_path=db_path).schedule(_items("A"))
    with store._connect() as conn:
        conn.execute(
            "INSERT INTO ingestion_jobs(job_id, status, items_json, created_at, updated_at)"
            " VALUES ('bg_ingest_0009', 'queued', '{not json', '2020-01-01', '2020-01-01')"
        )

    with caplog.at_level("WARNING"):
        jobs = store.load_all()

    assert [job.job_id for job in jobs] == [good.job_id]
    assert "bg_ingest_0009 could not be restored" in " ".join(
        record.getMessage() for record in caplog.records
    )


@pytest.mark.parametrize(
    ("job_id", "expected"),
    [("bg_ingest_0007", 7), ("bg_ingest_12", 12), ("not-a-job", 0), ("", 0)],
)
def test_job_sequence_reads_the_numeric_suffix(job_id, expected):
    assert _job_sequence(job_id) == expected
