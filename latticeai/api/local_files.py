"""Local file access and local knowledge graph routes."""

from __future__ import annotations

import asyncio
import os
import platform
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from latticeai.api.knowledge_graph import create_knowledge_graph_router
from latticeai.core.messages import (
    DEFAULT_LANGUAGE,
    http_error,
    resolve_language,
    translate,
)
from latticeai.services.local_knowledge import create_local_knowledge_router
from latticeai.tools import local_list, local_read, local_write

try:
    from latticeai import __version__ as _LATTICE_VERSION
except Exception:  # pragma: no cover - defensive
    _LATTICE_VERSION = "unknown"


class LocalAccessRequest(BaseModel):
    path: str
    approved: bool = False
    approval_token: Optional[str] = None


class LocalWriteRequest(BaseModel):
    path: str
    content: str
    approved: bool = False
    approval_token: Optional[str] = None


class FolderIngestRequest(BaseModel):
    path: str
    recursive: bool = True
    background: bool = False
    workspace_id: Optional[str] = None
    # Local filesystem reads follow the standard approval dance (same as
    # /local/read and /knowledge-graph/local/index): the first call returns a
    # permission_required payload with an approval token.
    approved: bool = False
    approval_token: Optional[str] = None


class FolderWatchEnableRequest(BaseModel):
    path: str
    recursive: bool = True
    workspace_id: Optional[str] = None
    # Watching continuously reads local disk → same approval dance as
    # /api/ingestion/folder. This is the explicit opt-in the review requires.
    approved: bool = False
    approval_token: Optional[str] = None


class ObsidianSyncRequest(BaseModel):
    """One-shot sync of an *external* Obsidian vault (v11.1.0).

    ``path`` is the user's own vault folder, so it takes the same local-read
    approval dance as ``/api/ingestion/folder``. ``dry_run`` reports the note,
    link, and tag counts a real run would touch without writing anything.
    """

    path: str
    workspace_id: Optional[str] = None
    dry_run: bool = False
    approved: bool = False
    approval_token: Optional[str] = None


