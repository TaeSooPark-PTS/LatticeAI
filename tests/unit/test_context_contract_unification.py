"""Chat and document generation share one context contract (v9.9.6).

Review 2026-07-27 P1 #5: "Chat/Docgen Context Assembler 단일화 — 같은 Brain이면
같은 품질 계약". Rendering still differs on purpose (documents want structured
sections, chat wants terse lines), but the *contract* is now one:

* the same ``approx_tokens`` accounting and an explicit budget,
* the same ``context_quality_signal`` shape,
* an assembly ``trace`` in the assembler's shape.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.context import ContextAssembler, approx_tokens
from latticeai.core.context_builder import (
    DEFAULT_DOCUMENT_CONTEXT_BUDGET,
    retrieve_context_for_generation,
)


class FakeGraph:
    """Minimal document-generation retrieval seam."""

    def __init__(self, results=None, fallback=""):
        self._results = results if results is not None else []
        self._fallback = fallback

    def search_for_document_generation(self, query, limit=10, **kwargs):
        return self._results

    def multi_hop_context(self, seed_ids, max_hops=2, **kwargs):
        return {"nodes": [], "edges": []}

    def context_for_query(self, query, limit=10, **kwargs):
        return self._fallback


def _doc(index: int, summary: str = "본문", type_="Document"):
    return {
        "id": f"doc-{index}",
        "type": type_,
        "title": f"문서 {index}",
        "summary": summary,
        "metadata": {"relative_path": f"docs/{index}.md"},
        "hybrid_score": 0.9,
    }


CHAT_QUALITY_KEYS = {"mode", "nodes", "limited", "reason"}
ASSEMBLY_TRACE_KEYS = {"budget_approx_tokens", "used_approx_tokens", "sections"}


def test_document_context_reports_the_same_quality_shape_as_chat():
    result = retrieve_context_for_generation(FakeGraph([_doc(1), _doc(2)]), "예산 보고서")
    quality = result["context_quality"]
    assert set(quality) >= CHAT_QUALITY_KEYS
    assert quality["mode"] == "hybrid"
    assert quality["nodes"] == 2
    assert quality["limited"] is False
    assert quality["reason"] is None


def test_thin_document_context_is_flagged_limited_exactly_like_chat():
    result = retrieve_context_for_generation(FakeGraph([_doc(1)]), "예산")
    quality = result["context_quality"]
    assert quality["nodes"] == 1
    assert quality["limited"] is True
    assert quality["reason"]


def test_lexical_fallback_reports_lexical_only_not_hybrid():
    result = retrieve_context_for_generation(
        FakeGraph([], fallback="관련 내용 일부"), "예산"
    )
    assert result["stats"]["method"] == "fallback"
    assert result["context_quality"]["mode"] == "lexical_only"
    assert result["context_quality"]["limited"] is True


def test_no_graph_or_empty_query_degrades_to_an_honest_none():
    for result in (
        retrieve_context_for_generation(None, "질문"),
        retrieve_context_for_generation(FakeGraph(), ""),
    ):
        assert result["context_quality"]["mode"] == "none"
        assert result["context_quality"]["nodes"] == 0
        assert result["context_markdown"] == ""
        assert set(result["trace"]) >= ASSEMBLY_TRACE_KEYS


def test_document_trace_matches_the_assembler_trace_shape():
    assembler = ContextAssembler(hybrid_search=lambda q, **kw: {"matches": []})
    chat_trace = assembler.assemble("질문", budget=2000).trace()
    doc_trace = retrieve_context_for_generation(FakeGraph([_doc(1)]), "질문")["trace"]
    assert set(chat_trace) == ASSEMBLY_TRACE_KEYS
    assert set(doc_trace) >= ASSEMBLY_TRACE_KEYS
    for section in doc_trace["sections"]:
        assert {"name", "source", "approx_tokens", "provenance"} <= set(section)


def test_document_context_respects_the_shared_token_budget():
    graph = FakeGraph([_doc(index, summary="가" * 600) for index in range(1, 9)])
    unbounded = retrieve_context_for_generation(graph, "질문", budget=100_000)
    bounded = retrieve_context_for_generation(graph, "질문", budget=120)
    assert approx_tokens(unbounded["context_markdown"]) > 120
    assert approx_tokens(bounded["context_markdown"]) <= 120
    assert bounded["stats"]["budget_trimmed"] is True
    assert unbounded["stats"]["budget_trimmed"] is False
    # The trim is reported, not silent.
    assert bounded["trace"]["used_approx_tokens"] <= bounded["trace"]["budget_approx_tokens"]


def test_default_budget_matches_the_chat_assembler_default():
    assert DEFAULT_DOCUMENT_CONTEXT_BUDGET == 2000
    default = retrieve_context_for_generation(FakeGraph([_doc(1)]), "질문")
    assert default["trace"]["budget_approx_tokens"] == 2000
