"""Wire PermissionMode into the running app without a large app_factory rewrite.

Creates one process-wide :class:`PermissionModeService`, points
``ToolDispatchService.permission_mode`` at it, and registers the HTTP dial.
Called from ``register_review_and_brain_tail_routers`` (which already has
``data_dir``, ``require_user``, ``append_audit_event``) and from
``build_chat_agent_runtime_from_context`` so the agent loop sees the same dial.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from latticeai.core.permission_mode import DEFAULT_MODE, normalize_mode
from latticeai.services.permission_mode_service import PermissionModeService

_LOCK = threading.Lock()
_SHARED: Optional[PermissionModeService] = None


def _default_data_dir() -> Path:
    raw = os.environ.get("LATTICEAI_DATA_DIR", "").strip()
    if raw:
        return Path(raw)
    return Path.home() / ".ltcai"


def _env_default_mode() -> Any:
    return normalize_mode(os.environ.get("LATTICEAI_PERMISSION_MODE", DEFAULT_MODE.value))


def get_permission_mode_service(
    *,
    data_dir: Optional[Path] = None,
    audit: Optional[Callable[..., None]] = None,
) -> PermissionModeService:
    """Process-wide singleton; first caller wins for data_dir/audit."""
    global _SHARED
    with _LOCK:
        if _SHARED is None:
            _SHARED = PermissionModeService(
                data_dir=Path(data_dir) if data_dir is not None else _default_data_dir(),
                default_mode=_env_default_mode(),
                audit=audit,
            )
        return _SHARED


def resolve_active_permission_mode(
    *,
    user_email: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> Any:
    return get_permission_mode_service().resolve(
        user_email=user_email, workspace_id=workspace_id,
    )


def bind_dispatch_permission_mode(dispatch_service: Any = None) -> None:
    """Point tool dispatch at the shared mode resolver."""
    from latticeai.services.tool_dispatch import DEFAULT_TOOL_DISPATCH_SERVICE

    service = dispatch_service or DEFAULT_TOOL_DISPATCH_SERVICE
    service.permission_mode = lambda: resolve_active_permission_mode()


def register_permission_mode_router(
    app: Any,
    *,
    require_user: Callable[..., str],
    data_dir: Optional[Path] = None,
    append_audit_event: Optional[Callable[..., None]] = None,
) -> Any:
    """Install GET/POST /api/permission-mode on ``app``. Idempotent by route path."""
    from latticeai.api.permission_mode import create_permission_mode_router

    svc = get_permission_mode_service(data_dir=data_dir, audit=append_audit_event)
    bind_dispatch_permission_mode()
    # Skip if already mounted (re-entrant factory / tests).
    existing = {
        getattr(route, "path", None)
        for route in getattr(app, "routes", ())
    }
    if "/api/permission-mode" in existing:
        return svc
    app.include_router(
        create_permission_mode_router(service=svc, require_user=require_user)
    )
    return svc


__all__ = [
    "get_permission_mode_service",
    "resolve_active_permission_mode",
    "bind_dispatch_permission_mode",
    "register_permission_mode_router",
]
