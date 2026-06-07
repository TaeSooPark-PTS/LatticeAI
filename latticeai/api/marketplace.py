"""Marketplace foundation API (local templates only)."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel


class TemplateImportRequest(BaseModel):
    data: Dict[str, Any] = {}


class TemplateInstallRequest(BaseModel):
    data: Dict[str, Any] = {}


class TemplateCloneRequest(BaseModel):
    name: Optional[str] = None


def create_marketplace_router(
    *,
    store,
    catalog,
    require_user: Callable[[Request], str],
    gate_read: Callable[[Request], Optional[str]],
    gate_write: Callable[[Request], Optional[str]],
    workspace_graph: Callable[[], Any],
) -> APIRouter:
    from latticeai.core.marketplace import MarketplaceError

    router = APIRouter()

    @router.get("/marketplace/templates")
    async def list_templates(request: Request, kind: Optional[str] = None):
        require_user(request)
        gate_read(request)
        try:
            return catalog.list_templates(kind=kind)
        except MarketplaceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/marketplace/templates/{kind}/{template_id}/export")
    async def export_template(kind: str, template_id: str, request: Request):
        require_user(request)
        gate_read(request)
        try:
            return catalog.export_template(kind, template_id)
        except MarketplaceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/marketplace/templates/import")
    async def import_template(req: TemplateImportRequest, request: Request):
        require_user(request)
        gate_read(request)
        try:
            return {"template": catalog.import_template(req.data)}
        except MarketplaceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/marketplace/templates/install")
    async def install_template(req: TemplateInstallRequest, request: Request):
        user = require_user(request)
        scope = gate_write(request)
        try:
            installed = catalog.install_template(
                req.data,
                store=store,
                user_email=user or None,
                workspace_id=scope,
                graph=workspace_graph(),
            )
        except MarketplaceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"installed": installed}

    @router.post("/marketplace/templates/{kind}/{template_id}/clone")
    async def clone_template(kind: str, template_id: str, req: TemplateCloneRequest, request: Request):
        require_user(request)
        gate_read(request)
        try:
            return {"template": catalog.clone_template(kind, template_id, req.name)}
        except MarketplaceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/marketplace/templates/registry")
    async def template_registry(request: Request):
        require_user(request)
        gate_read(request)
        return {"registry": store.list_template_registry()}

    return router
