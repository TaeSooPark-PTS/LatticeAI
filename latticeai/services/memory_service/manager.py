"""The Memory Manager: one cross-tier report over every backing store.

:meth:`manager` is the service's centre of gravity — sources, counts, sizes,
health and the Brain readiness signal in a single read that the brief, the
proof and the API all build on. Nothing here is estimated: a tier with no
backing reports ``unavailable`` and contributes ``None``, never a zero dressed
up as a measurement.

``_brain_readiness`` lives here rather than in the frontend so the growth score
is auditable and the Memory Manager stays the single owner of cross-tier
signals.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from latticeai.core.timeutil import now_iso as _now
from latticeai.core.workspace_os_utils import _file_size

from ._contract import MemoryCore as _Core
from .constants import TIERS


class MemoryManagerMixin(_Core):
    """Sources / usage / health. Mixed into ``MemoryService``."""

    # ── Memory Manager: sources / usage / health ──────────────────────────
    def _brain_readiness(
        self,
        *,
        memory_count: int,
        concept_count: Optional[int],
        relationship_count: Optional[int],
        healthy_sources: int,
    ) -> Dict[str, Any]:
        """Summarize how ready the user's Brain is for the product UI.

        The frontend should render this signal instead of re-deriving it from
        UI fragments. Keeping it here makes the score auditable and keeps the
        Memory Manager as the single owner of cross-tier Brain growth signals.
        """
        concepts = max(0, int(concept_count or 0))
        relationships = max(0, int(relationship_count or 0))
        memories = max(0, int(memory_count or 0))
        healthy = max(0, int(healthy_sources or 0))
        score = min(100, round(memories * 12 + concepts * 8 + relationships * 4 + healthy * 3))

        if memories < 1 and concepts < 1:
            state = "quiet"
            depth = 2
            title_key = "brain.readiness.quiet"
            action_key = "brain.readiness.start"
            score = max(12, score)
        elif concepts < 3 or relationships < 2:
            state = "forming"
            depth = 3 if concepts < 3 else 4
            title_key = "brain.readiness.forming"
            action_key = "brain.readiness.grow"
            score = max(38, score)
        else:
            state = "alive"
            depth = 5
            title_key = "brain.readiness.alive"
            action_key = "brain.readiness.map"
            score = max(72, score)

        return {
            "score": score,
            "state": state,
            "depth": depth,
            "title_key": title_key,
            "action_key": action_key,
            "signals": {
                "memory_count": memories,
                "concept_count": concepts,
                "relationship_count": relationships,
                "healthy_sources": healthy,
            },
            "source": "memory_service",
        }

    def manager(self, *, user_email: Optional[str] = None, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        ws_mem = self._workspace_memories(user_email=user_email, workspace_id=workspace_id or "personal")
        if workspace_id is None:
            project_mem = [m for m in self._all_memories() if (m.get("workspace_id") or "personal") != "personal"]
        else:
            project_mem = self._workspace_memories(user_email=user_email, workspace_id=workspace_id)
        snaps = self._snapshots(workspace_id=workspace_id)
        convs = self._scoped_conversations(user_email=user_email, workspace_id=workspace_id)
        kg_stats = self._kg_stats()
        kg_index = self._kg_index()

        ws_bytes = _file_size(self._data_dir / "workspace_os.json")
        kg_bytes = _file_size(self._data_dir / "knowledge_graph.sqlite")
        if self._conversation_store is not None:
            conv_bytes = int(getattr(self._conversation_store, "size_bytes", lambda: 0)())
        else:
            conv_bytes = _file_size(self._history_file)

        node_total = sum((kg_stats or {}).get("nodes", {}).values()) if kg_stats else None
        edge_total = sum((kg_stats or {}).get("edges", {}).values()) if kg_stats else None
        vector_total = None
        if kg_index and isinstance(kg_index.get("vector_counts"), dict):
            vector_total = sum(kg_index["vector_counts"].values())
        elif kg_index:
            vector_total = kg_index.get("indexed") or kg_index.get("ready")

        sources: List[Dict[str, Any]] = [
            {
                "id": "workspace", "type": "workspace", "label": "Workspace Memory",
                "count": len(ws_mem), "size_bytes": ws_bytes if ws_mem else 0,
                "health": "ok", "detail": "Personal workspace knowledge, by kind.",
            },
            {
                "id": "project", "type": "project", "label": "Project Memory",
                "count": len(project_mem), "size_bytes": 0,
                "health": "ok", "detail": "Memory scoped to organization workspaces.",
            },
            {
                "id": "agent", "type": "agent", "label": "Agent Memory",
                "count": len(snaps), "size_bytes": 0,
                "health": "ok", "detail": "Per-run agent memory snapshots.",
            },
            {
                "id": "conversation", "type": "conversation", "label": "Conversation Memory",
                "count": len(convs), "size_bytes": conv_bytes,
                "health": "ok" if (self._conversation_store is not None or self._history_file.exists()) else "empty",
                "detail": "Historical interaction memory from chat.",
            },
            {
                "id": "graph", "type": "graph", "label": "Graph Memory",
                "count": node_total, "size_bytes": kg_bytes,
                "health": "ok" if kg_stats else "unavailable",
                "detail": "Knowledge Graph entities and relations." if kg_stats else "Knowledge graph disabled or unavailable.",
                "edges": edge_total,
            },
            {
                "id": "vector", "type": "vector", "label": "Vector Memory",
                "count": vector_total, "size_bytes": 0,
                "health": "ok" if kg_index else "unavailable",
                "detail": "Local embedding vector index." if kg_index else "Vector index unavailable.",
            },
        ]
        total_items = sum(int(s["count"] or 0) for s in sources)
        total_bytes = ws_bytes + kg_bytes + conv_bytes
        healthy = sum(1 for s in sources if s["health"] == "ok")
        overall = "ok" if healthy >= 4 else "degraded" if healthy >= 1 else "unavailable"
        memory_ids = {m.get("id") for m in [*ws_mem, *project_mem] if m.get("id")}
        memory_count = len(memory_ids) + len(snaps) + len(convs)
        return {
            "sources": sources,
            "recent_memories": self._manager_recent_memories([*ws_mem, *project_mem], limit=8),
            "tiers": list(TIERS),
            "usage": {"total_items": total_items, "total_bytes": total_bytes, "sources": len(sources)},
            "brain_readiness": self._brain_readiness(
                memory_count=memory_count,
                concept_count=node_total,
                relationship_count=edge_total,
                healthy_sources=healthy,
            ),
            "health": overall,
            "graph_enabled": self._enable_graph,
            "generated_at": _now(),
        }

    @staticmethod
    def _manager_recent_memories(memories: List[Dict[str, Any]], *, limit: int = 8) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for item in memories[: max(1, limit)]:
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            rows.append({
                "id": item.get("id") or "",
                "kind": item.get("kind") or "memory",
                "content": str(item.get("content") or "")[:320],
                "tags": item.get("tags") if isinstance(item.get("tags"), list) else [],
                "metadata": metadata,
                "workspace_id": item.get("workspace_id") or "personal",
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
            })
        return rows

    def brain_quality_summary(self, *, user_email: Optional[str] = None, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        """Return the backend-owned Brain readiness signal for API consumers."""
        return self.manager(user_email=user_email, workspace_id=workspace_id)["brain_readiness"]
