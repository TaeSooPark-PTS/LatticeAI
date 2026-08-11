"""Reads over the real backing stores — the only place the tiers touch them.

Six tiers, six backends, one rule: never invent. A workspace/snapshot/
conversation read that fails raises :class:`MemoryServiceError` so the caller
learns the tier is unreadable instead of receiving an empty list; a graph or
vector read that fails degrades to ``None`` because those tiers are optional
and report themselves as ``unavailable`` upstream.

The conversation tier has two backings: the durable SQLite conversation store
when one is wired (v4), and the legacy JSON history file otherwise.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ._contract import MemoryCore as _Core
from .constants import LOGGER, MemoryServiceError


class MemoryStoreReadsMixin(_Core):
    """The store reads. Mixed into ``MemoryService``."""

    # ── helpers over the underlying stores ────────────────────────────────
    def _workspace_memories(self, *, user_email: Optional[str], workspace_id: Optional[str]) -> List[Dict[str, Any]]:
        try:
            return list(self._store.list_memories(user_email=user_email, workspace_id=workspace_id).get("memories", []))
        except Exception as exc:
            LOGGER.exception("workspace memory read failed")
            raise MemoryServiceError("workspace memory backend unavailable") from exc

    def _all_memories(self) -> List[Dict[str, Any]]:
        try:
            return list(self._store.list_memories().get("memories", []))
        except Exception as exc:
            LOGGER.exception("global memory read failed")
            raise MemoryServiceError("memory backend unavailable") from exc

    def _snapshots(self, *, workspace_id: Optional[str]) -> List[Dict[str, Any]]:
        try:
            return list(self._store.list_memory_snapshots(workspace_id=workspace_id, limit=200).get("snapshots", []))
        except Exception as exc:
            LOGGER.exception("memory snapshot read failed")
            raise MemoryServiceError("memory snapshot backend unavailable") from exc

    def _conversations(self) -> List[Dict[str, Any]]:
        if self._conversation_store is not None:
            try:
                grouped: Dict[str, List[Dict[str, Any]]] = {}
                for item in self._conversation_store.history():
                    grouped.setdefault(item.get("conversation_id") or "legacy-previous-history", []).append(item)
                return [{"id": conv_id, "messages": msgs} for conv_id, msgs in grouped.items()]
            except Exception as exc:
                LOGGER.exception("conversation store read failed")
                raise MemoryServiceError("conversation backend unavailable") from exc
        if not self._history_file.exists():
            return []
        try:
            with open(self._history_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            LOGGER.exception("legacy conversation history read failed")
            raise MemoryServiceError("conversation history is unreadable") from exc
        if isinstance(data, dict):
            convs = data.get("conversations")
            if isinstance(convs, list):
                return convs
            return [{"id": k, **(v if isinstance(v, dict) else {"messages": v})} for k, v in data.items()]
        if isinstance(data, list):
            return data
        return []

    def _scoped_conversations(self, *, user_email: Optional[str], workspace_id: Optional[str]) -> List[Dict[str, Any]]:
        if not user_email:
            return self._conversations()
        target_workspace = workspace_id or "personal"
        scoped: List[Dict[str, Any]] = []
        for conversation in self._conversations():
            messages = conversation.get("messages") or []
            if not isinstance(messages, list):
                continue
            kept = [
                message
                for message in messages
                if isinstance(message, dict)
                and message.get("user_email") == user_email
                and (message.get("workspace_id") or "personal") == target_workspace
            ]
            if kept:
                scoped.append({**conversation, "messages": kept})
        return scoped

    def _kg_stats(self) -> Optional[Dict[str, Any]]:
        if not self._enable_graph:
            return None
        try:
            return self._kg.stats()
        except Exception:
            LOGGER.exception("knowledge graph stats read failed")
            return None

    def _kg_index(self) -> Optional[Dict[str, Any]]:
        if not self._enable_graph:
            return None
        try:
            return self._kg.index_status()
        except Exception:
            LOGGER.exception("knowledge graph index status read failed")
            return None
