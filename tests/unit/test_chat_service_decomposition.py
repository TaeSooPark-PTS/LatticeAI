"""A3 regression: chat HTTP boundaries and non-HTTP service ownership."""

from __future__ import annotations

import asyncio
from pathlib import Path

from latticeai.services.chat_service import ChatService


class _TraceStore:
    def __init__(self) -> None:
        self.recorded = []

    def build_graph_trace(self, question, graph, context, **kwargs):
        return {
            "question": question,
            "graph": graph,
            "context": context,
            "allowed": kwargs.get("allowed_workspaces"),
        }

    def record_trace(self, **payload):
        self.recorded.append(payload)
        return {"id": "trace-service", **payload}


def test_chat_service_owns_exchange_persistence_and_notifications():
    saved = []
    notified = []
    service = ChatService(
        store=_TraceStore(),
        get_history=lambda **_scope: [],
        save_to_history=lambda *args, **kwargs: saved.append((args, kwargs)),
        get_history_user=lambda email, nickname: {
            "user_email": email,
            "user_nickname": nickname,
        },
    )

    asyncio.run(
        service.persist_exchange(
            request_message="hello",
            stored_user_message="hello\n[Image attached]",
            answer="world",
            source="web",
            history_meta={"conversation_id": "c1"},
            history_user={"user_email": "user@example.com"},
            notify=lambda *args: notified.append(args),
        )
    )

    assert [entry[0][0] for entry in saved] == ["user", "assistant"]
    assert saved[0][0][1] == "hello\n[Image attached]"
    assert saved[1][0][1] == "world"
    assert notified == [
        ("user", "hello", "web"),
        ("assistant", "world", "web"),
    ]


def test_chat_service_persists_answer_and_trace_as_one_workflow():
    store = _TraceStore()
    saved = []
    service = ChatService(
        store=store,
        get_history=lambda **_scope: [],
        save_to_history=lambda *args, **kwargs: saved.append((args, kwargs)),
        get_history_user=lambda email, nickname: {
            "user_email": email,
            "user_nickname": nickname,
        },
    )

    trace = asyncio.run(
        service.persist_answer(
            question="question",
            response="answer",
            conversation_id="conversation",
            user_email="user@example.com",
            user_nickname="User",
            source="web",
            trace={"sources": ["node:1"]},
            workspace_id="org:one",
            history_meta={"conversation_id": "conversation"},
        )
    )

    assert saved[0][0] == ("assistant", "answer")
    assert saved[0][1]["user_email"] == "user@example.com"
    assert trace["id"] == "trace-service"
    assert store.recorded[0]["workspace_id"] == "org:one"


def test_chat_service_scopes_and_groups_history_without_http():
    history = [
        {"content": "alpha decision", "conversation_id": "a"},
        {"content": "other", "conversation_id": "b"},
        {"content": "alpha follow-up", "conversation_id": "a"},
    ]
    service = ChatService(store=_TraceStore(), get_history=lambda **_scope: history)

    scoped = service.history_scope(
        "user@example.com",
        require_auth=True,
        allowed_workspaces_for=lambda _email: {"org:one"},
    )
    results = service.search_history(
        "alpha",
        scope=scoped,
        conversation_title=lambda item: item["conversation_id"].upper(),
    )

    assert scoped == {
        "user_email": "user@example.com",
        "allowed_workspaces": {"org:one"},
        "include_legacy_global": False,
    }
    assert len(results) == 1
    assert results[0]["title"] == "A"
    assert len(results[0]["messages"]) == 2


def test_chat_router_is_a_small_composition_root_with_real_feature_modules():
    repo_root = Path(__file__).resolve().parents[2]
    chat_source = (repo_root / "latticeai" / "api" / "chat.py").read_text(
        encoding="utf-8"
    )
    expected_modules = {
        "chat_agent_http.py",
        "chat_contracts.py",
        "chat_documents.py",
        "chat_history.py",
        "chat_intents.py",
        "chat_stream.py",
    }

    # Hybrid path added a small composition block (~20 lines). Keep the guard
    # tight enough to discourage further growth of the root without extraction.
    assert len(chat_source.splitlines()) < 600
    assert '@api_router.post("/chat")' in chat_source
    assert '@api_router.get("/history")' not in chat_source
    assert '@api_router.post("/agent")' not in chat_source
    assert expected_modules <= {
        path.name for path in (repo_root / "latticeai" / "api").glob("chat_*.py")
    }
