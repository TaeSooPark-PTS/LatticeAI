"""Knowledge-Graph-backed document generation: preparation and both responses.

``DocumentGenerationCoordinator`` decides whether a chat turn is a document
request, folds graph context into the prompt, and then answers either as SSE or
as JSON while keeping the per-conversation session, the sources footnote, and
the shared ``context_quality``/assembly trace contract intact.  The graph
retrieval seam is scripted so every branch is deterministic.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from latticeai.api import chat_documents
from latticeai.api.chat_documents import (
    DocumentGenerationCoordinator,
    DocumentPreparation,
)

DOC_REQUEST = "3분기 마케팅 전략 보고서 작성해줘"
SOURCES = [{"type": "Document", "title": "2분기 회고", "source": "kg:node-1"}]
FOOTNOTE_HEAD = "**참조된 지식 그래프 노드:**"
GRAPH = SimpleNamespace(name="kg")


# ── fakes ───────────────────────────────────────────────────────────────


class _DocRouter:
    """Scripted document model: streams chunks or answers in one shot."""

    def __init__(self, chunks=("초안 ", "본문"), *, fail: Optional[str] = None) -> None:
        self.chunks = list(chunks)
        self.fail = fail
        self.calls: List[Dict[str, Any]] = []

    def _record(self, model_id, message, system_prompt, max_tokens, temperature):
        self.calls.append({
            "model_id": model_id,
            "message": message,
            "system_prompt": system_prompt,
            "max_tokens": max_tokens,
            "temperature": temperature,
        })

    async def stream_generate_document_as(
        self, model_id, message, system_prompt, *, max_tokens, temperature
    ):
        self._record(model_id, message, system_prompt, max_tokens, temperature)
        for chunk in self.chunks:
            yield chunk
        if self.fail:
            raise RuntimeError(self.fail)

    async def generate_document_as(
        self, model_id, message, system_prompt, *, max_tokens, temperature
    ):
        self._record(model_id, message, system_prompt, max_tokens, temperature)
        return "".join(self.chunks)


class _ChatService:
    def __init__(self) -> None:
        self.persisted: List[Dict[str, Any]] = []

    async def persist_answer(self, **kwargs):
        self.persisted.append(kwargs)
        return {"id": "trace-doc", "response": kwargs["response"]}


def _req(**overrides: Any) -> SimpleNamespace:
    base: Dict[str, Any] = {
        "message": DOC_REQUEST,
        "conversation_id": "conv-doc",
        "max_tokens": 512,
        "temperature": 0.4,
        "stream": False,
        "user_nickname": "기획자",
        "source": "web",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _coordinator(router=None, *, service=None, graph=GRAPH, enable=True):
    notified: List[tuple] = []
    coordinator = DocumentGenerationCoordinator(
        model_router=router or _DocRouter(),
        knowledge_graph=graph,
        enable_graph=enable,
        chat_service=service or _ChatService(),
        notify=lambda *args: notified.append(args),
    )
    return coordinator, notified


def _script_retrieval(monkeypatch, payload):
    seen: List[Dict[str, Any]] = []

    def retrieve(graph, question, **kwargs):
        seen.append({"graph": graph, "question": question, **kwargs})
        if isinstance(payload, Exception):
            raise payload
        return payload

    monkeypatch.setattr(chat_documents, "retrieve_context_for_generation", retrieve)
    return seen


def _frames(response) -> List[str]:
    async def gather() -> List[str]:
        return [frame async for frame in response.body_iterator]

    return asyncio.run(gather())


def _payloads(frames: List[str]) -> List[Dict[str, Any]]:
    decoded = []
    for frame in frames:
        body = frame.split("data: ", 1)[1].strip()
        if body != "[DONE]":
            decoded.append(json.loads(body))
    return decoded


# ── preparation ─────────────────────────────────────────────────────────


def test_prepare_folds_graph_context_into_the_prompt(monkeypatch, caplog):
    seen = _script_retrieval(monkeypatch, {
        "context_markdown": "## 2분기 회고\n- 전환율 4%",
        "sources": SOURCES,
        "stats": {"budget_trimmed": True},
    })
    coordinator, _ = _coordinator()

    with caplog.at_level("DEBUG", logger="root"):
        preparation = coordinator.prepare(_req(), "[BASE]", workspace_id="org:one")

    assert preparation.is_document is True
    assert preparation.context.startswith("[BASE]")
    assert "[KNOWLEDGE GRAPH — Document Generation Context]" in preparation.context
    assert "전환율 4%" in preparation.context
    assert seen[0]["max_hops"] == 2
    assert seen[0]["max_results"] == 10
    assert seen[0]["allowed_workspaces"] == {"org:one"}
    assert "trimmed to the shared budget" in caplog.text


def test_prepare_leaves_the_prompt_alone_when_the_graph_has_nothing(monkeypatch):
    seen = _script_retrieval(monkeypatch, {"context_markdown": "", "sources": []})
    coordinator, _ = _coordinator()

    preparation = coordinator.prepare(_req(), "[BASE]", workspace_id=None)

    assert preparation.context == "[BASE]"
    assert preparation.retrieval == {"context_markdown": "", "sources": []}
    assert seen[0]["allowed_workspaces"] is None


def test_prepare_downgrades_to_plain_generation_when_retrieval_fails(monkeypatch, caplog):
    _script_retrieval(monkeypatch, RuntimeError("graph index locked"))
    coordinator, _ = _coordinator()

    with caplog.at_level("WARNING"):
        preparation = coordinator.prepare(_req(), "[BASE]", workspace_id="org:one")

    assert preparation.is_document is True
    assert preparation.retrieval is None
    assert preparation.context == "[BASE]"
    assert "Knowledge graph reinforcement skipped: graph index locked" in caplog.text


def test_prepare_skips_retrieval_entirely_for_an_ordinary_question(monkeypatch):
    def unexpected(*_args, **_kwargs):
        raise AssertionError("only document requests retrieve generation context")

    monkeypatch.setattr(chat_documents, "retrieve_context_for_generation", unexpected)
    coordinator, _ = _coordinator()

    preparation = coordinator.prepare(_req(message="안녕"), "[BASE]", workspace_id=None)

    assert preparation == DocumentPreparation(False, "[BASE]", None)


# ── response: nothing to do ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("preparation", "enable", "graph"),
    [
        (DocumentPreparation(False, "", None), True, SimpleNamespace(name="kg")),
        (DocumentPreparation(True, "", None), False, SimpleNamespace(name="kg")),
        (DocumentPreparation(True, "", None), True, None),
    ],
)
def test_response_declines_when_the_document_path_does_not_apply(preparation, enable, graph):
    coordinator, _ = _coordinator(graph=graph, enable=enable)

    assert _respond(coordinator, _req(), preparation) is None


def _respond(coordinator, req, preparation, **overrides):
    kwargs: Dict[str, Any] = {
        "model_id": "doc-model",
        "effective_email": "owner@example.com",
        "workspace_id": "org:one",
        "history_meta": {"conversation_id": "conv-doc"},
        "trace_seed": {},
    }
    kwargs.update(overrides)
    return asyncio.run(coordinator.response(req, preparation, **kwargs))


# ── response: JSON ──────────────────────────────────────────────────────


def test_json_document_answer_carries_footnote_trace_and_quality():
    router = _DocRouter(chunks=["# 전략\n", "본문"])
    service = _ChatService()
    coordinator, _ = _coordinator(router, service=service)
    trace_seed: Dict[str, Any] = {}
    preparation = DocumentPreparation(True, "[CTX]", {
        "context_markdown": "## 회고",
        "sources": SOURCES,
        "context_quality": {"mode": "hybrid", "nodes": 3},
        "trace": {"stages": ["graph"]},
    })

    response = _respond(coordinator, _req(stream=False), preparation, trace_seed=trace_seed)

    body = json.loads(bytes(response.body).decode("utf-8"))
    assert body["trace_id"] == "trace-doc"
    assert body["context_quality"] == {"mode": "hybrid", "nodes": 3}
    assert body["response"].startswith("# 전략")
    assert FOOTNOTE_HEAD in body["response"]
    assert "2분기 회고" in body["response"]
    # Shared context contract: the same signals chat records land on the trace.
    assert trace_seed["context_quality"] == {"mode": "hybrid", "nodes": 3}
    assert trace_seed["context_assembly"] == {"stages": ["graph"]}
    assert service.persisted[0]["workspace_id"] == "org:one"
    assert service.persisted[0]["user_email"] == "owner@example.com"
    assert router.calls[0]["max_tokens"] == 512
    assert router.calls[0]["temperature"] == 0.4


def test_json_document_answer_without_sources_omits_quality_and_uses_model_defaults():
    router = _DocRouter(chunks=["요약"])
    coordinator, _ = _coordinator(router)
    preparation = DocumentPreparation(True, "[CTX]", None)

    response = _respond(
        coordinator,
        _req(stream=False, max_tokens=0, temperature=0),
        preparation,
        trace_seed=None,
    )

    body = json.loads(bytes(response.body).decode("utf-8"))
    assert body["response"] == "요약"
    assert "context_quality" not in body
    assert router.calls[0]["max_tokens"] == 8192
    assert router.calls[0]["temperature"] == 0.3


def test_a_second_turn_in_the_same_conversation_revises_the_previous_document():
    router = _DocRouter(chunks=["첫 번째 문서"])
    coordinator, _ = _coordinator(router)
    preparation = DocumentPreparation(True, "[CTX]", {"context_markdown": "## 회고"})

    _respond(coordinator, _req(stream=False), preparation)
    router.chunks = ["수정된 문서"]
    _respond(coordinator, _req(stream=False), preparation)

    assert "이전에 생성한 문서" in router.calls[1]["system_prompt"]
    assert "첫 번째 문서" in router.calls[1]["system_prompt"]
    # A different conversation starts from a clean session.
    _respond(coordinator, _req(stream=False, conversation_id="other"), preparation)
    assert "이전에 생성한 문서" not in router.calls[2]["system_prompt"]


# ── response: SSE ───────────────────────────────────────────────────────


def test_streamed_document_ends_with_footnote_then_the_trace_frame():
    router = _DocRouter(chunks=["# 전략\n", "본문"])
    service = _ChatService()
    coordinator, _ = _coordinator(router, service=service)
    preparation = DocumentPreparation(True, "[CTX]", {
        "context_markdown": "## 회고",
        "sources": SOURCES,
    })

    response = _respond(coordinator, _req(stream=True), preparation)

    assert response.media_type == "text/event-stream"
    assert response.headers["x-doc-gen"] == "true"
    assert response.headers["x-model"] == "doc-model"
    frames = _frames(response)
    assert frames[-1] == "data: [DONE]\n\n"
    payloads = _payloads(frames)
    assert [frame["text"] for frame in payloads[:2]] == ["# 전략\n", "본문"]
    assert FOOTNOTE_HEAD in payloads[2]["text"]
    assert payloads[-1]["trace_id"] == "trace-doc"
    assert payloads[-1]["trace"]["id"] == "trace-doc"
    # Exactly one persisted answer, and it includes the footnote.
    assert len(service.persisted) == 1
    assert service.persisted[0]["response"].startswith("# 전략")
    assert FOOTNOTE_HEAD in service.persisted[0]["response"]


def test_streamed_document_failure_is_reported_and_still_persisted(caplog):
    router = _DocRouter(chunks=["앞부분"], fail="model backend crashed")
    service = _ChatService()
    coordinator, _ = _coordinator(router, service=service)
    preparation = DocumentPreparation(True, "[CTX]", {"context_markdown": "", "sources": []})

    with caplog.at_level("WARNING"):
        frames = _frames(_respond(coordinator, _req(stream=True), preparation))

    payloads = _payloads(frames)
    assert payloads[0]["text"] == "앞부분"
    assert payloads[1]["error"] == "model backend crashed"
    assert service.persisted[0]["response"] == "앞부분\n\n[stream_error] model backend crashed"
    assert "document stream failed" in caplog.text


def test_streamed_document_that_never_produced_text_persists_only_the_error():
    router = _DocRouter(chunks=[], fail="no model loaded")
    service = _ChatService()
    coordinator, _ = _coordinator(router, service=service)
    preparation = DocumentPreparation(True, "[CTX]", None)

    frames = _frames(_respond(coordinator, _req(stream=True), preparation))

    assert _payloads(frames)[0]["error"] == "no model loaded"
    assert service.persisted[0]["response"] == "[stream_error] no model loaded"
