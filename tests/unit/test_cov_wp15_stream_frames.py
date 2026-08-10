"""SSE framing seams for chat streaming and the hybrid cloud branch.

``test_agent_loop_hardening`` already drives the happy ``agent_live_stream``
path.  This file covers what is left of ``latticeai.api.chat_stream``: the
streamed single-answer shape, the agent payload frames, the drain that flushes
observer events queued as the run finishes, and every ``stream_chat`` failure
branch (model error before/after the first chunk, grounding failure,
persistence failure, and the error trailer).  It also drives
``latticeai.api.chat_hybrid`` — the per-request mode override and both cloud
decisions.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from latticeai.api import chat_hybrid, chat_stream
from latticeai.core.network_boundary import NetworkBoundaryMode

# ── helpers ─────────────────────────────────────────────────────────────


def _drain(stream) -> List[str]:
    async def gather() -> List[str]:
        return [frame async for frame in stream]

    return asyncio.run(gather())


def _payloads(frames: List[str]) -> List[Dict[str, Any]]:
    """Decode the classic unnamed ``data:`` frames, skipping named events."""
    decoded = []
    for frame in frames:
        if frame.startswith("event: "):
            continue
        body = frame.split("data: ", 1)[1].strip()
        if body != "[DONE]":
            decoded.append(json.loads(body))
    return decoded


def _req(**overrides: Any) -> SimpleNamespace:
    base: Dict[str, Any] = {
        "message": "브레인 상태 알려줘",
        "max_tokens": 256,
        "temperature": 0.2,
        "conversation_id": "conv-1",
        "user_email": "fallback@example.com",
        "user_nickname": "테스터",
        "source": "web",
        "stream": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _StreamRouter:
    """Yields scripted chunks, optionally failing part-way through."""

    current_model_id = "local-default"

    def __init__(self, chunks=(), *, fail: Optional[str] = None) -> None:
        self.chunks = list(chunks)
        self.fail = fail
        self.calls: List[tuple] = []

    async def stream_generate_as(self, *args):
        self.calls.append(args)
        for chunk in self.chunks:
            yield chunk
        if self.fail:
            raise RuntimeError(self.fail)


class _ChatService:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.persisted: List[Dict[str, Any]] = []
        self.trace_calls: List[tuple] = []

    def build_graph_trace(self, question, graph, context, **kwargs):
        self.trace_calls.append((question, graph, context, kwargs))
        return {"question": question, "graph_nodes": []}

    async def persist_answer(self, **kwargs):
        self.persisted.append(kwargs)
        if self.fail:
            raise RuntimeError("history disk full")
        return {"id": "trace-wp15", "response": kwargs["response"]}


def _stream_chat(router, service, **overrides):
    kwargs: Dict[str, Any] = {
        "router": router,
        "chat_service": service,
        "knowledge_graph": None,
        "enable_graph": False,
        "notify": lambda *_args: None,
        "trace_seed": None,
        "effective_email": "owner@example.com",
        "history_meta": {"conversation_id": "conv-1"},
        "model_id": "local-default",
        "workspace_id": "org:one",
        "context_quality": {"mode": "none", "nodes": 0},
    }
    kwargs.update(overrides)
    request = kwargs.pop("req", None) or _req()
    context = kwargs.pop("context", "[CTX]")
    image_data = kwargs.pop("image_data", None)
    return _drain(chat_stream.stream_chat(request, context, image_data, **kwargs))


# ── single answer / payload framing ─────────────────────────────────────


def test_single_answer_response_streams_when_the_client_asked_for_sse():
    response = chat_stream.single_answer_response(
        _req(stream=True), "네트워크는 오프라인입니다.", model="network_status"
    )

    assert response.media_type == "text/event-stream"
    assert response.headers["x-model"] == "network_status"
    frames = _drain(response.body_iterator)
    assert frames[-1] == "data: [DONE]\n\n"
    # One answering surface, reported once: the frame body names the same
    # model as the X-Model header instead of falling back to the helper's
    # "system" default.
    assert _payloads(frames) == [
        {"chunk": "네트워크는 오프라인입니다.", "model": "network_status"}
    ]


def test_agent_payload_stream_repeats_the_payload_on_the_empty_trailer():
    router = SimpleNamespace(current_model_id="router-default")
    payload = {"status": "ok", "files": ["memo.md"]}

    frames = _drain(chat_stream.agent_payload_stream("파일을 만들었습니다.", payload, router=router))

    assert frames[-1] == "data: [DONE]\n\n"
    first, trailer = _payloads(frames)
    assert first == {
        "chunk": "파일을 만들었습니다.",
        "model": "router-default",
        "agent": payload,
    }
    assert trailer == {"chunk": "", "model": "router-default", "agent": payload}


def test_agent_payload_stream_honours_an_explicit_model_id():
    frames = _drain(
        chat_stream.agent_payload_stream(
            "done",
            {"status": "ok"},
            router=SimpleNamespace(current_model_id="router-default"),
            model_id="pinned-model",
        )
    )

    assert all(frame["model"] == "pinned-model" for frame in _payloads(frames))


def test_agent_live_stream_flushes_steps_observed_as_the_run_finishes():
    """A step observed from the run's completion callback still reaches the client.

    The main select loop has already broken out by then (the run task is done
    and the queue getter is still pending), so the frame can only come from the
    post-loop drain.
    """

    async def start(observer):
        asyncio.current_task().add_done_callback(
            lambda _task: observer({"phase": "review", "event": "verified"})
        )
        return {"response": "작업을 마쳤습니다.", "status": "ok"}

    frames = _drain(
        chat_stream.agent_live_stream(start, router=SimpleNamespace(current_model_id="m"))
    )

    step_frames = [frame for frame in frames if frame.startswith("event: agent_step\n")]
    assert len(step_frames) == 1
    assert json.loads(step_frames[0].split("data: ", 1)[1]) == {
        "phase": "review",
        "event": "verified",
    }
    assert frames[-1] == "data: [DONE]\n\n"
    assert _payloads(frames)[0]["chunk"] == "작업을 마쳤습니다."


# ── stream_chat failure branches ────────────────────────────────────────


def test_stream_chat_reports_a_failure_before_the_first_chunk(caplog):
    router = _StreamRouter(fail="model backend crashed")
    service = _ChatService()

    with caplog.at_level("WARNING"):
        frames = _stream_chat(router, service)

    error_frame, trailer = _payloads(frames)
    assert error_frame == {"error": "model backend crashed", "model": "local-default"}
    assert trailer["error"] == "model backend crashed"
    assert trailer["chunk"] == ""
    # With nothing generated the persisted answer is only the honest marker.
    assert service.persisted[0]["response"] == "[stream_error] model backend crashed"
    assert "chat stream failed" in caplog.text


def test_stream_chat_keeps_partial_text_when_the_model_dies_mid_stream():
    router = _StreamRouter(
        chunks=[SimpleNamespace(text="앞부분 "), "뒷부분"], fail="socket closed"
    )
    service = _ChatService()

    frames = _stream_chat(router, service)

    chunks = [frame for frame in _payloads(frames) if "chunk" in frame]
    assert [frame["chunk"] for frame in chunks[:2]] == ["앞부분 ", "뒷부분"]
    assert service.persisted[0]["response"] == "앞부분 뒷부분\n\n[stream_error] socket closed"
    assert _payloads(frames)[-1]["error"] == "socket closed"


def test_stream_chat_survives_a_grounding_assessment_failure(monkeypatch, caplog):
    def explode(*_args, **_kwargs):
        raise ValueError("token index corrupt")

    monkeypatch.setattr(chat_stream, "assess_answer_grounding", explode)
    service = _ChatService()

    with caplog.at_level("WARNING"):
        frames = _stream_chat(_StreamRouter(chunks=["답변"]), service)

    trailer = _payloads(frames)[-1]
    assert "grounding" not in trailer
    assert trailer["trace_id"] == "trace-wp15"
    assert "answer grounding assessment failed" in caplog.text
    # The answer itself is still persisted — annotation never blocks the turn.
    assert service.persisted[0]["response"] == "답변"


def test_stream_chat_still_closes_the_stream_when_persistence_fails(caplog):
    service = _ChatService(fail=True)

    with caplog.at_level("WARNING"):
        frames = _stream_chat(_StreamRouter(chunks=["답변"]), service)

    trailer = _payloads(frames)[-1]
    assert "trace_id" not in trailer
    assert trailer["context_quality"] == {"mode": "none", "nodes": 0}
    assert frames[-1] == "data: [DONE]\n\n"
    assert "chat stream persistence failed" in caplog.text


# ── hybrid cloud branch ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("request_mode", "expected"),
    [
        ("cloud_allowed", NetworkBoundaryMode.CLOUD_ALLOWED),
        ("hybrid", NetworkBoundaryMode.CLOUD_ALLOWED),
        ("local", NetworkBoundaryMode.LOCAL_ONLY),
        ("nonsense", NetworkBoundaryMode.LOCAL_ONLY),
    ],
)
def test_request_network_mode_override_never_consults_the_persisted_dial(
    request_mode, expected, monkeypatch
):
    def unexpected(**_kwargs):
        raise AssertionError("per-request override must win outright")

    monkeypatch.setattr(chat_hybrid, "resolve_active_network_mode", unexpected)

    assert (
        chat_hybrid.resolve_request_network_mode(
            request_mode=request_mode,
            user_email="owner@example.com",
            workspace_id="org:one",
        )
        is expected
    )


def _hybrid(**overrides):
    kwargs: Dict[str, Any] = {
        "req": _req(source="web"),
        "mode": NetworkBoundaryMode.CLOUD_ALLOWED,
        "knowledge_graph": SimpleNamespace(name="kg"),
        "enable_graph": True,
        "effective_email": "owner@example.com",
        "workspace_id": "org:one",
        "history_meta": {"conversation_id": "conv-1"},
        "history_user": {"user_email": "owner@example.com"},
        "chat_service": _ChatService(),
        "notify": lambda *_args: None,
        "model_id": "cloud-model",
    }
    kwargs.update(overrides)
    return chat_hybrid.maybe_hybrid_stream_response(**kwargs)


def test_hybrid_falls_back_to_local_when_there_is_no_graph_to_summarize():
    assert _hybrid(knowledge_graph=None) is None
    assert _hybrid(enable_graph=False) is None


def test_hybrid_streams_the_cloud_turn_with_boundary_headers(monkeypatch):
    captured: Dict[str, Any] = {}

    async def fake_turn(**kwargs):
        captured.update(kwargs)
        yield "data: {}\n\n"

    monkeypatch.setattr(chat_hybrid, "stream_hybrid_cloud_turn", fake_turn)

    response = _hybrid()

    assert response is not None
    assert response.media_type == "text/event-stream"
    assert response.headers["x-hybrid"] == "1"
    assert response.headers["x-model"] == "cloud-model"
    assert response.headers["x-network-mode"] == "cloud_allowed"
    assert _drain(response.body_iterator) == ["data: {}\n\n"]
    assert captured["user_message"] == "브레인 상태 알려줘"
    assert captured["workspace_id"] == "org:one"
    assert captured["mode"] is NetworkBoundaryMode.CLOUD_ALLOWED
    assert captured["source"] == "web"


def test_hybrid_defaults_the_model_header_when_no_model_is_pinned(monkeypatch):
    async def fake_turn(**_kwargs):
        yield "data: {}\n\n"

    monkeypatch.setattr(chat_hybrid, "stream_hybrid_cloud_turn", fake_turn)

    response = _hybrid(model_id=None)

    assert response is not None
    assert response.headers["x-model"] == "cloud"
