"""Workflow Designer API router (v2).

Create / edit / validate / execute / inspect / export / import workflows plus
run history, layered on :mod:`lattice_brain.workflow` and the existing
``WorkspaceOSStore`` workflow persistence (so pre-2.0 workflow history is
preserved). Paths are namespaced under ``/workflows`` to avoid colliding with
``/workspace/workflows``.

server_app injects a ``build_runners`` callable that returns the executable
runner map (tool / skill / plugin / agent), which is what lets a workflow
actually drive plugins, skills, and multi-agent runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from latticeai.api.ui_redirects import app_redirect


class WorkflowDefinitionRequest(BaseModel):
    name: str
    nodes: List[Dict[str, Any]] = []
    metadata: Dict[str, Any] = {}


class WorkflowUpdateRequest(BaseModel):
    name: Optional[str] = None
    nodes: Optional[List[Dict[str, Any]]] = None
    metadata: Optional[Dict[str, Any]] = None


class WorkflowResumeRequest(BaseModel):
    approved: bool = True


class WorkflowRunRequest(BaseModel):
    inputs: Dict[str, Any] = {}


class WorkflowValidateRequest(BaseModel):
    name: str = "Draft"
    nodes: List[Dict[str, Any]] = []


class WorkflowImportRequest(BaseModel):
    data: Dict[str, Any] = {}


def create_workflow_designer_router(
    *,
    store,
    require_user: Callable[[Request], str],
    get_current_user: Callable[[Request], Optional[str]],
    gate_read: Callable[[Request], Optional[str]],
    gate_write: Callable[[Request], Optional[str]],
    workspace_graph: Callable[[], Any],
    build_runners: Callable[[Optional[str], Optional[str]], Dict[str, Callable[..., Any]]],
    append_audit_event: Callable[..., None],
    ui_file_response: Optional[Callable[[Path], Any]] = None,
    static_dir: Optional[Path] = None,
    hooks: Any = None,
    run_executor: Any = None,
    trigger_service: Any = None,
) -> APIRouter:
    from lattice_brain.workflow import (
        WorkflowEngine,
        validate_definition,
        export_workflow,
        import_workflow,
        WorkflowError,
    )

    router = APIRouter()

    @router.get("/workflows")
    async def workflows_page(request: Request):
        require_user(request)
        return app_redirect("workflows", request)

    @router.get("/workflows/api/definitions")
    async def list_definitions(request: Request, q: str = ""):
        require_user(request)
        scope = gate_read(request)
        return store.list_workflows(query=q, workspace_id=scope)

    @router.post("/workflows/api/definitions")
    async def create_definition(req: WorkflowDefinitionRequest, request: Request):
        current_user = require_user(request)
        scope = gate_write(request)
        errors = validate_definition({"name": req.name, "nodes": req.nodes})
        if errors:
            raise HTTPException(status_code=400, detail={"validation_errors": errors})
        workflow = store.create_workflow(
            name=req.name,
            steps=[{"action": n.get("type"), "node": n.get("id")} for n in req.nodes],
            nodes=req.nodes,
            metadata=req.metadata,
            user_email=current_user or None,
            graph=workspace_graph(),
            workspace_id=scope,
        )
        append_audit_event("workflow_created", user_email=current_user, workflow_id=workflow["id"])
        return {"workflow": workflow}

    @router.get("/workflows/api/definitions/{workflow_id}")
    async def get_definition(workflow_id: str, request: Request):
        require_user(request)
        scope = gate_read(request)
        try:
            return {"workflow": store.get_workflow(workflow_id, workspace_id=scope)}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Workflow not found: {exc}") from exc

    @router.patch("/workflows/api/definitions/{workflow_id}")
    async def update_definition(workflow_id: str, req: WorkflowUpdateRequest, request: Request):
        require_user(request)
        scope = gate_write(request)
        if req.nodes is not None:
            errors = validate_definition({"name": req.name or "wf", "nodes": req.nodes})
            if errors:
                raise HTTPException(status_code=400, detail={"validation_errors": errors})
        try:
            workflow = store.update_workflow_definition(
                workflow_id,
                name=req.name,
                nodes=req.nodes,
                metadata=req.metadata,
                workspace_id=scope,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Workflow not found: {exc}") from exc
        return {"workflow": workflow}

    @router.post("/workflows/api/validate")
    async def validate_workflow(req: WorkflowValidateRequest, request: Request):
        require_user(request)
        errors = validate_definition({"name": req.name, "nodes": req.nodes})
        return {"ok": not errors, "errors": errors}

    @router.post("/workflows/api/definitions/{workflow_id}/run")
    async def run_definition(workflow_id: str, req: WorkflowRunRequest, request: Request):
        current_user = require_user(request)
        scope = gate_write(request)
        try:
            workflow = store.get_workflow(workflow_id, workspace_id=scope)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Workflow not found: {exc}") from exc
        if run_executor is not None:
            result = await run_executor.start_workflow(
                workflow,
                workflow_id=workflow_id,
                user_email=current_user or None,
                scope=scope,
                inputs=req.inputs,
            )
            append_audit_event("workflow_run_queued", user_email=current_user, workflow_id=workflow_id, status="queued")
            return result
        runners = build_runners(current_user or None, scope)
        engine = WorkflowEngine(runners, hooks=hooks)
        result = engine.run(workflow, inputs=req.inputs)
        run = store.record_workflow_run(
            workflow_id=workflow_id,
            name=workflow.get("name") or "workflow",
            status=result.status,
            timeline=result.timeline,
            outputs=result.outputs,
            user_email=current_user or None,
            graph=workspace_graph(),
            workspace_id=scope,
            mode="live",
            pause={"node": result.paused_node, "pending": result.pending_approval,
                   "context": result.paused_context} if result.status == "awaiting_approval" else None,
        )
        append_audit_event("workflow_run", user_email=current_user, workflow_id=workflow_id, status=result.status)
        return {"run": run, "result": result.as_dict()}

    @router.post("/workflows/api/runs/{run_id}/stop")
    async def stop_run(run_id: str, request: Request):
        require_user(request)
        scope = gate_write(request)
        if run_executor is None:
            try:
                run = store.get_workflow_run(run_id, workspace_id=scope)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail=f"Workflow run not found: {run_id}") from exc
            return {
                "stopped": False,
                "reason": "asynchronous cancellation is not supported by the synchronous runtime",
                "run_id": run_id,
                "status": run.get("status"),
            }
        return run_executor.cancel(run_id, kind="workflow", scope=scope)

    @router.post("/workflows/api/runs/{run_id}/resume")
    async def resume_run(run_id: str, req: WorkflowResumeRequest, request: Request):
        """Decide a paused (awaiting_approval) run: approve → the paused node
        executes and the run continues; deny → the run fails honestly."""
        current_user = require_user(request)
        scope = gate_write(request)
        run_record = store.get_workflow_run(run_id, workspace_id=scope)
        pause = run_record.get("pause") or {}
        if run_record.get("status") != "awaiting_approval" or not pause.get("node"):
            raise HTTPException(status_code=409, detail="run is not awaiting approval")
        workflow = store.get_workflow(run_record.get("workflow_id"), workspace_id=scope)
        runners = build_runners(current_user or None, scope)
        engine = WorkflowEngine(runners, hooks=hooks)
        result = engine.resume(
            workflow,
            paused_node=pause["node"],
            paused_context=pause.get("context") or {},
            approved=bool(req.approved),
            prior_timeline=run_record.get("timeline") or [],
        )
        resumed = store.record_workflow_run(
            workflow_id=run_record.get("workflow_id"),
            name=run_record.get("name") or "workflow",
            status=result.status,
            timeline=result.timeline,
            outputs=result.outputs,
            user_email=current_user or None,
            graph=workspace_graph(),
            workspace_id=scope,
            mode="live",
            pause={"node": result.paused_node, "pending": result.pending_approval,
                   "context": result.paused_context} if result.status == "awaiting_approval" else None,
        )
        store.mark_workflow_run_resolved(run_id, resumed_run_id=resumed["id"],
                                         approved=bool(req.approved), workspace_id=scope)
        append_audit_event("workflow_run_resume", user_email=current_user,
                           run_id=run_id, approved=bool(req.approved), status=result.status)
        return {"run": resumed, "result": result.as_dict(), "resumed_from": run_id}

    @router.get("/workflows/api/definitions/{workflow_id}/runs")
    async def list_runs(workflow_id: str, request: Request, limit: int = 50):
        require_user(request)
        scope = gate_read(request)
        return store.list_workflow_runs(workflow_id=workflow_id, limit=limit, workspace_id=scope)

    @router.get("/workflows/api/runs")
    async def list_all_runs(request: Request, limit: int = 50):
        require_user(request)
        scope = gate_read(request)
        return store.list_workflow_runs(limit=limit, workspace_id=scope)

    @router.get("/workflows/api/triggers")
    async def trigger_status(request: Request):
        require_user(request)
        if trigger_service is None:
            return {"running": False, "tick_seconds": None, "armed": []}
        return trigger_service.describe()

    @router.get("/workflows/api/runs/{run_id}/replay")
    async def workflow_run_replay(run_id: str, request: Request):
        require_user(request)
        scope = gate_read(request)
        try:
            return {"replay": store.replay_workflow_run(run_id, workspace_id=scope)}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Workflow run not found: {run_id}") from exc

    @router.get("/workflows/api/export/{workflow_id}")
    async def export_definition(workflow_id: str, request: Request):
        require_user(request)
        scope = gate_read(request)
        try:
            workflow = store.get_workflow(workflow_id, workspace_id=scope)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Workflow not found: {exc}") from exc
        return export_workflow(workflow)

    @router.post("/workflows/api/import")
    async def import_definition(req: WorkflowImportRequest, request: Request):
        current_user = require_user(request)
        scope = gate_write(request)
        try:
            definition = import_workflow(req.data)
        except WorkflowError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        workflow = store.create_workflow(
            name=definition["name"],
            steps=[{"action": n.get("type"), "node": n.get("id")} for n in definition["nodes"]],
            nodes=definition["nodes"],
            metadata=definition.get("metadata") or {},
            user_email=current_user or None,
            graph=workspace_graph(),
            workspace_id=scope,
        )
        append_audit_event("workflow_imported", user_email=current_user, workflow_id=workflow["id"])
        return {"workflow": workflow}

    return router
