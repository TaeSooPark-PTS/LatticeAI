"""wp16 coverage — ``lattice_brain.graph._kg_common`` text/NLP helpers.

Chunking, concept/triple extraction, and the citation-locator maths. The
LLM-first extractors are exercised through a fake router injected at the
``get_llm_router`` seam (both the "no loop running" and "called from inside a
loop" shapes), so no model, no network, and no wall-clock is involved.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph import _kg_common as kg


class _FakeLoop:
    def __init__(self, running: bool):
        self._running = running

    def is_running(self) -> bool:
        return self._running


class _FakeRouter:
    """Minimal stand-in for the LLM router: one awaitable ``generate``."""

    def __init__(self, reply: str = "[]", *, model_id: str = "fake-model", fail=None):
        self.current_model_id = model_id
        self._reply = reply
        self._fail = fail
        self.prompts: list = []

    async def generate(self, prompt: str, **kwargs):
        self.prompts.append((prompt, kwargs))
        if self._fail is not None:
            raise self._fail
        return self._reply


def _install_router(monkeypatch, router, *, loop_running: bool) -> None:
    monkeypatch.setattr(kg, "ENABLE_LLM_EXTRACTION", True)
    monkeypatch.setattr(kg, "get_llm_router", lambda: router)
    monkeypatch.setattr(
        asyncio, "get_event_loop", lambda: _FakeLoop(loop_running)
    )


# ── chunking ─────────────────────────────────────────────────────────────────


def test_chunks_of_blank_text_is_empty() -> None:
    assert kg._chunks("") == []
    assert kg._chunks("   \n  ") == []


def test_chunk_strategy_falls_back_when_the_name_cannot_be_read() -> None:
    class _Unprintable:
        def __str__(self) -> str:
            raise RuntimeError("name unavailable")

    assert kg.chunk_strategy_for(_Unprintable()) == "plain"


def test_merge_small_sections_folds_a_short_tail_backwards() -> None:
    spans = [(0, 300, "Guide"), (300, 350, "Guide > Note")]

    merged = kg._merge_small_sections(spans, kg._MARKDOWN_MIN_SECTION_CHARS)

    # The undersized tail joins the previous section and keeps *its* heading.
    assert merged == [(0, 350, "Guide")]


def test_code_chunks_flush_the_pending_pack_before_a_monster_segment() -> None:
    body = "    value = 1\n" * 40
    pieces = kg.typed_chunks(
        "a = 1\n\ndef big():\n" + body, strategy="code", size=50, overlap=10
    )

    assert pieces[0]["text"] == "a = 1\n\n"
    assert pieces[0]["meta"]["start_char"] == 0
    assert len(pieces) > 2
    assert all(piece["meta"]["strategy"] == "code" for piece in pieces)
    # Every piece is still an exact slice at its recorded offset.
    cleaned = ("a = 1\n\ndef big():\n" + body).strip()
    for piece in pieces:
        start = piece["meta"]["start_char"]
        assert cleaned[start : start + len(piece["text"])] == piece["text"]


def test_typed_chunks_recovers_from_unusable_size_and_overlap() -> None:
    by_size = kg.typed_chunks("hello world", size="not-a-number")
    by_overlap = kg.typed_chunks("hello world", overlap=object())

    assert [piece["text"] for piece in by_size] == ["hello world"]
    assert [piece["text"] for piece in by_overlap] == ["hello world"]


# ── page offsets ─────────────────────────────────────────────────────────────


def test_pdf_page_offsets_rejects_malformed_page_entries() -> None:
    assert kg.pdf_page_offsets({"pages": [{"chars": 10}, "not-a-dict"]}) == []
    assert kg.pdf_page_offsets({"pages": [{"chars": 10}, {"chars": 5}]}) == [0, 12]


def test_page_for_offset_returns_none_for_unusable_input() -> None:
    assert kg.page_for_offset([0, 10], object()) is None
    assert kg.page_for_offset([0, "twelve"], 5) is None
    assert kg.page_for_offset([0, 10], 12) == 2


# ── LLM-backed concept extraction ────────────────────────────────────────────

_CONCEPT_REPLY = (
    "```json\n"
    '[{"concept": "Lattice AI", "importance": 0.9}, "Graph RAG", 42]\n'
    "```"
)


@pytest.mark.parametrize("loop_running", [False, True])
def test_llm_concept_extraction_parses_fenced_json(monkeypatch, loop_running) -> None:
    router = _FakeRouter(_CONCEPT_REPLY)
    _install_router(monkeypatch, router, loop_running=loop_running)

    concepts = kg._llm_extract_concepts("Lattice AI uses Graph RAG.", limit=5)

    assert concepts == ["Lattice AI", "Graph RAG"]
    prompt, kwargs = router.prompts[0]
    assert "Lattice AI uses Graph RAG." in prompt
    assert kwargs == {"max_tokens": 1024, "temperature": 0.1}


def test_llm_concept_extraction_returns_none_when_the_router_fails(
    monkeypatch,
) -> None:
    _install_router(
        monkeypatch, _FakeRouter(fail=RuntimeError("router down")), loop_running=False
    )

    assert kg._llm_extract_concepts("anything") is None


def test_llm_concept_extraction_returns_none_for_unusable_replies(monkeypatch) -> None:
    _install_router(monkeypatch, _FakeRouter('{"concept": "solo"}'), loop_running=False)
    assert kg._llm_extract_concepts("anything") is None

    _install_router(monkeypatch, _FakeRouter("[]"), loop_running=False)
    assert kg._llm_extract_concepts("anything") is None


def test_extract_concepts_prefers_the_llm_answer(monkeypatch) -> None:
    _install_router(monkeypatch, _FakeRouter(_CONCEPT_REPLY), loop_running=False)

    assert kg._extract_concepts("Lattice AI uses Graph RAG.") == [
        "Lattice AI",
        "Graph RAG",
    ]


# ── LLM-backed triple extraction ─────────────────────────────────────────────

_TRIPLE_REPLY = (
    "```json\n"
    '[{"subject": "Lattice AI", "relation": "\\uc0ac\\uc6a9\\ud568",'
    ' "object": "Graph RAG", "evidence": "Lattice AI uses Graph RAG.",'
    ' "confidence": 0.9},'
    ' {"subject": "Graph RAG", "object": "SQLite"}, "junk"]\n'
    "```"
)


@pytest.mark.parametrize("loop_running", [False, True])
def test_llm_triple_extraction_labels_its_evidence(monkeypatch, loop_running) -> None:
    router = _FakeRouter(_TRIPLE_REPLY)
    _install_router(monkeypatch, router, loop_running=loop_running)

    triples = kg._llm_extract_triples("text", ["Lattice AI", "Graph RAG"], limit=10)

    assert triples is not None
    verb, cooccurrence = triples
    # A named verb plus quoted evidence is a semantic edge at full weight…
    assert verb["relation"] == "사용함"
    assert verb["evidence"] == "verb"
    assert verb["weight"] == 0.9
    assert verb["context"] == "Lattice AI uses Graph RAG."
    # …a bare default relation with no evidence is adjacency, weighted down.
    assert cooccurrence["relation"] == "관련됨"
    assert cooccurrence["evidence"] == "cooccurrence"
    assert cooccurrence["weight"] == 0.28
    assert router.prompts[0][1] == {"max_tokens": 2048, "temperature": 0.1}


def test_llm_triple_extraction_returns_none_when_the_router_fails(monkeypatch) -> None:
    _install_router(
        monkeypatch, _FakeRouter(fail=ValueError("bad model")), loop_running=False
    )

    assert kg._llm_extract_triples("text", ["A", "B"]) is None


def test_llm_triple_extraction_returns_none_for_unusable_replies(monkeypatch) -> None:
    _install_router(monkeypatch, _FakeRouter('"not-a-list"'), loop_running=False)
    assert kg._llm_extract_triples("text", ["A", "B"]) is None

    _install_router(monkeypatch, _FakeRouter("[]"), loop_running=False)
    assert kg._llm_extract_triples("text", ["A", "B"]) is None


def test_extract_triples_prefers_the_llm_answer(monkeypatch) -> None:
    _install_router(monkeypatch, _FakeRouter(_TRIPLE_REPLY), loop_running=False)

    triples = kg._extract_triples("text", ["Lattice AI", "Graph RAG"])

    assert [triple["object"] for triple in triples] == ["Graph RAG", "SQLite"]


# ── rule-based extraction ────────────────────────────────────────────────────


def test_concept_rules_pick_up_quoted_and_backticked_terms() -> None:
    text = (
        'The `typed_chunks` helper and the "Digital Brain" surface stay stable; '
        "`build()` is skipped because it is a code expression."
    )

    concepts = kg._extract_concepts_rules(text, limit=12)

    assert "typed_chunks" in concepts
    assert "Digital Brain" in concepts
    assert "build()" not in concepts


@pytest.mark.parametrize(
    "concept,text,expected",
    [
        ("errorLog", "the errorLog rotates nightly", "Error"),
        ("main.py", "run main.py first", "Code"),
        ("Dashboard", "The Dashboard feature ships next week.", "Feature"),
    ],
)
def test_classify_node_type_uses_term_and_window_signals(
    concept: str, text: str, expected: str
) -> None:
    assert kg._classify_node_type(concept, text) == expected


def test_triple_rules_stop_at_the_requested_limit() -> None:
    triples = kg._extract_triples_rules(
        "Lattice AI uses Graph RAG. Graph RAG uses SQLite.",
        ["Lattice AI", "Graph RAG", "SQLite"],
        limit=1,
    )

    assert len(triples) == 1
    assert triples[0]["relation"] == "사용함"
    assert triples[0]["evidence"] == "verb"
