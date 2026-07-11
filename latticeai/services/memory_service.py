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
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from latticeai.core.workspace_os_utils import _file_size
from latticeai.core.timeutil import now_iso as _now

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
LOGGER = logging.getLogger(__name__)


class MemoryServiceError(RuntimeError):
    """Raised when a configured memory backend cannot be read reliably."""


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

    def brain_brief(
        self,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
        active_model: Optional[str] = None,
        recall_query: str = "",
        limit: int = 3,
    ) -> Dict[str, Any]:
        """Return a compact, evidence-backed Brain briefing for the home screen.

        The brief intentionally reuses the Memory Manager and Brain Proof data
        instead of inventing UI-only facts. It answers three product questions:
        what should the user notice, why can the Brain prove it, and what is the
        easiest next action.
        """
        manager = self.manager(user_email=user_email, workspace_id=workspace_id)
        proof = self.brain_proof(
            user_email=user_email,
            workspace_id=workspace_id,
            active_model=active_model,
            recall_query=recall_query,
            limit=limit,
        )
        readiness = manager.get("brain_readiness") or {}
        state = str(readiness.get("state") or "quiet")
        proofs = proof.get("proofs") or {}
        recall = proof.get("recall") or {}
        recall_items = [item for item in recall.get("items") or [] if isinstance(item, dict)]
        durable_items = int(proofs.get("durable_items") or 0)
        workspace_memories = int(proofs.get("workspace_memories") or 0)
        conversations = int(proofs.get("conversations") or 0)
        graph_concepts = int(proofs.get("graph_concepts") or 0)
        vector_items = int(proofs.get("vector_items") or 0)
        healthy_sources = int(proofs.get("healthy_sources") or 0)

        focus = self._brain_brief_focus(
            user_email=user_email,
            workspace_id=workspace_id,
            recall_items=recall_items,
            durable_items=durable_items,
            graph_concepts=graph_concepts,
            query=str(recall.get("query") or recall_query or ""),
        )
        actions = self._brain_brief_actions(
            state=state,
            has_durable_evidence=bool(proofs.get("has_durable_evidence")),
            has_recall=bool(recall_items),
            graph_concepts=graph_concepts,
        )
        suggested_questions = self._brain_brief_suggested_questions(
            focus=focus,
            has_durable_evidence=bool(proofs.get("has_durable_evidence")),
            has_recall=bool(recall_items),
            graph_concepts=graph_concepts,
            conversations=conversations,
        )
        proactive_actions = self._brain_brief_proactive_actions(
            focus=focus,
            state=state,
            has_durable_evidence=bool(proofs.get("has_durable_evidence")),
            has_recall=bool(recall_items),
            graph_concepts=graph_concepts,
            vector_items=vector_items,
            healthy_sources=healthy_sources,
        )
        return {
            "status": state,
            "score": int(readiness.get("score") or 0),
            "headline_key": f"brain.brief.headline.{state if state in {'quiet', 'forming', 'alive'} else 'quiet'}",
            "body_key": f"brain.brief.body.{state if state in {'quiet', 'forming', 'alive'} else 'quiet'}",
            "focus": focus,
            "next_actions": actions,
            "suggested_questions": suggested_questions,
            "proactive_actions": proactive_actions,
            "evidence": [
                {
                    "id": "durable",
                    "label_key": "brain.brief.evidence.durable",
                    "value": durable_items,
                    "detail_key": "brain.brief.evidence.durable.detail",
                },
                {
                    "id": "graph",
                    "label_key": "brain.brief.evidence.graph",
                    "value": graph_concepts,
                    "detail_key": "brain.brief.evidence.graph.detail",
                },
                {
                    "id": "sources",
                    "label_key": "brain.brief.evidence.sources",
                    "value": healthy_sources,
                    "detail_key": "brain.brief.evidence.sources.detail",
                },
            ],
            "signals": {
                "workspace_memories": workspace_memories,
                "conversations": conversations,
                "graph_concepts": graph_concepts,
                "vector_items": vector_items,
                "healthy_sources": healthy_sources,
            },
            "proof": {
                "query": recall.get("query") or "",
                "items": recall_items[: max(1, min(limit, 6))],
                "model_continuity": proof.get("model_continuity") or {},
            },
            "generated_at": _now(),
        }

    def _brain_brief_focus(
        self,
        *,
        user_email: Optional[str],
        workspace_id: Optional[str],
        recall_items: List[Dict[str, Any]],
        durable_items: int,
        graph_concepts: int,
        query: str,
    ) -> Dict[str, Any]:
        if recall_items:
            first = recall_items[0]
            return {
                "kind": "recall",
                "title": str(first.get("title") or "Memory"),
                "detail": str(first.get("snippet") or query or ""),
                "source": str(first.get("source") or "memory"),
                "score": float(first.get("score") or 0),
            }

        memories = self._workspace_memories(user_email=user_email, workspace_id=workspace_id or "personal")
        if memories:
            first_memory = memories[0]
            return {
                "kind": "memory",
                "title": str(first_memory.get("kind") or "memory"),
                "detail": str(first_memory.get("content") or "")[:240],
                "source": "workspace",
                "score": 1.0,
            }

        conversations = self._scoped_conversations(user_email=user_email, workspace_id=workspace_id)
        if conversations:
            latest = conversations[-1]
            messages = latest.get("messages") if isinstance(latest, dict) else []
            last_message = next((m for m in reversed(messages or []) if isinstance(m, dict) and str(m.get("content") or "").strip()), {})
            return {
                "kind": "conversation",
                "title": str(latest.get("title") or latest.get("id") or "conversation"),
                "detail": str(last_message.get("content") or "")[:240],
                "source": "conversation",
                "score": 1.0,
            }

        if graph_concepts > 0:
            return {
                "kind": "graph",
                "title": "Knowledge Graph",
                "detail": f"{graph_concepts} graph concepts are ready to inspect.",
                "source": "graph",
                "score": 1.0,
            }

        return {
            "kind": "empty",
            "title": "",
            "detail": "",
            "source": "none",
            "score": 0,
            "empty": durable_items <= 0,
        }

    @staticmethod
    def _brain_brief_actions(
        *,
        state: str,
        has_durable_evidence: bool,
        has_recall: bool,
        graph_concepts: int,
    ) -> List[Dict[str, Any]]:
        actions: List[Dict[str, Any]] = []
        if not has_durable_evidence:
            actions.extend([
                {
                    "id": "add_source",
                    "label_key": "brain.brief.action.add",
                    "detail_key": "brain.brief.action.add.detail",
                    "route": "/capture",
                    "priority": 10,
                },
                {
                    "id": "ask_brain",
                    "label_key": "brain.brief.action.ask",
                    "detail_key": "brain.brief.action.ask.detail",
                    "route": "",
                    "priority": 9,
                },
            ])
        else:
            actions.append({
                "id": "ask_brain",
                "label_key": "brain.brief.action.ask",
                "detail_key": "brain.brief.action.ask.detail",
                "route": "",
                "priority": 10,
            })
            if graph_concepts > 0 or state == "alive":
                actions.append({
                    "id": "inspect_topics",
                    "label_key": "brain.brief.action.topics",
                    "detail_key": "brain.brief.action.topics.detail",
                    "route": "/knowledge-graph",
                    "priority": 8,
                })
            if has_recall:
                actions.append({
                    "id": "verify_model",
                    "label_key": "brain.brief.action.verify",
                    "detail_key": "brain.brief.action.verify.detail",
                    "route": "",
                    "priority": 7,
                })
            actions.append({
                "id": "backup_brain",
                "label_key": "brain.brief.action.backup",
                "detail_key": "brain.brief.action.backup.detail",
                "route": "/settings",
                "priority": 6,
            })
        return sorted(actions, key=lambda item: int(item.get("priority") or 0), reverse=True)[:4]

    @staticmethod
    def _brain_brief_proactive_actions(
        *,
        focus: Dict[str, Any],
        state: str,
        has_durable_evidence: bool,
        has_recall: bool,
        graph_concepts: int,
        vector_items: int,
        healthy_sources: int,
    ) -> List[Dict[str, Any]]:
        """Return concrete, one-click actions Brain can proactively suggest."""
        focus_title = str(focus.get("title") or "").strip() or "Brain"
        focus_detail = str(focus.get("detail") or "").strip()
        actions: List[Dict[str, Any]] = []
        if not has_durable_evidence:
            actions.append({
                "id": "proactive_add_source",
                "intent": "route",
                "label_key": "brain.proactive.addSource.label",
                "detail_key": "brain.proactive.addSource.detail",
                "route": "/capture",
                "prompt": "Add a useful source to my Brain and explain what it learned.",
                "priority": 100,
            })
            actions.append({
                "id": "proactive_seed_memory",
                "intent": "ask",
                "label_key": "brain.proactive.seed.label",
                "detail_key": "brain.proactive.seed.detail",
                "prompt": "Help me seed my Brain with the most useful personal context to remember.",
                "priority": 90,
            })
            return actions

        if has_recall:
            actions.append({
                "id": "proactive_evidence_review",
                "intent": "ask",
                "label_key": "brain.proactive.evidence.label",
                "detail_key": "brain.proactive.evidence.detail",
                "prompt": (
                    f"Review the evidence Brain has for {focus_title}. "
                    "Separate confirmed facts, weak signals, contradictions, and next checks."
                ),
                "priority": 100,
                "context": {"focus": focus_title, "detail": focus_detail},
            })
            actions.append({
                "id": "proactive_delegate",
                "intent": "delegate",
                "label_key": "brain.proactive.delegate.label",
                "detail_key": "brain.proactive.delegate.detail",
                "prompt": (
                    f"Turn {focus_title} into an execution plan, verify the known context, "
                    "and return concrete next steps with risks."
                ),
                "priority": 95,
                "context": {"focus": focus_title, "detail": focus_detail},
            })
            actions.append({
                "id": "proactive_review_draft",
                "intent": "review",
                "label_key": "brain.proactive.review.label",
                "detail_key": "brain.proactive.review.detail",
                "prompt": (
                    f"Create a reviewable task from Brain's current focus: {focus_title}. "
                    f"{focus_detail[:240]}"
                ).strip(),
                "priority": 90,
                "context": {"focus": focus_title, "detail": focus_detail},
            })

        if graph_concepts > 0:
            actions.append({
                "id": "proactive_map_connections",
                "intent": "route",
                "label_key": "brain.proactive.map.label",
                "detail_key": "brain.proactive.map.detail",
                "route": "/knowledge-graph",
                "prompt": f"Map the strongest Knowledge Graph connections around {focus_title}.",
                "priority": 82,
                "context": {"focus": focus_title, "graph_concepts": graph_concepts},
            })

        if state == "alive" and vector_items > 0 and healthy_sources > 0:
            actions.append({
                "id": "proactive_weekly_brief",
                "intent": "review",
                "label_key": "brain.proactive.weekly.label",
                "detail_key": "brain.proactive.weekly.detail",
                "prompt": (
                    "Prepare a weekly Brain review: what changed, what decisions are pending, "
                    "what should be delegated, and what evidence is stale."
                ),
                "priority": 78,
                "context": {"vector_items": vector_items, "healthy_sources": healthy_sources},
            })

        return sorted(actions, key=lambda item: int(item.get("priority") or 0), reverse=True)[:4]

    @staticmethod
    def _brain_brief_suggested_questions(
        *,
        focus: Dict[str, Any],
        has_durable_evidence: bool,
        has_recall: bool,
        graph_concepts: int,
        conversations: int,
    ) -> List[Dict[str, Any]]:
        """Return reusable, localized UI prompt descriptors for the Brain home."""
        focus_title = str(focus.get("title") or "").strip()
        focus_kind = str(focus.get("kind") or "empty")
        questions: List[Dict[str, Any]] = []

        if not has_durable_evidence or focus_kind == "empty":
            questions.extend([
                {
                    "id": "start_brain",
                    "label_key": "brain.suggestion.start.label",
                    "detail_key": "brain.suggestion.start.detail",
                    "prompt_key": "brain.suggestion.start.prompt",
                    "params": {},
                    "priority": 10,
                },
                {
                    "id": "add_context",
                    "label_key": "brain.suggestion.context.label",
                    "detail_key": "brain.suggestion.context.detail",
                    "prompt_key": "brain.suggestion.context.prompt",
                    "params": {},
                    "priority": 9,
                },
            ])
            return questions

        questions.append({
            "id": "focus_next",
            "label_key": "brain.suggestion.focus.label",
            "detail_key": "brain.suggestion.focus.detail",
            "prompt_key": "brain.suggestion.focus.prompt",
            "params": {"focus": focus_title or "Brain"},
            "priority": 10,
        })

        if has_recall:
            questions.append({
                "id": "evidence_check",
                "label_key": "brain.suggestion.evidence.label",
                "detail_key": "brain.suggestion.evidence.detail",
                "prompt_key": "brain.suggestion.evidence.prompt",
                "params": {"focus": focus_title or "this topic"},
                "priority": 9,
            })

        if graph_concepts > 0:
            questions.append({
                "id": "graph_connections",
                "label_key": "brain.suggestion.graph.label",
                "detail_key": "brain.suggestion.graph.detail",
                "prompt_key": "brain.suggestion.graph.prompt",
                "params": {"focus": focus_title or "Knowledge Graph"},
                "priority": 8,
            })

        if conversations > 0:
            questions.append({
                "id": "conversation_followup",
                "label_key": "brain.suggestion.history.label",
                "detail_key": "brain.suggestion.history.detail",
                "prompt_key": "brain.suggestion.history.prompt",
                "params": {"focus": focus_title or "recent conversations"},
                "priority": 7,
            })

        return sorted(questions, key=lambda item: int(item.get("priority") or 0), reverse=True)[:4]

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
                # Evidence explainability: why this item was recalled, so the
                # citation UI can show the matched terms instead of a bare score.
                "matched_terms": item.get("matched_terms") or [],
                "confidence": item.get("confidence") or "low",
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

        def _matched_terms(*texts: Any) -> List[str]:
            haystack = " ".join(str(t or "") for t in texts).lower()
            return [tok for tok in query_tokens if tok in haystack]

        def _lexical_score(matched: List[str]) -> float:
            # Honest, comparable relevance: fraction of query tokens present.
            # Both tiers share this scorer so the cross-tier ranking is real,
            # not an artifact of per-tier constants.
            if not query_tokens:
                return 0.0
            return round(len(matched) / len(query_tokens), 4)

        results: List[Dict[str, Any]] = []

        errors: List[Dict[str, str]] = []
        try:
            mem = self._store.search_memories(q, user_email=user_email, limit=limit, workspace_id=workspace_id).get("memories", [])
        except Exception as exc:
            LOGGER.exception("workspace memory search failed")
            errors.append({"source": "workspace", "detail": str(exc)})
            mem = []
        for m in mem:
            matched = _matched_terms(m.get("content"), " ".join(m.get("tags") or []), m.get("kind"))
            results.append({
                "source": "workspace",
                "id": m.get("id"),
                "title": (m.get("kind") or "memory"),
                "snippet": str(m.get("content") or "")[:240],
                "kind": m.get("kind"),
                "score": _lexical_score(matched),
                "matched_terms": matched,
                "tags": m.get("tags") or [],
            })

        if self._enable_graph and q:
            try:
                # KnowledgeGraph.search returns {"query": ..., "matches": [...]}.
                search_kwargs = (
                    {"allowed_workspaces": {workspace_id}}
                    if workspace_id is not None
                    else {}
                )
                hits = self._kg.search(q, limit, **search_kwargs).get("matches", [])
            except Exception as exc:
                LOGGER.exception("knowledge graph memory search failed")
                errors.append({"source": "graph", "detail": str(exc)})
                hits = []
            for hit in hits[:limit]:
                matched = _matched_terms(hit.get("title"), hit.get("name"), hit.get("summary"), hit.get("content"))
                results.append({
                    "source": "graph",
                    "id": hit.get("id") or hit.get("node_id"),
                    "title": hit.get("title") or hit.get("name") or "node",
                    "snippet": str(hit.get("summary") or hit.get("content") or "")[:240],
                    "kind": hit.get("type") or "node",
                    "score": _lexical_score(matched),
                    "matched_terms": matched,
                })

        # Quality gate: when at least one result carries real lexical evidence,
        # zero-score rows are noise relative to it and are dropped. When nothing
        # scores (e.g. tokenization mismatch), everything is kept so the tiers'
        # own search filters still decide — the gate never empties a recall.
        candidates = len(results)
        if query_tokens and any(r.get("score", 0) > 0 for r in results):
            results = [r for r in results if r.get("score", 0) > 0]
        for r in results:
            r["confidence"] = "high" if r.get("score", 0) >= 0.65 else "medium" if r.get("score", 0) >= 0.3 else "low"

        results.sort(key=lambda r: r.get("score", 0), reverse=True)
        return {
            "query": q,
            "results": results[: max(1, min(limit, 100))],
            "count": len(results),
            "source": "live",
            "status": "degraded" if errors else "ok",
            "errors": errors,
            "quality_gate": {
                "candidates": candidates,
                "passed": len(results),
                "filtered": candidates - len(results),
                "gate": "lexical-evidence/v1",
            },
        }

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
            convs = self._scoped_conversations(user_email=user_email, workspace_id=workspace_id)
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
        failed: List[Dict[str, str]] = []
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
            except Exception as exc:
                LOGGER.exception("memory deletion failed for %s", mid)
                failed.append({"id": mid, "detail": str(exc)})
        result: Dict[str, Any] = {"removed": removed, "count": len(removed)}
        if skipped:
            result["skipped"] = skipped
        if failed:
            result["failed"] = failed
            result["status"] = "partial" if removed else "error"
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
        failed: List[Dict[str, str]] = []
        # Oldest first so the first occurrence (oldest) is kept.
        memories = list(reversed(self._workspace_memories(user_email=user_email, workspace_id=workspace_id)))
        for m in memories:
            key = (m.get("kind"), str(m.get("content") or "").strip())
            if key in seen:
                if m.get("id"):
                    try:
                        self._store.delete_memory(m["id"])
                        removed.append(m["id"])
                    except Exception as exc:
                        LOGGER.exception("memory compaction deletion failed for %s", m["id"])
                        failed.append({"id": m["id"], "detail": str(exc)})
            else:
                seen.add(key)
        return {
            "compacted": len(removed),
            "removed": removed,
            "remaining": len(seen),
            "failed": failed,
            "status": "partial" if failed and removed else "error" if failed else "ok",
        }

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
