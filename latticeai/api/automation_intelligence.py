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

import asyncio
import os
import time
from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from latticeai.core.quiet import quiet
from latticeai.services.automation_execution import (
    build_last_execution,
    dry_run_report,
    enqueue_failed_execution,
    is_automation_workflow,
    summarize_workflow_run,
)
from latticeai.services.automation_intelligence import AutomationIntelligenceService
from latticeai.services.brain_automation import find_installed_recipe_workflow


class SuggestionInstallRequest(BaseModel):
    suggestion_id: str
    enabled: bool = False


class AutomationRunNowRequest(BaseModel):
    workflow_id: str
    # Dry-run first: the default reports what WOULD happen without side
    # effects; an explicit dry_run=false runs the automation once for real.
    dry_run: bool = True


def _run_now_wait_seconds() -> float:
    raw = os.environ.get("LATTICEAI_AUTOMATION_RUN_NOW_WAIT", "30")
    try:
        return max(0.0, min(float(raw), 300.0))
    except (TypeError, ValueError):
        return 30.0


def create_automation_intelligence_router(
    *,
    service: AutomationIntelligenceService,
    store: Any,
    require_user: Callable[[Request], str],
    gate_read: Callable[[Request], Optional[str]],
    gate_write: Callable[[Request], Optional[str]],
    append_audit_event: Callable[..., None],
    workspace_graph: Callable[[], Any],
    run_executor: Any = None,
    review_queue: Any = None,
) -> APIRouter:
    from lattice_brain.runtime.statuses import RUN_TERMINAL_STATUSES
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

    def _stamp_last_execution(
        workflow_id: str, last_execution: dict, scope: Optional[str]
    ) -> None:
        """Persist the execution record on the workflow's metadata (merge)."""
        try:
            store.update_workflow_definition(
                workflow_id,
                metadata={"last_execution": last_execution},
                workspace_id=scope,
            )
        except Exception:  # noqa: BLE001 — surfacing must never undo the run
            quiet()

    @router.post("/api/automation/run-now")
    async def automation_run_now(req: AutomationRunNowRequest, request: Request):
        """Run an installed automation once — dry-run by default.

        * ``dry_run=true``: deterministic report of what WOULD happen; no
          runner executes and nothing but the ``last_execution`` stamp moves.
        * ``dry_run=false``: one real execution through the async run
          executor; the request waits briefly for completion, stamps the
          result, and enqueues failed executions into the review inbox.
        """
        user = require_user(request)
        scope = gate_write(request)
        try:
            workflow = store.get_workflow(req.workflow_id, workspace_id=scope)
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=404, detail=f"Automation not found: {req.workflow_id}"
            ) from exc
        if not is_automation_workflow(workflow):
            raise HTTPException(
                status_code=404,
                detail=f"Workflow is not an installed automation: {req.workflow_id}",
            )

        if req.dry_run:
            report = dry_run_report(workflow)
            last_execution = build_last_execution(
                mode="dry_run",
                status=report["status"],
                summary=report["summary"],
            )
            _stamp_last_execution(req.workflow_id, last_execution, scope)
            append_audit_event(
                "automation_run_now",
                user_email=user,
                workflow_id=req.workflow_id,
                dry_run=True,
                status=report["status"],
            )
            return {
                "workflow_id": req.workflow_id,
                "dry_run": True,
                "status": report["status"],
                "report": report,
                "last_execution": last_execution,
            }

        if run_executor is None:
            raise HTTPException(
                status_code=503,
                detail="Automation execution runtime is not available.",
            )
        started = await run_executor.start_workflow(
            workflow,
            workflow_id=req.workflow_id,
            user_email=user or None,
            scope=scope,
            inputs={"trigger": "run_now"},
        )
        run_id = str(((started or {}).get("run") or {}).get("id") or "")

        # Bounded, non-cancelling wait: poll the durable run row so a slow
        # execution keeps running in the background instead of being aborted.
        run = (started or {}).get("run") or {}
        deadline = time.monotonic() + _run_now_wait_seconds()
        while time.monotonic() < deadline:
            try:
                run = store.get_workflow_run(run_id, workspace_id=scope)
            except FileNotFoundError:
                break
            if str(run.get("status") or "") in RUN_TERMINAL_STATUSES:
                break
            await asyncio.sleep(0.05)

        status = str(run.get("status") or "running")
        completed = status in RUN_TERMINAL_STATUSES
        summary = (
            summarize_workflow_run(run)
            if completed
            else "started — still running in the background"
        )
        last_execution = build_last_execution(
            mode="live",
            status=status if completed else "running",
            summary=summary,
            run_id=run_id or None,
        )
        _stamp_last_execution(req.workflow_id, last_execution, scope)

        review_item = None
        if status == "failed":
            review_item = enqueue_failed_execution(
                review_queue,
                workflow=workflow,
                run_id=run_id or None,
                error=summary,
                user_email=user or None,
                workspace_id=scope,
            )
        append_audit_event(
            "automation_run_now",
            user_email=user,
            workflow_id=req.workflow_id,
            dry_run=False,
            run_id=run_id or None,
            status=last_execution["status"],
        )
        payload = {
            "workflow_id": req.workflow_id,
            "dry_run": False,
            "status": last_execution["status"],
            "run_id": run_id or None,
            "last_execution": last_execution,
        }
        if review_item is not None:
            payload["review_item_id"] = review_item.get("id")
        return payload

    def _combined_runs(request: Request, limit: int = 20) -> dict[str, Any]:
        require_user(request)
        scope = gate_read(request)
        capped = max(1, min(int(limit or 20), 100))
        if hasattr(store, "list_combined_runs"):
            return store.list_combined_runs(limit=capped, workspace_id=scope)
        # Store without the helper: merge the two existing listings in place.
        agent_listing = (
            store.list_agents(workspace_id=scope)
            if hasattr(store, "list_agents")
            else {"runs": []}
        )
        workflow_listing = (
            store.list_workflow_runs(limit=capped, workspace_id=scope)
            if hasattr(store, "list_workflow_runs")
            else {"runs": []}
        )
        from latticeai.core.workspace_runs import WorkspaceRuns

        rows = []
        for run in agent_listing.get("runs") or []:
            rows.append(WorkspaceRuns.activity_run_row(run, source="agent"))
        for run in workflow_listing.get("runs") or []:
            rows.append(WorkspaceRuns.activity_run_row(run, source="workflow"))
        rows.sort(key=lambda row: str(row.get("started_at") or ""), reverse=True)
        return {"runs": rows[:capped]}

    @router.get("/api/activity/runs")
    async def activity_runs(request: Request, limit: int = 20):
        """Unified agent + workflow run timeline (layout rebuild screen 09)."""
        return _combined_runs(request, limit=limit)

    @router.get("/automations/runs/combined")
    async def automations_runs_combined(request: Request, limit: int = 20):
        """Alias used by the frontend handoff for the same combined timeline."""
        return _combined_runs(request, limit=limit)

    return router


__all__ = ["create_automation_intelligence_router"]
