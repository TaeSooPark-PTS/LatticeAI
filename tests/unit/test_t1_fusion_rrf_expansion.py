"""Reciprocal Rank Fusion + graph candidate expansion.

Both are opt-in, and the first thing these tests pin is that they are *off*:
the default ranking, the default score keys and the default result shape are
what every other assertion in this repo already describes. After that they
pin what the options actually buy — fusing positions instead of incomparable
score scales, and reaching a node that is one edge away from a hit — and that
each reports its own counts instead of quietly reshaping the result list.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph.fusion import (
    DEFAULT_RRF_K,
    EXPANSION_DECAY,
    FUSION_STRATEGY_ENV,
    GRAPH_EXPANSION_ENV,
    expand_with_neighbors,
    fusion_profile,
    fusion_strategy_table,
    graph_expansion_enabled,
    rrf_fuse,
    rrf_score,
)
from lattice_brain.graph.retrieval_policy import resolve_policy
from lattice_brain.graph.store import KnowledgeGraphStore

DOCUMENTS = [
    ("Hybrid Retrieval Design", "Hybrid retrieval fuses lexical keyword matching with vector cosine similarity."),
    ("Vector Index Operations", "The vector index stores embeddings in SQLite and is rebuilt incrementally."),
    ("Ingestion Pipeline", "The ingestion pipeline chunks documents and records provenance for retrieval."),
    ("Release Checklist", "Ship notes, tag the build, and update the changelog."),
]


def _store(tmp_path: Path) -> KnowledgeGraphStore:
    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    for title, text in DOCUMENTS:
        store.ingest_source(
            source_type="note", title=title, text=text, source_uri=f"note:{title}"
        )
    return store


# ── strategy resolution ──────────────────────────────────────────────────────


def test_every_class_fuses_linearly_by_default(monkeypatch):
    monkeypatch.delenv(FUSION_STRATEGY_ENV, raising=False)
    assert set(fusion_strategy_table().values()) == {"alpha"}
    assert fusion_profile("어제 회의 내용")["strategy"] == "alpha"
    assert resolve_policy("출시 결정 내용")["fusion_strategy"] == "alpha"


def test_a_bare_strategy_name_applies_to_every_class(monkeypatch):
    monkeypatch.setenv(FUSION_STRATEGY_ENV, " RRF ")
    assert set(fusion_strategy_table().values()) == {"rrf"}


def test_a_json_object_selects_per_class(monkeypatch):
    monkeypatch.setenv(FUSION_STRATEGY_ENV, '{"code": "rrf", "nonsense": "rrf"}')
    table = fusion_strategy_table()
    assert table["code"] == "rrf"
    assert table["fact"] == "alpha"
    assert "nonsense" not in table


@pytest.mark.parametrize(
    "raw", ["", "   ", "not-a-strategy", "{bad json", '["rrf"]', '{"code": "faiss"}']
)
def test_an_unusable_strategy_setting_is_ignored(monkeypatch, raw):
    monkeypatch.setenv(FUSION_STRATEGY_ENV, raw)
    assert set(fusion_strategy_table().values()) == {"alpha"}


def test_a_caller_override_wins_over_the_environment(monkeypatch):
    monkeypatch.setenv(FUSION_STRATEGY_ENV, "rrf")
    table = fusion_strategy_table({"code": "alpha", "unknown": "rrf", "fact": "nope"})
    assert table["code"] == "alpha"
    assert table["fact"] == "rrf"  # invalid value ignored, env value kept


# ── RRF arithmetic ───────────────────────────────────────────────────────────


def test_rrf_score_decays_with_rank():
    assert rrf_score(1) == pytest.approx(1.0 / (DEFAULT_RRF_K + 1))
    assert rrf_score(1) > rrf_score(2) > rrf_score(50)
    assert rrf_score(0) == rrf_score(1)  # ranks are 1-based


def test_rrf_rewards_agreement_between_channels():
    fused = rrf_fuse({"lexical": ["a", "b", "c"], "vector": ["c", "a"]})
    # "a" is high in both; "c" is first in one and last in the other.
    assert fused["a"] > fused["c"] > fused["b"]


def test_rrf_ignores_score_magnitudes_entirely():
    """The point of RRF: only positions, so incomparable scales cannot skew it."""
    assert rrf_fuse({"only": ["a", "b"]}) == {
        "a": pytest.approx(rrf_score(1)),
        "b": pytest.approx(rrf_score(2)),
    }


def test_rrf_channel_weights_scale_a_channel_contribution():
    weighted = rrf_fuse(
        {"lexical": ["a"], "vector": ["b"]}, weights={"lexical": 2.0}
    )
    assert weighted["a"] == pytest.approx(2 * weighted["b"])


# ── expansion helper ─────────────────────────────────────────────────────────


def test_graph_expansion_is_off_unless_asked_for(monkeypatch):
    monkeypatch.delenv(GRAPH_EXPANSION_ENV, raising=False)
    assert graph_expansion_enabled() is False
    monkeypatch.setenv(GRAPH_EXPANSION_ENV, "0")
    assert graph_expansion_enabled() is False
    monkeypatch.setenv(GRAPH_EXPANSION_ENV, "on")
    assert graph_expansion_enabled() is True


def test_expansion_walks_seeds_and_damps_their_scores():
    def _neighbors(node_id):
        return {"neighbors": [{"id": f"{node_id}:child", "title": "child"}]}

    candidates, report = expand_with_neighbors([("a", 0.8)], _neighbors, cap=5)

    assert [c["node"]["id"] for c in candidates] == ["a:child"]
    assert candidates[0]["seed"] == "a"
    assert candidates[0]["score"] == pytest.approx(0.8 * EXPANSION_DECAY)
    assert report == {
        "enabled": True,
        "seeds": 1,
        "added": 1,
        "cap": 5,
        "truncated": False,
        "failed_seeds": 0,
    }


def test_expansion_never_repeats_a_node_already_in_the_pool():
    def _neighbors(node_id):
        return {"neighbors": [{"id": "known"}, {"id": "fresh"}, {"id": ""}]}

    candidates, report = expand_with_neighbors(
        [("a", 1.0)], _neighbors, exclude=["known"], cap=5
    )
    assert [c["node"]["id"] for c in candidates] == ["fresh"]
    assert report["added"] == 1


def test_expansion_stops_at_the_cap_and_says_so():
    def _neighbors(node_id):
        return {"neighbors": [{"id": f"{node_id}:{i}"} for i in range(5)]}

    candidates, report = expand_with_neighbors(
        [("a", 1.0), ("b", 0.9)], _neighbors, cap=2
    )
    assert len(candidates) == 2
    assert report["truncated"] is True
    assert report["seeds"] == 2


def test_a_cap_of_zero_walks_nothing():
    def _neighbors(node_id):  # pragma: no cover - must never be called
        raise AssertionError("no seed should be walked")

    candidates, report = expand_with_neighbors([("a", 1.0)], _neighbors, cap=0)
    assert candidates == [] and report["added"] == 0


def test_a_failing_seed_is_counted_not_raised():
    def _neighbors(node_id):
        if node_id == "broken":
            raise RuntimeError("node vanished")
        return {"neighbors": [{"id": "ok"}]}

    candidates, report = expand_with_neighbors(
        [("broken", 1.0), ("good", 0.5)], _neighbors, cap=5
    )
    assert [c["node"]["id"] for c in candidates] == ["ok"]
    assert report["failed_seeds"] == 1


def test_a_seed_with_no_neighbours_contributes_nothing():
    candidates, report = expand_with_neighbors([("a", 1.0)], lambda _id: None, cap=5)
    assert candidates == [] and report["added"] == 0


# ── wiring into hybrid_search ────────────────────────────────────────────────


def test_hybrid_search_reports_alpha_fusion_and_no_expansion_by_default(
    tmp_path, monkeypatch
):
    monkeypatch.delenv(FUSION_STRATEGY_ENV, raising=False)
    monkeypatch.delenv(GRAPH_EXPANSION_ENV, raising=False)
    store = _store(tmp_path)

    result = store.hybrid_search("hybrid retrieval vector", top_k=5)

    assert result["fusion_strategy"] == "alpha"
    assert result["graph_expansion"]["enabled"] is False
    assert result["graph_expansion"]["added"] == 0
    for match in result["matches"]:
        assert set(match["scores"]) == {"lexical", "vector"}
        assert match["fusion"] in {"lexical", "vector", "both"}


def test_an_empty_query_still_reports_its_strategy(tmp_path):
    store = _store(tmp_path)
    result = store.hybrid_search("   ")
    assert result["matches"] == []
    assert result["fusion_strategy"] == "alpha"


def test_rrf_reranks_from_positions_and_records_its_score(tmp_path, monkeypatch):
    monkeypatch.setenv(FUSION_STRATEGY_ENV, "rrf")
    store = _store(tmp_path)

    result = store.hybrid_search("hybrid retrieval vector", top_k=5)

    assert result["fusion_strategy"] == "rrf"
    assert result["matches"]
    scores = [match["score"] for match in result["matches"]]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] == pytest.approx(1.0)  # normalized to the best fused item
    for match in result["matches"]:
        assert "rrf" in match["scores"]


def test_rrf_over_a_query_that_matches_nothing_is_still_a_valid_result(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(FUSION_STRATEGY_ENV, "rrf")
    store = _store(tmp_path)
    result = store.hybrid_search("zzzz nonexistent token", top_k=5, min_vector_score=1.5)
    assert result["fusion_strategy"] == "rrf"
    assert all(match["score"] >= 0 for match in result["matches"])


def test_an_explicit_alpha_pins_linear_fusion_even_under_rrf(tmp_path, monkeypatch):
    monkeypatch.setenv(FUSION_STRATEGY_ENV, "rrf")
    store = _store(tmp_path)
    result = store.hybrid_search("hybrid retrieval vector", top_k=5, alpha=0.5)
    assert result["fusion_strategy"] == "alpha"
    for match in result["matches"]:
        assert "rrf" not in match["scores"]


def test_expansion_adds_neighbours_below_their_seed_and_counts_them(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(GRAPH_EXPANSION_ENV, "1")
    store = _store(tmp_path)

    result = store.hybrid_search("hybrid retrieval vector", top_k=20)

    report = result["graph_expansion"]
    assert report["enabled"] is True
    assert report["seeds"] > 0
    assert report["added"] > 0
    expanded = [m for m in result["matches"] if m["fusion"] == "graph"]
    assert expanded
    for match in expanded:
        assert "graph" in match["scores"]
        assert match["metadata"]["expanded_from"]
        assert match["score"] == match["scores"]["graph"]


def test_expansion_over_an_empty_graph_walks_nothing(tmp_path, monkeypatch):
    """No hits means no seeds: expansion has nothing to expand *from*."""
    monkeypatch.setenv(GRAPH_EXPANSION_ENV, "1")
    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    result = store.hybrid_search("anything at all", top_k=5)
    assert result["matches"] == []
    assert result["graph_expansion"]["enabled"] is False
    assert result["graph_expansion"]["added"] == 0


# ── context quality carries the vector caveat ────────────────────────────────


def test_context_quality_stays_four_keys_for_an_exact_complete_search(tmp_path):
    store = _store(tmp_path)
    meta = store.context_for_query_with_meta("hybrid retrieval vector", 4)
    assert set(meta["quality"]) == {"mode", "nodes", "limited", "reason"}


def test_context_quality_flags_an_approximate_vector_channel(tmp_path, monkeypatch):
    monkeypatch.setenv("LATTICEAI_VECTOR_INDEX", "quantized")
    store = _store(tmp_path)

    meta = store.context_for_query_with_meta("hybrid retrieval vector", 4)

    vector = meta["quality"]["vector"]
    assert vector["approx"] is True
    assert vector["backend"] == "quantized-int8"
    assert vector["embedded_rows"] > 0


def test_context_quality_flags_a_truncated_candidate_scan(tmp_path, monkeypatch):
    monkeypatch.setenv("LATTICEAI_VECTOR_MAX_CANDIDATES", "1")
    store = _store(tmp_path)

    meta = store.context_for_query_with_meta("hybrid retrieval vector", 4)

    vector = meta["quality"]["vector"]
    assert vector["truncated"] is True
    assert vector["degraded"] == "partial_recall"


def test_hybrid_search_echoes_the_vector_channel_shape(tmp_path):
    store = _store(tmp_path)
    block = store.hybrid_search("hybrid retrieval vector", top_k=5)["vector"]
    assert set(block) == {
        "backend",
        "approx",
        "exhaustive",
        "truncated",
        "embedded_rows",
        "degraded",
    }
    assert block["approx"] is False and block["exhaustive"] is True
    assert block["degraded"] is None
