"""FastAPI shell assembly for the application factory.

This seam owns only the outer web shell: app construction, CORS middleware,
and static asset mounts. Router registration stays in ``app_factory`` until
the route snapshot/reorder step is reviewed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable


def build_web_runtime(
    *,
    app_mode: str,
    app_version: str,
    lifespan: Any,
    default_host: str,
    default_port: int,
    cors_extra_origins: Iterable[str],
    cors_allow_network: bool,
    static_dir: Path,
) -> Dict[str, Any]:
    """Create the FastAPI shell and mount static assets in legacy order."""

    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(
        title=f"Lattice AI Server ({app_mode})",
        version=app_version,
        lifespan=lifespan,
    )

    cors_allowed_origins = [
        f"http://localhost:{default_port}",
        f"http://127.0.0.1:{default_port}",
        *cors_extra_origins,
    ]
    if cors_allow_network:
        cors_allowed_origins = cors_allowed_origins + [
            f"http://{default_host}:{default_port}",
            f"https://{default_host}:{default_port}",
        ]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )

    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    icons_dir = static_dir / "icons"
    if icons_dir.exists():
        app.mount("/icons", StaticFiles(directory=str(icons_dir)), name="icons")

    return {
        "app": app,
        "CORS_ALLOWED_ORIGINS": cors_allowed_origins,
    }
