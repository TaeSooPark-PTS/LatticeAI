"""Non-HTTP chat orchestration: history persistence and answer traces.

The API layer owns FastAPI requests/responses and SSE framing.  This service
owns the reusable bookkeeping that must be identical across normal answers,
streamed answers, document generation, and fast-path intents:

* workspace/user-scoped history reads;
* asynchronous persistence of user/assistant exchanges;
* Graph-RAG answer trace construction and recording;
* history search/grouping independent of HTTP.

``coerce`` preserves lightweight test/embedding contexts that provide the old
``build_graph_trace``/``record_trace`` façade without requiring them to grow a
full service implementation.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional

from latticeai.core.workspace_os import WorkspaceOSStore


class ChatService:
    def __init__(
        self,
        *,
        store: WorkspaceOSStore,
        get_history: Callable[..., List[Dict[str, Any]]],
        save_to_history: Optional[Callable[..., None]] = None,
        get_history_user: Optional[Callable[..., Dict[str, Any]]] = None,
        trace_delegate: Any = None,
    ):
        self._store = store
        self._get_history = get_history
        self._save_to_history = save_to_history
        self._get_history_user = get_history_user
        self._trace_delegate = trace_delegate

    @classmethod
    def coerce(
        cls,
        candidate: Any,
        *,
        store: Any,
        get_history: Callable[..., List[Dict[str, Any]]],
        save_to_history: Callable[..., None],
        get_history_user: Callable[..., Dict[str, Any]],
    ) -> "ChatService":
        """Return a fully wired service while preserving legacy trace fakes."""

        if isinstance(candidate, cls):
            candidate._get_history = get_history
            candidate._save_to_history = save_to_history
            candidate._get_history_user = get_history_user
            return candidate
        return cls(
            store=store,
            get_history=get_history,
            save_to_history=save_to_history,
            get_history_user=get_history_user,
            trace_delegate=candidate,
        )

    # ── conversation history ─────────────────────────────────────────────

    def history(self, **scope: Any) -> List[Dict[str, Any]]:
        return self._get_history(**scope)

    def history_scope(
        self,
        user_email: Optional[str],
        *,
        require_auth: bool,
        allowed_workspaces_for: Optional[Callable[[str], Any]] = None,
    ) -> Dict[str, Any]:
        scoped_user = user_email if require_auth else None
        allowed = None
        if require_auth and scoped_user and allowed_workspaces_for is not None:
            allowed = allowed_workspaces_for(scoped_user)
        return {
            "user_email": scoped_user,
            "allowed_workspaces": allowed,
            "include_legacy_global": not require_auth,
        }

    def history_user(
        self,
        user_email: Optional[str],
        user_nickname: Optional[str],
    ) -> Dict[str, Any]:
        if self._get_history_user is None:
            return {}
        return self._get_history_user(user_email, user_nickname)

    async def persist_entry(
        self,
        role: str,
        content: str,
        *,
        history_meta: Optional[Dict[str, Any]] = None,
        history_user: Optional[Dict[str, Any]] = None,
    ) -> None:
        if self._save_to_history is None:
            raise RuntimeError("chat history writer is not configured")
        await asyncio.to_thread(
            self._save_to_history,
            role,
            content,
            **(history_meta or {}),
            **(history_user or {}),
        )

    async def persist_exchange(
        self,
        *,
        request_message: str,
        stored_user_message: str,
        answer: str,
        source: Optional[str],
        history_meta: Dict[str, Any],
        history_user: Dict[str, Any],
        notify: Optional[Callable[[str, str, Optional[str]], None]] = None,
    ) -> None:
        await self.persist_entry(
            "user",
            stored_user_message,
            history_meta=history_meta,
            history_user=history_user,
        )
        await self.persist_entry(
            "assistant",
            answer,
            history_meta=history_meta,
            history_user=history_user,
        )
        if notify is not None:
            notify("user", request_message, source)
            notify("assistant", answer, source)

    def search_history(
        self,
        query: str,
        *,
        scope: Dict[str, Any],
        conversation_title: Callable[[Dict[str, Any]], str],
        limit: int = 30,
    ) -> List[Dict[str, Any]]:
        q_lower = str(query or "").strip().lower()
        if not q_lower:
            return []
        matches = [
            item
            for item in self.history(**scope)
            if q_lower in str(item.get("content") or "").lower()
        ]
        grouped: Dict[str, Dict[str, Any]] = {}
        for item in matches:
            conversation_id = item.get("conversation_id") or "legacy"
            if conversation_id not in grouped:
                grouped[conversation_id] = {
                    "conversation_id": conversation_id,
                    "title": conversation_title(item),
                    "messages": [],
                }
            grouped[conversation_id]["messages"].append(item)
        return list(grouped.values())[-max(1, int(limit or 30)) :]

    # ── answer-trace recording (Graph RAG) ───────────────────────────────

    def build_graph_trace(
        self,
        question: str,
        graph: Any,
        context: str = "",
        *,
        limit: int = 8,
        allowed_workspaces=None,
    ) -> Dict[str, Any]:
        target = self._trace_delegate or self._store
        return target.build_graph_trace(
            question,
            graph,
            context,
            limit=limit,
            allowed_workspaces=allowed_workspaces,
        )

    def record_trace(
        self,
        *,
        question: str,
        response: str,
        conversation_id: Optional[str],
        user_email: Optional[str],
        trace: Dict[str, Any],
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        target = self._trace_delegate or self._store
        return target.record_trace(
            question=question,
            response=response,
            conversation_id=conversation_id,
            user_email=user_email,
            trace=trace,
            workspace_id=workspace_id,
        )

    async def persist_answer(
        self,
        *,
        question: str,
        response: str,
        conversation_id: Optional[str],
        user_email: Optional[str],
        user_nickname: Optional[str],
        source: Optional[str],
        trace: Dict[str, Any],
        workspace_id: Optional[str],
        history_meta: Dict[str, Any],
        notify: Optional[Callable[[str, str, Optional[str]], None]] = None,
    ) -> Dict[str, Any]:
        await self.persist_entry(
            "assistant",
            response,
            history_meta=history_meta,
            history_user=self.history_user(user_email, user_nickname),
        )
        trace_record = self.record_trace(
            question=question,
            response=response,
            conversation_id=conversation_id,
            user_email=user_email,
            trace=trace,
            workspace_id=workspace_id,
        )
        if notify is not None:
            notify("assistant", response, source)
        return trace_record


__all__ = ["ChatService"]
