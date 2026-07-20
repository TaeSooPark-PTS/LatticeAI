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

v9.8.0 quality layer (additive): every suggestion carries a deterministic
``confidence`` score plus its ``confidence_factors`` evidence (repeat count,
distinct phrasings, intent match, related Brain nodes / indexed files);
suggestions below a minimum confidence are suppressed, duplicate suggestions
targeting an already-suggested or already-installed starter recipe are
deduplicated, and the response reports the suppression counters under
``quality``.
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

# Suggestion quality gates (v9.8.0, additive). Suggestions below the minimum
# confidence are suppressed (insufficient evidence); suggestions between the
# minimum and the low-confidence threshold are shown but flagged so the UI
# can render them less prominently.
_MIN_SUGGESTION_CONFIDENCE = 0.35
_LOW_CONFIDENCE_THRESHOLD = 0.5
_KG_GROUNDING_LIMIT = 5


def _question_confidence(
    count: int,
    examples: List[str],
    recipe_id: Optional[str],
    kg_related: Optional[int],
) -> tuple:
    """Deterministic confidence for a recurring-question suggestion.

    Evidence factors: how often the question repeats, how many distinct
    phrasings exist, whether it maps onto a known starter-recipe intent, and
    (when the graph is available) how many Brain nodes relate to it.
    """
    score = 0.3 + 0.5 * min(1.0, (int(count) - 1) / 4)
    score += min(0.15, 0.05 * len(examples or []))
    if recipe_id:
        score += 0.15
    if kg_related:
        score += min(0.2, 0.05 * int(kg_related))
    factors = {
        "repeat_count": int(count),
        "distinct_examples": len(examples or []),
        "intent_match": bool(recipe_id),
        "kg_related_nodes": kg_related,
    }
    return round(min(1.0, score), 2), factors


