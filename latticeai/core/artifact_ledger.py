"""Conversation artifact ledger — closing the re-search loop (v9.9.6).

A run writes ``index.html``, the file is queued for ingestion, and the user
immediately asks "이제 그 파일에 다크모드 넣어줘". Indexing (chunking, embedding,
graph writes) is asynchronous, so retrieval may not know about that file yet —
the next turn could plan as if it had never been created.

This ledger is the deterministic bridge: the agent path records what it just
wrote, and the context assembler injects it as a high-priority section. It is
intentionally **process-local and bounded**:

* it answers "what did *this conversation* just make?", a question whose
  useful lifetime is minutes;
* by the time a restart loses it, the ingestion pipeline has indexed those
  files and normal retrieval answers better than a ledger would;
* nothing downstream may treat it as durable storage — the Brain remains the
  single durable home for knowledge.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Sequence

from latticeai.core.timeutil import now_iso

__all__ = ["ArtifactLedger"]


class ArtifactLedger:
    """Bounded, thread-safe map of conversation → recently written artifacts."""

    def __init__(
        self,
        *,
        max_conversations: int = 200,
        max_per_conversation: int = 25,
    ) -> None:
        self._lock = threading.Lock()
        self._entries: "OrderedDict[tuple, List[Dict[str, Any]]]" = OrderedDict()
        self._max_conversations = max(1, int(max_conversations))
        self._max_per_conversation = max(1, int(max_per_conversation))

    @staticmethod
    def _key(
        user_email: Optional[str],
        conversation_id: Optional[str],
        workspace_id: Optional[str],
    ) -> tuple:
        return (
            str(user_email or ""),
            str(conversation_id or "default"),
            str(workspace_id or ""),
        )

    def record(
        self,
        paths: Sequence[Any],
        *,
        user_email: Optional[str] = None,
        conversation_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        run_id: str = "",
    ) -> List[Dict[str, Any]]:
        """Note artifacts this conversation just produced. Never raises."""
        cleaned: List[str] = []
        for entry in list(paths or []):
            path = entry if isinstance(entry, str) else (entry or {}).get("path")
            path = str(path or "").strip()
            if path and path not in cleaned:
                cleaned.append(path)
        if not cleaned:
            return []
        stamp = now_iso()
        key = self._key(user_email, conversation_id, workspace_id)
        with self._lock:
            existing = self._entries.pop(key, [])
            by_path = {item["path"]: item for item in existing}
            for path in cleaned:
                by_path[path] = {"path": path, "at": stamp, "run_id": str(run_id or "")}
            merged = sorted(by_path.values(), key=lambda item: item["at"])
            merged = merged[-self._max_per_conversation :]
            self._entries[key] = merged
            while len(self._entries) > self._max_conversations:
                self._entries.popitem(last=False)
            return list(merged)

    def recent(
        self,
        *,
        user_email: Optional[str] = None,
        conversation_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Most recent artifacts for one conversation, newest last.

        Returns ``[]`` for an unknown conversation — honest absence, never a
        cross-conversation leak.
        """
        key = self._key(user_email, conversation_id, workspace_id)
        with self._lock:
            items = list(self._entries.get(key, []))
        return items[-max(1, int(limit)) :]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
