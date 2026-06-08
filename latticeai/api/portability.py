"""Knowledge Graph portability routes — local export / import / backup / restore.

Reads (export, status) require a signed-in user. Mutating operations (import,
backup, restore, file export) require admin because the graph is machine-global,
not workspace-scoped. Nothing here touches a cloud service.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel


class ImportRequest(BaseModel):
    artifact: dict
    mode: str = "merge"
    dry_run: bool = False


class BackupRequest(BaseModel):
    path: Optional[str] = None


class RestoreRequest(BaseModel):
    path: str
    verify: bool = True


def create_portability_router(
    *,
    service: Any,
    require_user: Callable[[Request], str],
    require_admin: Callable[[Request], Any],
) -> APIRouter:
    router = APIRouter()

    def _require_service():
        if service is None or not service.available():
            raise HTTPException(status_code=503, detail="Knowledge Graph is disabled.")

    @router.get("/api/knowledge-graph/portability")
    async def portability_status(request: Request):
        require_user(request)
        _require_service()
        return service.snapshot_metadata()

    @router.post("/api/knowledge-graph/export")
    async def export_graph(request: Request):
        """Logical JSON export of the whole graph (read-only)."""
        require_user(request)
        _require_service()
        return service.export()

    @router.post("/api/knowledge-graph/export-file")
    async def export_graph_file(request: Request):
        require_admin(request)
        _require_service()
        return service.export_to_file()

    @router.post("/api/knowledge-graph/import")
    async def import_graph(req: ImportRequest, request: Request):
        require_admin(request)
        _require_service()
        try:
            return service.import_data(req.artifact, mode=req.mode, dry_run=req.dry_run)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/api/knowledge-graph/backup")
    async def backup_graph(req: BackupRequest, request: Request):
        require_admin(request)
        _require_service()
        return service.backup(req.path)

    @router.post("/api/knowledge-graph/restore")
    async def restore_graph(req: RestoreRequest, request: Request):
        require_admin(request)
        _require_service()
        try:
            return service.restore(req.path, verify=req.verify)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    return router
