"""Consent-first Brain automation recipes.

The recipes here are product-level starter workflows, not hidden background
jobs. Installing one creates a disabled draft workflow so the user can inspect
and enable it deliberately.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass(frozen=True)
class BrainAutomationRecipe:
    id: str
    name: str
    summary: str
    user_value: str
    cadence: str
    trigger: Dict[str, Any]
    prompt: str
    creates: List[str]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "summary": self.summary,
            "user_value": self.user_value,
            "cadence": self.cadence,
            "trigger": deepcopy(self.trigger),
            "creates": list(self.creates),
            "consent": {
                "default_state": "draft_disabled",
                "local_only": True,
                "external_actions": False,
                "requires_user_enable": True,
                "review_before_run": True,
            },
        }


_RECIPES: List[BrainAutomationRecipe] = [
    BrainAutomationRecipe(
        id="daily-memory-digest",
        name="Daily Memory Digest",
        summary="Collects the day's new memories into a short review draft.",
        user_value="Users see what the Brain kept today without searching through chats.",
        cadence="daily",
        trigger={"trigger": "interval", "interval_seconds": 86_400},
        prompt=(
            "Review today's new Brain memories and draft a concise digest with "
            "important decisions, unresolved questions, and suggested next actions. "
            "Do not contact external services."
        ),
        creates=["memory digest", "decision summary", "next-action suggestions"],
    ),
    BrainAutomationRecipe(
        id="weekly-project-review",
        name="Weekly Project Review",
        summary="Turns project context into a weekly checkpoint draft.",
        user_value="Users can restart a project without explaining the week again.",
        cadence="weekly",
        trigger={"trigger": "interval", "interval_seconds": 604_800},
        prompt=(
            "Review this workspace's recent memories, workflow runs, and decisions. "
            "Draft a project checkpoint with progress, risks, blockers, and next steps. "
            "Keep it local and ask before any external action."
        ),
        creates=["project checkpoint", "risk list", "next-week plan"],
    ),
    BrainAutomationRecipe(
        id="follow-up-radar",
        name="Follow-up Radar",
        summary="Looks for follow-up candidates when new knowledge enters the Brain.",
        user_value="Users get gentle reminders for loose ends without a noisy task system.",
        cadence="when new memory is saved",
        trigger={"trigger": "brain_event"},
        prompt=(
            "Inspect the new Brain memory for follow-up signals such as decisions, "
            "promises, deadlines, unresolved questions, or 'later' language. "
            "Return suggestions only; do not create tasks without approval."
        ),
        creates=["follow-up suggestions", "open-question list", "approval-ready task drafts"],
    ),
]

_RECIPE_BY_ID = {recipe.id: recipe for recipe in _RECIPES}


def list_brain_automation_recipes() -> Dict[str, Any]:
    """Return user-facing, consent-first automation recipe metadata."""
    return {
        "recipes": [recipe.as_dict() for recipe in _RECIPES],
        "principles": {
            "local_first": True,
            "drafts_before_automation": True,
            "no_external_actions_without_consent": True,
        },
    }


def find_installed_recipe_workflow(
    workflows: Any, recipe_id: str
) -> Dict[str, Any] | None:
    """Return an existing draft installed from ``recipe_id``, if any.

    Installing a recipe is idempotent: clicking "Create reviewable draft" twice
    should surface the existing draft instead of accumulating duplicates. We
    match on the ``brain_automation_recipe`` provenance metadata stamped by
    :func:`build_brain_automation_workflow`.
    """
    for workflow in workflows or []:
        metadata = (workflow or {}).get("metadata") or {}
        if (
            metadata.get("created_from") == "brain_automation_recipe"
            and metadata.get("recipe_id") == recipe_id
        ):
            return workflow
    return None


def build_brain_automation_workflow(recipe_id: str, *, enabled: bool = False) -> Dict[str, Any]:
    """Build a workflow definition for a recipe.

    ``enabled`` defaults to ``False`` so installing a recipe creates an
    inspectable draft. The trigger service treats explicit ``enabled: false`` as
    disarmed, while legacy workflows without the field keep their behavior.
    """
    recipe = _RECIPE_BY_ID.get(recipe_id)
    if recipe is None:
        raise KeyError(recipe_id)

    trigger_config = {
        **deepcopy(recipe.trigger),
        "enabled": bool(enabled),
        "consent_required": True,
        "local_only": True,
        "external_actions": False,
    }
    return {
        "name": recipe.name,
        "nodes": [
            {
                "id": "trigger",
                "type": "trigger",
                "name": "User-enabled schedule" if recipe.trigger["trigger"] == "interval" else "New Brain memory",
                "config": trigger_config,
                "next": "draft",
            },
            {
                "id": "draft",
                "type": "agent",
                "name": "Draft Brain review",
                "config": {
                    "agent": "agent:planner",
                    "prompt": recipe.prompt,
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
            "created_from": "brain_automation_recipe",
            "recipe_id": recipe.id,
            "recipe_summary": recipe.summary,
            "recipe_user_value": recipe.user_value,
            "automation_state": "enabled" if enabled else "draft_disabled",
            "local_only": True,
            "external_actions": False,
            "requires_user_enable": not enabled,
            "creates": list(recipe.creates),
        },
    }


# === A방향 (Act/automation + BrainAutomationPanel) E2E 시나리오 초안 ===
# (backend 인터페이스 list_brain_automation_recipes / find_installed... / build_... 완료 후 즉시 작성)
# 1. Recipe 목록 노출: frontend BrainAutomationPanel이 list_brain_automation_recipes() 호출 → recipes + consent metadata 표시.
# 2. "Create reviewable draft" 클릭:
#    - build_brain_automation_workflow(recipe_id, enabled=False) 로 draft 생성 (metadata.recipe_id + created_from=brain_automation_recipe)
#    - find_installed_recipe_workflow 로 사전 dedup 체크 → 이미 있으면 기존 반환, UI disabled + "✓ Reviewable draft ready" 피드백.
#    - 생성된 workflow는 automation_state="draft_disabled", trigger.enabled=False → TriggerService 무시.
# 3. Dedup guard (UI + backend): metadata.recipe_id + created_from 기준. 중복 클릭 가드 (no-dup) 로 double submit 방지.
# 4. User가 draft를 review/수정 후 enabled=True 로 전환 → TriggerService._triggered_workflows 에서 enabled True인 것만 arm.
# 5. Interval trigger E2E:
#    - reconcile_missed() : 다운타임 중 missed → "skipped" 이벤트 기록 (catch-up storm 없음).
#    - tick_intervals() : last_fired_at + interval + last_attempt_at dedup 가드 (10s cooldown) 로 중복 실행 방지.
#    - LATTICE_TZ 환경변수: describe()에 "tz" 노출, 이벤트 at 값은 epoch이지만 클라이언트가 LATTICE_TZ로 현지화.
# 6. Brain event trigger E2E: kg_ingest.* post_tool hook → on_brain_event → matching source_type 필터 → _fire (dedup 5s).
# 7. Failure degraded:
#    - _fire 에서 run_workflow 예외 → _record_fire_outcome → consecutive_failures++ , describe() "status":"degraded" ( >=3 ).
#    - 성공 시 reset. (실행 내부 실패는 workflow run record에 남음, scheduler는 launch health만).
# 8. Run provenance: fired run의 inputs["__trigger__"] = {"type": "interval"|"brain_event", ...} 로 감사/디버그 가능.
# 9. Consent-first: draft_disabled 기본, user enable 전까지 절대 실행 안 됨. "review_before_run": True.
# 10. End-to-end Act: draft → enable → trigger fire → agent:planner "Draft Brain review" 노드 → output (requires_review) → user review → save or discard.
#
# 다음: 실제 API (e.g. POST /brain/automation/install-draft) 가 UI에서 호출되면 위 시나리오에 대한 통합 테스트 + RunExecutor 경로 검증 즉시 추가.
# 현재 상태: backend recipe 인터페이스 + TriggerService edge 하드닝 + AgentRuntime wiring 완료. UI (App.tsx + styles) + test_brain_automation.py 는 별도 완료 보고됨.

