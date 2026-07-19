"""Question-driven everyday automation intelligence (v9.4.0).

Users repeat themselves: the same "오늘 뭐 해야 하지?", "프로젝트 상태 정리해줘",
"그 폴더에 새 문서 뭐 들어왔어?" questions come back every day. This service
watches for those patterns and turns them into one-click, consent-first
automation suggestions:

* **question_patterns** — mines the user's own chat history for recurring
  question intents. Clustering is deterministic and local (token-signature
  similarity, no model call), so the evidence shown to the user is their own
  literal questions.
* **suggestions** — converts recurring patterns and active local knowledge
  sources (folders the user connected to the Brain) into concrete automation
  proposals: a scheduled answer for a recurring question, a digest for a busy
  knowledge folder, or one of the starter recipes when the intent matches.
* **build_suggestion_workflow** — turns an accepted suggestion into a
  workflow definition using the same consent-first shape as the starter
  recipes: installed as a disabled draft, review-queue gated, local-only,
  no external actions.
* **overview** — one payload for the automation surface: suggestions,
  installed automations with their enable state, and pattern evidence.

Every suggestion id is deterministic, so install calls are idempotent and the
UI can safely re-request suggestions without duplicates.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from latticeai.core.timeutil import now_iso as _now

LOGGER = logging.getLogger(__name__)

_MIN_PATTERN_COUNT = 2
_MAX_HISTORY = 4000
_SIGNATURE_SIMILARITY = 0.6

_QUESTION_HINT_RE = re.compile(
    r"(\?|어때|뭐야|뭐가|뭘까|알려줘|보여줘|정리해|요약해|정리 좀|요약 좀|해줘"
    r"|what|how|why|when|where|status|summar|remind|list|show me|tell me)",
    re.IGNORECASE,
)

_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "to", "of", "in", "on", "for", "and",
    "or", "me", "my", "you", "please", "can", "could", "would", "it", "this",
    "that", "do", "does", "what", "how", "about",
    "좀", "그", "이", "저", "것", "거", "게", "내", "제", "나", "너", "우리",
    "해줘", "해주세요", "주세요", "합니다", "있어", "있나", "있는지", "어떻게",
    "뭐야", "뭐가", "알려줘", "보여줘",
}

# Intent buckets map recurring question language onto the starter recipes.
_INTENT_RULES = (
    (
        "digest",
        re.compile(r"(오늘|하루|today|daily).{0,12}(정리|요약|digest|summary|기억|메모)|"
                   r"(정리|요약).{0,8}(해줘|해 줘|부탁)|summar(y|ize)", re.IGNORECASE),
        "daily-memory-digest",
    ),
    (
        "project_review",
        re.compile(r"(프로젝트|project|진행|progress|status|상태|주간|weekly|이번 주)", re.IGNORECASE),
        "weekly-project-review",
    ),
    (
        "follow_up",
        re.compile(r"(리마인드|remind|챙겨|잊지|deadline|마감|follow.?up|나중에|까먹)", re.IGNORECASE),
        "follow-up-radar",
    ),
)


def _tokens(text: str) -> List[str]:
    return [
        token
        for token in re.findall(r"[\w가-힣]+", str(text or "").lower())
        if len(token) > 1 and token not in _STOPWORDS
    ]


def _signature(tokens: List[str]) -> frozenset:
    return frozenset(tokens)


def _similarity(left: frozenset, right: frozenset) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _stable_id(prefix: str, seed: str) -> str:
    return f"{prefix}-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:10]}"


@dataclass
class _Pattern:
    representative: str
    signature: frozenset
    count: int = 1
    last_asked: str = ""
    intent: str = "recurring_question"
    recipe_id: Optional[str] = None
    examples: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": _stable_id("pat", " ".join(sorted(self.signature))),
            "representative": self.representative[:160],
            "count": self.count,
            "last_asked": self.last_asked,
            "intent": self.intent,
            "recipe_id": self.recipe_id,
            "examples": self.examples[:3],
        }


class AutomationIntelligenceService:
    def __init__(
        self,
        *,
        conversation_store: Any = None,
        knowledge_graph: Any = None,
        store: Any = None,
        enable_graph: bool = True,
    ) -> None:
        self._conversations = conversation_store
        self._kg = knowledge_graph
        self._store = store
        self._enable_graph = bool(enable_graph and knowledge_graph is not None)

    # ── question pattern mining ──────────────────────────────────────────

    def _user_questions(
        self, *, user_email: Optional[str], workspace_id: Optional[str]
    ) -> List[Dict[str, Any]]:
        if self._conversations is None:
            return []
        try:
            items = self._conversations.history(
                user_email=user_email,
                allowed_workspaces={workspace_id} if workspace_id is not None else None,
                include_legacy_global=workspace_id is None,
                limit=_MAX_HISTORY,
            )
        except Exception:
            LOGGER.exception("automation intelligence history read failed")
            return []
        questions = []
        for item in items:
            if item.get("role") != "user":
                continue
            content = str(item.get("content") or "").strip()
            if not content or len(content) < 6 or content.startswith("/"):
                continue
            if not _QUESTION_HINT_RE.search(content):
                continue
            questions.append({"content": content, "timestamp": str(item.get("timestamp") or "")})
        return questions

    def question_patterns(
        self, *, user_email: Optional[str] = None, workspace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        questions = self._user_questions(user_email=user_email, workspace_id=workspace_id)
        patterns: List[_Pattern] = []
        for question in questions:
            tokens = _tokens(question["content"])
            if len(tokens) < 2:
                continue
            signature = _signature(tokens)
            match = None
            for pattern in patterns:
                if _similarity(pattern.signature, signature) >= _SIGNATURE_SIMILARITY:
                    match = pattern
                    break
            if match is None:
                patterns.append(
                    _Pattern(
                        representative=question["content"],
                        signature=signature,
                        last_asked=question["timestamp"],
                        examples=[question["content"]],
                    )
                )
                continue
            match.count += 1
            # Union keeps the cluster stable as phrasing drifts.
            match.signature = match.signature | signature
            if question["timestamp"] >= match.last_asked:
                match.last_asked = question["timestamp"]
                match.representative = question["content"]
            if question["content"] not in match.examples:
                match.examples.append(question["content"])

        recurring = [p for p in patterns if p.count >= _MIN_PATTERN_COUNT]
        for pattern in recurring:
            for intent, rule, recipe_id in _INTENT_RULES:
                if rule.search(pattern.representative):
                    pattern.intent = intent
                    pattern.recipe_id = recipe_id
                    break

        recurring.sort(key=lambda p: (p.count, p.last_asked), reverse=True)
        return {
            "questions_scanned": len(questions),
            "patterns": [p.as_dict() for p in recurring[:20]],
            "generated_at": _now(),
        }

    # ── knowledge sources ────────────────────────────────────────────────

    def _knowledge_sources(self) -> List[Dict[str, Any]]:
        if not self._enable_graph or not hasattr(self._kg, "local_sources"):
            return []
        try:
            return list(self._kg.local_sources().get("sources") or [])
        except Exception:
            LOGGER.exception("automation intelligence source read failed")
            return []

    # ── suggestions ──────────────────────────────────────────────────────

    def _installed_suggestion_ids(self, *, workspace_id: Optional[str]) -> Dict[str, Dict[str, Any]]:
        installed: Dict[str, Dict[str, Any]] = {}
        if self._store is None:
            return installed
        try:
            workflows = self._store.list_workflows(workspace_id=workspace_id).get("workflows") or []
        except Exception:
            LOGGER.exception("automation intelligence workflow read failed")
            return installed
        for workflow in workflows:
            metadata = (workflow or {}).get("metadata") or {}
            suggestion_id = metadata.get("suggestion_id")
            if metadata.get("created_from") == "automation_suggestion" and suggestion_id:
                installed[str(suggestion_id)] = workflow
        return installed

    def suggestions(
        self, *, user_email: Optional[str] = None, workspace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        pattern_report = self.question_patterns(user_email=user_email, workspace_id=workspace_id)
        installed = self._installed_suggestion_ids(workspace_id=workspace_id)

        items: List[Dict[str, Any]] = []
        for pattern in pattern_report["patterns"]:
            suggestion_id = _stable_id("sug-q", pattern["id"])
            items.append({
                "id": suggestion_id,
                "kind": "recurring_question",
                "intent": pattern["intent"],
                "recipe_id": pattern["recipe_id"],
                "title": pattern["representative"],
                "reason": {
                    "type": "repeated_question",
                    "count": pattern["count"],
                    "last_asked": pattern["last_asked"],
                    "examples": pattern["examples"],
                },
                "cadence": "daily",
                "installed": suggestion_id in installed,
                "workflow_id": (installed.get(suggestion_id) or {}).get("id"),
            })

        for source in self._knowledge_sources():
            file_status = source.get("file_status") or {}
            indexed = sum(int(v or 0) for v in file_status.values())
            if indexed <= 0:
                continue
            suggestion_id = _stable_id("sug-src", str(source.get("id") or source.get("root_path") or ""))
            items.append({
                "id": suggestion_id,
                "kind": "knowledge_source",
                "intent": "source_digest",
                "recipe_id": None,
                "title": str(source.get("label") or source.get("root_path") or "knowledge folder"),
                "reason": {
                    "type": "connected_source",
                    "root_path": source.get("root_path"),
                    "indexed_files": indexed,
                    "watch_enabled": bool(source.get("watch_enabled")),
                },
                "cadence": "when new knowledge arrives",
                "installed": suggestion_id in installed,
                "workflow_id": (installed.get(suggestion_id) or {}).get("id"),
            })

        return {
            "suggestions": items,
            "questions_scanned": pattern_report["questions_scanned"],
            "consent": {
                "default_state": "draft_disabled",
                "local_only": True,
                "external_actions": False,
                "requires_user_enable": True,
                "review_before_run": True,
            },
            "generated_at": _now(),
        }

    def find_suggestion(
        self,
        suggestion_id: str,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        for suggestion in self.suggestions(user_email=user_email, workspace_id=workspace_id)["suggestions"]:
            if suggestion["id"] == suggestion_id:
                return suggestion
        return None

    # ── workflow building ────────────────────────────────────────────────

    def build_suggestion_workflow(
        self, suggestion: Dict[str, Any], *, enabled: bool = False
    ) -> Dict[str, Any]:
        """Build a consent-first workflow definition from a suggestion.

        Mirrors the starter-recipe shape: trigger → draft agent → review
        output, installed disabled by default, review-queue gated, local
        only, and stamped with provenance so installs stay idempotent.
        """
        kind = suggestion.get("kind")
        title = str(suggestion.get("title") or "automation")[:80]
        if kind == "knowledge_source":
            trigger: Dict[str, Any] = {"trigger": "brain_event"}
            trigger_name = "New knowledge in connected folder"
            name = f"Folder digest: {title}"
            reason = suggestion.get("reason") or {}
            prompt = (
                "New knowledge arrived from the connected folder "
                f"'{reason.get('root_path') or title}'. Draft a short digest of what "
                "was added, what changed, and any follow-ups it suggests. Use only "
                "the local Brain; do not contact external services."
            )
            creates = ["folder digest", "change summary", "follow-up suggestions"]
        else:
            trigger = {"trigger": "interval", "interval_seconds": 86_400}
            trigger_name = "User-enabled schedule"
            name = f"Scheduled answer: {title}"
            prompt = (
                "The user repeatedly asks this question:\n"
                f"“{suggestion.get('title')}”\n"
                "Answer it from the current Brain (memories, knowledge graph, "
                "recent activity) as a concise draft the user can review. "
                "Use only local knowledge; do not contact external services."
            )
            creates = ["scheduled answer draft", "supporting evidence", "suggested follow-ups"]

        trigger_config = {
            **trigger,
            "enabled": bool(enabled),
            "review_queue": True,
            "consent_required": True,
            "local_only": True,
            "external_actions": False,
        }
        return {
            "name": name,
            "nodes": [
                {
                    "id": "trigger",
                    "type": "trigger",
                    "name": trigger_name,
                    "config": trigger_config,
                    "next": "draft",
                },
                {
                    "id": "draft",
                    "type": "agent",
                    "name": "Draft for review",
                    "config": {
                        "agent": "agent:planner",
                        "goal": prompt,
                        "prompt": prompt,
                        "roles": ["researcher", "planner", "executor", "reviewer"],
                        "mode": "draft",
                        "local_only": True,
                        "external_actions": False,
                        "requires_review": True,
                    },
                    "next": "output",
                },
                {
                    "id": "output",
                    "type": "output",
                    "name": "Review before saving",
                    "config": {
                        "value": "Draft ready for review. Save, edit, or discard it before it becomes durable memory.",
                    },
                    "next": None,
                },
            ],
            "metadata": {
                "created_from": "automation_suggestion",
                "suggestion_id": suggestion.get("id"),
                "suggestion_kind": kind,
                "suggestion_title": title,
                "suggestion_reason": suggestion.get("reason"),
                "automation_state": "enabled" if enabled else "draft_disabled",
                "local_only": True,
                "external_actions": False,
                "requires_user_enable": not enabled,
                "creates": creates,
            },
        }

    # ── overview for the automation surface ──────────────────────────────

    def overview(
        self, *, user_email: Optional[str] = None, workspace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        suggestion_report = self.suggestions(user_email=user_email, workspace_id=workspace_id)
        installed: List[Dict[str, Any]] = []
        if self._store is not None:
            try:
                workflows = self._store.list_workflows(workspace_id=workspace_id).get("workflows") or []
            except Exception:
                LOGGER.exception("automation intelligence workflow read failed")
                workflows = []
            for workflow in workflows:
                metadata = (workflow or {}).get("metadata") or {}
                if metadata.get("created_from") not in {"automation_suggestion", "brain_automation_recipe"}:
                    continue
                installed.append({
                    "id": workflow.get("id"),
                    "name": workflow.get("name"),
                    "created_from": metadata.get("created_from"),
                    "suggestion_id": metadata.get("suggestion_id"),
                    "recipe_id": metadata.get("recipe_id"),
                    "enabled": metadata.get("automation_state") == "enabled",
                    "requires_user_enable": bool(metadata.get("requires_user_enable", True)),
                    "creates": metadata.get("creates") or [],
                })
        return {
            "suggestions": suggestion_report["suggestions"],
            "questions_scanned": suggestion_report["questions_scanned"],
            "installed": installed,
            "consent": suggestion_report["consent"],
            "generated_at": _now(),
        }


__all__ = ["AutomationIntelligenceService"]
