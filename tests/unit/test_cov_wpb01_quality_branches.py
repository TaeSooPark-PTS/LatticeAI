"""wpb01 branch coverage — ``lattice_brain.quality``.

Drives the never-taken direction of the quality layer's decisions:

* the reranker's "candidate already carries a ``score``" path (no fused_score
  back-fill),
* the *losing* side of the keep-the-best ``merge`` reducer (by content
  prefix), which is the branch that actually proves the reducer keeps the
  winner,
* and the duplicate-hit side of ``detect_duplicate_edges``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.quality import (  # noqa: E402
    GraphEdgeQualityManager,
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


def test_detect_duplicate_edges_flags_the_second_edge_on_a_shared_key() -> None:
    edges = [
        {"id": "e1", "source": "a", "target": "b", "type": "rel", "confidence": 0.9},
        {"id": "e2", "source": "a", "target": "b", "type": "rel", "confidence": 0.3},
    ]

    manager = GraphEdgeQualityManager()

    assert manager.detect_duplicate_edges(edges) == ["e2"]
