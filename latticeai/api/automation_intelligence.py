"""Question-driven everyday automation API router (v9.4.0).

Exposes :class:`~latticeai.services.automation_intelligence.AutomationIntelligenceService`:
recurring-question patterns with evidence, automation suggestions (from the
user's own questions and connected knowledge folders), one-click consent-first
install, and a combined overview for the automation surface.

Installs follow the same consent contract as the starter recipes: the
workflow is created as a disabled draft (unless the user explicitly asks to
enable), review-queue gated, local-only, and idempotent per suggestion.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from latticeai.services.automation_intelligence import AutomationIntelligenceService
from latticeai.services.brain_automation import find_installed_recipe_workflow


class SuggestionInstallRequest(BaseModel):
    suggestion_id: str
    enabled: bool = False


def create_automation_intelligence_router(
    *,
    service: AutomationIntelligenceService,
    store: Any,
    require_user: Callable[[Request], str],
    gate_read: Callable[[Request], Optional[str]],
    gate_write: Callable[[Request], Optional[str]],
    append_audit_event: Callable[..., None],
    workspace_graph: Callable[[], Any],
) -> APIRouter:
    from lattice_brain.workflow import legacy_steps_from_nodes, validate_definition

    router = APIRouter()

    @router.get("/api/automation/patterns")
    async def automation_patterns(request: Request):
        user = require_user(request)
        scope = gate_read(request)
        return service.question_patterns(user_email=user, workspace_id=scope)

    @router.get("/api/automation/suggestions")
    async def automation_suggestions(request: Request):
        user = require_user(request)
        scope = gate_read(request)
        return service.suggestions(user_email=user, workspace_id=scope)

    @router.get("/api/automation/overview")
    async def automation_overview(request: Request):
        user = require_user(request)
        scope = gate_read(request)
        return service.overview(user_email=user, workspace_id=scope)

    @router.post("/api/automation/install")
    async def automation_install(req: SuggestionInstallRequest, request: Request):
        user = require_user(request)
        scope = gate_write(request)
        suggestion = service.find_suggestion(
            req.suggestion_id, user_email=user, workspace_id=scope
        )
        if suggestion is None:
            raise HTTPException(
                status_code=404,
                detail=f"Automation suggestion not found: {req.suggestion_id}",
            )
        definition = service.build_suggestion_workflow(suggestion, enabled=req.enabled)

        # Idempotent per suggestion: reuse the workflow provenance match used
        # for recipes, keyed on suggestion_id instead of recipe_id.
        existing = None
        for workflow in store.list_workflows(workspace_id=scope).get("workflows") or []:
            metadata = (workflow or {}).get("metadata") or {}
            if (
                metadata.get("created_from") == "automation_suggestion"
                and metadata.get("suggestion_id") == req.suggestion_id
            ):
                existing = workflow
                break
        if existing is None and suggestion.get("recipe_id"):
            existing = find_installed_recipe_workflow(
                store.list_workflows(workspace_id=scope).get("workflows"),
                suggestion["recipe_id"],
            )
        if existing is not None:
            return {
                "workflow": existing,
                "suggestion": suggestion,
                "enabled": bool((existing.get("metadata") or {}).get("automation_state") == "enabled"),
                "already_installed": True,
            }

        errors = validate_definition({"name": definition["name"], "nodes": definition["nodes"]})
        if errors:
            raise HTTPException(status_code=400, detail={"validation_errors": errors})
        workflow = store.create_workflow(
            name=definition["name"],
            steps=legacy_steps_from_nodes(definition["nodes"]),
            nodes=definition["nodes"],
            metadata=definition["metadata"],
            user_email=user or None,
            graph=workspace_graph(),
            workspace_id=scope,
        )
        append_audit_event(
            "automation_suggestion_installed",
            user_email=user,
            workflow_id=workflow.get("id"),
            suggestion_id=req.suggestion_id,
            suggestion_kind=suggestion.get("kind"),
            enabled=bool(req.enabled),
        )
        return {
            "workflow": workflow,
            "suggestion": suggestion,
            "enabled": bool(req.enabled),
            "already_installed": False,
        }

    return router


__all__ = ["create_automation_intelligence_router"]
