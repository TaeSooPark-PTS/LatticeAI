"""Command Center: daily briefing + universal command search (v9.5.0).

The Brain accumulates a lot of surfaces — knowledge, conversations,
automations, review queue, health. The Command Center condenses them into two
deterministic, local, read-only entry points:

* **briefing** — one payload answering "what does my Brain know about today?":
  recent knowledge, conversation activity, automation state, pending reviews,
  a health snapshot, and top automation suggestions. Each section degrades
  independently — a missing backend never breaks the briefing.
* **search** — one query across every surface at once: knowledge nodes
  (graph keyword search), the user's own conversations, and installed
  automations. Powers the Cmd+K command palette.
* **quick_actions** — state-derived next steps ("N items waiting for review",
  "enable your draft automation") with stable ids, so the UI can render
  one-click jumps without guessing.

Everything here is scoped to the requesting user and workspace, mirrors the
scoped-read conventions of the automation intelligence service, and never
calls a model.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from latticeai.core.timeutil import local_now, parse_iso
from latticeai.core.timeutil import now_iso as _now
from latticeai.core.workspace_os_utils import graph_scope_kwargs

LOGGER = logging.getLogger(__name__)

_RECENT_NODE_LIMIT = 6
_SEARCH_HISTORY_LIMIT = 2000
_BRIEFING_HISTORY_LIMIT = 2000

# Graph-hygiene advisory (review 2026-07-25 Wave 2.5): once the graph passes
# this many nodes with no noise-curate pass in the stale window, the briefing
# suggests a *dry-run* /knowledge-graph/curate/noise. Advisory only — nothing
# is deleted until the user explicitly applies the job.
_HYGIENE_MIN_NODES = 200
_HYGIENE_STALE_DAYS = 7


def _clip(text: Any, limit: int = 160) -> str:
    value = str(text or "").strip()
    return value[:limit]




class CommandCenterService:
    def __init__(
        self,
        *,
        conversation_store: Any = None,
        knowledge_graph: Any = None,
        store: Any = None,
        search_service: Any = None,
        brain_intelligence: Any = None,
        automation_intelligence: Any = None,
        review_queue: Any = None,
        enable_graph: bool = True,
    ) -> None:
        self._conversations = conversation_store
        self._kg = knowledge_graph
        self._store = store
        self._search = search_service
        self._brain = brain_intelligence
        self._automation = automation_intelligence
        self._review_queue = review_queue
        self._enable_graph = bool(enable_graph and knowledge_graph is not None)

    # ── scoped reads ─────────────────────────────────────────────────────

    def _scope_kwargs(self, workspace_id: Optional[str]) -> Dict[str, Any]:
        return graph_scope_kwargs(workspace_id)

    def _history(
        self, *, user_email: Optional[str], workspace_id: Optional[str], limit: int
    ) -> List[Dict[str, Any]]:
        if self._conversations is None:
            return []
        try:
            return list(
                self._conversations.history(
                    user_email=user_email,
                    limit=limit,
                    **self._scope_kwargs(workspace_id),
                )
            )
        except Exception:
            LOGGER.exception("command center history read failed")
            return []

    def _workflows(self, *, workspace_id: Optional[str]) -> List[Dict[str, Any]]:
        if self._store is None:
            return []
        try:
            return list(self._store.list_workflows(workspace_id=workspace_id).get("workflows") or [])
        except Exception:
            LOGGER.exception("command center workflow read failed")
            return []

    # ── briefing sections ────────────────────────────────────────────────

    def _knowledge_section(self, *, workspace_id: Optional[str]) -> Dict[str, Any]:
        if not self._enable_graph or not hasattr(self._kg, "graph"):
            return {"available": False, "recent": []}
        try:
            snapshot = self._kg.graph(limit=50, **self._scope_kwargs(workspace_id))
        except Exception:
            LOGGER.exception("command center knowledge read failed")
            return {"available": False, "recent": []}
        nodes = list(snapshot.get("nodes") or [])
        recent = [
            {
                "id": node.get("id"),
                "title": _clip(node.get("title"), 120),
                "type": node.get("type"),
                "updated_at": node.get("updated_at"),
            }
            for node in nodes[:_RECENT_NODE_LIMIT]
        ]
        return {"available": True, "recent": recent, "sampled_nodes": len(nodes)}

    def _conversation_section(
        self, *, user_email: Optional[str], workspace_id: Optional[str]
    ) -> Dict[str, Any]:
        items = self._history(
            user_email=user_email, workspace_id=workspace_id, limit=_BRIEFING_HISTORY_LIMIT
        )
        if not items:
            return {"available": self._conversations is not None, "messages": 0, "questions": 0}
        user_items = [item for item in items if item.get("role") == "user"]
        last = items[-1]
        return {
            "available": True,
            "messages": len(items),
            "questions": len(user_items),
            "last_active": str(last.get("timestamp") or ""),
            "last_question": _clip(
                next(
                    (item.get("content") for item in reversed(user_items)),
                    "",
                ),
                120,
            ),
        }

    def _automation_section(self, *, workspace_id: Optional[str]) -> Dict[str, Any]:
        if self._store is None:
            return {"available": False, "total": 0, "enabled": 0, "drafts": 0}
        workflows = self._workflows(workspace_id=workspace_id)
        enabled = 0
        drafts = 0
        last_execution: Optional[Dict[str, Any]] = None
        for workflow in workflows:
            metadata = (workflow or {}).get("metadata") or {}
            if metadata.get("automation_state") == "enabled":
                enabled += 1
            elif metadata.get("automation_state") == "draft_disabled":
                drafts += 1
            # Execution log surfacing (backlog #6): the briefing carries the
            # most recent stamped automation execution so "did my automation
            # run, and how did it go?" is answered on the home surface.
            stamped = metadata.get("last_execution")
            if isinstance(stamped, dict) and str(stamped.get("finished_at") or "") > str(
                (last_execution or {}).get("finished_at") or ""
            ):
                last_execution = {
                    "workflow_id": workflow.get("id"),
                    "name": _clip(workflow.get("name"), 120),
                    "mode": stamped.get("mode"),
                    "status": stamped.get("status"),
                    "summary": _clip(stamped.get("summary"), 200),
                    "finished_at": stamped.get("finished_at"),
                }
        section = {
            "available": True,
            "total": len(workflows),
            "enabled": enabled,
            "drafts": drafts,
        }
        if last_execution is not None:
            section["last_execution"] = last_execution  # type: ignore[assignment]
        return section

    def _review_section(
        self, *, user_email: Optional[str], workspace_id: Optional[str]
    ) -> Dict[str, Any]:
        if self._review_queue is None:
            return {"available": False, "pending": 0}
        try:
            listed = self._review_queue.list(
                workspace_id=workspace_id, user_email=user_email, status="pending"
            )
        except Exception:
            LOGGER.exception("command center review read failed")
            return {"available": False, "pending": 0}
        return {"available": True, "pending": len(listed.get("items") or [])}

    def _health_section(
        self, *, user_email: Optional[str], workspace_id: Optional[str]
    ) -> Dict[str, Any]:
        if self._brain is None:
            return {"available": False}
        try:
            report = self._brain.health_report(
                user_email=user_email, workspace_id=workspace_id
            )
        except Exception:
            LOGGER.exception("command center health read failed")
            return {"available": False}
        # ``BrainIntelligenceService.health_report()`` publishes the score and
        # the grade at the top level. This used to read them out of a nested
        # ``overall`` block that no producer has ever written, so the section
        # was permanently unavailable: Today's Briefing showed a dash with no
        # reason beside it, and the "check-health" quick action below could
        # never fire. Read the shape the real collaborator returns.
        score = report.get("overall_score")
        return {
            "available": score is not None,
            "grade": report.get("grade"),
            "score": score,
            "recommended_actions": list(report.get("recommended_actions") or [])[:3],
        }

    def _suggestion_section(
        self, *, user_email: Optional[str], workspace_id: Optional[str]
    ) -> Dict[str, Any]:
        if self._automation is None:
            return {"available": False, "count": 0, "top": []}
        try:
            payload = self._automation.suggestions(
                user_email=user_email, workspace_id=workspace_id
            )
        except Exception:
            LOGGER.exception("command center suggestion read failed")
            return {"available": False, "count": 0, "top": []}
        suggestions = [
            item
            for item in (payload.get("suggestions") or [])
            if not item.get("installed")
        ]
        top = [
            {
                "id": item.get("id"),
                "kind": item.get("kind"),
                "title": _clip(item.get("title"), 120),
            }
            for item in suggestions[:3]
        ]
        return {"available": True, "count": len(suggestions), "top": top}

    def _hygiene_section(self) -> Dict[str, Any]:
        """Advisory graph-hygiene signal (review 2026-07-25 Wave 2.5).

        Suggests a dry-run noise-curate pass when the graph has grown past
        ``_HYGIENE_MIN_NODES`` nodes and the last applied pass is missing or
        older than ``_HYGIENE_STALE_DAYS`` days. Fail-open: any backend error
        degrades to "no suggestion" — the briefing itself never breaks.
        """
        section: Dict[str, Any] = {
            "available": False,
            "suggest_noise_curate": False,
            "reason": "",
            "last_noise_curate_at": None,
            "node_count": 0,
        }
        if not self._enable_graph or not hasattr(self._kg, "stats"):
            return section
        try:
            stats = self._kg.stats() or {}
            node_count = sum(
                int(count or 0) for count in (stats.get("nodes") or {}).values()
            )
            last = (
                self._kg.last_noise_curate_at()
                if hasattr(self._kg, "last_noise_curate_at")
                else None
            )
            section.update(
                {
                    "available": True,
                    "node_count": node_count,
                    "last_noise_curate_at": last,
                }
            )
            if node_count < _HYGIENE_MIN_NODES:
                return section
            if last is not None and not self._older_than_days(
                last, _HYGIENE_STALE_DAYS
            ):
                return section
            section["suggest_noise_curate"] = True
            section["reason"] = (
                f"{node_count} nodes and no noise curation in the last "
                f"{_HYGIENE_STALE_DAYS} days"
                if last
                else f"{node_count} nodes and no noise curation recorded"
            )
        except Exception:
            LOGGER.exception("command center hygiene read failed")
            return {
                "available": False,
                "suggest_noise_curate": False,
                "reason": "",
                "last_noise_curate_at": None,
                "node_count": 0,
            }
        return section

    @staticmethod
    def _older_than_days(stamp: str, days: int) -> bool:
        parsed = parse_iso(stamp)
        if parsed is None:
            # Unreadable stamp → treat as stale; suggesting a dry-run is safe.
            return True
        now = datetime.now(parsed.tzinfo) if parsed.tzinfo else local_now()
        return (now - parsed) > timedelta(days=days)

    # ── quick actions ────────────────────────────────────────────────────

    def _quick_actions(self, sections: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        """State-derived next steps with stable ids and app hash targets."""
        actions: List[Dict[str, Any]] = []
        review = sections.get("review") or {}
        if review.get("pending"):
            actions.append(
                {
                    "id": "review-pending",
                    "kind": "review",
                    "count": review["pending"],
                    "target": "/act/review",
                }
            )
        automations = sections.get("automations") or {}
        if automations.get("drafts"):
            actions.append(
                {
                    "id": "enable-drafts",
                    "kind": "automation",
                    "count": automations["drafts"],
                    "target": "/act/workflows",
                }
            )
        suggestions = sections.get("suggestions") or {}
        if suggestions.get("count"):
            actions.append(
                {
                    "id": "install-suggestion",
                    "kind": "suggestion",
                    "count": suggestions["count"],
                    "target": "/act/workflows",
                }
            )
        knowledge = sections.get("knowledge") or {}
        if knowledge.get("available") and not knowledge.get("recent"):
            actions.append(
                {
                    "id": "connect-knowledge",
                    "kind": "capture",
                    "count": 0,
                    "target": "/capture/files",
                }
            )
        health = sections.get("health") or {}
        if health.get("available") and isinstance(health.get("score"), (int, float)) and health["score"] < 70:
            actions.append(
                {
                    "id": "check-health",
                    "kind": "health",
                    "count": 0,
                    "target": "/brain/graph",
                }
            )
        hygiene = sections.get("hygiene") or {}
        if hygiene.get("suggest_noise_curate"):
            actions.append(
                {
                    "id": "curate-noise",
                    "kind": "hygiene",
                    "count": int(hygiene.get("node_count") or 0),
                    "target": "/brain/graph",
                    # Dry-run by default on the API side: this endpoint only
                    # *reports* removals until dry_run=false is sent explicitly.
                    "endpoint": "/knowledge-graph/curate/noise",
                }
            )
        if not actions:
            actions.append(
                {"id": "ask-brain", "kind": "chat", "count": 0, "target": "/brain"}
            )
        return actions

    def briefing(
        self, *, user_email: Optional[str] = None, workspace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        sections = {
            "knowledge": self._knowledge_section(workspace_id=workspace_id),
            "conversations": self._conversation_section(
                user_email=user_email, workspace_id=workspace_id
            ),
            "automations": self._automation_section(workspace_id=workspace_id),
            "review": self._review_section(
                user_email=user_email, workspace_id=workspace_id
            ),
            "health": self._health_section(
                user_email=user_email, workspace_id=workspace_id
            ),
            "suggestions": self._suggestion_section(
                user_email=user_email, workspace_id=workspace_id
            ),
            "hygiene": self._hygiene_section(),
        }
        return {
            "generated_at": _now(),
            "sections": sections,
            "quick_actions": self._quick_actions(sections),
        }

    # ── universal search ─────────────────────────────────────────────────

    def _search_knowledge(
        self, query: str, *, workspace_id: Optional[str], limit: int
    ) -> List[Dict[str, Any]]:
        if self._search is None or not self._enable_graph:
            return []
        try:
            payload = self._search.keyword_search(
                query, limit=limit, **self._scope_kwargs(workspace_id)
            )
        except Exception:
            LOGGER.exception("command center knowledge search failed")
            return []
        return [
            {
                "id": item.get("id"),
                "title": _clip(item.get("title"), 120),
                "summary": _clip(item.get("summary"), 160),
                "type": item.get("type"),
            }
            for item in (payload.get("results") or [])[:limit]
        ]

    def _search_conversations(
        self,
        query: str,
        *,
        user_email: Optional[str],
        workspace_id: Optional[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        needle = query.lower()
        matches: List[Dict[str, Any]] = []
        seen_conversations: set = set()
        items = self._history(
            user_email=user_email, workspace_id=workspace_id, limit=_SEARCH_HISTORY_LIMIT
        )
        for item in reversed(items):
            content = str(item.get("content") or "")
            if needle not in content.lower():
                continue
            conversation_id = str(item.get("conversation_id") or "")
            if conversation_id and conversation_id in seen_conversations:
                continue
            if conversation_id:
                seen_conversations.add(conversation_id)
            matches.append(
                {
                    "conversation_id": conversation_id,
                    "role": item.get("role"),
                    "snippet": _clip(content, 140),
                    "timestamp": str(item.get("timestamp") or ""),
                }
            )
            if len(matches) >= limit:
                break
        return matches

    def _search_automations(
        self, query: str, *, workspace_id: Optional[str], limit: int
    ) -> List[Dict[str, Any]]:
        needle = query.lower()
        matches: List[Dict[str, Any]] = []
        for workflow in self._workflows(workspace_id=workspace_id):
            name = str(workflow.get("name") or "")
            if needle not in name.lower():
                continue
            metadata = (workflow or {}).get("metadata") or {}
            matches.append(
                {
                    "id": workflow.get("id"),
                    "name": _clip(name, 120),
                    "enabled": metadata.get("automation_state") == "enabled",
                }
            )
            if len(matches) >= limit:
                break
        return matches

    def search(
        self,
        query: str,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
        limit: int = 8,
    ) -> Dict[str, Any]:
        query = str(query or "").strip()
        limit = max(1, min(int(limit or 8), 20))
        if not query:
            return {"query": "", "groups": [], "generated_at": _now()}
        groups = [
            {
                "kind": "knowledge",
                "items": self._search_knowledge(
                    query, workspace_id=workspace_id, limit=limit
                ),
            },
            {
                "kind": "conversation",
                "items": self._search_conversations(
                    query,
                    user_email=user_email,
                    workspace_id=workspace_id,
                    limit=limit,
                ),
            },
            {
                "kind": "automation",
                "items": self._search_automations(
                    query, workspace_id=workspace_id, limit=limit
                ),
            },
        ]
        return {
            "query": query,
            "groups": [group for group in groups if group["items"]],
            "total": sum(len(group["items"]) for group in groups),
            "generated_at": _now(),
        }


__all__ = ["CommandCenterService"]
