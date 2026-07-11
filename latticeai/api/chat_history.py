"""Conversation-history HTTP routes.

Persistence and search grouping live in :mod:`latticeai.services.chat_service`;
this module only authenticates requests and shapes HTTP responses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict

from fastapi import APIRouter, HTTPException, Request


@dataclass(frozen=True)
class HistoryRouteDependencies:
    chat_service: Any
    require_user: Callable[[Request], str]
    scope_for_user: Callable[[str], Dict[str, Any]]
    group_conversations: Callable[[list], Any]
    get_conversation_messages: Callable[..., list]
    conversation_title: Callable[[Dict[str, Any]], str]
    clear_conversation: Callable[..., Dict[str, Any]]
    clear_history: Callable[..., Dict[str, Any]]
    append_audit_event: Callable[..., None]


def register_history_routes(router: APIRouter, deps: HistoryRouteDependencies) -> None:
    @router.get("/history")
    async def fetch_history(request: Request):
        """웹 화면에서 이전 대화를 불러올 수 있도록 히스토리를 반환합니다."""
        current_user = deps.require_user(request)
        return deps.chat_service.history(**deps.scope_for_user(current_user))

    @router.get("/history/conversations")
    async def fetch_history_conversations(request: Request):
        """저장된 히스토리를 대화 단위로 묶어 반환합니다."""
        current_user = deps.require_user(request)
        history = deps.chat_service.history(**deps.scope_for_user(current_user))
        return deps.group_conversations(history)

    @router.get("/history/conversations/{conversation_id:path}")
    async def fetch_history_conversation(conversation_id: str, request: Request):
        """선택한 대화의 메시지를 반환합니다."""
        current_user = deps.require_user(request)
        messages = deps.get_conversation_messages(
            conversation_id,
            **deps.scope_for_user(current_user),
        )
        if not messages:
            raise HTTPException(status_code=404, detail="대화를 찾을 수 없습니다.")
        return {"id": conversation_id, "messages": messages}

    @router.delete("/history/conversations/{conversation_id:path}")
    async def delete_history_conversation(conversation_id: str, request: Request):
        """선택한 대화방의 메시지만 삭제합니다."""
        email = deps.require_user(request)
        started_at = request.query_params.get("started_at")
        result = deps.clear_conversation(
            conversation_id,
            started_at,
            **deps.scope_for_user(email),
        )
        deps.append_audit_event(
            "conversation_delete",
            user_email=email,
            conversation_id=conversation_id,
            started_at=started_at,
            removed=result.get("removed", 0),
            kept=result.get("kept", 0),
        )
        return result

    @router.delete("/history")
    async def delete_history(request: Request, keep_last: int = 0):
        email = deps.require_user(request)
        result = deps.clear_history(keep_last, **deps.scope_for_user(email))
        deps.append_audit_event(
            "history_delete",
            user_email=email,
            keep_last=keep_last,
            removed=result.get("removed", 0),
            kept=result.get("kept", 0),
        )
        return result

    @router.get("/history/search")
    async def search_history(q: str, request: Request):
        """키워드로 채팅 히스토리를 검색합니다."""
        current_user = deps.require_user(request)
        results = deps.chat_service.search_history(
            q,
            scope=deps.scope_for_user(current_user),
            conversation_title=deps.conversation_title,
        )
        return {"results": results, "query": q}


__all__ = ["HistoryRouteDependencies", "register_history_routes"]
