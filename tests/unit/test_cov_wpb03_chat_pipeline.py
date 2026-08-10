"""wpb03: the ``/chat`` pipeline when each optional signal comes back empty.

``test_cov_wp15_chat_router`` drives every enrichment that *fires*: assembled
memory text, screenshot OCR, a graph trace dict, prior turns to prepend. The
turns below are the ones a fresh install actually produces — an assembler with
nothing to add, an image with no readable text, a path-scanner that found no
paths, an empty conversation, and a trace delegate that returns no dict at all
(the legacy shape ``ChatService`` still accepts). None of them may cost the
user their answer, and none may fabricate a trace field.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.chat import create_chat_router
from latticeai.services.app_context import AppContext


class _Router:
    current_model_id = "local-default"
    loaded_model_ids = ["local-default"]

    def __init__(self, answer: str = "답변입니다") -> None:
        self.answer = answer
        self.calls: List[Dict[str, Any]] = []

    async def generate_as(self, model_id, message=None, context=None, max_tokens=None,
                          temperature=None, image_data=None):
        self.calls.append({"model_id": model_id, "message": message, "context": context,
                           "image_data": image_data})
        return self.answer

    async def generate(self, *args, **kwargs):
        return await self.generate_as(self.current_model_id, *args, **kwargs)

    async def stream_generate_as(self, model_id, message, context, *_args):
        self.calls.append({"model_id": model_id, "message": message, "context": context})
        yield self.answer


class _Assembler:
    """Context assembler that found nothing worth adding."""

    def __init__(self, text: str = "") -> None:
        self.text = text
        self.calls: List[Dict[str, Any]] = []

    def assemble(self, message, **kwargs):
        self.calls.append({"message": message, **kwargs})
        return SimpleNamespace(
            text=self.text,
            trace=lambda: {"budget": kwargs.get("budget"), "used": bool(self.text)},
        )


def _build(tmp_path: Path, **overrides: Any):
    router = overrides.pop("router", None) or _Router()
    history: List[Dict[str, Any]] = overrides.pop("history", [])
    saved: List[tuple] = []
    audit: List[Dict[str, Any]] = []
    traces: List[Dict[str, Any]] = []

    fields: Dict[str, Any] = {
        "config": SimpleNamespace(
            is_public=False, auto_read_chat_paths=False, require_auth=True
        ),
        "model_router": router,
        "chat_service": SimpleNamespace(
            build_graph_trace=lambda *_args, **_kwargs: {"graph_nodes": []},
            record_trace=lambda **kwargs: traces.append(kwargs) or {"id": "trace-wpb03"},
        ),
        "workspace_store": SimpleNamespace(),
        "workspace_graph": lambda: None,
        "require_user": lambda _request: "owner@example.com",
        "enforce_rate_limit": lambda *_args, **_kwargs: None,
        "get_history_user": lambda email, nickname: {
            "user_email": email, "user_nickname": nickname
        },
        "save_to_history": lambda *args, **kwargs: saved.append((args, kwargs)),
        "append_audit_event": lambda event, **extra: audit.append({"event": event, **extra}),
        "clear_history": lambda *_args, **_kwargs: {"removed": 0, "kept": 0},
        "clear_conversation": lambda *_args, **_kwargs: {"removed": 0, "kept": 0},
        "get_history": lambda **_scope: list(history),
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
    app = FastAPI()
    app.include_router(create_chat_router(AppContext(**fields)))
    return SimpleNamespace(
        client=TestClient(app), router=router, saved=saved, audit=audit, traces=traces
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


# ── context assembly ────────────────────────────────────────────────────────


def test_an_assembler_with_nothing_to_add_still_records_its_trace(tmp_path):
    assembler = _Assembler("")
    wired = _build(tmp_path, context_assembler=assembler)

    response = _post(wired, message="회의 요약 알려줘")

    assert response.status_code == 200
    assert response.json()["response"] == "답변입니다"
    assert assembler.calls[0]["budget"] == 2000
    # The prompt is the language hint alone — no empty memory block was pasted in.
    context = wired.router.calls[0]["context"].strip()
    assert context.startswith("[LANGUAGE: ")
    assert "\n" not in context, context
    assert wired.traces[0]["trace"]["context_assembly"] == {"budget": 2000, "used": False}


# ── attachments ─────────────────────────────────────────────────────────────


def test_an_image_with_no_readable_text_adds_no_screenshot_block(tmp_path, monkeypatch):
    monkeypatch.setattr("latticeai.api.chat.extract_screenshot_context", lambda _payload: "")
    wired = _build(tmp_path)

    response = _post(wired, message="이 화면 설명해줘", image_data="shot")

    assert response.status_code == 200
    assert "SCREENSHOT" not in wired.router.calls[0]["context"]
    assert wired.router.calls[0]["image_data"] == "shot"
    # The turn is still recorded as having carried an image.
    assert wired.saved[0][0] == ("user", "이 화면 설명해줘\n[Image attached]")


# ── local-path scanner ──────────────────────────────────────────────────────


def test_a_message_with_no_local_paths_raises_no_block_audit(tmp_path):
    wired = _build(
        tmp_path,
        config=SimpleNamespace(
            is_public=False, auto_read_chat_paths=True, require_auth=True
        ),
    )

    response = _post(wired, message="오늘 일정 정리해줘", allow_file_context=True)

    assert response.status_code == 200
    assert response.json()["response"] == "답변입니다"
    assert [entry["event"] for entry in wired.audit] == []


# ── trace delegate that returns no dict ─────────────────────────────────────


def test_a_trace_delegate_that_returns_nothing_never_invents_trace_fields(tmp_path):
    traces: List[Dict[str, Any]] = []
    wired = _build(
        tmp_path,
        chat_service=SimpleNamespace(
            build_graph_trace=lambda *_args, **_kwargs: None,
            record_trace=lambda **kwargs: traces.append(kwargs) or {"id": "trace-wpb03"},
        ),
    )

    response = _post(wired, message="지난 회의 결론이 뭐였지")

    assert response.status_code == 200
    body = response.json()
    assert body["response"] == "답변입니다"
    # The quality and grounding signals are still returned to the client…
    assert body["context_quality"]["nodes"] == 0
    assert body["grounding"]["status"]
    # …but nothing was grafted onto a trace that does not exist.
    assert traces[0]["trace"] is None


# ── streaming with an empty conversation ────────────────────────────────────


def test_a_first_streamed_turn_carries_no_recent_conversation_header(tmp_path):
    wired = _build(tmp_path, history=[])

    response = wired.client.post(
        "/chat",
        json={
            "message": "브레인 상태 알려줘",
            "stream": True,
            "conversation_id": "conv-new",
            "network_mode": "local_only",
        },
    )

    assert response.status_code == 200
    assert response.headers["x-model"] == "local-default"
    assert "답변입니다" in response.text
    context = wired.router.calls[0]["context"]
    assert "[RECENT CONVERSATION]" not in context
    assert context.startswith("[LANGUAGE: ")
    assert context.count("\n") == 1, context
