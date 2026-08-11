"""The Brain Brief: what to notice, why it is believable, what to do next.

The home screen's compact briefing. It answers three product questions from
data that already exists — the Memory Manager report and the Brain Proof — and
invents nothing: the focus is a real recalled row, a real memory, a real
conversation or a real graph count, in that order, and an empty Brain says so.

Everything else here is a descriptor list, not copy: labels are i18n keys the
UI resolves, so the wording stays in the frontend and the *ordering* — which is
a product judgement about priority — stays here.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from latticeai.core.timeutil import now_iso as _now

from ._contract import MemoryCore as _Core


class MemoryBriefMixin(_Core):
    """The Brain Brief and its descriptors. Mixed into ``MemoryService``."""

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
