"""Wire NetworkBoundaryMode into the running app (hybrid Phase 1–2).

Creates one process-wide :class:`NetworkBoundaryService` and registers the
HTTP dial (including the Phase 2 transparency preview endpoint).
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from latticeai.core.network_boundary import (
    DEFAULT_NETWORK_MODE,
    normalize_network_mode,
)
from latticeai.services.network_boundary_service import NetworkBoundaryService

_LOCK = threading.Lock()
_SHARED: Optional[NetworkBoundaryService] = None


def _default_data_dir() -> Path:
    raw = os.environ.get("LATTICEAI_DATA_DIR", "").strip()
    if raw:
        return Path(raw)
    return Path.home() / ".ltcai"


def _env_default_mode() -> Any:
    return normalize_network_mode(
        os.environ.get("LATTICEAI_NETWORK_MODE", DEFAULT_NETWORK_MODE.value)
    )


def get_network_boundary_service(
    *,
    data_dir: Optional[Path] = None,
    audit: Optional[Callable[..., None]] = None,
) -> NetworkBoundaryService:
    global _SHARED
    with _LOCK:
        if _SHARED is None:
            _SHARED = NetworkBoundaryService(
                data_dir=Path(data_dir) if data_dir is not None else _default_data_dir(),
                default_mode=_env_default_mode(),
                audit=audit,
            )
            return _SHARED
        if data_dir is not None:
            _SHARED.rebind_data_dir(Path(data_dir))
        if audit is not None:
            _SHARED.rebind_audit(audit)
        return _SHARED


def resolve_active_network_mode(
    *,
    user_email: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> Any:
    return get_network_boundary_service().resolve(
        user_email=user_email, workspace_id=workspace_id,
    )


def register_network_boundary_router(
    app: Any,
    *,
    require_user: Callable[..., str],
    data_dir: Optional[Path] = None,
    append_audit_event: Optional[Callable[..., None]] = None,
    knowledge_graph: Any = None,
) -> Any:
    """Install network-boundary routes on ``app``. Idempotent by route path."""
    from latticeai.api.network_boundary import create_network_boundary_router

    svc = get_network_boundary_service(data_dir=data_dir, audit=append_audit_event)
    existing = {
        getattr(route, "path", None)
        for route in getattr(app, "routes", ())
    }
    if "/api/network-boundary" in existing:
        return svc
    app.include_router(
        create_network_boundary_router(
            service=svc,
            require_user=require_user,
            knowledge_graph=knowledge_graph,
        )
    )
    return svc


__all__ = [
    "get_network_boundary_service",
    "resolve_active_network_mode",
    "register_network_boundary_router",
]
