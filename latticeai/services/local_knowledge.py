"""Local folder knowledge-source API and optional filesystem watcher."""

import logging
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field


class LocalTreeRequest(BaseModel):
    path: str
    max_items: int = 200
    approved: bool = False
    approval_token: Optional[str] = None


class LocalKnowledgeAuditRequest(BaseModel):
    path: str
    include_ocr: bool = False
    max_files: int = 50_000
    approved: bool = False
    approval_token: Optional[str] = None


class LocalKnowledgeIndexRequest(BaseModel):
    path: str
    include_ocr: bool = False
    watch_enabled: bool = False
    max_files: int = 5_000
    consent: Dict[str, Any] = Field(default_factory=dict)
    approved: bool = False
    approval_token: Optional[str] = None


class LocalKnowledgeWatchRequest(BaseModel):
    source_id: str


class _LocalWatchHandler:
    def __init__(self, schedule_change: Callable[[], None]):
        self._schedule_change = schedule_change

    def on_any_event(self, event):  # pragma: no cover - exercised by OS watcher
        if getattr(event, "is_directory", False):
            return
        self._schedule_change()


class LocalKnowledgeWatcher:
    """Debounced watchdog wrapper for approved local knowledge sources."""

    def __init__(self, get_graph: Callable[[], Any], *, debounce_seconds: float = 5.0, hooks: Any = None):
        self._get_graph = get_graph
        self._debounce_seconds = debounce_seconds
        self._hooks = hooks
        self._lock = threading.Lock()
        self._watched: Dict[str, Dict[str, Any]] = {}
        self._observer_cls = None
        self._event_handler_base = None
        self._import_error = ""
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer

            self._observer_cls = Observer
            self._event_handler_base = FileSystemEventHandler
        except Exception as exc:  # pragma: no cover - depends on optional dependency
            self._import_error = str(exc)

    @property
    def available(self) -> bool:
        return self._observer_cls is not None and self._event_handler_base is not None

    def status(self) -> Dict[str, Any]:
        with self._lock:
            active = {
                source_id: {
                    "root_path": item["source"].get("root_path"),
                    "last_event_at": item.get("last_event_at"),
                    "last_indexed_at": item.get("last_indexed_at"),
                    "last_error": item.get("last_error"),
                }
                for source_id, item in self._watched.items()
            }
        return {
            "available": self.available,
            "error": "" if self.available else self._import_error or "watchdog is not installed",
            "debounce_seconds": self._debounce_seconds,
            "active": active,
        }

    def restore_enabled_sources(self) -> Dict[str, Any]:
        graph = self._get_graph()
        if graph is None:
            return {"restored": 0, "available": self.available}
        restored = 0
        try:
            for source in graph.local_sources().get("sources", []):
                if source.get("watch_enabled"):
                    consent = source.get("consent") or {}
                    restored_source = {
                        **source,
                        # Older sources had no explicit scope. Their local
                        # knowledge belongs to the personal Brain, never every
                        # workspace.
                        "workspace_id": source.get("workspace_id") or consent.get("workspace_id") or "personal",
                    }
                    result = self.start_source(restored_source)
                    if result.get("watching"):
                        restored += 1
        except Exception as exc:
            logging.warning("local knowledge watcher restore failed: %s", exc)
        return {"restored": restored, "available": self.available}

    def start_source(self, source: Dict[str, Any]) -> Dict[str, Any]:
        source_id = str(source.get("id") or "")
        root_path = str(source.get("root_path") or "")
        if not source_id or not root_path:
            return {"watching": False, "error": "source_id and root_path are required"}
        if not self.available:
            return {"watching": False, "source_id": source_id, "error": self._import_error or "watchdog is not installed"}
        root = Path(root_path).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            return {"watching": False, "source_id": source_id, "error": "source folder is not available"}

        self.stop_source(source_id)

        class Handler(_LocalWatchHandler, self._event_handler_base):  # type: ignore[misc, valid-type]
            def __init__(handler_self):
                self._event_handler_base.__init__(handler_self)
                _LocalWatchHandler.__init__(handler_self, lambda: self._schedule(source_id))

        observer = self._observer_cls()
        try:
            observer.schedule(Handler(), str(root), recursive=True)
            observer.start()
        except Exception as exc:
            logging.warning("local knowledge watcher start failed for %s: %s", root, exc)
            return {"watching": False, "source_id": source_id, "error": str(exc)}

        with self._lock:
            self._watched[source_id] = {
                "observer": observer,
                "timer": None,
                "source": dict(source),
                "last_event_at": None,
                "last_indexed_at": None,
                "last_error": None,
            }
        return {"watching": True, "source_id": source_id, "root_path": str(root)}

    def stop_source(self, source_id: str) -> Dict[str, Any]:
        with self._lock:
            item = self._watched.pop(source_id, None)
        if not item:
            return {"stopped": False, "source_id": source_id}
        timer = item.get("timer")
        if timer:
            timer.cancel()
        observer = item.get("observer")
        try:
            observer.stop()
            observer.join(timeout=3)
        except Exception as exc:
            logging.warning("local knowledge watcher stop failed for %s: %s", source_id, exc)
        return {"stopped": True, "source_id": source_id}

    def stop_all(self) -> None:
        for source_id in list(self.status().get("active", {}).keys()):
            self.stop_source(source_id)

    def _schedule(self, source_id: str) -> None:
        with self._lock:
            item = self._watched.get(source_id)
            if not item:
                return
            timer = item.get("timer")
            if timer:
                timer.cancel()
            item["last_event_at"] = _now_seconds()
            timer = threading.Timer(self._debounce_seconds, self._run_index, args=(source_id,))
            timer.daemon = True
            item["timer"] = timer
            timer.start()

    def _run_index(self, source_id: str) -> None:
        with self._lock:
            item = self._watched.get(source_id)
            if not item:
                return
            source = dict(item["source"])
            item["timer"] = None
        graph = self._get_graph()
        if graph is None:
            return
        consent = source.get("consent") or {}
        root = source.get("root_path")
        if self._hooks is not None:
            self._hooks.fire_hook("pre_index", "folder.reindex",
                                  payload={"source_id": source_id, "root_path": root, "trigger": "watch"})
        try:
            result = graph.index_local_folder(
                Path(source["root_path"]),
                include_ocr=bool(source.get("include_ocr")),
                watch_enabled=True,
                user_email=consent.get("approved_by"),
                workspace_id=source.get("workspace_id") or consent.get("workspace_id") or "personal",
                consent=consent,
                source_id_override=source_id,
            )
            with self._lock:
                if source_id in self._watched:
                    self._watched[source_id]["last_indexed_at"] = _now_seconds()
                    self._watched[source_id]["last_error"] = None
            if self._hooks is not None:
                self._hooks.fire_hook("post_index", "folder.reindex",
                                      payload={"source_id": source_id, "root_path": root, "trigger": "watch", "status": "ok"})
                counts = (result.get("counts") or {}) if isinstance(result, dict) else {}
                if int(counts.get("indexed") or 0) + int(counts.get("deleted") or 0) > 0:
                    self._hooks.fire_hook(
                        "post_tool",
                        "tool.kg_ingest.local_folder",
                        payload={
                            "tool": "kg_ingest.local_folder",
                            "status": "ok",
                            "source": "ingestion",
                            "source_type": "local_folder",
                            "source_id": source_id,
                        },
                        user_email=consent.get("approved_by"),
                        workspace_id=source.get("workspace_id"),
                    )
        except Exception as exc:
            logging.warning("local knowledge watcher reindex failed for %s: %s", source_id, exc)
            with self._lock:
                if source_id in self._watched:
                    self._watched[source_id]["last_error"] = str(exc)
            if self._hooks is not None:
                self._hooks.fire_hook("post_index", "folder.reindex",
                                      payload={"source_id": source_id, "root_path": root, "trigger": "watch", "status": "error", "error": str(exc)})


