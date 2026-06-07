"""Agent Registry API router (v3.2.0).

Exposes :class:`~latticeai.core.agent_registry.AgentRegistry` so registration,
discovery, metadata, versioning, capabilities, and configuration are reachable
from the /app Agents view. Paths sit under ``/agents/api/registry`` alongside
the existing runtime endpoints. Full paths in decorators (no ``prefix=``).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from latticeai.core.agent_registry import AgentRegistry


class AgentRegisterRequest(BaseModel):
    name: str
    type: str = "custom"
    description: str = ""
    capabilities: List[str] = []
    config: Dict[str, Any] = {}
    version: str = "1.0.0"


class AgentConfigRequest(BaseModel):
    config: Dict[str, Any] = {}
    enabled: Optional[bool] = None


def create_agent_registry_router(
    *,
    registry: AgentRegistry,
    require_user: Callable[[Request], str],
    append_audit_event: Callable[..., None],
) -> APIRouter:
    router = APIRouter()

    @router.get("/agents/api/registry")
    async def list_registry(request: Request, type: Optional[str] = None):
        require_user(request)
        return registry.list(agent_type=type)

    @router.get("/agents/api/registry/capabilities")
    async def registry_capabilities(request: Request):
        require_user(request)
        return {"capabilities": registry.capabilities()}

    @router.get("/agents/api/registry/discover")
    async def registry_discover(request: Request, capability: str = ""):
        require_user(request)
        return {"capability": capability, "agents": registry.discover(capability)}

    @router.post("/agents/api/registry")
    async def register_agent(req: AgentRegisterRequest, request: Request):
        user = require_user(request)
        try:
            entry = registry.register(
                name=req.name,
                agent_type=req.type,
                description=req.description,
                capabilities=req.capabilities,
                config=req.config,
                version=req.version,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        append_audit_event("agent_register", user_email=user, agent_id=entry["id"], type=entry["type"])
        return {"agent": entry}

    @router.get("/agents/api/registry/{agent_id:path}")
    async def get_agent(agent_id: str, request: Request):
        require_user(request)
        agent = registry.get(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
        return {"agent": agent}

    @router.patch("/agents/api/registry/{agent_id:path}")
    async def update_agent(agent_id: str, req: AgentConfigRequest, request: Request):
        user = require_user(request)
        try:
            agent = registry.update_config(agent_id, req.config, enabled=req.enabled)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}") from exc
        append_audit_event("agent_config", user_email=user, agent_id=agent_id)
        return {"agent": agent}

    @router.delete("/agents/api/registry/{agent_id:path}")
    async def remove_agent(agent_id: str, request: Request):
        user = require_user(request)
        try:
            result = registry.remove(agent_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        append_audit_event("agent_remove", user_email=user, agent_id=agent_id)
        return result

    return router
