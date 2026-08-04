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


# ── incremental rebuild: cost proportional to what changed ──────────────


class _CountingEmbedder:
    """Wraps the real embedder and records every text it is asked to encode.

    A proxy rather than a monkeypatch: ``LocalEmbeddingModel`` is a frozen
    dataclass, so its methods cannot be replaced in place.
    """

    def __init__(self, inner):
        self._inner = inner
        self.calls: list[str] = []

    @property
    def dim(self):
        return self._inner.dim

    @property
    def model_id(self):
        return self._inner.model_id

    def embed(self, text):
        self.calls.append(text)
        return self._inner.embed(text)

    def encode(self, vector):
        return self._inner.encode(vector)


def _embed_calls(store: KnowledgeGraphStore) -> list[str]:
    """Swap in the counting proxy and hand back its (live) call list."""
    proxy = _CountingEmbedder(store._embedding_model)
    store._embedding_model = proxy  # type: ignore[assignment]
    return proxy.calls


def test_incremental_rebuild_embeds_nothing_when_nothing_changed(tmp_path):
    """The whole point of `full=False`: an unchanged corpus costs zero embeddings.

    The skip already worked, but it cost one `SELECT` per node and per chunk to
    discover. The assertion here is on the outcome that matters to a user — a
    re-run of a settled index does no embedding work — and on the counters
    being honest about it.
    """
    store = _store(tmp_path)
    _seed_node(store)
    store.rebuild_vector_index(full=True)

    calls = _embed_calls(store)
    result = store.rebuild_vector_index(full=False)

    assert calls == []
    assert result["status"] == "completed"
    assert result["items_indexed"] == 0
    assert result["items_skipped"] == result["items_total"] > 0


def test_incremental_rebuild_embeds_only_the_changed_node(tmp_path):
    store = _store(tmp_path)
    _seed_node(store)
    store.rebuild_vector_index(full=True)

    store.ingest_source(
        source_type="note",
        title="Second Note",
        text="A different note that has never been embedded before.",
        source_uri="note:second",
    )
    # Ingest auto-syncs the new node, so clear just its rows to model the case
    # a rebuild exists for: content present in the graph, absent from the index.
    with store._connect() as conn:
        conn.execute(
            "DELETE FROM vector_embeddings WHERE item_id IN "
            "(SELECT id FROM nodes WHERE title='Second Note')"
        )

    calls = _embed_calls(store)
    result = store.rebuild_vector_index(full=False)

    assert result["items_indexed"] >= 1
    assert result["items_skipped"] >= 1
    # Only the missing node was embedded — the settled rows were not touched.
    assert len(calls) == result["items_indexed"]
    assert all("different note" in text.lower() or "Second Note" in text for text in calls)


def test_full_rebuild_re_embeds_everything(tmp_path):
    """`full=True` must stay a real rebuild — the skip path must not leak into it."""
    store = _store(tmp_path)
    _seed_node(store)
    store.rebuild_vector_index(full=True)

    calls = _embed_calls(store)
    result = store.rebuild_vector_index(full=True)

    assert result["items_indexed"] == result["items_total"] > 0
    assert result["items_skipped"] == 0
    assert len(calls) == result["items_total"]


def test_source_items_stream_instead_of_materialising(tmp_path):
    """A rebuild must not hold the whole corpus in memory to decide what to skip."""
    import inspect

    store = _store(tmp_path)
    _seed_node(store)
    assert inspect.isgeneratorfunction(type(store)._iter_vector_source_items)
    with store._connect() as conn:
        items = type(store)._iter_vector_source_items(store, conn)
        assert inspect.isgenerator(items)
        assert next(iter(items))["item_id"]
