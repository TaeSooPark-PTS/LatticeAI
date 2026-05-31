"""Application dependency context for router assembly.

The concrete FastAPI app is still assembled in ``server_app``. This dataclass
documents the shared dependency boundary for routers and services so future
extractions can receive a typed context instead of importing the app module.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class AppContext:
    config: Any
    data_dir: Path
    static_dir: Path
    model_router: Any
    workspace_store: Any
    workspace_service: Any
    knowledge_graph: Any
    local_kg_watcher: Any
    require_user: Callable[..., str]
    require_admin: Callable[..., tuple]

