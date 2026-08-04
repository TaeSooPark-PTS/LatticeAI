"""Project session router — the multi-turn project loop (v9.9.6).

CRUD over :class:`~latticeai.core.project_sessions.ProjectSessionStore`. A
project session carries what a single agent run cannot: the files the project
already produced, what is still open, and the last honest verification result.
Runs fold themselves in through the agent HTTP layer; this surface is what the
user drives.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from latticeai.core.messages import http_error, resolve_language


class CreateProjectRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    goal: str = ""


class UpdateProjectRequest(BaseModel):
    title: Optional[str] = None
    goal: Optional[str] = None
    status: Optional[str] = None


class TodosRequest(BaseModel):
    todos: List[Any] = Field(default_factory=list)


def create_project_sessions_router(
    *,
    store: Any,
    require_user: Callable[[Request], Any],
    gate_read: Optional[Callable[[Request], Optional[str]]] = None,
    gate_write: Optional[Callable[[Request], Optional[str]]] = None,
) -> APIRouter:
    router = APIRouter()

    def _scope(request: Request, write: bool = False):
        gate = gate_write if write else gate_read
        return gate(request) if gate is not None else None

    @router.get("/api/projects")
    async def list_projects(request: Request, status: str = "active"):
        user = require_user(request)
        return store.list(
            user_email=user, workspace_id=_scope(request), status=status
        )

    @router.post("/api/projects")
    async def create_project(req: CreateProjectRequest, request: Request):
        user = require_user(request)
        return store.create(
            title=req.title,
            goal=req.goal,
            user_email=user,
            workspace_id=_scope(request, write=True),
        )

    @router.get("/api/projects/{session_id}")
    async def get_project(session_id: str, request: Request):
        user = require_user(request)
        record = store.get(session_id, user_email=user, workspace_id=_scope(request))
        if record is None:
            raise http_error(404, "project.not_found", resolve_language(request))
        return record

    @router.patch("/api/projects/{session_id}")
    async def update_project(session_id: str, req: UpdateProjectRequest, request: Request):
        user = require_user(request)
        record = store.update(
            session_id,
            title=req.title,
            goal=req.goal,
            status=req.status,
            user_email=user,
            workspace_id=_scope(request, write=True),
        )
        if record is None:
            raise http_error(404, "project.not_found", resolve_language(request))
        return record

    @router.put("/api/projects/{session_id}/todos")
    async def set_todos(session_id: str, req: TodosRequest, request: Request):
        user = require_user(request)
        record = store.set_todos(
            session_id,
            req.todos,
            user_email=user,
            workspace_id=_scope(request, write=True),
        )
        if record is None:
            raise http_error(404, "project.not_found", resolve_language(request))
        return record

    @router.delete("/api/projects/{session_id}")
    async def delete_project(session_id: str, request: Request):
        user = require_user(request)
        removed = store.delete(
            session_id, user_email=user, workspace_id=_scope(request, write=True)
        )
        if not removed:
            raise http_error(404, "project.not_found", resolve_language(request))
        return {"status": "deleted", "id": session_id}

    return router


__all__ = ["create_project_sessions_router"]