def _source_confidence(indexed: int, watch_enabled: bool) -> tuple:
    """Deterministic confidence for a knowledge-source digest suggestion."""
    score = 0.25 + 0.6 * min(1.0, int(indexed) / 25)
    if watch_enabled:
        score += 0.1
    factors = {"indexed_files": int(indexed), "watch_enabled": bool(watch_enabled)}
    return round(min(1.0, score), 2), factors

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

    def _kg_related_count(
        self, text: str, *, workspace_id: Optional[str]
    ) -> Optional[int]:
        """Count Brain nodes related to a recurring question (grounding evidence).

        Returns ``None`` when the graph is unavailable so confidence scoring can
        distinguish "grounding impossible" from "grounded with zero hits".
        Scoping mirrors the conversation-history read: an explicit workspace is
        strict, no workspace falls back to the unscoped/legacy view.
        """
        if not self._enable_graph or not hasattr(self._kg, "search"):
            return None
        try:
            report = self._kg.search(
                str(text or "")[:200],
                limit=_KG_GROUNDING_LIMIT,
                allowed_workspaces={workspace_id} if workspace_id is not None else None,
                include_legacy_global=workspace_id is None,
            )
        except Exception:
            LOGGER.exception("automation intelligence KG grounding failed")
            return None
        if not isinstance(report, dict):
            return None
        return len(report.get("matches") or [])

    # ── suggestions ──────────────────────────────────────────────────────

    def _installed_workflows(
        self, *, workspace_id: Optional[str]
    ) -> tuple:
        """Map installed automation workflows by suggestion id and recipe id.

        The recipe map lets a recurring-question suggestion that targets an
        already-installed starter recipe surface as *installed* instead of
        re-suggesting the same automation — the install API is idempotent on
        exactly this provenance, so the read side must agree.
        """
        by_suggestion: Dict[str, Dict[str, Any]] = {}
        by_recipe: Dict[str, Dict[str, Any]] = {}
        if self._store is None:
            return by_suggestion, by_recipe
        try:
            workflows = self._store.list_workflows(workspace_id=workspace_id).get("workflows") or []
        except Exception:
            LOGGER.exception("automation intelligence workflow read failed")
            return by_suggestion, by_recipe
        for workflow in workflows:
            metadata = (workflow or {}).get("metadata") or {}
            created_from = metadata.get("created_from")
            if created_from == "automation_suggestion" and metadata.get("suggestion_id"):
                by_suggestion[str(metadata["suggestion_id"])] = workflow
            elif created_from == "brain_automation_recipe" and metadata.get("recipe_id"):
                by_recipe[str(metadata["recipe_id"])] = workflow
        return by_suggestion, by_recipe

    def suggestions(
        self, *, user_email: Optional[str] = None, workspace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        pattern_report = self.question_patterns(user_email=user_email, workspace_id=workspace_id)
        by_suggestion, by_recipe = self._installed_workflows(workspace_id=workspace_id)

        items: List[Dict[str, Any]] = []
        suppressed_low_confidence = 0
        suppressed_duplicates = 0
        seen_recipe_ids: set = set()

        # Patterns arrive sorted by (count, last_asked) desc, so the first
        # suggestion per recipe is the strongest — later ones targeting the
        # same recipe would install the identical workflow and are suppressed.
        for pattern in pattern_report["patterns"]:
            recipe_id = pattern["recipe_id"]
            if recipe_id and recipe_id in seen_recipe_ids:
                suppressed_duplicates += 1
                continue
            kg_related = self._kg_related_count(
                pattern["representative"], workspace_id=workspace_id
            )
            confidence, factors = _question_confidence(
                pattern["count"], pattern["examples"], recipe_id, kg_related
            )
            if confidence < _MIN_SUGGESTION_CONFIDENCE:
                suppressed_low_confidence += 1
                continue
            if recipe_id:
                seen_recipe_ids.add(recipe_id)
            suggestion_id = _stable_id("sug-q", pattern["id"])
            workflow = by_suggestion.get(suggestion_id) or (
                by_recipe.get(str(recipe_id)) if recipe_id else None
            )
            items.append({
                "id": suggestion_id,
                "kind": "recurring_question",
                "intent": pattern["intent"],
                "recipe_id": recipe_id,
                "title": pattern["representative"],
                "reason": {
                    "type": "repeated_question",
                    "count": pattern["count"],
                    "last_asked": pattern["last_asked"],
                    "examples": pattern["examples"],
                },
                "cadence": "daily",
                "confidence": confidence,
                "confidence_factors": factors,
                "low_confidence": confidence < _LOW_CONFIDENCE_THRESHOLD,
                "installed": workflow is not None,
                "workflow_id": (workflow or {}).get("id"),
            })

        for source in self._knowledge_sources():
            file_status = source.get("file_status") or {}
            indexed = sum(int(v or 0) for v in file_status.values())
            if indexed <= 0:
                continue
            confidence, factors = _source_confidence(
                indexed, bool(source.get("watch_enabled"))
            )
            if confidence < _MIN_SUGGESTION_CONFIDENCE:
                # A barely-indexed folder is not enough evidence for a digest
                # automation yet — do not suggest.
                suppressed_low_confidence += 1
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
                "confidence": confidence,
                "confidence_factors": factors,
                "low_confidence": confidence < _LOW_CONFIDENCE_THRESHOLD,
                "installed": suggestion_id in by_suggestion,
                "workflow_id": (by_suggestion.get(suggestion_id) or {}).get("id"),
            })

        return {
            "suggestions": items,
            "questions_scanned": pattern_report["questions_scanned"],
            "quality": {
                "min_confidence": _MIN_SUGGESTION_CONFIDENCE,
                "low_confidence_threshold": _LOW_CONFIDENCE_THRESHOLD,
                "suppressed_low_confidence": suppressed_low_confidence,
                "suppressed_duplicates": suppressed_duplicates,
            },
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
                "suggestion_confidence": suggestion.get("confidence"),
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
            "quality": suggestion_report["quality"],
            "consent": suggestion_report["consent"],
            "generated_at": _now(),
        }


__all__ = ["AutomationIntelligenceService"]
