"""Brute-force vector recall must be capped *loudly*, never silently.

Review 2026-08 P1 #2: without sqlite-vec the search scored only the 10 000
most recently indexed rows while reporting as though it had seen the whole
index. On a 200 000-row brain that made recall "the newest 5%" with nothing in
the response saying so. These tests pin the two decisions that make the cap
honest — how it is resolved, and what the search reports about it.
"""
from __future__ import annotations

import pytest

from lattice_brain.graph.retrieval_vector import (
    DEFAULT_VECTOR_MAX_CANDIDATES,
    VECTOR_MAX_CANDIDATES_CEILING,
    VECTOR_MAX_CANDIDATES_ENV,
    KnowledgeGraphVectorMixin,
    _configured_vector_max_candidates,
)


class TestConfiguredCap:
    """``LATTICEAI_VECTOR_MAX_CANDIDATES`` resolution."""

    def test_unset_uses_documented_default(self, monkeypatch):
        monkeypatch.delenv(VECTOR_MAX_CANDIDATES_ENV, raising=False)
        assert _configured_vector_max_candidates() == DEFAULT_VECTOR_MAX_CANDIDATES

    @pytest.mark.parametrize("blank", ["", "   ", "\t"])
    def test_blank_is_treated_as_unset(self, monkeypatch, blank):
        monkeypatch.setenv(VECTOR_MAX_CANDIDATES_ENV, blank)
        assert _configured_vector_max_candidates() == DEFAULT_VECTOR_MAX_CANDIDATES

    def test_explicit_value_is_honoured(self, monkeypatch):
        monkeypatch.setenv(VECTOR_MAX_CANDIDATES_ENV, "250")
        assert _configured_vector_max_candidates() == 250

    def test_surrounding_whitespace_is_tolerated(self, monkeypatch):
        monkeypatch.setenv(VECTOR_MAX_CANDIDATES_ENV, "  250  ")
        assert _configured_vector_max_candidates() == 250

    @pytest.mark.parametrize("uncapped", ["0", "-1", "-9999"])
    def test_zero_or_negative_means_scan_everything(self, monkeypatch, uncapped):
        """``0`` is the documented opt-in to exhaustive (exact) recall."""
        monkeypatch.setenv(VECTOR_MAX_CANDIDATES_ENV, uncapped)
        assert _configured_vector_max_candidates() is None

    def test_absurd_value_is_clamped_to_the_ceiling(self, monkeypatch):
        monkeypatch.setenv(VECTOR_MAX_CANDIDATES_ENV, str(VECTOR_MAX_CANDIDATES_CEILING * 10))
        assert _configured_vector_max_candidates() == VECTOR_MAX_CANDIDATES_CEILING

    def test_garbage_falls_back_instead_of_breaking_search(self, monkeypatch):
        """An unparseable cap must not take every search down with it."""
        monkeypatch.setenv(VECTOR_MAX_CANDIDATES_ENV, "not-a-number")
        assert _configured_vector_max_candidates() == DEFAULT_VECTOR_MAX_CANDIDATES


class TestRecallReport:
    """The report is the only thing that makes a partial scan visible."""

    def test_full_scan_is_not_marked_truncated(self):
        report = KnowledgeGraphVectorMixin._recall_report(
            backend="brute-force", cap=10_000, candidates_total=42, candidates_scanned=42
        )
        assert report["truncated"] is False
        assert report["detail"] is None
        assert report["candidates_scanned"] == report["candidates_total"] == 42

    def test_partial_scan_is_marked_and_explained(self):
        report = KnowledgeGraphVectorMixin._recall_report(
            backend="brute-force",
            cap=10_000,
            candidates_total=200_000,
            candidates_scanned=10_000,
        )
        assert report["truncated"] is True
        detail = report["detail"]
        assert detail is not None
        # The numbers must be in the message: "scanned N of M" is the whole point.
        assert "10000" in detail and "200000" in detail
        # And it must say the cut is by recency, not relevance — that is the
        # part a reader would otherwise get wrong.
        assert "recency" in detail
        # It must name the escape hatch rather than leaving the reader stuck.
        assert VECTOR_MAX_CANDIDATES_ENV in detail

    def test_report_carries_the_backend_and_cap_verbatim(self):
        report = KnowledgeGraphVectorMixin._recall_report(
            backend="sqlite-vec", cap=None, candidates_total=5, candidates_scanned=5
        )
        assert report["backend"] == "sqlite-vec"
        assert report["max_candidates"] is None

    def test_empty_index_is_not_reported_as_truncated(self):
        report = KnowledgeGraphVectorMixin._recall_report(
            backend="brute-force", cap=10_000, candidates_total=0, candidates_scanned=0
        )
        assert report["truncated"] is False
        assert report["detail"] is None
