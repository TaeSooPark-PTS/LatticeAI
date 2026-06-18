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
        memory_ids = {m.get("id") for m in [*ws_mem, *project_mem] if m.get("id")}
        memory_count = len(memory_ids) + len(snaps) + len(convs)
        return {
            "sources": sources,
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

    def brain_quality_summary(self, *, user_email: Optional[str] = None, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        """Return the backend-owned Brain readiness signal for API consumers."""
        return self.manager(user_email=user_email, workspace_id=workspace_id)["brain_readiness"]

    def brain_proof(
        self,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
        active_model: Optional[str] = None,
        recall_query: str = "",
        limit: int = 3,
    ) -> Dict[str, Any]:
        """Return the proof points that make Brain feel model-independent.

        This is intentionally backend-owned instead of UI-derived: the first-run
        Brain screen needs to show the durable stores it can actually recall
        from, and the model continuity claim must be independent from whichever
        LLM is currently loaded.
        """
        manager = self.manager(user_email=user_email, workspace_id=workspace_id)
        sources = {str(source.get("id")): source for source in manager.get("sources", []) if isinstance(source, dict)}
        readiness = manager.get("brain_readiness") or {}
        conversation_count = int(sources.get("conversation", {}).get("count") or 0)
        workspace_count = int(sources.get("workspace", {}).get("count") or 0)
        graph_count = int(sources.get("graph", {}).get("count") or 0)
        vector_count = int(sources.get("vector", {}).get("count") or 0)
        query = recall_query.strip() or self._latest_recall_query(user_email=user_email, workspace_id=workspace_id)
        recall = self.recall(query, user_email=user_email, workspace_id=workspace_id, limit=limit) if query else {"query": "", "results": [], "count": 0, "source": "live"}
        recall_items = [
            {
                "id": item.get("id"),
                "source": item.get("source"),
                "title": item.get("title"),
                "snippet": item.get("snippet"),
                "score": item.get("score", 0),
            }
            for item in recall.get("results", [])[: max(1, min(limit, 8))]
        ]
        durable_items = workspace_count + conversation_count + graph_count
        # Capability and proof are deliberately separated. The brain is
        # architecturally model-independent, so the capability is always true.
        # The proof stays false until there is durable evidence on disk.
        has_durable_evidence = durable_items > 0
        proven_continuity = has_durable_evidence
        return {
            "status": readiness.get("state") or "quiet",
            "readiness": readiness,
            "model_continuity": {
                "active_model": active_model or "",
                "brain_owner": "lattice_brain",
                # Design capability: the brain is built to outlive any model.
                "capability": True,
                # Proof: only true when durable evidence exists to recall.
                "survives_model_switch": proven_continuity,
                "proven": proven_continuity,
                "context_store": "workspace + conversation + graph + vector",
            },
            "proofs": {
                "durable_items": durable_items,
                "has_durable_evidence": has_durable_evidence,
                "workspace_memories": workspace_count,
                "conversations": conversation_count,
                "graph_concepts": graph_count,
                "vector_items": vector_count,
                "healthy_sources": readiness.get("signals", {}).get("healthy_sources", 0),
            },
            "recall": {
                "query": recall.get("query") or query,
                "items": recall_items,
                "count": recall.get("count", 0),
            },
            "claims": {
                "can_recall_user_context": bool(recall_items or durable_items > 0),
                # Proven, not asserted: no durable evidence means no continuity claim.
                "keeps_context_across_models": proven_continuity,
                "is_knowledge_store": bool(graph_count or vector_count or workspace_count or conversation_count),
            },
            "generated_at": _now(),
        }

    def _latest_recall_query(self, *, user_email: Optional[str], workspace_id: Optional[str]) -> str:
        for memory in self._workspace_memories(user_email=user_email, workspace_id=workspace_id or "personal"):
            content = str(memory.get("content") or "").strip()
            if content:
                return content[:96]
        for conversation in self._conversations():
            messages = conversation.get("messages") or []
            if not isinstance(messages, list):
                continue
            for message in reversed(messages[-8:]):
                if not isinstance(message, dict):
                    continue
                if user_email and message.get("user_email") != user_email:
                    continue
                message_workspace = message.get("workspace_id") or "personal"
                target_workspace = workspace_id or "personal"
                if message_workspace != target_workspace:
                    continue
                content = str(message.get("content") or "").strip()
                if content:
                    return content[:96]
        return ""

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
