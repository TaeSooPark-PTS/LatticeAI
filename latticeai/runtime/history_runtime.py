"""Conversation-history query/clear seam extracted from the app factory.

The read and clear helpers (scope resolution, ``get_history``, conversation
grouping, and the clear operations) used to be defined inline in
``app_factory._build``. They are behaviour-preserving closures over the durable
conversation store and the workspace service; moving them here keeps the factory
a wiring path. Names are returned unchanged for the legacy ``server_app``
compatibility namespace.

Note: ``save_to_history`` (the *write* path) stays in the factory — it is bound
up with redaction, audit, and the ingestion pipeline that are assembled around
it — so only the query/clear side moves here.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


def build_history_query_runtime(
    *,
    conversations: Any,
    workspace_service: Any,
    require_auth: bool,
    logging: Any,
) -> Dict[str, Any]:
    """Return the history scope/query/clear helpers as a name → callable dict."""

    def _history_allowed_workspaces_for(user_email: Optional[str]):
        if not require_auth or not user_email:
            return None
        try:
            return set(workspace_service.readable_workspaces(user_email))
        except Exception as exc:
            logging.warning("history workspace scope resolution failed for %s: %s", user_email, exc)
            return set()

    def _history_include_legacy_global(user_email: Optional[str]) -> bool:
        return not require_auth or not user_email

    def get_history(
        user_email: Optional[str] = None,
        allowed_workspaces=None,
        include_legacy_global: Optional[bool] = None,
    ):
        try:
            if allowed_workspaces is None and user_email:
                allowed_workspaces = _history_allowed_workspaces_for(user_email)
            if include_legacy_global is None:
                include_legacy_global = _history_include_legacy_global(user_email)
            return conversations.history(
                user_email=user_email,
                allowed_workspaces=allowed_workspaces,
                include_legacy_global=include_legacy_global,
            )
        except Exception as e:
            logging.warning("get_history failed: %s", e)
            return []

    def conversation_title(item: Dict) -> str:
        content = str(item.get("content") or "").strip()
        content = re.sub(r"\s+", " ", content)
        return content[:48] or "새 대화"

    def group_history_conversations(history: Optional[List[Dict]] = None) -> List[Dict]:
        history = history if history is not None else get_history()
        grouped: Dict[str, Dict] = {}
        order: List[str] = []

        for index, item in enumerate(history):
            conv_id = item.get("conversation_id")
            if not conv_id:
                conv_id = "legacy-previous-history"

            if conv_id not in grouped:
                grouped[conv_id] = {
                    "id": conv_id,
                    "title": "이전 대화 기록" if conv_id == "legacy-previous-history" else conversation_title(item),
                    "created_at": item.get("timestamp"),
                    "updated_at": item.get("timestamp"),
                    "message_count": 0,
                    "last_message": "",
                    "source": item.get("source"),
                }
                order.append(conv_id)

            conv = grouped[conv_id]
            conv["message_count"] += 1
            conv["updated_at"] = item.get("timestamp") or conv.get("updated_at")
            conv["last_message"] = conversation_title(item)
            if conv_id != "legacy-previous-history" and item.get("role") == "user" and (not conv.get("title") or conv["title"] == "새 대화"):
                conv["title"] = conversation_title(item)

        return sorted((grouped[key] for key in order), key=lambda item: item.get("updated_at") or "", reverse=True)

    def get_conversation_messages(
        conversation_id: str,
        *,
        user_email: Optional[str] = None,
        allowed_workspaces=None,
        include_legacy_global: Optional[bool] = None,
    ) -> List[Dict]:
        history = get_history(
            user_email=user_email,
            allowed_workspaces=allowed_workspaces,
            include_legacy_global=include_legacy_global,
        )
        if conversation_id == "legacy-previous-history":
            return [item for item in history if not item.get("conversation_id")]
        return [item for item in history if item.get("conversation_id") == conversation_id]

    def clear_history(
        keep_last: int = 0,
        *,
        user_email: Optional[str] = None,
        allowed_workspaces=None,
        include_legacy_global: Optional[bool] = None,
    ) -> Dict:
        if allowed_workspaces is None and user_email:
            allowed_workspaces = _history_allowed_workspaces_for(user_email)
        if include_legacy_global is None:
            include_legacy_global = _history_include_legacy_global(user_email)
        return conversations.clear_all(
            keep_last=keep_last,
            user_email=user_email,
            allowed_workspaces=allowed_workspaces,
            include_legacy_global=include_legacy_global,
        )

    def clear_conversation(
        conversation_id: str,
        started_at: Optional[str] = None,
        *,
        user_email: Optional[str] = None,
        allowed_workspaces=None,
        include_legacy_global: Optional[bool] = None,
    ) -> Dict:
        if allowed_workspaces is None and user_email:
            allowed_workspaces = _history_allowed_workspaces_for(user_email)
        if include_legacy_global is None:
            include_legacy_global = _history_include_legacy_global(user_email)
        return conversations.clear_conversation(
            conversation_id,
            started_at=started_at,
            user_email=user_email,
            allowed_workspaces=allowed_workspaces,
            include_legacy_global=include_legacy_global,
        )

    return {
        "_history_allowed_workspaces_for": _history_allowed_workspaces_for,
        "_history_include_legacy_global": _history_include_legacy_global,
        "get_history": get_history,
        "conversation_title": conversation_title,
        "group_history_conversations": group_history_conversations,
        "get_conversation_messages": get_conversation_messages,
        "clear_history": clear_history,
        "clear_conversation": clear_conversation,
    }


__all__ = ["build_history_query_runtime"]
