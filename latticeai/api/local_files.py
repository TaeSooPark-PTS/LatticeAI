"""Local file access and local knowledge graph routes."""

from __future__ import annotations

import os
import platform
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from latticeai.api.knowledge_graph import create_knowledge_graph_router
from local_knowledge_api import create_local_knowledge_router
from tools import local_list, local_read, local_write

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
) -> APIRouter:
    router = APIRouter()

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
        watch = local_kg_watcher.status() if local_kg_watcher else {"available": False, "active": {}}
        sources = []
        try:
            if knowledge_graph is not None:
                sources = (knowledge_graph.local_sources() or {}).get("sources", [])
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


__all__ = ["LocalAccessRequest", "LocalWriteRequest", "create_local_files_router"]
