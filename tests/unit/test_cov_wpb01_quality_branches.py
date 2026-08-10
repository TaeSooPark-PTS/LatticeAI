"""wpb01 branch coverage — ``lattice_brain.quality``.

Drives the never-taken direction of five decisions in the quality layer:

* the reranker's "candidate already carries a ``score``" path (no fused_score
  back-fill),
* the *losing* side of the two keep-the-best reducers (``merge`` by content
  prefix and ``merge_duplicate_edges`` by (source, target, type)), which are
  the branches that actually prove the reducers keep the winner,
* and the empty-payload walk through :meth:`LatticeBrainQuality.full_quality_pass`,
  where every optional section is absent.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.quality import (  # noqa: E402
    GraphEdgeQualityManager,
    LatticeBrainQuality,
    MemoryCandidate,
    MemoryQualityManager,
    RerankerInterface,
)


def test_rerank_keeps_an_explicit_score_instead_of_fused_score() -> None:
    """A candidate that already has ``score`` is not overwritten by fused_score."""
    candidates = [
        {"id": "a", "score": 0.9, "fused_score": 0.1, "text": "alpha"},
        {"id": "b", "score": 0.2, "fused_score": 0.8, "text": "beta"},
    ]
    ranked = RerankerInterface().rerank("alpha", candidates, top_k=2)

    assert [item["id"] for item in ranked] == ["a", "b"]
    # The inputs were not mutated by the score back-fill path.
    assert candidates[0]["score"] == 0.9
    assert candidates[1]["score"] == 0.2


def test_merge_keeps_the_higher_scoring_candidate_for_a_shared_prefix() -> None:
    """The second candidate loses the ``key in merged`` comparison."""
    shared = "the first thirty characters are shared by both candidates"
    winner = MemoryCandidate(id="win", content=shared + " ALPHA", score=0.9)
    loser = MemoryCandidate(id="lose", content=shared + " BETA", score=0.1)

    merged = MemoryQualityManager().merge([winner, loser])

    assert [c.id for c in merged] == ["win"]
    assert merged[0].score == 0.9


def test_merge_duplicate_edges_discards_the_lower_confidence_duplicate() -> None:
    edges = [
        {"id": "e1", "source": "a", "target": "b", "type": "rel", "confidence": 0.9},
        {"id": "e2", "source": "a", "target": "b", "type": "rel", "confidence": 0.3},
    ]

    manager = GraphEdgeQualityManager()
    merged = manager.merge_duplicate_edges(edges)

    assert [e["id"] for e in merged] == ["e1"]
    assert manager.detect_duplicate_edges(edges) == ["e2"]


def test_full_quality_pass_on_an_empty_payload_returns_only_the_envelope() -> None:
    """No optional section present: every stage is skipped, status stays ok."""
    result = LatticeBrainQuality().full_quality_pass({})

    assert result["status"] == "ok"
    assert isinstance(result["timestamp"], float)
    assert set(result) == {"status", "timestamp"}


def test_full_quality_pass_ignores_unrelated_payload_keys() -> None:
    result = LatticeBrainQuality().full_quality_pass({"unrelated": [1, 2, 3]})

    assert set(result) == {"status", "timestamp"}
