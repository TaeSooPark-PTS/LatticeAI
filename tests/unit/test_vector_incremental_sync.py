"""Incremental vector-index sync tests.

Covers: index_node_incremental (restore missing embeddings for one node,
noop when fresh, skipped for unknown nodes, never raises on embedder
failure) and the IngestionPipeline auto-sync wiring (opt-out flag, env var,
duplicate skip, exception-safe pending downgrade — a vector failure must
NEVER fail the ingest).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph.store import KnowledgeGraphStore
from lattice_brain.ingestion import IngestionItem, IngestionPipeline


def _store(tmp_path: Path) -> KnowledgeGraphStore:
    return KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")


def _seed_node(store: KnowledgeGraphStore) -> str:
    result = store.ingest_source(
        source_type="note",
        title="Incremental Sync Note",
        text="Incremental vector sync embeds only the newly ingested node and its chunks.",
        source_uri="note:incremental",
    )
    return result["node_id"]


def test_index_node_incremental_restores_missing_embeddings(tmp_path):
    store = _store(tmp_path)
    node_id = _seed_node(store)
    with store._connect() as conn:
        conn.execute("DELETE FROM vector_embeddings")
    assert store.node_is_embedded(node_id) is False
    assert store.index_status()["status"] == "needs_reindex"

    outcome = store.index_node_incremental(node_id)

    assert outcome["status"] == "indexed"
    assert outcome["items_indexed"] >= 2  # node + at least one chunk
    assert store.node_is_embedded(node_id) is True


def test_index_node_incremental_is_noop_when_fresh(tmp_path):
    store = _store(tmp_path)
    node_id = _seed_node(store)
    outcome = store.index_node_incremental(node_id)
    assert outcome["status"] == "noop"
    assert outcome["items_indexed"] == 0
    assert outcome["items_skipped"] >= 1


def test_index_node_incremental_skips_unknown_or_empty_node(tmp_path):
    store = _store(tmp_path)
    assert store.index_node_incremental("node:does-not-exist")["status"] == "skipped"
    assert store.index_node_incremental("")["status"] == "skipped"


def test_index_node_incremental_never_raises_on_embedder_failure(tmp_path):
    store = _store(tmp_path)
    node_id = _seed_node(store)
    with store._connect() as conn:
        conn.execute("DELETE FROM vector_embeddings")

    class _BrokenEmbedder:
        model_id = "broken-model"
        dim = 8

        def embed(self, text):
            raise RuntimeError("provider offline")

        def encode(self, vector):
            raise RuntimeError("provider offline")

    store._embedding_model = _BrokenEmbedder()
    outcome = store.index_node_incremental(node_id)
    assert outcome["status"] == "failed"
    assert "provider offline" in (outcome["detail"] or "")
    # Backlog stays visible so a later rebuild picks it up.
    with store._connect() as conn:
        remaining = conn.execute("SELECT COUNT(*) AS c FROM vector_embeddings").fetchone()["c"]
    assert remaining == 0


def test_pipeline_marks_pending_when_vector_sync_fails(tmp_path):
    store = _store(tmp_path)
    pipe = IngestionPipeline(store)

    def _broken(node_id):
        raise RuntimeError("vector store offline")

    store.index_node_incremental = _broken
    res = pipe.ingest(IngestionItem(source_type="note", title="n", text="vector failure must not fail ingest"))

    assert res.status == "ok"          # the graph write landed
    assert res.node_id
    assert res.indexing_status == "pending"
    assert "vector index sync failed" in (res.detail or "")


def test_pipeline_marks_pending_when_sync_reports_failed(tmp_path):
    store = _store(tmp_path)
    pipe = IngestionPipeline(store)
    store.index_node_incremental = lambda node_id: {"status": "failed", "detail": "no embedder"}
    res = pipe.ingest(IngestionItem(source_type="note", title="n", text="failed sync downgrades to pending"))
    assert res.status == "ok"
    assert res.indexing_status == "pending"
    assert "no embedder" in (res.detail or "")


def test_pipeline_auto_vector_index_opt_out(tmp_path):
    store = _store(tmp_path)
    calls = []
    store.index_node_incremental = lambda node_id: calls.append(node_id) or {"status": "noop"}

    pipe = IngestionPipeline(store, auto_vector_index=False)
    res = pipe.ingest(IngestionItem(source_type="note", title="n", text="opt-out body"))
    assert res.status == "ok"
    assert res.indexing_status == "indexed"
    assert calls == []


def test_pipeline_env_var_disables_auto_sync(tmp_path, monkeypatch):
    monkeypatch.setenv("LATTICEAI_AUTO_VECTOR_INDEX", "0")
    store = _store(tmp_path)
    calls = []
    store.index_node_incremental = lambda node_id: calls.append(node_id) or {"status": "noop"}
    pipe = IngestionPipeline(store)
    res = pipe.ingest(IngestionItem(source_type="note", title="n", text="env opt-out body"))
    assert res.status == "ok"
    assert calls == []


def test_pipeline_skips_sync_for_duplicate_ingest(tmp_path):
    store = _store(tmp_path)
    calls = []
    store.index_node_incremental = lambda node_id: calls.append(node_id) or {"status": "noop"}
    pipe = IngestionPipeline(store)
    item = IngestionItem(source_type="note", title="n", text="duplicate sync skip body")

    first = pipe.ingest(item)
    second = pipe.ingest(item)

    assert first.duplicate is False and second.duplicate is True
    assert len(calls) == 1  # only the non-duplicate ingest triggered a sync


def test_pipeline_auto_sync_runs_by_default(tmp_path):
    store = _store(tmp_path)
    pipe = IngestionPipeline(store)
    res = pipe.ingest(IngestionItem(source_type="note", title="n", text="default auto sync body"))
    assert res.status == "ok"
    assert res.indexing_status == "indexed"
    assert res.embedded is True
    assert store.index_status()["status"] == "ready"
