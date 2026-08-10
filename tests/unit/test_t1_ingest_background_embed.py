"""Ingest now, embed a moment later — the loop that closes "pending".

``indexing_status="pending"`` has always been honest and has never been
*resolved*: nothing came back for the node, so a transient embedder failure
meant permanently unsearchable content until someone ran a rebuild by hand.
The scenario test here is the whole feature: a failing inline sync leaves a
queued job, one background tick embeds it, and the content becomes findable
without any manual step.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph.store import KnowledgeGraphStore
from lattice_brain.ingestion import IngestionItem, IngestionPipeline


def _store(tmp_path: Path) -> KnowledgeGraphStore:
    return KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")


def test_a_transient_embed_failure_is_repaired_by_one_background_tick(tmp_path):
    store = _store(tmp_path)
    pipe = IngestionPipeline(store)
    real_index = store.index_node_incremental
    attempts: list[str] = []

    def _flaky(node_id):
        attempts.append(node_id)
        if len(attempts) == 1:
            raise RuntimeError("embedding provider offline")
        return real_index(node_id)

    store.index_node_incremental = _flaky

    result = pipe.ingest(
        IngestionItem(
            source_type="note",
            title="Deferred Note",
            text="Background embedding keeps ingested content searchable.",
        )
    )

    # The write landed; only the embedding is owed, and it is owed to someone.
    assert result.status == "ok"
    assert result.indexing_status == "pending"
    assert "queued for background embedding" in (result.detail or "")
    assert store.vector_freshness_breakdown()["queued"] == 1

    drained = pipe.drain_vector_queue()

    assert (drained["claimed"], drained["indexed"], drained["failed"]) == (1, 1, 0)
    assert store.vector_freshness_breakdown()["queued"] == 0
    assert store.index_status()["status"] == "ready"
    assert store.vector_search("background embedding searchable")["matches"]


def test_the_backlog_outlives_the_process_that_created_it(tmp_path):
    store = _store(tmp_path)
    store.index_node_incremental = lambda node_id: {"status": "failed", "detail": "no embedder"}
    IngestionPipeline(store).ingest(
        IngestionItem(source_type="note", title="n", text="durable backlog body")
    )

    reopened = KnowledgeGraphStore(store.db_path, store.blob_dir)
    assert reopened.vector_queue.pending_count() == 1


def test_a_store_without_a_queue_keeps_the_old_pending_behaviour(tmp_path, monkeypatch):
    monkeypatch.setattr(KnowledgeGraphStore, "vector_queue", None)
    store = _store(tmp_path)
    store.index_node_incremental = lambda node_id: {"status": "failed", "detail": "no embedder"}
    pipe = IngestionPipeline(store)

    result = pipe.ingest(
        IngestionItem(source_type="note", title="n", text="no queue on this store")
    )

    assert result.indexing_status == "pending"
    assert "no embedder" in (result.detail or "")
    assert "queued" not in (result.detail or "")
    drained = pipe.drain_vector_queue()
    assert drained["claimed"] == 0
    assert "no background vector queue" in drained["detail"]


def test_a_queue_that_refuses_the_job_never_fails_the_ingest(tmp_path, monkeypatch):
    class _AngryQueue:
        def schedule(self, node_id, *, detail=None):
            raise RuntimeError("disk full")

    monkeypatch.setattr(KnowledgeGraphStore, "vector_queue", _AngryQueue())
    store = _store(tmp_path)
    store.index_node_incremental = lambda node_id: {"status": "failed", "detail": "no embedder"}

    result = IngestionPipeline(store).ingest(
        IngestionItem(source_type="note", title="n", text="queue refuses the job")
    )

    assert result.status == "ok"
    assert result.indexing_status == "pending"
    assert "queued for background embedding" not in (result.detail or "")
