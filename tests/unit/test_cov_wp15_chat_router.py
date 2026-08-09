"""The ``/chat`` pipeline as assembled by ``create_chat_router``.

``test_chat_no_model`` / ``test_chat_request_isolation`` cover identity and the
missing-model refusal.  This file drives the remaining branches of the request
pipeline: the mirror-bridge failure, the unknown-model 404, the clear command,
the file-intent funnel and agent hand-off, context assembly (success and
failure), screenshot context on a non-streaming turn, the recall-success
counter, and the two responses that short-circuit the local model — document
generation and the hybrid cloud stream.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api import chat_documents, chat_hybrid
from latticeai.api.chat import create_chat_router
from latticeai.core.agent import AgentState
from latticeai.services.app_context import AppContext

DOC_REQUEST = "3분기 마케팅 전략 보고서 작성해줘"


# ── fakes ───────────────────────────────────────────────────────────────


class _Router:
    current_model_id = "local-default"
    loaded_model_ids = ["local-default"]

    def __init__(self, answer: str = "답변입니다") -> None:
        self.answer = answer
        self.calls: List[Dict[str, Any]] = []

    async def generate_as(
        self,
        model_id,
        message=None,
        context=None,
        max_tokens=None,
        temperature=None,
        image_data=None,
    ):
        self.calls.append({
            "model_id": model_id,
            "message": message,
            "context": context,
            "image_data": image_data,
        })
        return self.answer

    async def generate(self, *args, **kwargs):
        return await self.generate_as(self.current_model_id, *args, **kwargs)

    async def stream_generate_as(self, model_id, message, context, *_args):
        self.calls.append({"model_id": model_id, "message": message, "context": context})
        yield self.answer

    async def generate_document_as(self, model_id, message, system_prompt, **_kwargs):
        self.calls.append({"model_id": model_id, "message": message, "document": True})
        return "생성된 문서"


class _Graph:
    """Lexical/hybrid store just rich enough for the context-quality signal."""

    def __init__(self, matches=()) -> None:
        self.matches = list(matches)
        self.queries: List[str] = []

    def hybrid_search(self, query, top_k=6, **_kwargs):
        self.queries.append(query)
        return {"mode": "hybrid", "matches": list(self.matches)}


class _Funnel:
    def __init__(self) -> None:
        self.counts: Dict[str, int] = {}
        self.recalls = 0

    def increment(self, name: str) -> None:
        self.counts[name] = self.counts.get(name, 0) + 1

    def record_recall_success(self) -> None:
        self.recalls += 1


class _Assembler:
    def __init__(self, text: str = "", *, fail: Optional[str] = None) -> None:
        self.text = text
        self.fail = fail
        self.calls: List[Dict[str, Any]] = []

    def assemble(self, message, **kwargs):
        self.calls.append({"message": message, **kwargs})
        if self.fail:
            raise RuntimeError(self.fail)
        return SimpleNamespace(
            text=self.text,
            trace=lambda: {"budget": kwargs.get("budget"), "used": bool(self.text)},
        )


class _FileRuntime:
    """Minimal agent runtime that finishes a run in one pass."""

    async def plan(self, ctx, req, _lang, _user, model_id=None):
        ctx.plan = {"goal": req.message, "steps": []}
        ctx.state = AgentState.PLANNING

    def approve(self, ctx, _user, *, approved_by_human=False):
        ctx.state = AgentState.EXECUTING

    async def run_to_completion(self, ctx, *_args, **_kwargs):
        ctx.final_message = "파일을 만들었습니다."
        ctx.state = AgentState.DONE

    async def memory_update(self, *_args, **_kwargs):
        return None


# ── wiring ──────────────────────────────────────────────────────────────


def _build(tmp_path: Path, **overrides):
    router = overrides.pop("router", None) or _Router()
    history: List[Dict[str, Any]] = overrides.pop("history", [])
    saved: List[tuple] = []
    audit: List[Dict[str, Any]] = []
    traces: List[Dict[str, Any]] = []

    def get_history(**_scope):
        return list(history)

    fields: Dict[str, Any] = {
        "config": SimpleNamespace(
            is_public=False, auto_read_chat_paths=False, require_auth=True
        ),
        "model_router": router,
        "chat_service": SimpleNamespace(
            build_graph_trace=lambda *_args, **_kwargs: {"graph_nodes": []},
            record_trace=lambda **kwargs: traces.append(kwargs) or {"id": "trace-wp15"},
        ),
        "workspace_store": SimpleNamespace(),
        "workspace_graph": lambda: None,
        "require_user": lambda _request: "owner@example.com",
        "enforce_rate_limit": lambda *_args, **_kwargs: None,
        "get_history_user": lambda email, nickname: {
            "user_email": email,
            "user_nickname": nickname,
        },
        "save_to_history": lambda *args, **kwargs: saved.append((args, kwargs)),
        "append_audit_event": (
            lambda event, **extra: audit.append({"event": event, **extra})
        ),
        "clear_history": lambda *_args, **_kwargs: {"removed": 4, "kept": 0},
        "clear_conversation": lambda *_args, **_kwargs: {"removed": 2, "kept": 1},
        "get_history": get_history,
        "group_history_conversations": lambda entries: [{"count": len(entries)}],
        "get_conversation_messages": lambda *_args, **_kwargs: [],
        "conversation_title": lambda _item: "Conversation",
        "allowed_workspaces_for": lambda _user: {"org:one"},
        "enable_graph": False,
        "knowledge_graph": None,
        "public_model": "",
        "base_dir": tmp_path,
        "data_dir": tmp_path / "data",
    }
    fields.update(overrides)
    context = AppContext(**fields)
    app = FastAPI()
    app.include_router(create_chat_router(context))
    return SimpleNamespace(
        client=TestClient(app),
        router=router,
        saved=saved,
        audit=audit,
        traces=traces,
    )


def _post(wired, **body: Any):
    payload: Dict[str, Any] = {
        "message": "브레인 상태 알려줘",
        "stream": False,
        "conversation_id": "conv-1",
        # Pin the boundary so the persisted dial is never consulted.
        "network_mode": "local_only",
    }
    payload.update(body)
    return wired.client.post("/chat", json=payload)


# ── bridge / scope / model selection ────────────────────────────────────


def test_a_failing_chat_mirror_never_costs_the_user_their_answer(tmp_path, caplog):
    def broken_bridge(_role, _text, _source):
        raise RuntimeError("telegram bridge offline")

    wired = _build(tmp_path, on_chat_message=broken_bridge)

    with caplog.at_level("WARNING"):
        response = _post(wired)

    assert response.status_code == 200
    assert response.json()["response"] == "답변입니다"
    assert "chat message bridge failed: telegram bridge offline" in caplog.text


def test_history_is_read_through_the_authenticated_scope(tmp_path):
    entries = [{"role": "user", "content": "이전 질문", "user_email": "owner@example.com"}]
    wired = _build(tmp_path, history=entries)

    response = wired.client.get("/history")

    assert response.status_code == 200
    assert response.json() == entries


def test_requesting_a_model_that_is_not_loaded_is_a_localized_404(tmp_path):
    wired = _build(tmp_path)

    response = wired.client.post(
        "/chat",
        json={"message": "안녕", "stream": False, "model": "ghost-model"},
        headers={"X-Lattice-Language": "en"},
    )

    assert response.status_code == 404
    assert "ghost-model" in response.json()["detail"]
    assert wired.router.calls == []


# ── fast-path intents ───────────────────────────────────────────────────


def test_clear_command_is_answered_by_the_clear_intent(tmp_path):
    wired = _build(tmp_path)

    response = _post(wired, message="/clear")

    assert response.status_code == 200
    assert "현재 대화방 채팅창을 정리했습니다" in response.json()["response"]
    assert "제거 2개" in response.json()["response"]
    assert [entry["event"] for entry in wired.audit] == ["clear_command"]
    assert wired.audit[0]["scope"] == "conversation"
    assert wired.router.calls == []


def test_a_file_request_counts_in_the_funnel_and_is_handed_to_the_agent(tmp_path):
    funnel = _Funnel()
    wired = _build(tmp_path, funnel_metrics=funnel, chat_agent_runtime=_FileRuntime())

    response = _post(wired, message="파일 만들어줘")

    assert response.status_code == 200
    body = response.json()
    assert body["routed_to_agent"] is True
    assert body["response"] == "파일을 만들었습니다."
    assert funnel.counts["file_requests"] == 1
    # No inline target to write, so the local generate path is never used.
    assert wired.router.calls == []


# ── context assembly ────────────────────────────────────────────────────


def test_assembled_context_reaches_the_prompt_and_the_answer_trace(tmp_path):
    assembler = _Assembler("[기억] 지난 회의 요약")
    wired = _build(tmp_path, context_assembler=assembler)

    response = _post(wired, message="회의 요약 알려줘")

    assert response.status_code == 200
    assert "[기억] 지난 회의 요약" in wired.router.calls[0]["context"]
    assert assembler.calls[0]["budget"] == 2000
    assert assembler.calls[0]["conversation_id"] == "conv-1"
    assert assembler.calls[0]["user_email"] == "owner@example.com"
    assert wired.traces[0]["trace"]["context_assembly"] == {"budget": 2000, "used": True}


def test_a_broken_context_assembler_is_logged_and_the_turn_continues(tmp_path, caplog):
    wired = _build(tmp_path, context_assembler=_Assembler(fail="vector store offline"))

    with caplog.at_level("WARNING"):
        response = _post(wired, message="회의 요약 알려줘")

    assert response.status_code == 200
    assert "Context assembly skipped: vector store offline" in caplog.text
    assert "context_assembly" not in wired.traces[0]["trace"]


# ── attachments, recall metric ──────────────────────────────────────────


def test_a_screenshot_turn_appends_ocr_context_and_a_trimmed_history(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "latticeai.api.chat.extract_screenshot_context",
        lambda payload: "[SCREENSHOT INGESTION]\n- ocr_text:\n" + payload.upper(),
    )
    history = [
        {
            "role": "user",
            "content": "이전 질문",
            "user_email": "owner@example.com",
            "conversation_id": "conv-1",
        },
        {
            "role": "assistant",
            "content": "이미지를 업로드해 주세요",
            "conversation_id": "conv-1",
        },
    ]
    wired = _build(tmp_path, history=history)

    response = _post(wired, message="이 화면 설명해줘", image_data="shot")

    assert response.status_code == 200
    prompt_context = wired.router.calls[0]["context"]
    assert "[SCREENSHOT INGESTION]" in prompt_context
    assert "SHOT" in prompt_context
    assert "[RECENT CONVERSATION]" in prompt_context
    assert "user: 이전 질문" in prompt_context
    # include_image_missing_replies=False drops the "upload an image" reply.
    assert "업로드해 주세요" not in prompt_context
    assert wired.router.calls[0]["image_data"] == "shot"
    # The stored user turn records that an image came with it.
    assert wired.saved[0][0] == ("user", "이 화면 설명해줘\n[Image attached]")


def test_recall_success_is_counted_only_when_the_graph_actually_matched(tmp_path):
    funnel = _Funnel()
    graph = _Graph(matches=[{"id": "n1"}, {"id": "n2"}])
    wired = _build(tmp_path, funnel_metrics=funnel, enable_graph=True, knowledge_graph=graph)

    response = _post(wired, message="지난 회의 결론이 뭐였지")

    assert response.status_code == 200
    assert response.json()["context_quality"]["nodes"] == 2
    assert funnel.recalls == 1

    empty = _build(
        tmp_path,
        funnel_metrics=funnel,
        enable_graph=True,
        knowledge_graph=_Graph(matches=[]),
    )
    _post(empty, message="지난 회의 결론이 뭐였지")
    assert funnel.recalls == 1


# ── responses that replace the local model ──────────────────────────────


def test_a_document_request_is_answered_by_the_document_generator(tmp_path, monkeypatch):
    monkeypatch.setattr(
        chat_documents,
        "retrieve_context_for_generation",
        lambda *_args, **_kwargs: {
            "context_markdown": "## 2분기 회고",
            "sources": [{"type": "Document", "title": "2분기 회고", "source": "kg:1"}],
        },
    )
    wired = _build(tmp_path, enable_graph=True, knowledge_graph=_Graph())

    response = _post(wired, message=DOC_REQUEST)

    assert response.status_code == 200
    body = response.json()
    assert body["response"].startswith("생성된 문서")
    assert "참조된 지식 그래프 노드" in body["response"]
    assert body["trace_id"] == "trace-wp15"
    assert wired.router.calls[0]["document"] is True


def test_a_cloud_allowed_turn_streams_the_hybrid_response(tmp_path, monkeypatch):
    captured: Dict[str, Any] = {}

    async def fake_turn(**kwargs):
        captured.update(kwargs)
        yield "data: {}\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(chat_hybrid, "stream_hybrid_cloud_turn", fake_turn)
    wired = _build(tmp_path, enable_graph=True, knowledge_graph=_Graph())

    response = wired.client.post(
        "/chat",
        json={
            "message": "요즘 환율 어떻게 돼",
            "stream": True,
            "conversation_id": "conv-1",
            "network_mode": "cloud_allowed",
        },
    )

    assert response.status_code == 200
    assert response.headers["x-hybrid"] == "1"
    assert response.headers["x-network-mode"] == "cloud_allowed"
    assert response.text == "data: {}\n\ndata: [DONE]\n\n"
    assert captured["user_message"] == "요즘 환율 어떻게 돼"
    assert captured["user_email"] == "owner@example.com"
    # The local model was never asked to answer.
    assert wired.router.calls == []


@pytest.mark.parametrize("mode", ["local_only", "cloud_allowed"])
def test_the_hybrid_branch_needs_a_graph_before_it_can_send_anything(tmp_path, mode):
    wired = _build(tmp_path, enable_graph=False, knowledge_graph=None)

    response = _post(wired, message="요즘 환율 어떻게 돼", network_mode=mode)

    assert response.status_code == 200
    assert response.json()["response"] == "답변입니다"
