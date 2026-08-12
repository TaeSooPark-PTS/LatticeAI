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
from latticeai.runtime.service_singletons import (
    rebind_singleton,
    singleton_data_dir,
)
from latticeai.services.permission_mode_service import PermissionModeService

_LOCK = threading.Lock()
_SHARED: Optional[PermissionModeService] = None


def _env_default_mode() -> Any:
    return normalize_mode(os.environ.get("LATTICEAI_PERMISSION_MODE", DEFAULT_MODE.value))


def get_permission_mode_service(
    *,
    data_dir: Optional[Path] = None,
    audit: Optional[Callable[..., None]] = None,
) -> PermissionModeService:
    """Process-wide singleton.

    An early lazy caller (a tool dispatch before routers are mounted) would
    otherwise pin the service to the fallback ``data_dir`` with no audit sink.
    Explicit arguments therefore *rebind* an already-created service instead of
    being silently dropped, so the app's real data dir and audit log always win.
    """
    global _SHARED
    with _LOCK:
        if _SHARED is None:
            _SHARED = PermissionModeService(
                data_dir=singleton_data_dir(data_dir),
                default_mode=_env_default_mode(),
                audit=audit,
            )
            return _SHARED
        return rebind_singleton(_SHARED, data_dir=data_dir, audit=audit)


def resolve_active_permission_mode(
    *,
    user_email: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> Any:
    return get_permission_mode_service().resolve(
        user_email=user_email, workspace_id=workspace_id,
    )


def bind_dispatch_permission_mode(dispatch_service: Any = None) -> None:
    """Point tool dispatch at the shared mode resolver.

    The bound callable accepts scope kwargs so per-user and per-workspace
    overrides actually reach ``enforce_policy`` — an unscoped resolver would
    always return the process-wide default and make the dial a no-op.
    """
    from latticeai.services.tool_dispatch import DEFAULT_TOOL_DISPATCH_SERVICE

    service = dispatch_service or DEFAULT_TOOL_DISPATCH_SERVICE
    service.permission_mode = resolve_active_permission_mode


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
    # Skip if already mounted (re-entrant factory / tests). The guard is a flag
    # on app.state rather than a scan of route paths: fastapi >= 0.140 wraps an
    # included router in an opaque entry whose flat ``path`` is None, so path
    # introspection stopped seeing the mount and a re-entrant call duplicated
    # the routes. This function is the only in-tree mount site for this router.
    state = getattr(app, "state", None)
    if state is not None and getattr(state, "_ltcai_permission_mode_mounted", False):
        return svc
    app.include_router(
        create_permission_mode_router(service=svc, require_user=require_user)
    )
    if state is not None:
        state._ltcai_permission_mode_mounted = True
    return svc


__all__ = [
    "get_permission_mode_service",
    "resolve_active_permission_mode",
    "bind_dispatch_permission_mode",
    "register_permission_mode_router",
]
