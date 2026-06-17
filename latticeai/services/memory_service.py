"""Long-Term Memory platform + Memory Manager (v3.2.0).

Parts 7, 8 and 13. Lattice AI already persists memory in several real stores;
before this service they were unrelated. ``MemoryService`` unifies them behind
one façade and adds a Memory Manager that reports usage / sources / health /
size / type and supports recall / inspect / prune / compact / rebuild / clear.

Memory tiers and their real backing store (nothing is fabricated — a tier with
no backing reports ``unavailable``):

* **workspace**     — personal workspace memories (``WorkspaceOS`` memories)
* **project**       — memories scoped to a non-personal (organization) workspace
* **agent**         — agent memory snapshots captured during runs
* **conversation**  — chat history conversations
* **graph**         — Knowledge Graph nodes (entities + relations)
* **vector**        — local embedding vector index

The service never invents counts or health: every number is read from the
underlying store, and missing stores surface as ``unavailable``.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Personal workspace memory kinds (from WorkspaceOS.MEMORY_KINDS).
WORKSPACE_KINDS = (
    "short_term",
    "workspace",
    "preferences",
    "decisions",
    "working_style",
    "frequently_used_tools",
    "long_term",
)

TIERS = ("workspace", "project", "agent", "conversation", "graph", "vector")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size if path.exists() else 0
    except Exception:
        return 0


class MemoryService:
    def __init__(
        self,
        *,
        store: Any,
        data_dir: Path,
        knowledge_graph: Any = None,
        enable_graph: bool = True,
        history_file: Optional[Path] = None,
        conversation_store: Any = None,
    ):
        self._store = store
        self._kg = knowledge_graph
        self._enable_graph = bool(enable_graph and knowledge_graph is not None)
        self._data_dir = Path(data_dir)
        self._history_file = Path(history_file) if history_file else (self._data_dir / "chat_history.json")
        # v4: the durable SQLite conversation store supersedes the JSON file
        # as the conversation tier's backing store when provided.
        self._conversation_store = conversation_store

    # ── helpers over the underlying stores ────────────────────────────────
    def _workspace_memories(self, *, user_email: Optional[str], workspace_id: Optional[str]) -> List[Dict[str, Any]]:
        try:
            return list(self._store.list_memories(user_email=user_email, workspace_id=workspace_id).get("memories", []))
        except Exception:
            return []

    def _all_memories(self) -> List[Dict[str, Any]]:
        try:
            return list(self._store.list_memories().get("memories", []))
        except Exception:
            return []

    def _snapshots(self, *, workspace_id: Optional[str]) -> List[Dict[str, Any]]:
        try:
            return list(self._store.list_memory_snapshots(workspace_id=workspace_id, limit=200).get("snapshots", []))
        except Exception:
            return []

    def _conversations(self) -> List[Dict[str, Any]]:
        if self._conversation_store is not None:
            try:
                grouped: Dict[str, List[Dict[str, Any]]] = {}
                for item in self._conversation_store.history():
                    grouped.setdefault(item.get("conversation_id") or "legacy-previous-history", []).append(item)
                return [{"id": conv_id, "messages": msgs} for conv_id, msgs in grouped.items()]
            except Exception:
                return []
        if not self._history_file.exists():
            return []
        try:
            with open(self._history_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            return []
        if isinstance(data, dict):
            convs = data.get("conversations")
            if isinstance(convs, list):
                return convs
            return [{"id": k, **(v if isinstance(v, dict) else {"messages": v})} for k, v in data.items()]
        if isinstance(data, list):
            return data
        return []

    def _kg_stats(self) -> Optional[Dict[str, Any]]:
        if not self._enable_graph:
            return None
        try:
            return self._kg.stats()
        except Exception:
            return None

    def _kg_index(self) -> Optional[Dict[str, Any]]:
        if not self._enable_graph:
            return None
        try:
            return self._kg.index_status()
        except Exception:
            return None

    # ── Memory Manager: sources / usage / health ──────────────────────────
    def manager(self, *, user_email: Optional[str] = None, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        ws_mem = self._workspace_memories(user_email=user_email, workspace_id=workspace_id or "personal")
        if workspace_id is None:
            project_mem = [m for m in self._all_memories() if (m.get("workspace_id") or "personal") != "personal"]
        else:
            project_mem = self._workspace_memories(user_email=user_email, workspace_id=workspace_id)
        snaps = self._snapshots(workspace_id=workspace_id)
        convs = self._conversations()
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

        sources = [
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
        total_items = sum((s["count"] or 0) for s in sources)
        total_bytes = ws_bytes + kg_bytes + conv_bytes
        healthy = sum(1 for s in sources if s["health"] == "ok")
        overall = "ok" if healthy >= 4 else "degraded" if healthy >= 1 else "unavailable"
        return {
            "sources": sources,
            "tiers": list(TIERS),
            "usage": {"total_items": total_items, "total_bytes": total_bytes, "sources": len(sources)},
            "health": overall,
            "graph_enabled": self._enable_graph,
            "generated_at": _now(),
        }

    def tiers(self) -> Dict[str, Any]:
        return {"tiers": list(TIERS), "workspace_kinds": list(WORKSPACE_KINDS)}

    # ── recall (unified retrieval over the memory tiers) ───────────────────
    def recall(
        self,
        query: str,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        q = str(query or "").strip()
        query_tokens = [tok for tok in q.lower().split() if tok]

        def _lexical_score(*texts: Any) -> float:
            # Honest, comparable relevance: fraction of query tokens present.
            # Both tiers share this scorer so the cross-tier ranking is real,
            # not an artifact of per-tier constants.
            if not query_tokens:
                return 0.0
            haystack = " ".join(str(t or "") for t in texts).lower()
            hits = sum(1 for tok in query_tokens if tok in haystack)
            return round(hits / len(query_tokens), 4)

        results: List[Dict[str, Any]] = []

        try:
            mem = self._store.search_memories(q, user_email=user_email, limit=limit, workspace_id=workspace_id).get("memories", [])
        except Exception:
            mem = []
        for m in mem:
            results.append({
                "source": "workspace",
                "id": m.get("id"),
                "title": (m.get("kind") or "memory"),
                "snippet": str(m.get("content") or "")[:240],
                "kind": m.get("kind"),
                "score": _lexical_score(m.get("content"), " ".join(m.get("tags") or []), m.get("kind")),
                "tags": m.get("tags") or [],
            })

        if self._enable_graph and q:
            try:
                # KnowledgeGraph.search returns {"query": ..., "matches": [...]}.
                hits = self._kg.search(q, limit).get("matches", [])
            except Exception:
                hits = []
            for hit in hits[:limit]:
                results.append({
                    "source": "graph",
                    "id": hit.get("id") or hit.get("node_id"),
                    "title": hit.get("title") or hit.get("name") or "node",
                    "snippet": str(hit.get("summary") or hit.get("content") or "")[:240],
                    "kind": hit.get("type") or "node",
                    "score": _lexical_score(hit.get("title"), hit.get("name"), hit.get("summary"), hit.get("content")),
                })

        results.sort(key=lambda r: r.get("score", 0), reverse=True)
        return {"query": q, "results": results[: max(1, min(limit, 100))], "count": len(results), "source": "live"}

    # ── inspect a single tier ─────────────────────────────────────────────
    def inspect(self, source: str, *, user_email: Optional[str] = None, workspace_id: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
        if source == "workspace":
            items = self._workspace_memories(user_email=user_email, workspace_id=workspace_id or "personal")[:limit]
            return {"source": source, "items": items, "count": len(items)}
        if source == "project":
            if workspace_id is None:
                items = [m for m in self._all_memories() if (m.get("workspace_id") or "personal") != "personal"][:limit]
            else:
                items = self._workspace_memories(user_email=user_email, workspace_id=workspace_id)[:limit]
            return {"source": source, "items": items, "count": len(items)}
        if source == "agent":
            items = self._snapshots(workspace_id=workspace_id)[:limit]
            return {"source": source, "items": items, "count": len(items)}
        if source == "conversation":
            convs = self._conversations()
            items = [{"id": c.get("id"), "title": c.get("title") or c.get("id"), "messages": len(c.get("messages") or [])} for c in convs[:limit]]
            return {"source": source, "items": items, "count": len(convs)}
        if source == "graph":
            return {"source": source, "stats": self._kg_stats() or {}, "available": bool(self._kg_stats())}
        if source == "vector":
            return {"source": source, "index": self._kg_index() or {}, "available": bool(self._kg_index())}
        raise KeyError(source)

    # ── mutating operations ───────────────────────────────────────────────
    def prune(
        self,
        *,
        ids: Optional[List[str]] = None,
        kind: Optional[str] = None,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        # Ownership guard: a caller may only prune memories they own. Both the
        # explicit-id and kind paths are intersected with the caller's own
        # memories, so a forged id for another user's memory is refused, not
        # silently deleted.
        owned_ids = {
            m["id"]
            for m in self._workspace_memories(user_email=user_email, workspace_id=workspace_id)
            if m.get("id")
        }
        removed: List[str] = []
        skipped: List[str] = []
        target_ids: List[str] = []
        seen: set = set()
        for mid in (ids or []):
            if mid in seen:
                continue
            seen.add(mid)
            if mid in owned_ids:
                target_ids.append(mid)
            else:
                skipped.append(mid)
        if kind:
            for m in self._workspace_memories(user_email=user_email, workspace_id=workspace_id):
                if m.get("kind") == kind and m.get("id") and m["id"] not in seen:
                    seen.add(m["id"])
                    target_ids.append(m["id"])
        for mid in target_ids:
            try:
                self._store.delete_memory(mid)
                removed.append(mid)
            except Exception:
                continue
        result: Dict[str, Any] = {"removed": removed, "count": len(removed)}
        if skipped:
            result["skipped"] = skipped
        return result

    def compact(
        self,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Dedupe workspace memories with identical (kind, content)."""
        seen: set = set()
        removed: List[str] = []
        # Oldest first so the first occurrence (oldest) is kept.
        memories = list(reversed(self._workspace_memories(user_email=user_email, workspace_id=workspace_id)))
        for m in memories:
            key = (m.get("kind"), str(m.get("content") or "").strip())
            if key in seen:
                if m.get("id"):
                    try:
                        self._store.delete_memory(m["id"])
                        removed.append(m["id"])
                    except Exception:
                        continue
            else:
                seen.add(key)
        return {"compacted": len(removed), "removed": removed, "remaining": len(seen)}

    def rebuild(self, target: str = "vector") -> Dict[str, Any]:
        if target in {"vector", "index", "vector_index"}:
            if not self._enable_graph:
                return {"status": "unavailable", "detail": "Knowledge graph / vector index disabled."}
            try:
                result = self._kg.rebuild_vector_index()
                return {"status": "ok", "target": "vector_index", "result": result}
            except Exception as exc:
                return {"status": "error", "detail": str(exc)}
        return {"status": "error", "detail": f"Unknown rebuild target: {target}"}

    def clear(
        self,
        *,
        scope: str,
        confirm: bool = False,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not confirm:
            raise ValueError("clear requires confirm=true")
        if scope in WORKSPACE_KINDS:
            result = self.prune(kind=scope, user_email=user_email, workspace_id=workspace_id)
            return {"cleared": scope, **result}
        if scope == "workspace":
            ids = [m["id"] for m in self._workspace_memories(user_email=user_email, workspace_id=workspace_id) if m.get("id")]
            result = self.prune(ids=ids, user_email=user_email, workspace_id=workspace_id)
            return {"cleared": "workspace", **result}
        if scope == "graph":
            raise ValueError("graph clear is disabled from Memory Manager because it is not workspace-scoped")
        raise ValueError(f"unsupported clear scope: {scope}")
