"""Multi-Agent Runtime 2.0 API router (v2.0).

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


class AgentRunRequest(BaseModel):
    goal: str
    roles: List[str] = []
    inputs: Dict[str, Any] = {}
    max_retries: int = 2


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
) -> APIRouter:
    from latticeai.core.multi_agent import AGENT_ROLES, ROLE_AGENT_IDS

    router = APIRouter()

    @router.get("/agents")
    async def agents_page(request: Request):
        require_user(request)
        if ui_file_response is None or static_dir is None:
            raise HTTPException(status_code=404, detail="Multi-Agent UI not available.")
        page = static_dir / "agents.html"
        if not page.exists():
            raise HTTPException(status_code=404, detail="Multi-Agent UI not found.")
        return ui_file_response(page)

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
        return store.list_agents(workspace_id=scope)

    @router.post("/agents/api/run")
    async def agent_run(req: AgentRunRequest, request: Request):
        current_user = require_user(request)
        scope = gate_write(request)
        if not str(req.goal or "").strip():
            raise HTTPException(status_code=400, detail="goal is required")
        orchestrator = orchestrator_factory(current_user or None, scope)
        result = orchestrator.run(
            req.goal,
            user_email=current_user or None,
            workspace_id=scope,
            inputs=req.inputs,
            roles=req.roles or None,
            max_retries=max(0, min(int(req.max_retries or 0), 5)),
        )
        run = store.record_agent_run(
            agent_id=result.agent_id,
            status=result.status,
            input_text=req.goal,
            output_text=result.output,
            timeline=result.timeline,
            relationships=[ROLE_AGENT_IDS.get(r, f"agent:{r}") for r in result.roles_run],
            user_email=current_user or None,
            graph=workspace_graph(),
            workspace_id=scope,
        )
        append_audit_event("multi_agent_run", user_email=current_user, agent_id=result.agent_id, status=result.status, retries=result.retries)
        return {"run": run, "result": result.as_dict()}

    return router
