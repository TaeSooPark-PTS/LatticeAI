"""Multi-Agent Runtime API router (v2).

Exposes the built-in agent roles and an orchestrated run endpoint that connects
to Workspace, Memory, Knowledge Graph, Workflow runs, and the Timeline. Paths
are namespaced under ``/agents`` (plural) so they never collide with the
existing single-agent ``/agent`` endpoints.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from latticeai.api.ui_redirects import app_redirect


_CORE_EXECUTION_ROLES = ["planner", "executor", "reviewer"]
_MEMORY_GROUNDED_ROLES = ["researcher", *_CORE_EXECUTION_ROLES]


def _memory_grounded_roles(roles: List[str]) -> Optional[List[str]]:
    """Ground standard user-initiated agent runs in Brain recall first.

    Explicit specialist/custom pipelines keep their requested shape. The
    desktop Brain and Work surfaces both send the historical three-role core
    sequence, so normalizing it at the API boundary upgrades existing clients
    without a frontend-only compatibility branch.
    """
    requested = list(roles or _CORE_EXECUTION_ROLES)
    if requested == _CORE_EXECUTION_ROLES:
        return list(_MEMORY_GROUNDED_ROLES)
    return requested or None


class AgentRunRequest(BaseModel):
    goal: str
    roles: List[str] = []
    inputs: Dict[str, Any] = {}
    max_retries: int = 2


class MemorySnapshotRequest(BaseModel):
    label: str = "agent memory snapshot"
    reason: str = ""
    memory_ids: List[str] = []


def create_agents_router(
    *,
    store,
    orchestrator_factory: Callable[[Optional[str], Optional[str]], Any],
    require_user: Callable[[Request], str],
    get_current_user: Callable[[Request], Optional[str]],
    gate_read: Callable[[Request], Optional[str]],
    gate_write: Callable[[Request], Optional[str]],
    workspace_graph: Callable[[], Any],
    append_audit_event: Callable[..., None],
    ui_file_response: Optional[Callable[[Path], Any]] = None,
    static_dir: Optional[Path] = None,
    agent_runtime: Any = None,
    run_executor: Any = None,
) -> APIRouter:
    from lattice_brain.runtime.multi_agent import AGENT_ROLES, ROLE_AGENT_IDS
    from lattice_brain.runtime.agent_runtime import AgentRuntime, AgentRuntimeUnavailable

    # Single AgentRuntime boundary: the router (and via it, the frontend) talks
    # to this façade instead of reaching into the orchestrator/store directly.
    runtime = agent_runtime or AgentRuntime(
        store=store,
        orchestrator_factory=orchestrator_factory,
        workspace_graph=workspace_graph,
        append_audit_event=append_audit_event,
    )

    router = APIRouter()

    # ── AgentRuntime boundary endpoints ───────────────────────────────────
    @router.get("/agents/api/runtime/status")
    async def agent_runtime_status(request: Request):
        require_user(request)
        scope = gate_read(request)
        return runtime.status(scope=scope)

    @router.get("/agents/api/runtime/health")
    async def agent_runtime_health(request: Request):
        require_user(request)
        return runtime.health()

    @router.get("/agents/api/runtime/config")
    async def agent_runtime_config(request: Request):
        require_user(request)
        return runtime.config()

    @router.get("/agents/api/runs/{run_id}/events")
    async def agent_run_events(run_id: str, request: Request):
        require_user(request)
        scope = gate_read(request)
        try:
            return runtime.events(run_id, scope=scope)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Agent run not found: {run_id}") from exc

    @router.post("/agents/api/runs/{run_id}/stop")
    async def agent_run_stop(run_id: str, request: Request):
        require_user(request)
        scope = gate_write(request)
        return runtime.stop(run_id, scope=scope)

    @router.get("/agents")
    async def agents_page(request: Request):
        require_user(request)
        return app_redirect("agents", request)

    @router.get("/agents/api/roles")
    async def agent_roles(request: Request):
        require_user(request)
        return {
            "roles": [
                {"role": role, "agent_id": ROLE_AGENT_IDS.get(role, f"agent:{role}")}
                for role in AGENT_ROLES
            ],
            "default_pipeline": ["planner", "executor", "reviewer"],
        }

    @router.get("/agents/api/runs")
    async def agent_runs(request: Request):
        require_user(request)
        scope = gate_read(request)
        return runtime.list_runs(scope=scope)

    @router.get("/agents/api/handoffs")
    async def agent_handoffs(request: Request, run_id: str = ""):
        require_user(request)
        scope = gate_read(request)
        return store.list_handoffs(workspace_id=scope, run_id=run_id or None)

    @router.get("/agents/api/runs/{run_id}")
    async def agent_run_detail(run_id: str, request: Request):
        require_user(request)
        scope = gate_read(request)
        try:
            return runtime.get_run(run_id, scope=scope)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Agent run not found: {run_id}") from exc

    @router.get("/agents/api/runs/{run_id}/replay")
    async def agent_run_replay(run_id: str, request: Request):
        require_user(request)
        scope = gate_read(request)
        try:
            return runtime.replay(run_id, scope=scope)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Agent run not found: {run_id}") from exc

    @router.get("/agents/api/memory/snapshots")
    async def agent_memory_snapshots(request: Request, limit: int = 50):
        require_user(request)
        scope = gate_read(request)
        return store.list_memory_snapshots(workspace_id=scope, limit=limit)

    @router.post("/agents/api/memory/snapshots")
    async def agent_memory_snapshot(req: MemorySnapshotRequest, request: Request):
        current_user = require_user(request)
        scope = gate_write(request)
        snapshot = store.create_memory_snapshot(
            label=req.label,
            reason=req.reason,
            memory_ids=req.memory_ids or None,
            user_email=current_user or None,
            workspace_id=scope,
        )
        return {"snapshot": snapshot}

    @router.post("/agents/api/run")
    async def agent_run(req: AgentRunRequest, request: Request):
        current_user = require_user(request)
        scope = gate_write(request)
        grounded_roles = _memory_grounded_roles(req.roles)
        try:
            if run_executor is not None:
                return await run_executor.start_agent(
                    req.goal,
                    user_email=current_user or None,
                    scope=scope,
                    roles=grounded_roles,
                    inputs=req.inputs,
                    max_retries=req.max_retries,
                )
            # Worker thread: an LLM-backed run blocks on model generation and
            # must not stall the event loop (the sync model bridge also
            # requires a loop-free thread).
            import asyncio

            return await asyncio.to_thread(
                runtime.start,
                req.goal,
                user_email=current_user or None,
                scope=scope,
                roles=grounded_roles,
                inputs=req.inputs,
                max_retries=req.max_retries,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except AgentRuntimeUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PermissionError as exc:
            # A pre_run hook gated this run (e.g. a policy/permission hook).
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.post("/agents/api/run/preview")
    async def agent_run_preview(req: AgentRunRequest, request: Request):
        require_user(request)
        scope = gate_read(request)
        return runtime.preview(
            req.goal,
            scope=scope,
            roles=_memory_grounded_roles(req.roles),
            inputs=req.inputs,
            max_retries=req.max_retries,
        )

    return router
