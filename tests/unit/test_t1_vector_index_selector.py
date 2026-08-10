"""Backend selection: what you asked for, what you got, and why they differ.

``LATTICEAI_VECTOR_INDEX`` has exactly two ways to disappoint a user — a name
that does not exist, and ``hnsw`` without the compiled extra — and both look
identical from the outside ("search seems the same"). These tests pin that
each one falls back to the exact scan *and* leaves a reason behind, because
the reason is the only difference between a fallback and a silent downgrade.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph.vector_index import (
    BRUTE_FORCE_BACKEND,
    HNSW_BACKEND,
    QUANTIZED_BACKEND,
    VECTOR_INDEX_ENV,
    BruteForceIndex,
    HnswIndex,
    QuantizedIndex,
    build_index,
    resolve_vector_index,
)


@pytest.fixture
def fake_hnswlib(monkeypatch):
    module = types.ModuleType("hnswlib")
    module.Index = object
    monkeypatch.setitem(sys.modules, "hnswlib", module)
    return module


@pytest.fixture
def no_hnswlib(monkeypatch):
    monkeypatch.setitem(sys.modules, "hnswlib", None)


@pytest.mark.parametrize("unset", [None, "", "   "])
def test_an_unset_backend_is_the_exact_scan(monkeypatch, unset):
    if unset is None:
        monkeypatch.delenv(VECTOR_INDEX_ENV, raising=False)
    else:
        monkeypatch.setenv(VECTOR_INDEX_ENV, unset)
    selection = resolve_vector_index()
    assert selection.name == "brute"
    assert selection.backend == BRUTE_FORCE_BACKEND
    assert selection.honored is True
    assert selection.detail is None
    assert (selection.approx, selection.exhaustive) == (False, True)


def test_quantized_is_selected_verbatim(monkeypatch):
    monkeypatch.setenv(VECTOR_INDEX_ENV, "  QUANTIZED ")
    selection = resolve_vector_index()
    assert selection.as_dict() == {
        "requested": "quantized",
        "name": "quantized",
        "backend": QUANTIZED_BACKEND,
        "approx": True,
        "exhaustive": True,
        "honored": True,
        "detail": None,
    }


def test_an_unknown_backend_falls_back_and_names_the_typo(monkeypatch):
    monkeypatch.setenv(VECTOR_INDEX_ENV, "faiss")
    selection = resolve_vector_index()
    assert selection.name == "brute" and selection.requested == "faiss"
    assert selection.honored is False
    assert "faiss" in selection.detail and "brute-force" in selection.detail


def test_hnsw_is_selected_when_the_extra_is_installed(fake_hnswlib, monkeypatch):
    monkeypatch.setenv(VECTOR_INDEX_ENV, "hnsw")
    selection = resolve_vector_index()
    assert selection.name == HNSW_BACKEND and selection.honored is True
    assert (selection.approx, selection.exhaustive) == (True, False)
    assert selection.detail is None


def test_hnsw_without_the_extra_falls_back_and_says_how_to_get_it(
    no_hnswlib, monkeypatch
):
    monkeypatch.setenv(VECTOR_INDEX_ENV, "hnsw")
    selection = resolve_vector_index()
    assert selection.name == "brute" and selection.requested == HNSW_BACKEND
    assert selection.honored is False
    assert "ltcai[hnsw]" in selection.detail
    assert "brute-force" in selection.detail


def test_an_explicit_request_beats_the_environment(monkeypatch):
    monkeypatch.setenv(VECTOR_INDEX_ENV, "quantized")
    assert resolve_vector_index("brute").name == "brute"


def test_build_index_returns_the_selected_implementation(fake_hnswlib, monkeypatch):
    monkeypatch.delenv(VECTOR_INDEX_ENV, raising=False)
    assert isinstance(build_index(resolve_vector_index("brute"), dim=4), BruteForceIndex)
    assert isinstance(
        build_index(resolve_vector_index("quantized"), dim=4), QuantizedIndex
    )
    assert isinstance(build_index(resolve_vector_index("hnsw"), dim=4), HnswIndex)


def test_only_the_exact_backend_takes_the_injected_similarity():
    """Pretending the others honour it would make an ignored argument look used."""
    calls: list[str] = []

    def _similarity(left, right):
        calls.append("exact")
        return 1.0

    exact = build_index(resolve_vector_index("brute"), dim=1, similarity=_similarity)
    exact.add("a", [1.0])
    exact.search([1.0], 1)
    assert calls == ["exact"]

    quantized = build_index(
        resolve_vector_index("quantized"), dim=1, similarity=_similarity
    )
    quantized.add("a", [1.0])
    quantized.search([1.0], 1)
    assert calls == ["exact"]
