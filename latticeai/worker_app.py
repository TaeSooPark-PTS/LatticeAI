from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from latticeai.app_factory import build_context
from latticeai.runtime.build_phases.worker_profile import phase_worker_routes

if TYPE_CHECKING:
    from fastapi import FastAPI

    from latticeai.core.config import Config


def create_worker_app(config: "Optional[Config]" = None) -> "FastAPI":
    ctx = build_context(config)
    phase_worker_routes(ctx)
    return ctx.app


def main() -> None:
    import uvicorn

    from latticeai.core.config import Config

    config = Config.from_env()
    uvicorn.run(
        create_worker_app(config),
        host=config.host,
        port=config.port,
        log_level="info",
    )


__all__ = ["create_worker_app", "main"]


if __name__ == "__main__":
    main()
