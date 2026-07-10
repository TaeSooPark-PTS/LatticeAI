"""Memory methods extracted from WorkspaceOSStore for decomposition.

WorkspaceMemory manager.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .workspace_os_utils import _json_hash, _listify, _now


class WorkspaceMemory:
    def __init__(self, store: Any):
        self._store = store

    def upsert_memory(
        self,
        *,
        kind: str,
        content: str,
        user_email: Optional[str],
        tags: Optional[List[str]] = None,
        memory_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        graph: Any = None,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        MEMORY_KINDS = {"short_term", "workspace", "preferences", "decisions", "working_style", "frequently_used_tools", "long_term"}
        if kind not in MEMORY_KINDS:
            raise ValueError(f"unknown memory kind: {kind}")
        if not str(content or "").strip():
            raise ValueError("content is required")
        state = self._store.load_state()
        memories = _listify(state.get("memories"))
        now = _now()
        memory_id = memory_id or f"memory-{_json_hash([kind, content, user_email, now])[:16]}"
        existing = next((item for item in memories if item.get("id") == memory_id), None)
        record = existing or {"id": memory_id, "created_at": now}
        record.update({
            "kind": kind,
            "content": content,
            "user_email": user_email,
            "tags": tags or [],
            "metadata": {**(metadata or {}), "memory_scope": kind},
            "workspace_id": self._store._resolve_scope(workspace_id, state) if existing is None else self._store._record_workspace(record),
            "updated_at": now,
        })
        if graph is not None:
            try:
                ingested = graph.ingest_event(
                    "Memory",
                    f"{kind}: {content[:80]}",
                    user_email=user_email,
                    source="workspace_os",
                    workspace_id=record["workspace_id"],
                    metadata={"memory_id": memory_id, "kind": kind, "tags": tags or []},
                )
                record["graph_node_id"] = ingested.get("node_id")
            except Exception as exc:
                record["graph_error"] = str(exc)
        if existing is None:
            memories.append(record)
        state["memories"] = memories
        self._store.save_state(state)
        self._store.record_timeline_event("memory", "memory_upserted", {"memory_id": memory_id, "kind": kind}, workspace_id=record.get("workspace_id"))
        return record

    def list_memories(self, user_email: Optional[str] = None, kind: Optional[str] = None, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        memories = self._store._scoped(_listify(self._store.load_state().get("memories")), workspace_id)
        if user_email:
            memories = [item for item in memories if item.get("user_email") in {None, user_email}]
        if kind:
            memories = [item for item in memories if item.get("kind") == kind]
        return {"memories": list(reversed(memories))}

    # search_memories can be added similarly if needed
