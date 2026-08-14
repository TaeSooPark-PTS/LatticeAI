"""The AI-Worker application profile (v11.6.0, plan §WP-I6).

``create_app`` builds the product server: 464 routes, the SPA, the whole
platform. v11.6.0 moves 439 of those into ``lattice-host`` and leaves Python as
what the system diagram has always drawn — the **AI Worker**: it infers with the
loaded MLX model, it runs tool handlers, it is the single writer of the Brain,
it stages proposals, it parses documents and it produces embeddings.

``create_worker_app`` is that process's entrypoint. It is deliberately *not* a
second assembly: it runs the same construction ``create_app`` does and then
applies the worker profile (:mod:`latticeai.runtime.build_phases.worker_profile`),
which mounts the three v11.6.0 seams and reduces the route table to the 25
KEEP_WORKER routes, the graph single-writer door and those seams. Two reasons
for that shape rather than a slimmer phase list:

* the routers the worker keeps are constructed by the same phases as everything
  else — the document tools and the model lifecycle come out of
  ``phase_interaction``, the voice and index-drain surfaces out of
  ``phase_platform_features`` — so a "worker phase list" would have to split
  four modules that WP-P1 is about to delete outright;
* the contract the Rust gateway needs *now* is the allowlist, and an allowlist
  is a fact about the served surface, not about how it was assembled. Deleting
  the complement (plan §Wave 3) leaves this function unchanged.

The host launches it as an ASGI factory::

    uvicorn latticeai.worker_app:create_worker_app --factory --host 127.0.0.1 --port 8000

and gates the worker's readiness on ``GET /health``, whose ``access`` block —
``require_auth`` and ``externally_reachable`` — the supervisor reads to decide
whether its own native lanes may talk to this process. That route is on the
worker profile for exactly that reason.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from latticeai.app_factory import build_context
from latticeai.runtime.build_phases.worker_profile import phase_worker_routes

if TYPE_CHECKING:  # imports for annotations only — keep module import light
    from fastapi import FastAPI

    from latticeai.core.config import Config


def create_worker_app(config: "Optional[Config]" = None) -> "FastAPI":
    """Build the FastAPI application an AI-Worker process serves."""
    ctx = build_context(config)
    phase_worker_routes(ctx)
    return ctx.app


def main() -> None:
    """Serve the worker profile (``python -m latticeai.worker_app``).

    The host normally spawns uvicorn against the factory itself; this exists so
    a person debugging a worker can start one the same way, on the same address
    the configuration names.
    """
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
