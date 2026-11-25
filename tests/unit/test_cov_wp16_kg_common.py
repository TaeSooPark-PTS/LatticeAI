from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph import _kg_common as kg
from lattice_brain.graph._kg_common import extraction as kg_extraction


class _FakeLoop:
    def __init__(self, running: bool):
        self._running = running

    def is_running(self) -> bool:
        return self._running


class _FakeRouter:
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
    monkeypatch.setattr(kg_extraction, "ENABLE_LLM_EXTRACTION", True)
    monkeypatch.setattr(kg_extraction, "get_llm_router", lambda: router)
    monkeypatch.setattr(asyncio, "get_event_loop", lambda: _FakeLoop(loop_running))


def test_chunks_of_blank_text_is_empty() -> None:
    assert kg._chunks("") == []
    assert kg._chunks("   \n  ") == []


_CONCEPT_REPLY = (
    '```json\n[{"concept": "Lattice AI", "importance": 0.9}, "Graph RAG", 42]\n```'
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
    assert verb["relation"] == "사용함"
    assert verb["evidence"] == "verb"
    assert verb["weight"] == 0.9
    assert verb["context"] == "Lattice AI uses Graph RAG."
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