def create_local_files_router(
    *,
    require_user,
    require_admin=None,
    tool_response,
    permission_gateway,
    knowledge_graph,
    require_graph,
    static_dir: Path,
    local_kg_watcher,
    ingestion_pipeline=None,
    hooks=None,
    data_dir: Optional[Path] = None,
    allowed_workspaces_for=None,
    workspace_service=None,
    folder_watch=None,
) -> APIRouter:
    router = APIRouter()

    # ── opt-in folder watch service (backlog #8) ──────────────────────────────
    # Constructed here (not injected) unless a test provides one; restore()
    # only resumes watches persisted with the explicit opt-in — a fresh
    # data_dir never starts a polling thread.
    if folder_watch is None and ingestion_pipeline is not None and data_dir is not None:
        try:
            from latticeai.services.folder_watch import FolderWatchService

            folder_watch = FolderWatchService(
                pipeline=ingestion_pipeline,
                config_path=Path(data_dir) / "folder_watch.json",
            )
            folder_watch.restore()
        except Exception:  # noqa: BLE001 — watch mode is optional, never blocks routes
            folder_watch = None

    @router.get("/api/local-agent/status")
    async def local_agent_status(request: Request):
        """Real on-device runtime status — the 'Local Agent' is the Lattice
        server running on this machine. Every field below is *probed*, not
        hardcoded: filesystem access is a real write/read/delete; the graph and
        watcher are reached live; the mode/handshake are derived from those
        probes. A failing subsystem yields ``degraded``/``error`` honestly.

        Allowed modes: offline · starting · online · degraded · error.
        """
        require_user(request)
        started = time.perf_counter()
        errors = []

        # ── filesystem capability: a real write → read → delete probe ─────────
        fs_ok = False
        probe_dir = Path(data_dir) if data_dir else Path(static_dir).parent
        try:
            probe_dir.mkdir(parents=True, exist_ok=True)
            probe = probe_dir / f".local_agent_probe_{uuid.uuid4().hex}"
            token = uuid.uuid4().hex
            probe.write_text(token, encoding="utf-8")
            fs_ok = probe.read_text(encoding="utf-8") == token
            probe.unlink()
        except Exception as exc:
            errors.append(f"filesystem: {exc}")

        # ── graph subsystem reachability (real call) ──────────────────────────
        graph_reachable = None
        if knowledge_graph is not None:
            try:
                knowledge_graph.stats()
                graph_reachable = True
            except Exception as exc:
                graph_reachable = False
                errors.append(f"graph: {exc}")

        # ── watcher + connected sources (real) ────────────────────────────────
        watch: Dict[str, Any] = (
            local_kg_watcher.status()
            if local_kg_watcher
            else {"available": False, "active": {}}
        )
        sources: List[Any] = []
        try:
            if knowledge_graph is not None:
                sources = list((knowledge_graph.local_sources() or {}).get("sources") or [])
        except Exception as exc:
            errors.append(f"sources: {exc}")
        watched = len(watch.get("active", {}) or {})

        # ── derive mode + handshake from the probes (no constants) ────────────
        if not fs_ok:
            mode = "error"
        elif graph_reachable is False:
            mode = "degraded"
        else:
            mode = "online"
        handshake_ok = fs_ok and graph_reachable is not False
        latency_ms = round((time.perf_counter() - started) * 1000, 2)

        return {
            "agent": {
                "id": "lattice-local-runtime",
                "name": "Lattice Local Agent",
                "kind": "on-device-runtime",
                "online": mode == "online",
                "platform": platform.platform(),
                "machine": platform.machine(),
                "python": platform.python_version(),
            },
            "online": mode == "online",
            "mode": mode,
            "version": _LATTICE_VERSION,
            "pid": os.getpid(),
            "handshake": {
                "ok": handshake_ok,
                "transport": "in-process",
                "latency_ms": latency_ms,
                "detail": "Probed the in-process runtime (filesystem + graph); the local Lattice server is the on-device agent — no separate desktop process.",
            },
            "health": {
                "status": mode,
                "filesystem_access": fs_ok,
                "graph_reachable": graph_reachable,
                "watcher_available": bool(watch.get("available")),
            },
            "filesystem_access": fs_ok,
            "watcher_available": bool(watch.get("available")),
            "connected_folders": len(sources),
            "watched_folders": watched,
            "folders": {"connected": len(sources), "watching": watched},
            "watch": watch,
            "sources": sources,
            "last_seen": datetime.now().isoformat(timespec="seconds"),
            "error": "; ".join(errors) if errors else None,
        }

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
            raise http_error(404, "common.file_not_found", resolve_language(request))
        return FileResponse(str(target))

    # ── v9.8.0 ingestion jobs API (frozen paths — consumed by the frontend) ───
    def _require_pipeline():
        if ingestion_pipeline is None or not ingestion_pipeline.available():
            raise http_error(503, "capture.ingestion_disabled", DEFAULT_LANGUAGE)

    def _ingestion_write_workspace(request: Request, body_workspace: Optional[str], user: str) -> Optional[str]:
        header = request.headers.get("X-Workspace-Id")
        header = header.strip() if header and header.strip() else None
        supplied = [value for value in (body_workspace, header) if value]
        if len(set(supplied)) > 1:
            raise http_error(403, "common.workspace_mismatch", resolve_language(request))
        requested = supplied[0] if supplied else None
        if workspace_service is None:
            return requested
        try:
            return workspace_service.resolve_write_scope(requested, user or None)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.get("/api/ingestion/jobs")
    async def ingestion_jobs(request: Request, limit: int = 20):
        """Recent background ingestion jobs (newest first)."""
        require_user(request)
        _require_pipeline()
        limit = max(1, min(int(limit or 20), 100))
        return {"jobs": ingestion_pipeline.list_background_jobs(limit=limit)}

    @router.get("/api/ingestion/jobs/{job_id}")
    async def ingestion_job_detail(job_id: str, request: Request):
        """One job with its progress counters and (capped) error records."""
        require_user(request)
        _require_pipeline()
        job = ingestion_pipeline.get_background_job(job_id)
        if job is None:
            raise http_error(404, "ingestion.job_not_found", resolve_language(request))
        return job.as_dict()

    @router.post("/api/ingestion/jobs/{job_id}/resume")
    async def ingestion_job_resume(job_id: str, request: Request, background_tasks: BackgroundTasks):
        """Resume an interrupted/partial/failed job from its remaining items."""
        user = require_user(request)
        _require_pipeline()
        job = ingestion_pipeline.get_background_job(job_id)
        if job is None:
            raise http_error(404, "ingestion.job_not_found", resolve_language(request))
        if job.status == "running":
            return {"status": "already_running", "job_id": job_id, "job": job.as_dict()}
        remaining = len(job.remaining_indices())
        if remaining == 0 and job.status == "completed":
            return {"status": "nothing_to_resume", "job_id": job_id, "job": job.as_dict()}
        background_tasks.add_task(
            ingestion_pipeline.resume_background_job, job_id, user_email=user or None,
        )
        return {
            "status": "resuming",
            "job_id": job_id,
            "remaining": remaining,
            "job": job.as_dict(),
        }

    @router.post("/api/ingestion/folder")
    async def ingestion_folder(req: FolderIngestRequest, request: Request, background_tasks: BackgroundTasks):
        """Ingest a local folder through the unified pipeline.

        Reads local disk, so it follows the same approval dance as
        ``/local/read`` and ``/knowledge-graph/local/index``: without
        ``approved`` + ``approval_token`` the response is a
        ``permission_required`` payload. ``background=true`` schedules a job
        (summary includes ``job_id``) and executes it after the response.
        """
        current_user = permission_gateway.require_local_user(request)
        _require_pipeline()
        workspace_id = _ingestion_write_workspace(request, req.workspace_id, current_user)
        path = (req.path or "").strip()
        if not path:
            raise http_error(400, "common.path_required", resolve_language(request))
        if not req.approved:
            return permission_gateway.local_permission_response(path, "read", current_user)
        permission_gateway.require_local_approval(
            token=req.approval_token,
            path=path,
            action="read",
            user_email=current_user,
        )
        summary = ingestion_pipeline.ingest_folder(
            path,
            recursive=req.recursive,
            background=req.background,
            owner=current_user or None,
            workspace_id=workspace_id,
            user_email=current_user or None,
        )
        job_id = summary.get("job_id")
        if req.background and job_id:
            # Execute after the response is sent; progress is visible via
            # GET /api/ingestion/jobs/{job_id}.
            background_tasks.add_task(
                ingestion_pipeline.run_background_job, job_id, user_email=current_user or None,
            )
        return summary

    # ── Obsidian vault bridge: manual one-shot sync (v11.1.0) ────────────────
    @router.post("/api/ingestion/obsidian")
    async def ingestion_obsidian(req: ObsidianSyncRequest, request: Request):
        """Ingest an approved external Obsidian vault through the one gate.

        Every ``.md`` note goes through the same pipeline door as files and
        folders; on top of that, in-vault links become ``REFERENCES`` edges
        between the note nodes and frontmatter tags become ``Topic`` links. A
        link whose target is missing or ambiguous is reported in
        ``links.unresolved`` rather than guessed at. Re-running is idempotent.

        Reads local disk, so it follows the standard approval dance: without
        ``approved`` + ``approval_token`` the answer is a ``permission_required``
        payload.
        """
        current_user = permission_gateway.require_local_user(request)
        _require_pipeline()
        workspace_id = _ingestion_write_workspace(request, req.workspace_id, current_user)
        path = (req.path or "").strip()
        if not path:
            raise http_error(400, "ingestion.vault_path_required", resolve_language(request))
        if not req.approved:
            return permission_gateway.local_permission_response(path, "read", current_user)
        permission_gateway.require_local_approval(
            token=req.approval_token,
            path=path,
            action="read",
            user_email=current_user,
        )
        from latticeai.services.obsidian_bridge import ObsidianVaultBridge

        bridge = ObsidianVaultBridge(
            pipeline=ingestion_pipeline, knowledge_graph=knowledge_graph,
        )
        # Walking a vault and embedding its notes is blocking work; the server
        # owns one event loop and it may not sit here (10.9.0 ASYNC gate).
        return await asyncio.to_thread(
            bridge.sync,
            path,
            owner=current_user or None,
            workspace_id=workspace_id,
            user_email=current_user or None,
            dry_run=req.dry_run,
        )

    # ── folder watch mode: opt-in, off by default (backlog #8) ────────────────
    def _require_folder_watch(request: Request):
        _require_pipeline()
        if folder_watch is None:
            raise http_error(503, "ingestion.watch_unavailable", resolve_language(request))

    @router.get("/api/ingestion/watch")
    async def folder_watch_status(request: Request):
        """Watch-mode status: stored opt-ins, poller state, last scan results."""
        require_user(request)
        _require_folder_watch(request)
        return folder_watch.status()

    @router.post("/api/ingestion/watch")
    async def folder_watch_enable(req: FolderWatchEnableRequest, request: Request):
        """Explicitly opt a previously-ingested folder into watch mode.

        Follows the same local-read approval dance as ``/api/ingestion/folder``.
        Enabling snapshots the folder as the baseline; only *future* new or
        changed files are re-ingested (through the normal pipeline, with the
        watch owner's workspace scope).
        """
        current_user = permission_gateway.require_local_user(request)
        _require_folder_watch(request)
        workspace_id = _ingestion_write_workspace(request, req.workspace_id, current_user)
        path = (req.path or "").strip()
        if not path:
            raise http_error(400, "common.path_required", resolve_language(request))
        if not req.approved:
            return permission_gateway.local_permission_response(path, "read", current_user)
        permission_gateway.require_local_approval(
            token=req.approval_token,
            path=path,
            action="read",
            user_email=current_user,
        )
        result = folder_watch.enable(
            path,
            owner=current_user or None,
            workspace_id=workspace_id,
            recursive=req.recursive,
        )
        if result.get("status") != "ok":
            raise HTTPException(
                status_code=400,
                detail=result.get("detail")
                or translate("ingestion.watch_enable_failed", resolve_language(request)),
            )
        return result

    @router.delete("/api/ingestion/watch")
    async def folder_watch_disable(
        request: Request,
        watch_id: Optional[str] = None,
        path: Optional[str] = None,
    ):
        """Opt back out of watch mode (removes the stored consent record)."""
        require_user(request)
        _require_folder_watch(request)
        if not watch_id and not path:
            raise http_error(400, "ingestion.watch_selector_required", resolve_language(request))
        result = folder_watch.disable(watch_id=watch_id, path=path)
        if result.get("status") == "not_found":
            raise http_error(404, "ingestion.watch_not_found", resolve_language(request))
        return result

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
            require_admin=require_admin,
            static_dir=static_dir,
            allowed_workspaces_for=allowed_workspaces_for,
            ingestion_pipeline=ingestion_pipeline,
            workspace_service=workspace_service,
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
            hooks=hooks,
            workspace_service=workspace_service,
        )
    )

    return router


__all__ = [
    "FolderIngestRequest",
    "FolderWatchEnableRequest",
    "LocalAccessRequest",
    "LocalWriteRequest",
    "ObsidianSyncRequest",
    "create_local_files_router",
]
