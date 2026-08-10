"""The durable pending-embed backlog.

What is being pinned is the difference between "we noticed" and "we came
back": a node whose inline embedding failed must survive a restart as queued
work, get retried a bounded number of times, and then stay visibly failed
rather than looping forever or quietly vanishing.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph.vector_index import (
    VECTOR_JOB_STATUSES,
    VectorEmbedQueue,
    VectorJobStore,
)


def _queue(tmp_path: Path, indexer=None, **kwargs) -> VectorEmbedQueue:
    return VectorEmbedQueue(
        db_path=tmp_path / "brain" / "kg.sqlite", indexer=indexer, **kwargs
    )


# ── persistence ──────────────────────────────────────────────────────────────


def test_the_backlog_survives_a_restart(tmp_path):
    db_path = tmp_path / "kg.sqlite"
    VectorEmbedQueue(db_path=db_path).schedule("node:a", detail="embedder offline")

    restarted = VectorEmbedQueue(db_path=db_path)
    assert restarted.pending_count() == 1
    assert restarted.snapshot()["pending"] == 1
    assert restarted.describe() == {
        "persistent": True,
        "db_path": str(db_path),
        "detail": None,
    }


def test_a_queue_without_a_database_is_disabled_not_silently_forgetful():
    queue = VectorEmbedQueue()
    assert queue.available is False
    assert queue.schedule("node:a") is False
    assert queue.snapshot() == dict.fromkeys(VECTOR_JOB_STATUSES, 0)
    assert queue.pending_count() == 0
    described = queue.describe()
    assert described["persistent"] is False and described["db_path"] is None
    assert "not tracked" in described["detail"]
    # Nothing to drain, and it says why rather than pretending it drained.
    assert queue.tick()["claimed"] == 0


def test_an_unusable_database_degrades_to_disabled(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory", encoding="utf-8")
    queue = VectorEmbedQueue(db_path=blocker / "nested" / "kg.sqlite")
    assert queue.available is False
    assert "persistence unavailable" in queue.describe()["detail"]


def test_scheduling_an_empty_node_id_is_refused(tmp_path):
    assert _queue(tmp_path).schedule("   ") is False


def test_a_queueing_failure_never_propagates(tmp_path, monkeypatch):
    queue = _queue(tmp_path)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(queue._store, "enqueue", _boom)
    assert queue.schedule("node:a") is False


def test_a_broken_counts_read_reports_zeros_rather_than_raising(tmp_path, monkeypatch):
    queue = _queue(tmp_path)

    def _boom():
        raise RuntimeError("table gone")

    monkeypatch.setattr(queue._store, "counts", _boom)
    assert queue.snapshot() == dict.fromkeys(VECTOR_JOB_STATUSES, 0)


# ── draining ─────────────────────────────────────────────────────────────────


def test_a_tick_indexes_the_backlog_and_empties_it(tmp_path):
    indexed: list[str] = []
    queue = _queue(
        tmp_path,
        indexer=lambda node_id: indexed.append(node_id) or {"status": "indexed"},
    )
    queue.schedule("node:a")
    queue.schedule("node:b")

    summary = queue.tick()

    assert indexed == ["node:a", "node:b"]
    assert (summary["claimed"], summary["indexed"], summary["failed"]) == (2, 2, 0)
    assert queue.pending_count() == 0
    assert queue.snapshot()["done"] == 2


def test_a_tick_claims_at_most_the_requested_limit(tmp_path):
    queue = _queue(tmp_path, indexer=lambda node_id: {"status": "noop"})
    for index in range(3):
        queue.schedule(f"node:{index}")
    assert queue.tick(limit=2)["claimed"] == 2
    assert queue.pending_count() == 1


def test_a_queue_without_an_indexer_leaves_the_backlog_alone(tmp_path):
    queue = _queue(tmp_path)
    queue.schedule("node:a")
    summary = queue.tick()
    assert summary["claimed"] == 0
    assert "no indexer configured" in summary["detail"]
    assert queue.pending_count() == 1


def test_a_failing_node_is_retried_until_its_budget_runs_out(tmp_path):
    attempts: list[str] = []

    def _always_fails(node_id):
        attempts.append(node_id)
        return {"status": "failed", "detail": "no embedder"}

    queue = _queue(tmp_path, indexer=_always_fails, max_attempts=2)
    queue.schedule("node:a")

    first = queue.tick()
    assert (first["retried"], first["failed"]) == (1, 0)
    assert queue.pending_count() == 1

    second = queue.tick()
    assert (second["retried"], second["failed"]) == (0, 1)
    assert attempts == ["node:a", "node:a"]
    # Terminal, and still visible — not retried forever, not forgotten.
    assert queue.pending_count() == 0
    assert queue.snapshot()["failed"] == 1
    assert queue._store.attempts_for("node:a") == 2


def test_an_exploding_indexer_is_treated_as_a_failure_not_a_crash(tmp_path):
    def _boom(node_id):
        raise RuntimeError("embedding provider down")

    queue = _queue(tmp_path, indexer=_boom, max_attempts=1)
    queue.schedule("node:a")
    summary = queue.tick()
    assert summary["failed"] == 1
    assert queue.snapshot()["failed"] == 1


def test_an_indexer_returning_nothing_counts_as_done(tmp_path):
    queue = _queue(tmp_path, indexer=lambda node_id: None)
    queue.schedule("node:a")
    assert queue.tick()["indexed"] == 1


def test_tick_async_runs_the_same_drain_off_the_event_loop(tmp_path):
    queue = _queue(tmp_path, indexer=lambda node_id: {"status": "indexed"})
    queue.schedule("node:a")
    summary = asyncio.run(queue.tick_async())
    assert summary["indexed"] == 1


# ── store internals ──────────────────────────────────────────────────────────


def test_requeueing_a_finished_node_makes_it_pending_again(tmp_path):
    store = VectorJobStore(tmp_path / "kg.sqlite")
    store.enqueue("node:a", detail="first")
    store.claim(10)
    store.finish("node:a", status="done", detail=None)
    assert store.counts()["done"] == 1

    store.enqueue("node:a", detail="text changed")
    assert store.counts()["pending"] == 1
    # Attempts are cumulative across requeues: a node that keeps coming back
    # keeps its history rather than resetting its budget every time.
    assert store.attempts_for("node:a") == 1


def test_attempts_for_an_unknown_node_is_zero(tmp_path):
    assert VectorJobStore(tmp_path / "kg.sqlite").attempts_for("node:missing") == 0


def test_counts_zero_fill_every_known_status(tmp_path):
    counts = VectorJobStore(tmp_path / "kg.sqlite").counts()
    assert counts == dict.fromkeys(VECTOR_JOB_STATUSES, 0)


@pytest.mark.parametrize("limit", [0, -5])
def test_claim_never_asks_sqlite_for_a_nonpositive_limit(tmp_path, limit):
    store = VectorJobStore(tmp_path / "kg.sqlite")
    store.enqueue("node:a")
    assert store.claim(limit) == ["node:a"]
