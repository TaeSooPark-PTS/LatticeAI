"""P1b coverage — multimodal facts, ingestion DTOs, quiet, extraction leftovers.

Drives the remaining Brain Core compute doors with real tiny files (PNG via
Pillow, sidecar `.srt` / `.vtt`, a few bytes of text) and mocked ffmpeg /
pytesseract / LLM-router seams. No product code is patched except public
callables those modules already expose as test seams.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, List

import pytest
from PIL import Image

from lattice_brain import quiet as quiet_mod
from lattice_brain.graph._kg_common import extraction as kg_extraction
from lattice_brain.graph._kg_common.extraction import (
    COOCCURRENCE_CONCEPT_LIMIT,
    _extract_concepts,
    _extract_concepts_rules,
    _extract_triples,
    _extract_triples_rules,
    _llm_extract_concepts,
    _llm_extract_triples,
    _semantic_items,
    _topic_candidates,
)
from lattice_brain.quiet import quiet


class _FakeLoop:
    def __init__(self, running: bool) -> None:
        self._running = running

    def is_running(self) -> bool:
        return self._running


class _FakeRouter:
    def __init__(self, reply: str = "[]", *, model_id: str = "fake-model", fail=None):
        self.current_model_id = model_id
        self._reply = reply
        self._fail = fail
        self.prompts: List[Any] = []

    async def generate(self, prompt: str, **kwargs):
        self.prompts.append((prompt, kwargs))
        if self._fail is not None:
            raise self._fail
        return self._reply


def _install_router(monkeypatch, router, *, loop_running: bool) -> None:
    monkeypatch.setattr(kg_extraction, "ENABLE_LLM_EXTRACTION", True)
    monkeypatch.setattr(kg_extraction, "get_llm_router", lambda: router)
    monkeypatch.setattr(asyncio, "get_event_loop", lambda: _FakeLoop(loop_running))


@pytest.fixture(params=[False, True])
def loop_running(request):
    return request.param


# ── helpers ──────────────────────────────────────────────────────────────────


def _write_png(path: Path, *, size=(8, 8), colour=(200, 40, 40), mode: str = "RGB") -> Path:
    image = Image.new(mode, size, colour if mode != "P" else 1)
    image.save(path, format="PNG")
    return path


class _FakeLoop:
    def __init__(self, running: bool) -> None:
        self._running = running

    def is_running(self) -> bool:
        return self._running


class _FakeRouter:
    def __init__(self, reply: str = "[]", *, model_id: str = "fake-model", fail=None):
        self.current_model_id = model_id
        self._reply = reply
        self._fail = fail
        self.prompts: List[Any] = []

    async def generate(self, prompt: str, **kwargs):
        self.prompts.append((prompt, kwargs))
        if self._fail is not None:
            raise self._fail
        return self._reply


def _install_router(monkeypatch, router, *, loop_running: bool) -> None:
    monkeypatch.setattr(kg_extraction, "ENABLE_LLM_EXTRACTION", True)
    monkeypatch.setattr(kg_extraction, "get_llm_router", lambda: router)
    monkeypatch.setattr(asyncio, "get_event_loop", lambda: _FakeLoop(loop_running))


def test_runtime_package_lazy_exports():
    import lattice_brain.runtime as runtime_pkg

    assert runtime_pkg.HookContext is not None
    assert runtime_pkg.HookResult is not None
    assert callable(runtime_pkg.dispatch_tool)
    with pytest.raises(AttributeError):
        _ = runtime_pkg.not_a_runtime_export
    # A second lookup hits the now-bound module attribute, still via __getattr__
    # only for unknown names.
    with pytest.raises(AttributeError):
        runtime_pkg.__getattr__("still_missing")

def test_quiet_without_active_exception_and_disabled_logger(monkeypatch):
    quiet()
    quiet("no-exc")

    monkeypatch.setattr(quiet_mod.logger, "isEnabledFor", lambda _level: False)

    def inner():
        raise ValueError("inner-boom")

    try:
        inner()
    except ValueError:
        quiet("nested-disabled")

    class NoName:
        pass

    err = RuntimeError("x")
    monkeypatch.setattr(sys, "exc_info", lambda: (NoName, err, None))
    quiet("no-tb")
    monkeypatch.setattr(sys, "exc_info", lambda: (None, err, None))
    quiet()

def test_quiet_logs_when_enabled(monkeypatch):
    recorded = []

    def fake_log(level, msg, *args, **kwargs):
        recorded.append((level, msg, args, kwargs))

    monkeypatch.setattr(quiet_mod.logger, "isEnabledFor", lambda _level: True)
    monkeypatch.setattr(quiet_mod.logger, "log", fake_log)

    def inner():
        raise RuntimeError("logged")

    try:
        inner()
    except RuntimeError:
        quiet("because")
        quiet()

    assert recorded
    assert any("suppressed" in entry[1] for entry in recorded)
    assert any("because" in str(entry[2]) for entry in recorded)

def test_llm_extract_concepts_guard_paths(monkeypatch):
    monkeypatch.setattr(kg_extraction, "ENABLE_LLM_EXTRACTION", False)
    monkeypatch.setattr(kg_extraction, "get_llm_router", lambda: _FakeRouter())
    assert _llm_extract_concepts("text") is None

    monkeypatch.setattr(kg_extraction, "ENABLE_LLM_EXTRACTION", True)
    monkeypatch.setattr(kg_extraction, "get_llm_router", lambda: None)
    assert _llm_extract_concepts("text") is None

    monkeypatch.setattr(kg_extraction, "get_llm_router", lambda: _FakeRouter(model_id=""))
    assert _llm_extract_concepts("text") is None

def test_llm_extract_concepts_parses_and_rejects(monkeypatch, loop_running):
    _install_router(
        monkeypatch,
        _FakeRouter('```\n[{"concept": "Alpha"}, "Beta", 7, {"nope": 1}]\n```'),
        loop_running=loop_running,
    )
    assert _llm_extract_concepts("Alpha and Beta", limit=3) == ["Alpha", "Beta"]

    _install_router(monkeypatch, _FakeRouter('{"concept": "solo"}'), loop_running=False)
    assert _llm_extract_concepts("x") is None

    _install_router(monkeypatch, _FakeRouter("[]"), loop_running=False)
    assert _llm_extract_concepts("x") is None

    _install_router(monkeypatch, _FakeRouter("not-json", fail=None), loop_running=False)
    # valid string but not JSON
    monkeypatch.setattr(kg_extraction, "get_llm_router", lambda: _FakeRouter("not-json"))
    monkeypatch.setattr(asyncio, "get_event_loop", lambda: _FakeLoop(False))
    assert _llm_extract_concepts("x") is None

    _install_router(monkeypatch, _FakeRouter(fail=RuntimeError("down")), loop_running=True)
    assert _llm_extract_concepts("x") is None

def test_extract_concepts_prefers_llm_then_falls_back(monkeypatch):
    _install_router(monkeypatch, _FakeRouter('[{"concept": "OnlyLLM"}]'), loop_running=False)
    assert _extract_concepts("Lattice AI uses Graph RAG.") == ["OnlyLLM"]

    monkeypatch.setattr(kg_extraction, "ENABLE_LLM_EXTRACTION", False)
    monkeypatch.setattr(kg_extraction, "get_llm_router", lambda: None)
    rules = _extract_concepts("Lattice AI uses Graph RAG.")
    assert any("lattice" in c.lower() for c in rules)

def test_llm_extract_triples_guard_and_weighting(monkeypatch):
    monkeypatch.setattr(kg_extraction, "ENABLE_LLM_EXTRACTION", False)
    monkeypatch.setattr(kg_extraction, "get_llm_router", lambda: _FakeRouter())
    assert _llm_extract_triples("t", ["A", "B"]) is None

    monkeypatch.setattr(kg_extraction, "ENABLE_LLM_EXTRACTION", True)
    monkeypatch.setattr(kg_extraction, "get_llm_router", lambda: None)
    assert _llm_extract_triples("t", ["A", "B"]) is None

    monkeypatch.setattr(kg_extraction, "get_llm_router", lambda: _FakeRouter(model_id=""))
    assert _llm_extract_triples("t", ["A", "B"]) is None

    reply = (
        "```json\n"
        "["
        '{"subject": "A", "relation": "사용함", "object": "B",'
        ' "evidence": "A uses B.", "confidence": 5},'
        '{"subject": "B", "object": "C", "confidence": -2},'
        '{"subject": "C", "object": "D", "relation": "관련됨",'
        ' "evidence": "they sit together"},'
        '{"missing": true}'
        "]\n"
        "```"
    )
    _install_router(monkeypatch, _FakeRouter(reply), loop_running=False)
    triples = _llm_extract_triples("text", ["A", "B", "C"], limit=10)
    assert triples is not None
    assert triples[0]["evidence"] == "verb"
    assert triples[0]["weight"] == 1.0
    assert triples[1]["evidence"] == "cooccurrence"
    assert triples[1]["weight"] == pytest.approx(0.035)
    assert triples[2]["evidence"] == "cooccurrence"

    _install_router(monkeypatch, _FakeRouter("[]"), loop_running=True)
    assert _llm_extract_triples("t", ["A", "B"]) is None

    _install_router(monkeypatch, _FakeRouter('"nope"'), loop_running=False)
    assert _llm_extract_triples("t", ["A", "B"]) is None

    _install_router(
        monkeypatch,
        _FakeRouter('[{"subject": "A", "object": "B", "confidence": "bad"}]'),
        loop_running=False,
    )
    assert _llm_extract_triples("t", ["A", "B"]) is None

def test_extract_triples_prefers_llm_then_rules(monkeypatch):
    _install_router(
        monkeypatch,
        _FakeRouter('[{"subject": "A", "object": "B", "relation": "사용함", "evidence": "A uses B."}]'),
        loop_running=False,
    )
    llm = _extract_triples("A uses B.", ["A", "B"])
    assert llm[0]["subject"] == "A"

    monkeypatch.setattr(kg_extraction, "ENABLE_LLM_EXTRACTION", False)
    rules = _extract_triples("Lattice AI uses Graph RAG.", ["Lattice AI", "Graph RAG"])
    assert rules
    assert rules[0]["evidence"] == "verb"

def test_extract_concepts_rules_covers_reject_keep_and_dedup():
    assert _extract_concepts_rules("") == []
    assert _extract_concepts_rules(None) == []

    text = (
        'The `typed_chunks` helper and `build()` expression plus "Digital Brain" '
        'and "  " and "the" and `12` sit next to Lattice AI and VS Code. '
        "Hello there everyone. "
        "Python is great. Python is used everywhere. "
        "I like Claude and FastAPI here. "
        "The system also mentions gpt-4o and a-b and mlx-vlm. "
        "그래프RAG와 멀티모달AI는 중요하다. "
        "지식그래프는 중요하다. "
        "도구는 중요하고 도구가 필요하다. "
        "기능은 무시된다. "
        "짧은는 한번만. "
    )
    concepts = _extract_concepts_rules(text, limit=20)
    lowered = {c.lower() for c in concepts}
    assert "typed_chunks" in lowered
    assert "digital brain" in lowered
    assert "build()" not in lowered
    assert "12" not in lowered
    assert "the" not in lowered
    assert "gpt-4o" in lowered
    assert "a-b" not in lowered
    assert "mlx-vlm" in lowered
    assert any("lattice" in c.lower() for c in concepts)
    # "Lattice" should be dropped when it never appears without " AI".
    assert "lattice" not in lowered
    assert "python" in lowered
    assert "claude" in lowered
    assert "hello" not in lowered
    assert any("그래프" in c or "rag" in c.lower() for c in concepts)

    # Prefix that is *not* followed by space/hyphen must stay ("Lat" vs "Lattice AI").
    mixed = _extract_concepts_rules("Lat and Lattice AI both appear independently.")
    lowered_mixed = {c.lower() for c in mixed}
    assert "lattice ai" in lowered_mixed

    # Shorter term kept when it also appears alone.
    both = _extract_concepts_rules("Claude ships Claude Sonnet and Claude too.")
    lowered_both = {c.lower() for c in both}
    assert "claude" in lowered_both

    limited = _extract_concepts_rules(
        "Alpha Beta and Gamma Delta and Epsilon Zeta and Eta Theta.",
        limit=2,
    )
    assert len(limited) <= 2

def test_extract_triples_rules_guards_dedup_limit_and_enumeration():
    assert _extract_triples_rules("anything", ["only"]) == []
    assert _extract_triples_rules("anything", []) == []

    short = _extract_triples_rules("Alpha.", ["Alpha", "Bravo"])
    assert short == []

    one_present = _extract_triples_rules(
        "Alpha sits here alone in this long enough sentence.",
        ["Alpha", "Bravo"],
    )
    assert one_present == []

    verb = _extract_triples_rules(
        "Lattice AI uses Graph RAG. Graph RAG uses SQLite later.",
        ["Lattice AI", "Graph RAG", "SQLite"],
    )
    assert verb
    assert verb[0]["evidence"] == "verb"

    # Same undirected pair + edge is dropped the second time.
    dup = _extract_triples_rules(
        "Alpha uses Bravo today. Bravo uses Alpha tomorrow as well.",
        ["Alpha", "Bravo"],
    )
    assert len(dup) == 1

    limited = _extract_triples_rules(
        "Lattice AI uses Graph RAG. Graph RAG uses SQLite.",
        ["Lattice AI", "Graph RAG", "SQLite"],
        limit=1,
    )
    assert len(limited) == 1

    names = ["Alpha", "Bravo", "Charlie", "Delta", "Echo"]
    listed = "Alpha Bravo Charlie Delta Echo listed here together."
    assert len(names) > COOCCURRENCE_CONCEPT_LIMIT
    enumerated = _extract_triples_rules(listed, names)
    assert enumerated == []

    # A verb-backed sentence with many concepts is *not* dropped.
    used = _extract_triples_rules(
        "Alpha uses Bravo Charlie Delta Echo Foxtrot today.",
        names + ["Foxtrot"],
    )
    assert used

def test_semantic_items_and_topic_candidates():
    assert _semantic_items("") == []
    assert _semantic_items("short") == []

    text = (
        "We decided to keep the write path native.\n"
        "결정했다 이 방향으로 가기로 한다.\n"
        "TODO: implement the extract seam now.\n"
        "다음 작업을 해야 한다 구현 확인.\n"
        "We decided another long enough decision line.\n"
        "TODO: second task item is listed here.\n"
        "TODO: third task item is listed here.\n"
        "TODO: fourth task item is listed here.\n"
        "TODO: fifth task item is listed here.\n"
        "TODO: sixth task item is listed here.\n"
    )
    items = _semantic_items(text)
    assert any(item["type"] == "Decision" for item in items)
    assert any(item["type"] == "Task" for item in items)
    assert len(items) <= 8

    both = _semantic_items("We decided the TODO: implement this task now")
    kinds = {item["type"] for item in both}
    assert kinds == {"Decision", "Task"}

    topics = _topic_candidates("Lattice AI uses Graph RAG.", limit=4)
    assert topics
    assert len(topics) <= 4

    fallback = _topic_candidates("the and for with xyzabc defghi 참고", limit=3)
    # Rule extractor finds nothing useful; token fallback still yields xyzabc.
    assert any(token.lower() in {"xyzabc", "defghi"} for token in fallback)

    empty = _topic_candidates("the and for with this that from into", limit=4)
    assert empty == [] or all(token.lower() not in {"the", "and"} for token in empty)

    capped = _topic_candidates("abc def ghi jkl mno pqr stu", limit=2)
    assert len(capped) <= 2
