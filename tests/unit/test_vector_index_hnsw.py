"""HNSW incremental append policy — rebuild vs add_items."""

from __future__ import annotations

import pytest

from latticeai.core.vector_index import (
    DELETE_REBUILD_RATIO,
    HnswIndex,
    hnswlib_available,
)


def _vec(seed: int, dim: int = 8) -> list[float]:
    return [float((seed * (i + 1)) % 17) / 17.0 for i in range(dim)]


def _items(n: int, dim: int = 8, start: int = 0):
    return [(f"id-{i}", _vec(i, dim), {}) for i in range(start, start + n)]


def test_add_items_appends_when_only_new_ids_arrive():
    index = HnswIndex(dim=8, model_id="hash")
    first = index.add_items(_items(10))
    second = index.add_items(_items(12))
    assert first in {"append", "rebuild"}
    assert second == "append"
    assert index.stats()["size"] == 12


def test_add_items_rebuilds_on_dim_change():
    index = HnswIndex(dim=8, model_id="hash")
    index.add_items(_items(4, dim=8))
    decision = index.add_items(_items(4, dim=16), dim=16)
    assert decision == "rebuild"
    assert index.dim == 16


def test_add_items_rebuilds_on_model_change():
    index = HnswIndex(dim=8, model_id="hash")
    index.add_items(_items(4))
    decision = index.add_items(_items(4), model_id="other")
    assert decision == "rebuild"
    assert index.model_id == "other"


def test_add_items_rebuilds_when_ids_disappear():
    index = HnswIndex(dim=8, model_id="hash")
    index.add_items(_items(20))
    # Drop half — well over DELETE_REBUILD_RATIO.
    decision = index.add_items(_items(10))
    assert decision == "rebuild"
    assert index.stats()["size"] == 10
    assert DELETE_REBUILD_RATIO == 0.10


@pytest.mark.skipif(not hnswlib_available(), reason="hnswlib extra not installed")
def test_live_append_answers_a_query_without_dropping_old_ids():
    index = HnswIndex(dim=8, model_id="hash")
    index.add_items(_items(30))
    index._ensure_graph()
    decision = index.add_items(_items(35))
    assert decision == "append"
    hits = index.search(_vec(0), top_k=5)
    assert hits
    assert hits[0][0].startswith("id-")


def test_remove_drops_an_id_from_the_held_set():
    index = HnswIndex(dim=8, model_id="hash")
    index.add_items(_items(8))
    index.remove("id-0")
    assert index.stats()["size"] == 7
    index.add("id-9", _vec(9))
    assert index.stats()["size"] == 8


def test_sidecar_roundtrip_rejects_a_stale_fingerprint(tmp_path):
    index = HnswIndex(dim=8, model_id="hash")
    index.add_items(_items(6))
    db = tmp_path / "brain.sqlite"
    db.write_bytes(b"")
    if not hnswlib_available():
        assert index.save(db, fingerprint="fp-1") is False
        return
    assert index.save(db, fingerprint="fp-1") is True
    loaded = HnswIndex(dim=8, model_id="hash")
    assert loaded.load(db, fingerprint="fp-other") is False
    assert loaded.load(db, fingerprint="fp-1") is True
    assert loaded.loaded_from_sidecar is True
    assert loaded.stats()["size"] == 6
    with pytest.raises(RuntimeError):
        loaded.add("id-x", _vec(99))
    loaded2 = HnswIndex(dim=8, model_id="hash")
    loaded2.load(db, fingerprint="fp-1")
    with pytest.raises(RuntimeError):
        loaded2.remove("id-0")
    # A sidecar graph has no source vectors — mutation must rebuild.
    assert loaded.add_items(_items(7)) == "rebuild"


def test_search_respects_min_score_and_empty_index():
    empty = HnswIndex(dim=8, model_id="hash")
    assert empty.search(_vec(0), top_k=3) == []
    index = HnswIndex(dim=8, model_id="hash")
    index.add_items(_items(10))
    if not hnswlib_available():
        assert index.search(_vec(0), top_k=3) == []
        return
    hits = index.search(_vec(0), top_k=3, filter={"min_score": 0.99})
    assert all(score >= 0.99 for _, score in hits)
    junk = index.search(_vec(0), top_k=3, filter={"min_score": "nope"})
    assert junk


def test_backend_flags_and_identity_helpers():
    index = HnswIndex(dim=8, model_id="hash")
    assert index.backend == "hnsw"
    assert index.approx is True
    assert index.exhaustive is False
    assert index.dim == 8
    assert index.model_id == "hash"
    assert index.unavailable_detail is None or isinstance(index.unavailable_detail, str)
    stats = index.stats()
    assert stats["backend"] == "hnsw"
    assert stats["approx"] is True


@pytest.mark.skipif(
    not hnswlib_available(),
    reason="append vs rebuild is the real library's distinction; without hnswlib every add is a rebuild",
)
def test_second_add_items_with_the_same_set_is_a_noop_append():
    index = HnswIndex(dim=8, model_id="hash")
    index.add_items(_items(5))
    if hnswlib_available():
        index._ensure_graph()
    assert index.add_items(_items(5)) == "append"
