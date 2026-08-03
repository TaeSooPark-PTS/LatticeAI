"""FastAPI shell assembly for the application factory.

This seam owns only the outer web shell: app construction, the request-side
middleware (CSRF origin guard, CORS), and static asset mounts. Router
registration stays in ``app_factory`` until the route snapshot/reorder step is
reviewed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List


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
    csrf_trusted_origins: Iterable[str] = (),
) -> Dict[str, Any]:
    """Create the FastAPI shell and mount static assets in legacy order."""

    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles

    from latticeai.core.csrf import CSRFOriginGuardMiddleware, CSRFOriginPolicy
    from latticeai.core.security import host_is_loopback

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

    # An origin that CORS already lets send *credentialed* cross-origin
    # requests is, by that decision, trusted with the session cookie; listing
    # it again here would be a second place to forget. The explicit
    # LATTICEAI_CSRF_TRUSTED_ORIGINS entries are for the reverse-proxy case,
    # where the public hostname is not the bind address.
    csrf_allowed_origins: List[str] = [
        *cors_allowed_origins,
        *csrf_trusted_origins,
    ]
    csrf_policy = CSRFOriginPolicy(
        trusted_origins=csrf_allowed_origins,
        server_host=default_host,
        server_port=default_port,
        bind_is_loopback=host_is_loopback(default_host),
    )

    # Registration order is stack order in reverse: the LAST middleware added
    # is the OUTERMOST. CORS must stay outside the guard so a rejected request
    # still comes back with the CORS headers the browser needs to surface the
    # 403 instead of an opaque network error.
    app.add_middleware(CSRFOriginGuardMiddleware, policy=csrf_policy)
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
        "CSRF_ALLOWED_ORIGINS": csrf_allowed_origins,
    }
