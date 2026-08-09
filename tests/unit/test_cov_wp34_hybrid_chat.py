"""Coverage for the hybrid cloud chat turn (wp34).

The SSE generator is the product's only egress path, so the tests drive it end
to end with a fake KG store and a fake adapter and assert on the event stream:
what refused, what streamed, what was persisted, and what the client is told
when the provider fails mid-turn.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from latticeai.core.network_boundary import NetworkBoundaryMode
from latticeai.services import hybrid_chat
from latticeai.services.cloud_streaming import CloudTurnResult
from latticeai.services.cloud_token_guard import TokenBudget


class _Store:
    """Minimal hybrid_search surface used by build_minimal_context."""

    def __init__(self, matches=None):
        self._matches = matches if matches is not None else [
            {
                "node_id": "node-1",
                "title": "릴리스 절차",
                "summary": "태그를 만들고 CI를 통과시킨다",
                "type": "Decision",
                "score": 0.9,
                "metadata": {},
            }
        ]

    def hybrid_search(self, query, *, top_k=20, allowed_workspaces=None, include_legacy_global=False):
        return {"mode": "hybrid", "matches": list(self._matches)}


class _StreamingAdapter:
    provider_name = "fake-cloud"
    default_model = "fake-model"

    def __init__(self, pieces=("hello ", "world"), fail_at=None):
        self.pieces = pieces
        self.fail_at = fail_at
        self.calls = []

    def stream(self, *, system, user, context, model=None):
        self.calls.append({"system": system, "user": user, "context": context, "model": model})

        async def _gen():
            for index, piece in enumerate(self.pieces):
                if self.fail_at == index:
                    raise RuntimeError("provider dropped the connection")
                yield piece

        return _gen()


def _events(**kwargs):
    async def _drain():
        return [chunk async for chunk in hybrid_chat.stream_hybrid_cloud_turn(**kwargs)]

    return asyncio.run(_drain())


def _payloads(events):
    out = []
    for chunk in events:
        body = chunk[len("data: "):].strip()
        if body == "[DONE]":
            continue
        out.append(json.loads(body))
    return out


# ── non-streaming turn ───────────────────────────────────────────────────────


def test_non_streaming_turn_refuses_over_the_token_budget(monkeypatch):
    monkeypatch.setattr(
        hybrid_chat, "budget_for", lambda _key: TokenBudget(max_tokens_per_turn=0)
    )

    with pytest.raises(PermissionError, match="cloud token guard"):
        asyncio.run(
            hybrid_chat.run_hybrid_cloud_turn(
                user_message="릴리스 절차 알려줘",
                knowledge_graph=_Store(),
                mode=NetworkBoundaryMode.CLOUD_ALLOWED,
                adapter=_StreamingAdapter(),
                user_email="wp34-run@example.com",
            )
        )


# ── streaming turn: refusals ─────────────────────────────────────────────────


def test_local_only_mode_never_opens_the_cloud_path():
    events = _events(
        user_message="릴리스 절차 알려줘",
        knowledge_graph=_Store(),
        mode=NetworkBoundaryMode.LOCAL_ONLY,
        adapter=_StreamingAdapter(),
    )

    payloads = _payloads(events)
    assert payloads[0]["type"] == "error"
    assert "local_only" in payloads[0]["detail"]
    assert events[-1] == "data: [DONE]\n\n"


def test_streaming_turn_refuses_over_the_token_budget(monkeypatch):
    monkeypatch.setattr(
        hybrid_chat, "budget_for", lambda _key: TokenBudget(max_tokens_per_turn=0)
    )
    adapter = _StreamingAdapter()

    events = _events(
        user_message="릴리스 절차 알려줘",
        knowledge_graph=_Store(),
        mode="cloud_allowed",
        adapter=adapter,
    )

    payloads = _payloads(events)
    assert payloads[0]["type"] == "error"
    assert "cloud token guard" in payloads[0]["detail"]
    assert adapter.calls == [], "nothing may reach the provider after a refusal"
    assert events[-1] == "data: [DONE]\n\n"


# ── streaming turn: happy path ───────────────────────────────────────────────


class _ChatService:
    def __init__(self, fail=False):
        self.persisted = []
        self.fail = fail

    async def persist_entry(self, role, text, *, history_meta, history_user):
        if self.fail:
            raise RuntimeError("history file is read-only")
        self.persisted.append((role, text, history_meta, history_user))


def test_streaming_turn_emits_context_tokens_and_a_done_summary():
    adapter = _StreamingAdapter(pieces=("안녕", "하세요"))
    chat_service = _ChatService()
    notified = []

    events = _events(
        user_message="릴리스 절차 알려줘",
        knowledge_graph=_Store(),
        mode=NetworkBoundaryMode.CLOUD_ALLOWED,
        adapter=adapter,
        model="fake-model",
        workspace_id="w1",
        user_email="wp34-stream@example.com",
        chat_service=chat_service,
        history_meta={"conversation_id": "c1"},
        history_user={"user_email": "wp34-stream@example.com"},
        notify=lambda role, text, source: notified.append((role, text, source)),
        source="web",
    )

    payloads = _payloads(events)
    assert [p["type"] for p in payloads] == ["hybrid_context", "token", "token", "hybrid_done"]
    assert payloads[0]["node_ids"] == ["node-1"]
    assert payloads[0]["titles"] == ["릴리스 절차"]
    assert [p["text"] for p in payloads[1:3]] == ["안녕", "하세요"]

    done = payloads[-1]
    assert done["answer"] == "안녕하세요"
    assert done["provider"] == "fake-cloud"
    assert done["sent_node_ids"] == ["node-1"]
    assert done["kg_expansion"]["status"] == "staged"
    assert chat_service.persisted[0][:2] == ("assistant", "안녕하세요")
    assert notified == [("assistant", "안녕하세요", "web")]
    assert adapter.calls[0]["context"], "the compact local context must be sent"
    assert events[-1] == "data: [DONE]\n\n"


def test_persistence_failure_does_not_break_the_stream():
    chat_service = _ChatService(fail=True)

    events = _events(
        user_message="릴리스 절차 알려줘",
        knowledge_graph=_Store(),
        mode=NetworkBoundaryMode.CLOUD_ALLOWED,
        adapter=_StreamingAdapter(pieces=("ok",)),
        user_email="wp34-persist@example.com",
        chat_service=chat_service,
        notify=lambda *_a: pytest.fail("notify must not run after a persistence failure"),
    )

    payloads = _payloads(events)
    assert payloads[-1]["type"] == "hybrid_done"
    assert payloads[-1]["answer"] == "ok"
    assert chat_service.persisted == []


def test_provider_failure_is_surfaced_as_an_honest_error_event():
    events = _events(
        user_message="릴리스 절차 알려줘",
        knowledge_graph=_Store(),
        mode=NetworkBoundaryMode.CLOUD_ALLOWED,
        adapter=_StreamingAdapter(pieces=("partial", "rest"), fail_at=1),
        user_email="wp34-fail@example.com",
    )

    payloads = _payloads(events)
    assert payloads[-1]["type"] == "error"
    assert "provider dropped the connection" in payloads[-1]["detail"]
    assert payloads[-1]["error"] == payloads[-1]["detail"]
    assert events[-1] == "data: [DONE]\n\n"


def test_a_non_streaming_adapter_falls_back_to_a_single_turn(monkeypatch):
    """An adapter without ``stream`` is answered through the bridge instead."""

    class _Bridge:
        def __init__(self, adapter=None):
            self.adapter = adapter

        async def run_turn(self, *, user_message, minimal, mode, model=None):
            return CloudTurnResult(
                user_message=user_message,
                answer_text="whole answer",
                sent_node_ids=list(minimal.node_ids),
                provider="bridge-provider",
                model=str(model or ""),
            )

    monkeypatch.setattr(hybrid_chat, "CloudStreamingBridge", _Bridge)

    events = _events(
        user_message="릴리스 절차 알려줘",
        knowledge_graph=_Store(),
        mode=NetworkBoundaryMode.CLOUD_ALLOWED,
        adapter=object(),
        model="m1",
        user_email="wp34-bridge@example.com",
    )

    payloads = _payloads(events)
    assert [p["type"] for p in payloads] == ["hybrid_context", "token", "hybrid_done"]
    assert payloads[1]["chunk"] == "whole answer"
    assert payloads[-1]["provider"] == "bridge-provider"
