"""Knowledge Graph portability routes — local export / import / backup / restore.

Status reads require a signed-in user. Whole-graph exports, provenance, and all
mutating operations require admin because the graph is machine-global, not
workspace-scoped. Nothing here touches a cloud service.

The ``/share`` family (v11.1.0) adds the selective Brain Network prototype:
export a *chosen* subgraph signed by this device, and receive one as review
proposals rather than a merge. It is gated on ``LATTICEAI_BRAIN_NETWORK`` and
answers 403 with the reason while that flag is off — except ``GET
/api/knowledge-graph/share``, which reports ``enabled: false`` so a UI can say
why the feature is absent instead of guessing.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from latticeai.core.messages import http_error, resolve_language, translate
from latticeai.services.review_queue import InvalidReviewTransition


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


class SubgraphSelection(BaseModel):
    """What to share. At least one selector is required by the service."""

    node_ids: List[str] = []
    node_types: List[str] = []
    source_types: List[str] = []
    workspace_id: Optional[str] = None
    include_legacy_global: bool = False
    include_neighbors: bool = False
    redact_provenance: bool = True


class SubgraphArchiveRequest(SubgraphSelection):
    path: Optional[str] = None
    passphrase: str


class SubgraphImportRequest(BaseModel):
    """Either an inline ``artifact`` or a ``path`` (+ passphrase) on disk."""

    artifact: Optional[Dict[str, Any]] = None
    path: Optional[str] = None
    passphrase: Optional[str] = None
    workspace_id: Optional[str] = None
    dry_run: bool = False


class SubgraphAcceptRequest(BaseModel):
    workspace_id: Optional[str] = None


def create_portability_router(
    *,
    service: Any,
    require_user: Callable[[Request], str],
    require_admin: Callable[[Request], Any],
    review_queue: Any = None,
) -> APIRouter:
    router = APIRouter()

    def _require_service(request: Request):
        if service is None or not service.available():
            raise http_error(503, "common.graph_disabled", resolve_language(request))

    def _require_review_queue(request: Request):
        if review_queue is None:
            raise http_error(503, "portability.review_queue_unavailable", resolve_language(request))

    @router.get("/api/knowledge-graph/portability")
    async def portability_status(request: Request):
        require_user(request)
        _require_service(request)
        return service.snapshot_metadata()

    @router.get("/api/brain/storage")
    async def brain_storage_status(request: Request):
        require_user(request)
        _require_service(request)
        return service.storage_status()

    @router.get("/api/knowledge-graph/backup-health")
    async def backup_health(request: Request):
        require_user(request)
        _require_service(request)
        return service.backup_health()

    @router.get("/api/knowledge-graph/provenance")
    async def recent_provenance(request: Request, limit: int = 50, source_type: Optional[str] = None):
        """Recent ingestions (provenance trail) for the ingestion-sources UI."""
        require_admin(request)
        _require_service(request)
        return service.recent_ingestions(limit=limit, source_type=source_type)

    @router.post("/api/knowledge-graph/export")
    async def export_graph(request: Request):
        """Logical JSON export of the whole graph (read-only)."""
        require_admin(request)
        _require_service(request)
        return service.export()

    @router.post("/api/knowledge-graph/export-file")
    async def export_graph_file(request: Request):
        require_admin(request)
        _require_service(request)
        return service.export_to_file()

    @router.post("/api/knowledge-graph/import")
    async def import_graph(req: ImportRequest, request: Request):
        require_admin(request)
        _require_service(request)
        try:
            return service.import_data(req.artifact, mode=req.mode, dry_run=req.dry_run)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/api/knowledge-graph/backup")
    async def backup_graph(req: BackupRequest, request: Request):
        require_admin(request)
        _require_service(request)
        return service.backup(req.path)

    @router.post("/api/knowledge-graph/restore")
    async def restore_graph(req: RestoreRequest, request: Request):
        require_admin(request)
        _require_service(request)
        try:
            return service.restore(req.path, verify=req.verify, dry_run=req.dry_run, confirm=req.confirm)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/api/knowledge-graph/archive")
    async def encrypted_archive(req: EncryptedArchiveRequest, request: Request):
        require_admin(request)
        _require_service(request)
        try:
            return service.encrypted_archive(req.path, passphrase=req.passphrase)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/api/knowledge-graph/archive/inspect")
    async def inspect_encrypted_archive(req: EncryptedInspectRequest, request: Request):
        require_admin(request)
        _require_service(request)
        try:
            return service.inspect_encrypted_archive(req.path, passphrase=req.passphrase)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/api/knowledge-graph/archive/verify")
    async def verify_encrypted_archive(req: EncryptedVerifyRequest, request: Request):
        require_admin(request)
        _require_service(request)
        result = service.verify_encrypted_archive(req.path, passphrase=req.passphrase)
        if not result.get("ok"):
            raise HTTPException(
            status_code=400,
            detail="; ".join(
                result.get("errors")
                or [translate("portability.verification_failed", resolve_language(request))]
            ),
        )
        return result

    @router.post("/api/knowledge-graph/archive/import")
    async def import_encrypted_archive(req: EncryptedRestoreRequest, request: Request):
        require_admin(request)
        _require_service(request)
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
        _require_service(request)
        try:
            return service.restore_encrypted_archive(
                req.path,
                passphrase=req.passphrase,
                dry_run=req.dry_run,
                confirm=req.confirm,
            )
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    # ── selective subgraph share (opt-in; 403 with the reason while off) ─────
    @router.get("/api/knowledge-graph/share")
    async def share_status(request: Request):
        """Reports whether sharing is on, and names the flag when it is not."""
        require_user(request)
        status = service.share_status()
        if not status.get("enabled"):
            status["detail"] = translate(
                "portability.brain_network_disabled", resolve_language(request)
            )
        return status

    @router.post("/api/knowledge-graph/share/export")
    async def share_export(req: SubgraphSelection, request: Request):
        """Signed JSON bundle of the selected subgraph (no file written)."""
        require_admin(request)
        _require_service(request)
        try:
            return service.export_subgraph(**req.model_dump())
        except PermissionError:
            raise http_error(403, "portability.brain_network_disabled", resolve_language(request))
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/api/knowledge-graph/share/archive")
    async def share_archive(req: SubgraphArchiveRequest, request: Request):
        """Same bundle, written as an encrypted ``.latticebrain`` file."""
        require_admin(request)
        _require_service(request)
        body = req.model_dump()
        path = body.pop("path")
        passphrase = body.pop("passphrase")
        try:
            return service.export_subgraph_archive(path, passphrase=passphrase, **body)
        except PermissionError:
            raise http_error(403, "portability.brain_network_disabled", resolve_language(request))
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/api/knowledge-graph/share/import")
    async def share_import(req: SubgraphImportRequest, request: Request):
        """Receive a bundle as review proposals. Never merges on its own."""
        require_admin(request)
        _require_service(request)
        user = require_user(request)
        _require_review_queue(request)
        try:
            artifact = req.artifact
            if artifact is None:
                if not req.path or not req.passphrase:
                    raise ValueError("Provide an artifact, or a path with its passphrase.")
                artifact = service.read_subgraph_archive(req.path, passphrase=req.passphrase)
            return service.import_subgraph_proposals(
                artifact,
                review_sink=review_queue,
                workspace_id=req.workspace_id,
                user_email=user or None,
                dry_run=req.dry_run,
            )
        except PermissionError:
            raise http_error(403, "portability.brain_network_disabled", resolve_language(request))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/api/knowledge-graph/share/proposals/{item_id}/accept")
    async def share_accept(item_id: str, req: SubgraphAcceptRequest, request: Request):
        """Merge one reviewed proposal into this Brain and approve the item."""
        require_admin(request)
        _require_service(request)
        _require_review_queue(request)
        try:
            return service.accept_subgraph_proposal(
                item_id, review_sink=review_queue, workspace_id=req.workspace_id,
            )
        except PermissionError:
            raise http_error(403, "portability.brain_network_disabled", resolve_language(request))
        except FileNotFoundError:
            raise http_error(404, "review.item_not_found", resolve_language(request))
        except InvalidReviewTransition as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/api/brain/storage/postgres/docker")
    async def setup_postgres_docker(req: DockerPostgresRequest, request: Request):
        require_admin(request)
        _require_service(request)
        return service.postgres_docker_setup(
            consent=req.consent,
            dry_run=req.dry_run,
            port=req.port,
        )

    @router.post("/api/brain/storage/migrate-postgres")
    async def migrate_sqlite_to_postgres(req: SQLiteToPostgresRequest, request: Request):
        require_admin(request)
        _require_service(request)
        try:
            return service.migrate_sqlite_to_postgres(
                dsn=req.dsn,
                schema=req.schema_name,
                dry_run=req.dry_run,
            )
        except (ValueError, FileNotFoundError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    return router
