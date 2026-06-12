"""Workspace OS + Organization Workspace API router.

Extracted from ``server_app.py`` in v1.2.0. Routes are unchanged (`/workspace`
and `/workspace/*`); request/response shapes are preserved. Permission
guardrails for workspace-scoped reads/writes are centralized in
:class:`latticeai.services.workspace_service.WorkspaceService`.

The factory mirrors the existing ``create_auth_router`` / ``create_admin_router``
convention: server_app constructs the dependency callables/objects and passes
them in, so this module never imports the FastAPI app (no import cycle).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from latticeai.api.ui_redirects import app_redirect
from latticeai.services.app_context import AppContext


# ── Request models (workspace-only; moved verbatim from server_app) ──────────

class WorkspaceOnboardingStepRequest(BaseModel):
    step: str
    status: str = "complete"
    data: Dict = {}
    error: str = ""


class WorkspaceOnboardingCompleteRequest(BaseModel):
    data: Dict = {}


class WorkspaceSnapshotRequest(BaseModel):
    name: str = "Workspace snapshot"


class WorkspaceSnapshotCompareRequest(BaseModel):
    before_id: str
    after_id: str


class WorkspaceMemoryRequest(BaseModel):
    kind: str
    content: str
    tags: List[str] = []
    memory_id: Optional[str] = None
    metadata: Dict = {}


class WorkspaceAgentRunRequest(BaseModel):
    agent_id: str = "agent:executor"
    status: str = "ok"
    input: str = ""
    output: str = ""
    timeline: List[Dict] = []
    relationships: List[str] = []


class WorkspaceWorkflowRequest(BaseModel):
    name: str
    steps: List[Dict] = []
    metadata: Dict = {}


class WorkspaceWorkflowEventRequest(BaseModel):
    event_type: str
    payload: Dict = {}


class WorkspaceComputerMemoryRequest(BaseModel):
    enabled: bool = False
    consent: Dict = {}
    scopes: List[str] = []


class WorkspaceComputerActivityRequest(BaseModel):
    activity: Dict = {}


class WorkspaceSkillActionRequest(BaseModel):
    skill: str
    plugin: Optional[str] = None
    enabled: Optional[bool] = None
    version: Optional[str] = None
    metadata: Dict = {}


class WorkspaceVSCodeRequest(BaseModel):
    action: str
    file_path: Optional[str] = None
    language: Optional[str] = None
    content: str = ""
    selection: str = ""
    prompt: str = ""


class WorkspaceCreateRequest(BaseModel):
    name: str
    settings: Dict = {}


class WorkspaceUpdateRequest(BaseModel):
    name: Optional[str] = None
    settings: Optional[Dict] = None


class WorkspaceMemberRequest(BaseModel):
    user_id: str
    role: str = "member"


class WorkspaceMemberRoleRequest(BaseModel):
    role: str


class WorkspaceActivateRequest(BaseModel):
    workspace_id: str


def _workspace_scope_from_request(request: Request) -> Optional[str]:
    """Resolve a requested workspace id from header/query, or None.

    ``None`` lets the service fall back to the active workspace (Personal by
    default), preserving pre-1.1 behaviour for clients that send no header.
    """
    header = request.headers.get("X-Workspace-Id")
    if header and header.strip():
        return header.strip()
    query = request.query_params.get("workspace_id")
    return query.strip() if query and query.strip() else None


def create_workspace_router(context: AppContext) -> APIRouter:
    """Build the workspace/org router from the typed application context.

    Replaces the historical ~30-kwarg factory signature: ``context``
    (:class:`latticeai.services.app_context.AppContext`) carries the same
    dependencies as typed fields.
    """
    router = APIRouter()

    # Bind injected deps to the names the moved handler bodies expect.
    service = context.workspace_service
    require_user = context.require_user
    require_admin = context.require_admin
    get_current_user = context.get_current_user
    append_audit_event = context.append_audit_event
    get_history = context.get_history
    get_audit_log = context.get_audit_log
    load_users = context.load_users
    scan_environment = context.scan_environment
    local_sysinfo = context.local_sysinfo
    get_recommendations = context.get_recommendations
    install_skill = context.install_skill
    remove_skill_directory = context.remove_skill_directory
    redact_secret_text = context.redact_secret_text
    capability_registry = context.capability_registry

    svc = service
    WORKSPACE_OS = service.store
    _workspace_graph = context.workspace_graph
    _graph_stats_safe = context.graph_stats
    _workspace_models_payload = context.workspace_models
    _workspace_settings_payload = context.workspace_settings
    _require_graph = context.require_graph
    KNOWLEDGE_GRAPH = context.knowledge_graph
    LOCAL_KG_WATCHER = context.local_kg_watcher
    SKILLS_DIR = context.skills_dir
    LOCAL_MODEL = context.local_model
    PUBLIC_MODEL = context.public_model
    _fetch_skills_marketplace = context.fetch_skills_marketplace
    _workspace_scope = _workspace_scope_from_request

    def _gate_read(request: Request):
        try:
            return svc.resolve_read_scope(_workspace_scope(request), get_current_user(request))
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    def _gate_write(request: Request):
        try:
            return svc.resolve_write_scope(_workspace_scope(request), get_current_user(request))
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    def _load_snapshot_authorized(request: Request, snapshot_id: str) -> dict:
        """Fetch a snapshot and authorize against the RECORD'S own workspace.

        By-id access must not bypass workspace gating: a snapshot belonging to
        an organization workspace is readable only by its members. Snapshots
        predating workspace scoping carry no workspace_id and stay readable
        (legacy-global compatibility).
        """
        try:
            snapshot = WORKSPACE_OS.get_snapshot(snapshot_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Snapshot not found: {exc}") from exc
        try:
            svc.authorize_record_read(snapshot, get_current_user(request))
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return snapshot

    # ── Workspace UI pages ────────────────────────────────────────────────

    @router.get("/workspace")
    async def workspace_page(request: Request):
        require_user(request)
        return app_redirect("workspace-admin", request)

    @router.get("/onboarding")
    async def onboarding_page(request: Request):
        require_user(request)
        return app_redirect("workspace-admin", request)

    # ── Workspace OS summary / onboarding ─────────────────────────────────

    @router.get("/workspace/os")
    async def workspace_os_summary(request: Request):
        user = require_user(request)
        summary = svc.summary(user or None)
        summary["graph"] = _graph_stats_safe()
        summary["models"] = _workspace_models_payload()
        summary["edition"] = capability_registry.describe()
        return summary

    @router.get("/workspace/onboarding/status")
    async def workspace_onboarding_status(request: Request):
        require_user(request)
        return WORKSPACE_OS.onboarding_status(load_users(), _graph_stats_safe())

    @router.post("/workspace/onboarding/step")
    async def workspace_onboarding_step(req: WorkspaceOnboardingStepRequest, request: Request):
        current_user = require_user(request)
        return WORKSPACE_OS.update_onboarding_step(
            req.step,
            status=req.status,
            data=req.data,
            error=req.error,
            user_email=current_user or None,
        )

    @router.post("/workspace/onboarding/complete")
    async def workspace_onboarding_complete(req: WorkspaceOnboardingCompleteRequest, request: Request):
        current_user = require_user(request)
        append_audit_event("onboarding_complete", user_email=current_user, platform="AI Workspace OS")
        return WORKSPACE_OS.complete_onboarding(req.data, user_email=current_user or None)

    @router.get("/workspace/onboarding/hardware")
    async def workspace_onboarding_hardware(request: Request):
        require_user(request)
        env = await asyncio.to_thread(scan_environment)
        sysinfo = await local_sysinfo(request)
        payload = {"environment": env, "sysinfo": sysinfo, "scanned_at": datetime.now().isoformat()}
        WORKSPACE_OS.update_onboarding_step("hardware", status="complete", data=payload, user_email=get_current_user(request))
        return payload

    @router.get("/workspace/onboarding/model-recommendations")
    async def workspace_onboarding_model_recommendations(request: Request):
        require_user(request)
        env = await asyncio.to_thread(scan_environment)
        recommendations = get_recommendations(env)
        # Tri-state, family-grouped catalog (recommended / compatible /
        # not_recommended) for this machine, used by the onboarding model step.
        catalog = None
        try:
            from auto_setup import probe as auto_setup_probe
            from latticeai.services.model_recommendation import recommend_catalog
            profile = await asyncio.to_thread(lambda: auto_setup_probe().to_json())
            catalog = recommend_catalog(profile, engine="local_mlx")
        except Exception as exc:  # pragma: no cover - recommendation is best-effort
            logging.warning("model recommendation catalog failed: %s", exc)
        payload = {
            "environment": env,
            "recommendations": recommendations,
            "catalog": catalog,
            "default_local_model": LOCAL_MODEL,
            "default_public_model": PUBLIC_MODEL,
        }
        WORKSPACE_OS.update_onboarding_step("model_recommendation", status="complete", data=payload, user_email=get_current_user(request))
        return payload

    # ── Graph traces ──────────────────────────────────────────────────────

    @router.get("/workspace/traces")
    async def workspace_traces(request: Request, conversation_id: Optional[str] = None, limit: int = 50):
        require_user(request)
        scope = _gate_read(request)
        return WORKSPACE_OS.list_traces(conversation_id=conversation_id, limit=limit, workspace_id=scope)

    # ── Local indexing dashboard (graph is machine-global shared state) ───

    @router.get("/workspace/indexing")
    async def workspace_indexing_dashboard(request: Request):
        require_user(request)
        graph = _workspace_graph()
        watcher_status = LOCAL_KG_WATCHER.status() if LOCAL_KG_WATCHER else {"available": False, "active": {}}
        return WORKSPACE_OS.build_indexing_dashboard(graph, watcher_status)

    @router.post("/workspace/indexing/{source_id}/pause")
    async def workspace_indexing_pause(source_id: str, request: Request):
        require_user(request)
        _require_graph()
        return WORKSPACE_OS.pause_indexing(KNOWLEDGE_GRAPH, source_id, LOCAL_KG_WATCHER)

    @router.post("/workspace/indexing/{source_id}/resume")
    async def workspace_indexing_resume(source_id: str, request: Request):
        require_user(request)
        _require_graph()
        return WORKSPACE_OS.resume_indexing(KNOWLEDGE_GRAPH, source_id, LOCAL_KG_WATCHER)

    @router.post("/workspace/indexing/{source_id}/remove")
    async def workspace_indexing_remove(source_id: str, request: Request):
        require_user(request)
        _require_graph()
        return WORKSPACE_OS.remove_index_source(KNOWLEDGE_GRAPH, source_id, LOCAL_KG_WATCHER)

    # ── Snapshots / Time Machine / Knowledge Diff ─────────────────────────

    @router.get("/workspace/snapshots")
    async def workspace_snapshots(request: Request):
        require_user(request)
        scope = _gate_read(request)
        return WORKSPACE_OS.list_snapshots(workspace_id=scope)

    @router.post("/workspace/snapshots")
    async def workspace_snapshot_create(req: WorkspaceSnapshotRequest, request: Request):
        current_user = require_user(request)
        scope = _gate_write(request)
        result = WORKSPACE_OS.create_snapshot(
            name=req.name,
            graph=_workspace_graph(),
            history=get_history(),
            settings=_workspace_settings_payload(),
            models=_workspace_models_payload(),
            workspace_id=scope,
        )
        append_audit_event("workspace_snapshot", user_email=current_user, snapshot_id=result["snapshot"]["id"])
        return result

    @router.post("/workspace/snapshots/compare")
    async def workspace_snapshot_compare(req: WorkspaceSnapshotCompareRequest, request: Request):
        require_user(request)
        _load_snapshot_authorized(request, req.before_id)
        _load_snapshot_authorized(request, req.after_id)
        try:
            return WORKSPACE_OS.compare_snapshots(req.before_id, req.after_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Snapshot not found: {exc}") from exc

    @router.get("/workspace/snapshots/{snapshot_id}")
    async def workspace_snapshot_get(snapshot_id: str, request: Request):
        require_user(request)
        return _load_snapshot_authorized(request, snapshot_id)

    @router.get("/workspace/snapshots/{snapshot_id}/{area}")
    async def workspace_snapshot_area(snapshot_id: str, area: str, request: Request):
        require_user(request)
        _load_snapshot_authorized(request, snapshot_id)
        try:
            return WORKSPACE_OS.snapshot_view(snapshot_id, area)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Snapshot not found: {exc}") from exc

    @router.post("/workspace/snapshots/{snapshot_id}/export")
    async def workspace_snapshot_export(snapshot_id: str, request: Request):
        current_user = require_user(request)
        _load_snapshot_authorized(request, snapshot_id)
        try:
            result = WORKSPACE_OS.export_snapshot(snapshot_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Snapshot not found: {exc}") from exc
        append_audit_event("workspace_snapshot_export", user_email=current_user, snapshot_id=snapshot_id, path=result.get("export_path"))
        return result

    @router.post("/workspace/snapshots/{snapshot_id}/restore")
    async def workspace_snapshot_restore(snapshot_id: str, request: Request):
        current_user = require_user(request)
        snapshot = _load_snapshot_authorized(request, snapshot_id)
        scope = _gate_write(request)
        if snapshot.get("workspace_id") and snapshot.get("workspace_id") != scope:
            raise HTTPException(status_code=403, detail="snapshot belongs to a different workspace")
        try:
            result = WORKSPACE_OS.restore_snapshot(
                snapshot_id,
                graph=_workspace_graph(),
                workspace_id=scope,
                user_email=current_user or None,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Snapshot not found: {exc}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        append_audit_event("workspace_snapshot_restore", user_email=current_user, snapshot_id=snapshot_id, restore_id=result.get("restore", {}).get("id"))
        return result

    @router.get("/workspace/time-machine")
    async def workspace_time_machine(request: Request, limit: int = 100):
        require_user(request)
        scope = _gate_read(request)
        return WORKSPACE_OS.timeline(get_audit_log(), limit=limit, workspace_id=scope)

    @router.get("/workspace/time-machine/{snapshot_id}/{area}")
    async def workspace_time_machine_view(snapshot_id: str, area: str, request: Request):
        require_user(request)
        _load_snapshot_authorized(request, snapshot_id)
        try:
            return WORKSPACE_OS.snapshot_view(snapshot_id, area)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Snapshot not found: {exc}") from exc

    # ── Personal memory ───────────────────────────────────────────────────

    @router.get("/workspace/memories")
    async def workspace_memories(request: Request, kind: Optional[str] = None):
        current_user = require_user(request)
        scope = _gate_read(request)
        return WORKSPACE_OS.list_memories(user_email=current_user or None, kind=kind, workspace_id=scope)

    @router.get("/workspace/memories/search")
    async def workspace_memory_search(q: str, request: Request, limit: int = 20):
        current_user = require_user(request)
        scope = _gate_read(request)
        return WORKSPACE_OS.search_memories(q, user_email=current_user or None, limit=limit, workspace_id=scope)

    @router.post("/workspace/memories")
    async def workspace_memory_upsert(req: WorkspaceMemoryRequest, request: Request):
        current_user = require_user(request)
        scope = _gate_write(request)
        try:
            record = WORKSPACE_OS.upsert_memory(
                kind=req.kind,
                content=req.content,
                tags=req.tags,
                memory_id=req.memory_id,
                metadata=req.metadata,
                user_email=current_user or None,
                graph=_workspace_graph(),
                workspace_id=scope,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"memory": record}

    @router.delete("/workspace/memories/{memory_id}")
    async def workspace_memory_delete(memory_id: str, request: Request):
        require_user(request)
        try:
            record = WORKSPACE_OS.get_memory(memory_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Memory not found: {exc}") from exc
        try:
            svc.authorize_memory_delete(record, get_current_user(request))
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        try:
            return WORKSPACE_OS.delete_memory(memory_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Memory not found: {exc}") from exc

    # ── Agents & workflows ────────────────────────────────────────────────

    @router.get("/workspace/agents")
    async def workspace_agents(request: Request):
        require_user(request)
        scope = _gate_read(request)
        return WORKSPACE_OS.list_agents(workspace_id=scope)

    @router.post("/workspace/agents/runs")
    async def workspace_agent_run(req: WorkspaceAgentRunRequest, request: Request):
        current_user = require_user(request)
        scope = _gate_write(request)
        run = WORKSPACE_OS.record_agent_run(
            agent_id=req.agent_id,
            status=req.status,
            input_text=req.input,
            output_text=req.output,
            timeline=req.timeline,
            relationships=req.relationships,
            user_email=current_user or None,
            graph=_workspace_graph(),
            workspace_id=scope,
        )
        return {"run": run}

    @router.get("/workspace/relationships/{node_id:path}")
    async def workspace_relationships(node_id: str, request: Request, target_id: Optional[str] = None):
        require_user(request)
        _require_graph()
        return WORKSPACE_OS.relationship_explorer(KNOWLEDGE_GRAPH, node_id, target_id=target_id)

    # ── Local computer memory ─────────────────────────────────────────────

    @router.get("/workspace/computer-memory")
    async def workspace_computer_memory(request: Request):
        require_user(request)
        return WORKSPACE_OS.load_state().get("computer_memory")

    @router.post("/workspace/computer-memory")
    async def workspace_computer_memory_config(req: WorkspaceComputerMemoryRequest, request: Request):
        current_user = require_user(request)
        try:
            config = WORKSPACE_OS.configure_computer_memory(
                enabled=req.enabled,
                approved_by=current_user or None,
                consent=req.consent,
                scopes=req.scopes or None,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        append_audit_event("computer_memory_config", user_email=current_user, enabled=req.enabled)
        return {"computer_memory": config}

    @router.post("/workspace/computer-memory/activity")
    async def workspace_computer_memory_activity(req: WorkspaceComputerActivityRequest, request: Request):
        require_user(request)
        return WORKSPACE_OS.record_computer_activity(req.activity, graph=_workspace_graph())

    # ── Workflows ─────────────────────────────────────────────────────────

    @router.get("/workspace/workflows")
    async def workspace_workflows(request: Request, q: str = ""):
        require_user(request)
        scope = _gate_read(request)
        return WORKSPACE_OS.list_workflows(query=q, workspace_id=scope)

    @router.post("/workspace/workflows")
    async def workspace_workflow_create(req: WorkspaceWorkflowRequest, request: Request):
        current_user = require_user(request)
        scope = _gate_write(request)
        workflow = WORKSPACE_OS.create_workflow(
            name=req.name,
            steps=req.steps,
            metadata=req.metadata,
            user_email=current_user or None,
            graph=_workspace_graph(),
            workspace_id=scope,
        )
        return {"workflow": workflow}

    @router.post("/workspace/workflows/{workflow_id}/events")
    async def workspace_workflow_event(workflow_id: str, req: WorkspaceWorkflowEventRequest, request: Request):
        require_user(request)
        try:
            return {"workflow": WORKSPACE_OS.record_workflow_event(workflow_id, req.event_type, req.payload)}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Workflow not found: {exc}") from exc

    # ── Skills (installed skills are machine-global shared state) ─────────

    @router.get("/workspace/skills")
    async def workspace_skills(request: Request):
        require_user(request)
        marketplace = []
        try:
            marketplace = await _fetch_skills_marketplace()
        except Exception as exc:
            logging.warning("workspace skills marketplace unavailable: %s", exc)
        return WORKSPACE_OS.list_skill_registry(SKILLS_DIR, marketplace)

    @router.post("/workspace/skills/install")
    async def workspace_skill_install(req: WorkspaceSkillActionRequest, request: Request):
        admin_email, _ = require_admin(request)
        if req.plugin:
            result = await install_skill(req.plugin, req.skill)
        else:
            result = {"status": "recorded", "skill": req.skill}
        entry = WORKSPACE_OS.mark_skill_installed(req.skill, version=req.version or "local", metadata={"install_result": result, **req.metadata})
        append_audit_event("skill_install", user_email=admin_email, plugin=req.plugin, skill=req.skill, workspace_os=True)
        return {"skill": entry, "install": result}

    @router.post("/workspace/skills/uninstall")
    async def workspace_skill_uninstall(req: WorkspaceSkillActionRequest, request: Request):
        admin_email, _ = require_admin(request)
        removal = remove_skill_directory(SKILLS_DIR, req.skill)
        entry = WORKSPACE_OS.mark_skill_uninstalled(req.skill)
        append_audit_event("skill_uninstall", user_email=admin_email, skill=req.skill, workspace_os=True)
        return {"skill": entry, "removal": removal}

    @router.post("/workspace/skills/enable")
    async def workspace_skill_enable(req: WorkspaceSkillActionRequest, request: Request):
        require_user(request)
        return {"skill": WORKSPACE_OS.set_skill_enabled(req.skill, True)}

    @router.post("/workspace/skills/disable")
    async def workspace_skill_disable(req: WorkspaceSkillActionRequest, request: Request):
        require_user(request)
        return {"skill": WORKSPACE_OS.set_skill_enabled(req.skill, False)}

    @router.post("/workspace/skills/update")
    async def workspace_skill_update(req: WorkspaceSkillActionRequest, request: Request):
        admin_email, _ = require_admin(request)
        if req.plugin:
            result = await install_skill(req.plugin, req.skill)
        else:
            result = {"status": "version_recorded", "skill": req.skill}
        entry = WORKSPACE_OS.mark_skill_installed(req.skill, version=req.version or "latest", metadata={"update_result": result, **req.metadata})
        append_audit_event("skill_update", user_email=admin_email, plugin=req.plugin, skill=req.skill, workspace_os=True)
        return {"skill": entry, "update": result}

    # ── Audit timeline (admin only) ───────────────────────────────────────

    @router.get("/workspace/audit-timeline")
    async def workspace_audit_timeline(
        request: Request,
        user: Optional[str] = None,
        event_type: Optional[str] = None,
        model: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        limit: int = 100,
    ):
        require_admin(request)
        return WORKSPACE_OS.filter_audit_timeline(
            get_audit_log(),
            user=user,
            event_type=event_type,
            model=model,
            since=since,
            until=until,
            limit=limit,
        )

    # ── VS Code workflow bridge ───────────────────────────────────────────

    @router.post("/workspace/vscode/send")
    async def workspace_vscode_send(req: WorkspaceVSCodeRequest, request: Request):
        current_user = require_user(request)
        content = req.selection or req.content or req.prompt
        workflow = WORKSPACE_OS.create_workflow(
            name=f"VS Code: {req.action}",
            steps=[
                {"action": req.action, "file_path": req.file_path, "language": req.language},
                {"action": "send_to_lattice", "chars": len(content or "")},
            ],
            metadata={
                "file_path": req.file_path,
                "language": req.language,
                "content_preview": redact_secret_text(content or "")[:500],
            },
            user_email=current_user or None,
            graph=_workspace_graph(),
        )
        if _workspace_graph() is not None and content:
            try:
                _workspace_graph().ingest_event(
                    "VSCodeWorkflow",
                    req.action,
                    user_email=current_user or None,
                    source="vscode",
                    metadata={
                        "file_path": req.file_path,
                        "language": req.language,
                        "chars": len(content),
                        "workflow_id": workflow["id"],
                    },
                )
            except Exception as exc:
                logging.warning("vscode workflow graph ingest failed: %s", exc)
        return {"status": "ok", "workflow": workflow}

    # ── Organization Workspaces, membership, roles, and edition seam ──────

    @router.get("/workspace/registry")
    async def workspace_registry(request: Request):
        user = require_user(request)
        return svc.list_workspaces(user or None)

    @router.get("/workspace/editions")
    async def workspace_editions(request: Request):
        require_user(request)
        return capability_registry.describe()

    @router.post("/workspace/activate")
    async def workspace_activate(req: WorkspaceActivateRequest, request: Request):
        user = require_user(request)
        try:
            return svc.set_active_workspace(req.workspace_id, user or None)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Workspace not found: {exc}") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.post("/workspace/orgs")
    async def workspace_org_create(req: WorkspaceCreateRequest, request: Request):
        user = require_user(request)
        try:
            workspace = svc.create_organization_workspace(
                name=req.name,
                owner_user_id=user or None,
                settings=req.settings,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        append_audit_event("workspace_created", user_email=user, workspace_id=workspace["workspace_id"])
        return {"workspace": workspace}

    @router.get("/workspace/orgs/{workspace_id}")
    async def workspace_org_get(workspace_id: str, request: Request):
        user = require_user(request)
        try:
            return {"workspace": svc.get_workspace(workspace_id, user or None)}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Workspace not found: {exc}") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.get("/workspace/orgs/{workspace_id}/summary")
    async def workspace_org_summary(workspace_id: str, request: Request):
        user = require_user(request)
        try:
            return svc.workspace_summary(workspace_id, user or None)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Workspace not found: {exc}") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.patch("/workspace/orgs/{workspace_id}")
    async def workspace_org_update(workspace_id: str, req: WorkspaceUpdateRequest, request: Request):
        user = require_user(request)
        try:
            workspace = svc.update_workspace(workspace_id, name=req.name, settings=req.settings, actor=user or None)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Workspace not found: {exc}") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        append_audit_event("workspace_updated", user_email=user, workspace_id=workspace_id)
        return {"workspace": workspace}

    @router.post("/workspace/orgs/{workspace_id}/archive")
    async def workspace_org_archive(workspace_id: str, request: Request):
        user = require_user(request)
        try:
            workspace = svc.archive_workspace(workspace_id, actor=user or None)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Workspace not found: {exc}") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        append_audit_event("workspace_archived", user_email=user, workspace_id=workspace_id)
        return {"workspace": workspace}

    @router.post("/workspace/orgs/{workspace_id}/members")
    async def workspace_org_add_member(workspace_id: str, req: WorkspaceMemberRequest, request: Request):
        user = require_user(request)
        try:
            workspace = svc.add_member(workspace_id, user_id=req.user_id, role=req.role, actor=user or None)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Workspace not found: {exc}") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        append_audit_event("workspace_member_added", user_email=user, workspace_id=workspace_id, member=req.user_id, role=req.role)
        return {"workspace": workspace}

    @router.patch("/workspace/orgs/{workspace_id}/members/{user_id}")
    async def workspace_org_update_member(workspace_id: str, user_id: str, req: WorkspaceMemberRoleRequest, request: Request):
        user = require_user(request)
        try:
            workspace = svc.update_member_role(workspace_id, user_id=user_id, role=req.role, actor=user or None)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Not found: {exc}") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        append_audit_event("workspace_member_role_updated", user_email=user, workspace_id=workspace_id, member=user_id, role=req.role)
        return {"workspace": workspace}

    @router.delete("/workspace/orgs/{workspace_id}/members/{user_id}")
    async def workspace_org_remove_member(workspace_id: str, user_id: str, request: Request):
        user = require_user(request)
        try:
            workspace = svc.remove_member(workspace_id, user_id=user_id, actor=user or None)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Not found: {exc}") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        append_audit_event("workspace_member_removed", user_email=user, workspace_id=workspace_id, member=user_id)
        return {"workspace": workspace}

    return router
