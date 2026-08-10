"""Vector-index backends: exact, quantized, and approximate.

Covers the three implementations of the ``VectorIndex`` protocol plus the
shared helpers. The properties that matter here are not "does it return
something" but the ones retrieval depends on:

* the exact backend must reproduce the historical scoring loop, including its
  refusal to compare mismatched dimensions and its stable tie ordering;
* the quantized backend must stay *exhaustive* (every vector compared) while
  admitting its scores are estimates;
* the HNSW backend must be unusable-but-honest without ``hnswlib``, and must
  never trust a sidecar it cannot prove matches the current index.

``hnswlib`` is an optional compiled extension, so every test here drives a
fake module through ``sys.modules``. That keeps the two branches identical on
a developer machine that has it installed and on CI, which does not.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph.vector_index import (
    BRUTE_FORCE_BACKEND,
    HNSW_META_SUFFIX,
    HNSW_SUFFIX,
    QUANTIZED_BACKEND,
    BruteForceIndex,
    HnswIndex,
    QuantizedIndex,
    dot_similarity,
    hnswlib_available,
    load_hnswlib,
    quantize,
    score_floor,
    sidecar_paths,
    take_top,
)
from lattice_brain.graph.vector_index.base import NO_FLOOR

# ── fake hnswlib ─────────────────────────────────────────────────────────────


class _FakeGraph:
    """Enough of ``hnswlib.Index`` to exercise every call site we make."""

    def __init__(self, *, space: str, dim: int) -> None:
        self.space = space
        self.dim = dim
        self.vectors: dict[int, list[float]] = {}
        self.ef: int | None = None
        self.max_elements = 0

    def init_index(self, *, max_elements: int, ef_construction: int, M: int) -> None:
        self.max_elements = max_elements
        self.ef_construction = ef_construction
        self.m = M

    def add_items(self, data, ids) -> None:
        for vector, label in zip(data, ids):
            self.vectors[int(label)] = [float(value) for value in vector]

    def set_ef(self, ef: int) -> None:
        self.ef = ef

    def knn_query(self, queries, k: int):
        query = list(queries[0])
        scored = sorted(
            (
                (label, 1.0 - sum(a * b for a, b in zip(query, vector)))
                for label, vector in self.vectors.items()
            ),
            key=lambda pair: pair[1],
        )[:k]
        return [[label for label, _ in scored]], [[distance for _, distance in scored]]

    def save_index(self, path: str) -> None:
        Path(path).write_text(
            json.dumps({str(k): v for k, v in self.vectors.items()}), encoding="utf-8"
        )

    def load_index(self, path: str, max_elements: int) -> None:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        self.vectors = {int(k): v for k, v in payload.items()}
        self.max_elements = max_elements


@pytest.fixture
def fake_hnswlib(monkeypatch):
    module = types.ModuleType("hnswlib")
    module.Index = _FakeGraph
    monkeypatch.setitem(sys.modules, "hnswlib", module)
    return module


@pytest.fixture
def no_hnswlib(monkeypatch):
    """``None`` in sys.modules makes ``import hnswlib`` raise ImportError."""
    monkeypatch.setitem(sys.modules, "hnswlib", None)


def _unit(*values: float) -> list[float]:
    norm = sum(value * value for value in values) ** 0.5
    return [value / norm for value in values]


# ── shared helpers ───────────────────────────────────────────────────────────


def test_dot_similarity_is_cosine_for_unit_vectors():
    assert dot_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert dot_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_dot_similarity_refuses_a_dimension_mismatch():
    """Truncating to the shorter vector would produce a meaningless number."""
    with pytest.raises(ValueError, match="dimension mismatch"):
        dot_similarity([1.0, 0.0], [1.0])


@pytest.mark.parametrize(
    "value", [None, {}, {"other": 1}, {"min_score": None}, {"min_score": "nope"}]
)
def test_score_floor_without_a_usable_value_is_no_floor(value):
    assert score_floor(value) == NO_FLOOR


def test_score_floor_keeps_zero_which_is_a_real_floor():
    """0.0 is falsy but meaningful — it must not collapse to "no floor"."""
    assert score_floor({"min_score": 0.0}) == 0.0
    assert score_floor({"min_score": "0.25"}) == 0.25


def test_take_top_sorts_descending_and_cuts():
    assert take_top([("a", 0.1), ("b", 0.9), ("c", 0.5)], 2) == [("b", 0.9), ("c", 0.5)]
    assert take_top([("a", 0.1)], 0) == []


# ── BruteForceIndex ──────────────────────────────────────────────────────────


def test_brute_force_reports_itself_as_exact_and_exhaustive():
    index = BruteForceIndex()
    index.add("a", [1.0, 0.0], {"item_type": "node"})
    stats = index.stats()
    assert (index.backend, index.approx, index.exhaustive) == (
        BRUTE_FORCE_BACKEND,
        False,
        True,
    )
    assert stats.as_dict() == {
        "backend": BRUTE_FORCE_BACKEND,
        "size": 1,
        "dim": 2,
        "approx": False,
        "exhaustive": True,
        "available": True,
        "detail": None,
    }


def test_brute_force_ranks_by_similarity_and_honours_the_floor():
    index = BruteForceIndex(dim=2)
    index.rebuild(
        [
            ("near", _unit(1.0, 0.1), {}),
            ("far", _unit(0.0, 1.0), {}),
        ]
    )
    ranked = index.search(_unit(1.0, 0.0), 10)
    assert [item_id for item_id, _ in ranked] == ["near", "far"]
    assert index.search(_unit(1.0, 0.0), 10, filter={"min_score": 0.5}) == ranked[:1]


def test_brute_force_uses_the_injected_similarity():
    """The embedder owns similarity; the index must not reimplement it."""
    calls: list[tuple[int, int]] = []

    def _similarity(left, right):
        calls.append((len(list(left)), len(list(right))))
        return 0.42

    index = BruteForceIndex(dim=2, similarity=_similarity)
    index.add("a", [1.0, 0.0])
    assert index.search([1.0, 0.0], 1) == [("a", 0.42)]
    assert calls == [(2, 2)]


def test_brute_force_add_returns_metadata_and_remove_forgets_it():
    index = BruteForceIndex()
    index.add("a", [1.0], {"item_type": "chunk"})
    assert index.metadata_for("a") == {"item_type": "chunk"}
    assert index.metadata_for("missing") == {}
    index.remove("a")
    index.remove("a")  # removing twice is not an error
    assert index.search([1.0], 5) == []


# ── QuantizedIndex ───────────────────────────────────────────────────────────


def test_quantize_of_a_zero_vector_has_no_scale_to_find():
    codes, scale = quantize([0.0, 0.0, 0.0])
    assert scale == 0.0
    assert list(codes) == [0, 0, 0]


def test_quantize_uses_the_full_int8_range_for_the_peak():
    codes, scale = quantize([0.5, -0.5, 0.25])
    assert list(codes) == [127, -127, 64]
    assert scale == pytest.approx(0.5 / 127)


def test_quantized_scores_track_the_exact_ones_within_a_percent():
    query = _unit(0.9, 0.3, 0.1)
    items = [
        ("near", _unit(0.85, 0.35, 0.1)),
        ("mid", _unit(0.4, 0.6, 0.2)),
        ("far", _unit(0.0, 0.1, 0.9)),
    ]
    exact = BruteForceIndex(dim=3)
    approx = QuantizedIndex(dim=3)
    exact.rebuild([(i, v, {}) for i, v in items])
    approx.rebuild([(i, v, {}) for i, v in items])

    exact_ranked = exact.search(query, 3)
    approx_ranked = approx.search(query, 3)
    assert [i for i, _ in approx_ranked] == [i for i, _ in exact_ranked]
    for (_, left), (_, right) in zip(exact_ranked, approx_ranked):
        assert left == pytest.approx(right, abs=0.02)


def test_quantized_is_exhaustive_but_admits_its_scores_are_estimates():
    index = QuantizedIndex()
    index.add("a", [1.0, 0.0], {"item_type": "node"})
    stats = index.stats()
    assert (index.backend, index.approx, index.exhaustive) == (
        QUANTIZED_BACKEND,
        True,
        True,
    )
    assert stats.size == 1 and stats.dim == 2
    assert "8-bit" in (stats.detail or "")
    assert index.metadata_for("a") == {"item_type": "node"}


def test_quantized_honours_the_floor_and_forgets_removed_ids():
    index = QuantizedIndex(dim=2)
    index.rebuild([("near", _unit(1.0, 0.05), {}), ("far", _unit(0.0, 1.0), {})])
    assert [i for i, _ in index.search(_unit(1.0, 0.0), 5, filter={"min_score": 0.5})] == [
        "near"
    ]
    index.remove("near")
    index.remove("near")
    assert [i for i, _ in index.search(_unit(1.0, 0.0), 5)] == ["far"]


def test_quantized_refuses_a_dimension_mismatch_like_the_exact_backend():
    index = QuantizedIndex(dim=2)
    index.add("a", [1.0, 0.0])
    with pytest.raises(ValueError, match="dimension mismatch"):
        index.search([1.0], 1)


# ── HnswIndex without the optional extra ─────────────────────────────────────


def test_load_hnswlib_reports_why_it_is_unavailable(no_hnswlib):
    module, detail = load_hnswlib()
    assert module is None
    assert "hnswlib" in detail and "ltcai[hnsw]" in detail
    assert hnswlib_available() is False


def test_hnsw_without_the_extra_searches_nothing_and_says_so(no_hnswlib, tmp_path):
    index = HnswIndex(dim=2)
    index.add("a", [1.0, 0.0])
    assert index.available is False
    assert index.search([1.0, 0.0], 5) == []
    stats = index.stats()
    assert stats.available is False and stats.approx is True
    assert stats.exhaustive is False
    assert "hnswlib" in (stats.detail or "") == (index.unavailable_detail or "")
    # Neither half of the sidecar can be written or trusted without the engine.
    assert index.save(tmp_path / "kg.sqlite", fingerprint="f") is False
    assert index.load(tmp_path / "kg.sqlite", fingerprint="f") is False


# ── HnswIndex with a fake engine ─────────────────────────────────────────────


def test_hnsw_ranks_neighbours_and_reports_approximation(fake_hnswlib):
    index = HnswIndex(dim=3)
    index.rebuild(
        [
            ("near", _unit(1.0, 0.1, 0.0), {"item_type": "node"}),
            ("far", _unit(0.0, 0.1, 1.0), {}),
        ]
    )
    ranked = index.search(_unit(1.0, 0.0, 0.0), 5)
    assert [item_id for item_id, _ in ranked] == ["near", "far"]
    assert index.available is True
    assert (index.approx, index.exhaustive) == (True, False)
    assert index.stats().size == 2
    assert index.metadata_for("near") == {"item_type": "node"}


def test_hnsw_honours_the_score_floor(fake_hnswlib):
    index = HnswIndex(dim=2)
    index.rebuild([("near", _unit(1.0, 0.05), {}), ("far", _unit(0.0, 1.0), {})])
    assert [i for i, _ in index.search(_unit(1.0, 0.0), 5, filter={"min_score": 0.5})] == [
        "near"
    ]


def test_hnsw_over_an_empty_index_returns_nothing(fake_hnswlib):
    assert HnswIndex(dim=2).search([1.0, 0.0], 5) == []


def test_hnsw_rebuilds_only_after_a_mutation(fake_hnswlib):
    index = HnswIndex(dim=2)
    index.add("a", _unit(1.0, 0.0))
    first = index.search([1.0, 0.0], 1)
    assert first and index._ann is not None
    graph = index._ann
    index.search([1.0, 0.0], 1)
    assert index._ann is graph  # unchanged input → the graph is reused
    index.remove("a")
    index.add("b", _unit(0.0, 1.0))
    assert [i for i, _ in index.search([0.0, 1.0], 1)] == ["b"]
    assert index._ann is not graph


# ── HnswIndex sidecar ────────────────────────────────────────────────────────


def test_sidecar_paths_sit_next_to_the_brain_database(tmp_path):
    index_path, meta_path = sidecar_paths(tmp_path / "kg.sqlite")
    assert index_path.name == "kg" + HNSW_SUFFIX
    assert meta_path.name == "kg" + HNSW_META_SUFFIX
    assert index_path.parent == tmp_path


def test_sidecar_round_trips_and_is_search_only_afterwards(fake_hnswlib, tmp_path):
    db_path = tmp_path / "brain" / "kg.sqlite"
    built = HnswIndex(dim=2)
    built.rebuild([("near", _unit(1.0, 0.05), {}), ("far", _unit(0.0, 1.0), {})])
    assert built.save(db_path, fingerprint="model:2:2:now") is True
    assert built.loaded_from_sidecar is False

    loaded = HnswIndex(dim=2)
    assert loaded.load(db_path, fingerprint="model:2:2:now") is True
    assert loaded.loaded_from_sidecar is True
    assert loaded.stats().size == 2
    assert [i for i, _ in loaded.search(_unit(1.0, 0.0), 2)] == ["near", "far"]
    # It holds labels, not vectors, so mutating it would silently drop the
    # index — that has to fail loudly instead.
    with pytest.raises(RuntimeError, match="loaded from a sidecar"):
        loaded.add("new", [1.0, 0.0])
    with pytest.raises(RuntimeError, match="loaded from a sidecar"):
        loaded.remove("near")
    # A full rebuild is the supported way back to a mutable index.
    loaded.rebuild([("fresh", _unit(1.0, 0.0), {})])
    assert loaded.loaded_from_sidecar is False
    assert [i for i, _ in loaded.search(_unit(1.0, 0.0), 2)] == ["fresh"]


def test_an_empty_index_writes_no_sidecar(fake_hnswlib, tmp_path):
    assert HnswIndex(dim=2).save(tmp_path / "kg.sqlite", fingerprint="f") is False
    assert not (tmp_path / ("kg" + HNSW_SUFFIX)).exists()


def test_a_sidecar_that_cannot_be_written_is_not_an_error(fake_hnswlib, tmp_path):
    """A missing sidecar only costs a rebuild — never a failed search."""
    index = HnswIndex(dim=2)
    index.rebuild([("a", _unit(1.0, 0.0), {})])
    blocked = tmp_path / "file.sqlite"
    blocked.write_text("not a directory", encoding="utf-8")
    assert index.save(blocked / "nested" / "kg.sqlite", fingerprint="f") is False


def test_a_missing_sidecar_is_simply_not_loaded(fake_hnswlib, tmp_path):
    assert HnswIndex(dim=2).load(tmp_path / "kg.sqlite", fingerprint="f") is False


@pytest.mark.parametrize(
    "meta",
    [
        {"fingerprint": "other", "dim": 2, "labels": ["a"]},   # different index
        {"fingerprint": "f", "dim": 9, "labels": ["a"]},       # different embedder
        {"fingerprint": "f", "dim": 2, "labels": []},          # nothing in it
    ],
)
def test_a_sidecar_that_does_not_match_is_rejected(fake_hnswlib, tmp_path, meta):
    db_path = tmp_path / "kg.sqlite"
    index_path, meta_path = sidecar_paths(db_path)
    index_path.write_text("{}", encoding="utf-8")
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    assert HnswIndex(dim=2).load(db_path, fingerprint="f") is False


def test_a_corrupt_sidecar_is_rejected_rather_than_raising(fake_hnswlib, tmp_path):
    db_path = tmp_path / "kg.sqlite"
    index_path, meta_path = sidecar_paths(db_path)
    meta_path.write_text("{not json", encoding="utf-8")
    assert HnswIndex(dim=2).load(db_path, fingerprint="f") is False
    # Valid metadata pointing at an unreadable binary is equally not trusted.
    meta_path.write_text(
        json.dumps({"fingerprint": "f", "dim": 2, "labels": ["a"]}), encoding="utf-8"
    )
    assert not index_path.exists()
    assert HnswIndex(dim=2).load(db_path, fingerprint="f") is False
