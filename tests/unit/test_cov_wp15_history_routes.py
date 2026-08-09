"""Conversation-history HTTP surface.

Every route registered by ``register_history_routes`` is exercised against
scripted dependencies: the raw feed, the grouped list, one conversation (found
and missing), the two delete endpoints and their audit records, and search.
The routes must authenticate through ``require_user`` and pass the caller's
scope straight through to the service — never a wider one.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from latticeai.api.chat_history import HistoryRouteDependencies, register_history_routes

HISTORY = [
    {"role": "user", "content": "분기 계획", "conversation_id": "conv-a"},
    {"role": "assistant", "content": "정리했습니다", "conversation_id": "conv-a"},
    {"role": "user", "content": "다른 주제", "conversation_id": "conv-b"},
]


class _ChatService:
    def __init__(self) -> None:
        self.history_scopes: List[Dict[str, Any]] = []
        self.search_calls: List[Dict[str, Any]] = []

    def history(self, **scope):
        self.history_scopes.append(scope)
        return list(HISTORY)

    def search_history(self, query, *, scope, conversation_title):
        self.search_calls.append({"query": query, "scope": scope})
        return [{
            "conversation_id": "conv-a",
            "title": conversation_title({"conversation_id": "conv-a"}),
            "messages": [HISTORY[0]],
        }]


class _Recorder:
    """Captures the calls the delete routes make on their dependencies."""

    def __init__(self) -> None:
        self.audit: List[Dict[str, Any]] = []
        self.cleared_conversations: List[tuple] = []
        self.cleared_history: List[tuple] = []

    def append_audit_event(self, event, **fields):
        self.audit.append({"event": event, **fields})

    def clear_conversation(self, conversation_id, started_at, **scope):
        self.cleared_conversations.append((conversation_id, started_at, scope))
        return {"removed": 2, "kept": 1}

    def clear_history(self, keep_last, **scope):
        self.cleared_history.append((keep_last, scope))
        return {"removed": 3, "kept": 0}


def _scope(user: str) -> Dict[str, Any]:
    return {"user_email": user, "allowed_workspaces": {"org:one"}, "include_legacy_global": False}


@pytest.fixture()
def wired():
    service = _ChatService()
    recorder = _Recorder()
    messages: Dict[str, List[Dict[str, Any]]] = {
        "conv-a": [HISTORY[0], HISTORY[1]],
        "with/slash": [HISTORY[2]],
    }
    lookups: List[tuple] = []

    def require_user(request: Request) -> str:
        user = request.headers.get("X-Test-User", "")
        if not user:
            raise HTTPException(status_code=401, detail="login required")
        return user

    def get_conversation_messages(conversation_id, **scope):
        lookups.append((conversation_id, scope))
        return list(messages.get(conversation_id, []))

    router = APIRouter()
    register_history_routes(
        router,
        HistoryRouteDependencies(
            chat_service=service,
            require_user=require_user,
            scope_for_user=_scope,
            group_conversations=lambda history: [
                {"id": "conv-a", "count": len(history)},
            ],
            get_conversation_messages=get_conversation_messages,
            conversation_title=lambda item: item["conversation_id"].upper(),
            clear_conversation=recorder.clear_conversation,
            clear_history=recorder.clear_history,
            append_audit_event=recorder.append_audit_event,
        ),
    )
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    client.headers.update({"X-Test-User": "owner@example.com"})
    return {
        "client": client,
        "service": service,
        "recorder": recorder,
        "lookups": lookups,
    }


def test_history_feed_is_read_through_the_callers_own_scope(wired):
    response = wired["client"].get("/history")

    assert response.status_code == 200
    assert response.json() == HISTORY
    assert wired["service"].history_scopes == [_scope("owner@example.com")]


def test_history_routes_refuse_an_unauthenticated_caller(wired):
    anonymous = TestClient(wired["client"].app)

    assert anonymous.get("/history").status_code == 401
    assert anonymous.get("/history/conversations").status_code == 401
    assert anonymous.get("/history/search", params={"q": "x"}).status_code == 401
    assert anonymous.delete("/history").status_code == 401
    assert wired["service"].history_scopes == []


def test_conversation_list_groups_the_scoped_feed(wired):
    response = wired["client"].get("/history/conversations")

    assert response.status_code == 200
    assert response.json() == [{"id": "conv-a", "count": 3}]
    assert wired["service"].history_scopes == [_scope("owner@example.com")]


def test_one_conversation_is_returned_with_its_messages(wired):
    response = wired["client"].get("/history/conversations/conv-a")

    assert response.status_code == 200
    assert response.json() == {"id": "conv-a", "messages": [HISTORY[0], HISTORY[1]]}
    assert wired["lookups"] == [("conv-a", _scope("owner@example.com"))]


def test_a_conversation_id_containing_a_slash_still_resolves(wired):
    response = wired["client"].get("/history/conversations/with/slash")

    assert response.status_code == 200
    assert response.json()["id"] == "with/slash"


def test_an_unknown_conversation_is_a_localized_404(wired):
    client = wired["client"]

    english = client.get(
        "/history/conversations/missing", headers={"X-Lattice-Language": "en"}
    )
    korean = client.get(
        "/history/conversations/missing", headers={"X-Lattice-Language": "ko"}
    )

    assert english.status_code == 404
    assert english.json()["detail"] == "That conversation no longer exists."
    assert korean.json()["detail"] == "대화를 찾을 수 없습니다."


def test_deleting_one_conversation_audits_what_it_removed(wired):
    response = wired["client"].delete(
        "/history/conversations/conv-a", params={"started_at": "2026-08-01T09:00:00"}
    )

    assert response.status_code == 200
    assert response.json() == {"removed": 2, "kept": 1}
    assert wired["recorder"].cleared_conversations == [
        ("conv-a", "2026-08-01T09:00:00", _scope("owner@example.com"))
    ]
    assert wired["recorder"].audit == [{
        "event": "conversation_delete",
        "user_email": "owner@example.com",
        "conversation_id": "conv-a",
        "started_at": "2026-08-01T09:00:00",
        "removed": 2,
        "kept": 1,
    }]


def test_deleting_a_conversation_without_a_start_marker_deletes_the_whole_room(wired):
    wired["client"].delete("/history/conversations/conv-a")

    assert wired["recorder"].cleared_conversations[0][1] is None
    assert wired["recorder"].audit[0]["started_at"] is None


def test_clearing_history_keeps_the_requested_tail_and_audits_it(wired):
    response = wired["client"].delete("/history", params={"keep_last": 5})

    assert response.status_code == 200
    assert response.json() == {"removed": 3, "kept": 0}
    assert wired["recorder"].cleared_history == [(5, _scope("owner@example.com"))]
    assert wired["recorder"].audit == [{
        "event": "history_delete",
        "user_email": "owner@example.com",
        "keep_last": 5,
        "removed": 3,
        "kept": 0,
    }]


def test_clearing_history_defaults_to_removing_everything(wired):
    wired["client"].delete("/history")

    assert wired["recorder"].cleared_history == [(0, _scope("owner@example.com"))]


def test_search_echoes_the_query_and_titles_each_hit(wired):
    response = wired["client"].get("/history/search", params={"q": "분기"})

    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "분기"
    assert body["results"][0]["title"] == "CONV-A"
    assert wired["service"].search_calls == [
        {"query": "분기", "scope": _scope("owner@example.com")}
    ]


def test_search_requires_the_query_parameter(wired):
    assert wired["client"].get("/history/search").status_code == 422
