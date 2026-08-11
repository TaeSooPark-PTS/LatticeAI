"""``vector_search`` over the pluggable index layer.

The migration's contract is that the default path did not change: same rows,
same scores, same ordering, same ``recall`` block. What is new is that the
backend is selectable and that the result says which one answered. These
tests pin both halves — parity for ``brute``, and an honest ``index`` block
(plus a real two-phase lookup) for the others.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# ``VECTOR_SCAN_BATCH`` is a global of the search half that reads it
# (v11.3.0 decomposition), so batching is forced there, not on the package.
from lattice_brain.graph.retrieval_vector import search as rv
from lattice_brain.graph.store import KnowledgeGraphStore
from lattice_brain.graph.vector_index import (
    BRUTE_FORCE_BACKEND,
    QUANTIZED_BACKEND,
    VECTOR_INDEX_ENV,
    VectorEmbedQueue,
    sidecar_paths,
)

DOCS = [
    ("Hybrid Retrieval Design", "Hybrid retrieval fuses lexical keyword matching with vector cosine similarity."),
    ("Vector Index Operations", "The vector index stores embeddings in SQLite and is rebuilt incrementally."),
    ("Release Checklist", "Ship notes, tag the build, and update the changelog."),
]


def _store(tmp_path: Path) -> KnowledgeGraphStore:
    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    for title, text in DOCS:
        store.ingest_source(
            source_type="note", title=title, text=text, source_uri=f"note:{title}"
        )
    return store


class _FakeGraph:
    """Exact-cosine stand-in for ``hnswlib.Index`` (see the backend tests)."""

    def __init__(self, *, space: str, dim: int) -> None:
        self.space, self.dim = space, dim
        self.vectors: dict[int, list[float]] = {}

    def init_index(self, *, max_elements, ef_construction, M) -> None:
        self.max_elements = max_elements

    def add_items(self, data, ids) -> None:
        for vector, label in zip(data, ids):
            self.vectors[int(label)] = [float(value) for value in vector]

    def set_ef(self, ef) -> None:
        self.ef = ef

    def knn_query(self, queries, k):
        query = list(queries[0])
        scored = sorted(
            (
                (label, 1.0 - sum(a * b for a, b in zip(query, vector)))
                for label, vector in self.vectors.items()
            ),
            key=lambda pair: pair[1],
        )[:k]
        return [[label for label, _ in scored]], [[d for _, d in scored]]

    def save_index(self, path) -> None:
        Path(path).write_text(
            json.dumps({str(k): v for k, v in self.vectors.items()}), encoding="utf-8"
        )

    def load_index(self, path, max_elements) -> None:
        self.vectors = {
            int(k): v
            for k, v in json.loads(Path(path).read_text(encoding="utf-8")).items()
        }


@pytest.fixture
def hnsw_selected(monkeypatch):
    module = types.ModuleType("hnswlib")
    module.Index = _FakeGraph
    monkeypatch.setitem(sys.modules, "hnswlib", module)
    monkeypatch.setenv(VECTOR_INDEX_ENV, "hnsw")


# ── the default path is unchanged ────────────────────────────────────────────


def test_the_default_backend_is_still_the_exact_scan(tmp_path, monkeypatch):
    monkeypatch.delenv(VECTOR_INDEX_ENV, raising=False)
    store = _store(tmp_path)

    result = store.vector_search("hybrid retrieval vector", limit=5)

    assert result["recall"]["backend"] == BRUTE_FORCE_BACKEND
    assert result["recall"]["truncated"] is False
    assert result["recall"]["detail"] is None
    assert result["index"]["name"] == "brute"
    assert result["index"]["approx"] is False
    assert result["index"]["honored"] is True
    scores = [match["score"] for match in result["matches"]]
    assert scores == sorted(scores, reverse=True)


def test_batching_the_scan_does_not_change_the_answer(tmp_path, monkeypatch):
    """Feeding the index in batches must be invisible to the caller."""
    monkeypatch.delenv(VECTOR_INDEX_ENV, raising=False)
    store = _store(tmp_path)
    whole = store.vector_search("hybrid retrieval vector", limit=10)

    monkeypatch.setattr(rv, "VECTOR_SCAN_BATCH", 1)
    batched = store.vector_search("hybrid retrieval vector", limit=10)

    assert batched["matches"] == whole["matches"]
    assert batched["recall"] == whole["recall"]


def test_the_score_floor_still_drops_rows_under_the_index_layer(tmp_path):
    store = _store(tmp_path)
    result = store.vector_search("hybrid retrieval vector", min_score=1.5)
    assert result["matches"] == []
    assert result["recall"]["candidates_scanned"] > 0


# ── quantized ────────────────────────────────────────────────────────────────


def test_the_quantized_backend_answers_and_declares_itself_approximate(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(VECTOR_INDEX_ENV, "quantized")
    store = _store(tmp_path)

    result = store.vector_search("hybrid retrieval vector", limit=5)

    assert result["recall"]["backend"] == QUANTIZED_BACKEND
    assert result["index"]["approx"] is True
    assert result["index"]["exhaustive"] is True
    # Exhaustive but approximate: nothing was skipped, the numbers are estimates.
    assert result["recall"]["truncated"] is False
    assert "approximate backend" in result["recall"]["detail"]
    assert store._vector_search_backend() == QUANTIZED_BACKEND
    assert [m["id"] for m in result["matches"]]


def test_an_unknown_backend_still_searches_and_explains_itself(tmp_path, monkeypatch):
    monkeypatch.setenv(VECTOR_INDEX_ENV, "annoy")
    store = _store(tmp_path)

    result = store.vector_search("hybrid retrieval vector", limit=5)

    assert result["index"]["honored"] is False
    assert "annoy" in result["index"]["detail"]
    assert result["recall"]["backend"] == BRUTE_FORCE_BACKEND
    assert result["matches"]


# ── hnsw ─────────────────────────────────────────────────────────────────────


def test_the_ann_path_returns_the_same_top_hit_and_persists_a_sidecar(
    tmp_path, hnsw_selected
):
    store = _store(tmp_path)
    exact = KnowledgeGraphStore(store.db_path, store.blob_dir)

    approximate = store.vector_search("hybrid retrieval vector", limit=3)

    index_path, meta_path = sidecar_paths(store.db_path)
    assert index_path.exists() and meta_path.exists()
    assert approximate["index"]["approx"] is True
    assert approximate["index"]["sidecar"] is False
    assert "not guaranteed" in approximate["recall"]["detail"]
    assert approximate["recall"]["candidates_scanned"] == (
        approximate["recall"]["candidates_total"]
    )

    # A second search on the same store reuses the graph it already holds.
    reused = store.vector_search("hybrid retrieval vector", limit=3)
    assert [m["id"] for m in reused["matches"]] == [
        m["id"] for m in approximate["matches"]
    ]

    # A fresh store over the same database — the restart case — picks the
    # graph up off disk instead of rebuilding it.
    restarted = KnowledgeGraphStore(store.db_path, store.blob_dir)
    from_disk = restarted.vector_search("hybrid retrieval vector", limit=3)
    assert from_disk["index"]["sidecar"] is True
    assert [m["id"] for m in from_disk["matches"]] == [
        m["id"] for m in approximate["matches"]
    ]

    # And the answer agrees with the exact scan on this corpus.
    exact._vector_index_selection = lambda: rv.resolve_vector_index("brute")
    assert [m["id"] for m in exact.vector_search("hybrid retrieval vector", limit=3)[
        "matches"
    ]] == [m["id"] for m in approximate["matches"]]


def test_a_stale_sidecar_is_rebuilt_after_new_content_lands(tmp_path, hnsw_selected):
    store = _store(tmp_path)
    store.vector_search("hybrid retrieval", limit=3)

    store.ingest_source(
        source_type="note",
        title="Quantization Notes",
        text="int8 quantization keeps the vector index small.",
        source_uri="note:quantization",
    )
    refreshed = store.vector_search("quantization int8", limit=3)

    assert refreshed["index"]["sidecar"] is False  # fingerprint changed → rebuild
    assert any("Quantization" in (m["title"] or "") for m in refreshed["matches"])


def test_an_empty_index_falls_through_to_the_ordinary_scan(tmp_path, hnsw_selected):
    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    result = store.vector_search("nothing is indexed yet")
    assert result["matches"] == []
    assert result["recall"]["candidates_total"] == 0
    assert result["index"]["name"] == "hnsw"


def test_a_floor_that_nothing_clears_yields_no_rows_to_fetch(tmp_path, hnsw_selected):
    store = _store(tmp_path)
    result = store.vector_search("hybrid retrieval", limit=3, min_score=1.5)
    assert result["matches"] == []
    assert result["recall"]["candidates_total"] > 0


def test_a_broken_ann_backend_still_answers_from_the_exact_scan(
    tmp_path, hnsw_selected
):
    store = _store(tmp_path)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("hnsw graph corrupt")

    store._vector_search_ann = _boom

    result = store.vector_search("hybrid retrieval vector", limit=3)

    assert result["matches"]  # the answer survived
    assert result["index"]["requested"] == "hnsw"
    assert result["index"]["name"] == "brute"
    assert "hnsw search failed" in result["index"]["detail"]
    assert result["recall"]["backend"] == BRUTE_FORCE_BACKEND


# ── status surfaces ──────────────────────────────────────────────────────────


def test_index_status_names_the_backend_that_will_score(tmp_path, monkeypatch):
    monkeypatch.setenv(VECTOR_INDEX_ENV, "quantized")
    store = _store(tmp_path)
    reported = store.index_status()["storage"]["vector_index"]
    assert reported["name"] == "quantized"
    assert reported["approx"] is True
    assert reported["honored"] is True


def test_freshness_breakdown_splits_pending_into_missing_and_stale(tmp_path):
    store = _store(tmp_path)
    ready = store.vector_freshness_breakdown()
    assert ready["status"] == "ready"
    assert ready["embedded"] == ready["total"] > 0
    assert (ready["pending"], ready["missing"], ready["stale"]) == (0, 0, 0)
    assert ready["queued"] == 0

    with store._connect() as conn:
        conn.execute("DELETE FROM vector_embeddings")
    behind = store.vector_freshness_breakdown()
    assert behind["status"] == "pending"
    assert behind["missing"] == behind["pending"] == behind["total"]
    assert behind["stale"] == 0
    assert behind["embedded"] == 0
    # The compact contract other surfaces read is untouched by the split.
    assert set(store.vector_freshness()) == {
        "status",
        "pending_items",
        "total_items",
        "detail",
    }


def test_freshness_breakdown_degrades_when_the_index_cannot_be_read(
    tmp_path, monkeypatch
):
    store = _store(tmp_path)

    def _boom():
        raise RuntimeError("index table missing")

    monkeypatch.setattr(store, "index_status", _boom)
    breakdown = store.vector_freshness_breakdown()
    assert breakdown["status"] == "unavailable"
    assert "index table missing" in breakdown["detail"]
    assert (breakdown["embedded"], breakdown["pending"], breakdown["total"]) == (0, 0, 0)


def test_freshness_reports_an_untracked_backlog_as_unknown_not_zero(tmp_path):
    store = _store(tmp_path)
    store._vector_queue = VectorEmbedQueue()  # no database → nothing measured
    assert store.vector_freshness_breakdown()["queued"] is None


def test_the_store_keeps_one_background_queue(tmp_path):
    store = _store(tmp_path)
    assert store.vector_queue is store.vector_queue
    assert store.vector_queue.available is True