def _now_seconds() -> float:
    import time

    return time.time()


def create_local_knowledge_router(
    *,
    get_graph: Callable[[], Any],
    require_graph: Callable[[], None],
    require_user: Callable[[Request], str],
    require_local_user: Callable[[Request], str],
    local_permission_response: Callable[..., dict],
    require_local_approval: Callable[..., None],
    watcher: Optional[LocalKnowledgeWatcher] = None,
    hooks: Any = None,
    workspace_service: Any = None,
) -> APIRouter:
    router = APIRouter()

    def graph():
        require_graph()
        return get_graph()

    def write_workspace(request: Request, user: str) -> Optional[str]:
        requested = request.headers.get("X-Workspace-Id")
        requested = requested.strip() if requested and requested.strip() else None
        if workspace_service is None:
            return requested
        try:
            return workspace_service.resolve_write_scope(requested, user or None)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.get("/knowledge-graph/local/roots")
    async def knowledge_graph_local_roots(request: Request):
        require_user(request)
        return graph().discover_local_roots()

    @router.get("/knowledge-graph/local/sources")
    async def knowledge_graph_local_sources(request: Request):
        require_user(request)
        payload = graph().local_sources()
        watch_status = watcher.status() if watcher else {"available": False, "active": {}}
        active = watch_status.get("active", {})
        for source in payload.get("sources", []):
            source["watch_active"] = source.get("id") in active
            source["watch_status"] = active.get(source.get("id"))
        payload["watch"] = watch_status
        return payload

    @router.get("/knowledge-graph/local/health")
    async def knowledge_graph_local_health(request: Request, error_samples: int = 3):
        """Per-folder memory state (v9.9.7): indexing coverage, failures with
        their stored reasons, and watch state.

        Vector freshness rides along as an explicitly **global** figure — the
        vector index is not per-folder, and claiming otherwise would invent a
        number.
        """
        require_user(request)
        kg = graph()
        payload = kg.local_source_health(error_samples=error_samples)
        watch_status = watcher.status() if watcher else {"available": False, "active": {}}
        active = watch_status.get("active", {})
        for folder in payload.get("folders", []):
            folder["watch_active"] = folder.get("id") in active
        payload["watch"] = watch_status
        try:
            payload["vector_freshness_global"] = kg.vector_freshness()
        except Exception as exc:  # noqa: BLE001 — one missing signal, not a 500
            payload["vector_freshness_global"] = {
                "status": "unavailable", "detail": str(exc),
                "pending_items": 0, "total_items": 0,
            }
        return payload

    @router.get("/knowledge-graph/local/watch/status")
    async def knowledge_graph_local_watch_status(request: Request):
        require_user(request)
        graph()
        return watcher.status() if watcher else {"available": False, "active": {}, "error": "watcher unavailable"}

    @router.post("/knowledge-graph/local/watch/stop")
    async def knowledge_graph_local_watch_stop(req: LocalKnowledgeWatchRequest, request: Request):
        require_user(request)
        kg = graph()
        try:
            kg.set_local_source_watch(req.source_id, False)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        result = watcher.stop_source(req.source_id) if watcher else {"stopped": False, "source_id": req.source_id}
        return {"status": "ok", "watch": result}

    @router.post("/knowledge-graph/local/tree")
    async def knowledge_graph_local_tree(req: LocalTreeRequest, request: Request):
        current_user = require_local_user(request)
        kg = graph()
        if not req.approved:
            return local_permission_response(req.path, "list", current_user)
        require_local_approval(token=req.approval_token, path=req.path, action="list", user_email=current_user)
        try:
            return kg.preview_local_tree(Path(req.path), max_items=req.max_items)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/knowledge-graph/local/audit")
    async def knowledge_graph_local_audit(req: LocalKnowledgeAuditRequest, request: Request):
        current_user = require_local_user(request)
        kg = graph()
        if not req.approved:
            return local_permission_response(req.path, "list", current_user)
        require_local_approval(token=req.approval_token, path=req.path, action="list", user_email=current_user)
        try:
            return kg.audit_local_folder(Path(req.path), include_ocr=req.include_ocr, max_files=req.max_files)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/knowledge-graph/local/index")
    async def knowledge_graph_local_index(req: LocalKnowledgeIndexRequest, request: Request):
        current_user = require_local_user(request)
        workspace_id = write_workspace(request, current_user)
        kg = graph()
        if not req.approved:
            return local_permission_response(req.path, "read", current_user)
        require_local_approval(token=req.approval_token, path=req.path, action="read", user_email=current_user)
        consent = {
            **(req.consent or {}),
            "approved_by": current_user,
            "workspace_id": workspace_id or "personal",
        }
        source_id_override = None
        try:
            target_root = Path(req.path).expanduser().resolve()
            target_scope = workspace_id or "personal"
            for source in kg.local_sources().get("sources", []):
                source_consent = source.get("consent") or {}
                source_scope = source_consent.get("workspace_id") or "personal"
                source_root_value = str(source.get("root_path") or "").strip()
                if not source_root_value:
                    continue
                source_root = Path(source_root_value).expanduser().resolve()
                if source_scope == target_scope and source_root == target_root:
                    source_id_override = source.get("id")
                    break
        except Exception:
            # Source reuse is a compatibility optimization; indexing still has
            # a deterministic workspace-scoped ID when discovery is unavailable.
            source_id_override = None
        if hooks is not None:
            hooks.fire_hook("pre_index", "folder.index",
                            payload={"root_path": req.path, "trigger": "connect", "watch": req.watch_enabled},
                            user_email=current_user, workspace_id=workspace_id)
        try:
            result = kg.index_local_folder(
                Path(req.path),
                include_ocr=req.include_ocr,
                watch_enabled=req.watch_enabled,
                user_email=current_user,
                workspace_id=workspace_id or "personal",
                consent=consent,
                max_files=req.max_files,
                source_id_override=source_id_override,
            )
        except ValueError as exc:
            if hooks is not None:
                hooks.fire_hook("post_index", "folder.index",
                                payload={"root_path": req.path, "trigger": "connect", "status": "error", "error": str(exc)},
                                user_email=current_user, workspace_id=workspace_id)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if hooks is not None:
            _idx = (result.get("index") or {}) if isinstance(result, dict) else {}
            hooks.fire_hook("post_index", "folder.index",
                            payload={"root_path": req.path, "trigger": "connect", "status": "ok",
                                     "indexed": _idx.get("indexed") or (result or {}).get("indexed")},
                            user_email=current_user, workspace_id=workspace_id)
            counts = (result.get("counts") or {}) if isinstance(result, dict) else {}
            if int(counts.get("indexed") or 0) + int(counts.get("deleted") or 0) > 0:
                hooks.fire_hook(
                    "post_tool",
                    "tool.kg_ingest.local_folder",
                    payload={
                        "tool": "kg_ingest.local_folder",
                        "status": "ok",
                        "source": "ingestion",
                        "source_type": "local_folder",
                        "source_id": (result.get("source") or {}).get("id") if isinstance(result, dict) else None,
                    },
                    user_email=current_user,
                    workspace_id=workspace_id,
                )

        if watcher:
            if req.watch_enabled:
                source_payload = {
                    **result.get("source", {}),
                    "workspace_id": workspace_id or "personal",
                    "consent": consent,
                }
                result["watch"] = watcher.start_source(source_payload)
            else:
                result["watch"] = watcher.stop_source(result.get("source", {}).get("id", ""))
        return result

    return router
