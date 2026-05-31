"""Local file access and local knowledge graph routes."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from knowledge_graph_api import create_knowledge_graph_router
from local_knowledge_api import create_local_knowledge_router
from tools import local_list, local_read, local_write


class LocalAccessRequest(BaseModel):
    path: str
    approved: bool = False
    approval_token: Optional[str] = None


class LocalWriteRequest(BaseModel):
    path: str
    content: str
    approved: bool = False
    approval_token: Optional[str] = None


def create_local_files_router(
    *,
    require_user,
    tool_response,
    permission_gateway,
    knowledge_graph,
    require_graph,
    static_dir: Path,
    local_kg_watcher,
) -> APIRouter:
    router = APIRouter()

    @router.post("/local/list")
    async def local_list_endpoint(req: LocalAccessRequest, request: Request):
        current_user = permission_gateway.require_local_user(request)
        if not req.approved:
            return permission_gateway.local_permission_response(req.path, "list", current_user)
        permission_gateway.require_local_approval(
            token=req.approval_token,
            path=req.path,
            action="list",
            user_email=current_user,
        )
        return tool_response(local_list, req.path)

    @router.get("/local/list")
    async def local_list_get_endpoint(path: str, request: Request):
        current_user = permission_gateway.require_local_user(request)
        return permission_gateway.local_permission_response(path, "list", current_user)

    @router.post("/local/read")
    async def local_read_endpoint(req: LocalAccessRequest, request: Request):
        current_user = permission_gateway.require_local_user(request)
        if not req.approved:
            return permission_gateway.local_permission_response(req.path, "read", current_user)
        permission_gateway.require_local_approval(
            token=req.approval_token,
            path=req.path,
            action="read",
            user_email=current_user,
        )
        return tool_response(local_read, req.path)

    @router.get("/local/serve")
    async def local_serve_file(path: str, request: Request, approval_token: Optional[str] = None):
        current_user = permission_gateway.require_local_user(request)
        permission_gateway.require_local_approval(
            token=approval_token,
            path=path,
            action="read",
            user_email=current_user,
        )
        target = Path(path).expanduser().resolve()
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(str(target))

    @router.post("/local/write")
    async def local_write_endpoint(req: LocalWriteRequest, request: Request):
        current_user = permission_gateway.require_local_user(request)
        if not req.approved:
            return permission_gateway.local_permission_response(req.path, "write", current_user, req.content)
        permission_gateway.require_local_approval(
            token=req.approval_token,
            path=req.path,
            action="write",
            user_email=current_user,
            content=req.content,
        )
        return tool_response(local_write, req.path, req.content)

    router.include_router(
        create_knowledge_graph_router(
            get_graph=lambda: knowledge_graph,
            require_graph=require_graph,
            require_user=require_user,
            static_dir=static_dir,
        )
    )

    router.include_router(
        create_local_knowledge_router(
            get_graph=lambda: knowledge_graph,
            require_graph=require_graph,
            require_user=require_user,
            require_local_user=permission_gateway.require_local_user,
            local_permission_response=permission_gateway.local_permission_response,
            require_local_approval=permission_gateway.require_local_approval,
            watcher=local_kg_watcher,
        )
    )

    return router


__all__ = ["LocalAccessRequest", "LocalWriteRequest", "create_local_files_router"]
