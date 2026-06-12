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
    dry_run: bool = False
    confirm: bool = False


class EncryptedArchiveRequest(BaseModel):
    path: Optional[str] = None
    passphrase: str


class EncryptedRestoreRequest(BaseModel):
    path: str
    passphrase: str
    dry_run: bool = False
    confirm: bool = False


class EncryptedInspectRequest(BaseModel):
    path: str
    passphrase: Optional[str] = None


class EncryptedVerifyRequest(BaseModel):
    path: str
    passphrase: str


class DockerPostgresRequest(BaseModel):
    consent: bool = False
    dry_run: bool = False
    port: int = 5432


class SQLiteToPostgresRequest(BaseModel):
    dsn: str
    schema_name: str = "lattice_brain"
    dry_run: bool = True


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

    @router.get("/api/brain/storage")
    async def brain_storage_status(request: Request):
        require_user(request)
        _require_service()
        return service.storage_status()

    @router.get("/api/knowledge-graph/backup-health")
    async def backup_health(request: Request):
        require_user(request)
        _require_service()
        return service.backup_health()

    @router.get("/api/knowledge-graph/provenance")
    async def recent_provenance(request: Request, limit: int = 50, source_type: Optional[str] = None):
        """Recent ingestions (provenance trail) for the ingestion-sources UI."""
        require_user(request)
        _require_service()
        return service.recent_ingestions(limit=limit, source_type=source_type)

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
            return service.restore(req.path, verify=req.verify, dry_run=req.dry_run, confirm=req.confirm)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/api/knowledge-graph/archive")
    async def encrypted_archive(req: EncryptedArchiveRequest, request: Request):
        require_admin(request)
        _require_service()
        try:
            return service.encrypted_archive(req.path, passphrase=req.passphrase)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/api/knowledge-graph/archive/inspect")
    async def inspect_encrypted_archive(req: EncryptedInspectRequest, request: Request):
        require_admin(request)
        _require_service()
        try:
            return service.inspect_encrypted_archive(req.path, passphrase=req.passphrase)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/api/knowledge-graph/archive/verify")
    async def verify_encrypted_archive(req: EncryptedVerifyRequest, request: Request):
        require_admin(request)
        _require_service()
        result = service.verify_encrypted_archive(req.path, passphrase=req.passphrase)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail="; ".join(result.get("errors") or ["Archive verification failed."]))
        return result

    @router.post("/api/knowledge-graph/archive/import")
    async def import_encrypted_archive(req: EncryptedRestoreRequest, request: Request):
        require_admin(request)
        _require_service()
        try:
            return service.import_encrypted_archive(
                req.path,
                passphrase=req.passphrase,
                dry_run=req.dry_run,
                confirm=req.confirm,
            )
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/api/knowledge-graph/archive/restore")
    async def restore_encrypted_archive(req: EncryptedRestoreRequest, request: Request):
        require_admin(request)
        _require_service()
        try:
            return service.restore_encrypted_archive(
                req.path,
                passphrase=req.passphrase,
                dry_run=req.dry_run,
                confirm=req.confirm,
            )
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/api/brain/storage/postgres/docker")
    async def setup_postgres_docker(req: DockerPostgresRequest, request: Request):
        require_admin(request)
        _require_service()
        return service.postgres_docker_setup(
            consent=req.consent,
            dry_run=req.dry_run,
            port=req.port,
        )

    @router.post("/api/brain/storage/migrate-postgres")
    async def migrate_sqlite_to_postgres(req: SQLiteToPostgresRequest, request: Request):
        require_admin(request)
        _require_service()
        try:
            return service.migrate_sqlite_to_postgres(
                dsn=req.dsn,
                schema=req.schema_name,
                dry_run=req.dry_run,
            )
        except (ValueError, FileNotFoundError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    return router
