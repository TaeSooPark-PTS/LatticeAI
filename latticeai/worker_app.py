"""The AI-Worker application profile.

``create_app`` builds the full FastAPI application surface, while
``lattice-host`` owns the product gateway, knowledge-graph writes, and
governance/proposal state. Python remains the loopback **AI Worker**: it runs
local/provider LLM inference, tool handlers, document parsing/rendering, ASR,
concept extraction, HNSW sidecar queries, and neural/fallback text embeddings.

``create_worker_app`` is that process's entrypoint. It runs ``build_context``
and applies :func:`latticeai.runtime.build_phases.worker_profile.phase_worker_routes`,
which mounts the loopback seams (``WORKER_SEAM_ROUTES`` and
``WORKER_COMPUTE_ROUTES``) and filters the route table to the exact 20 routes
declared in :func:`~latticeai.runtime.build_phases.worker_profile.worker_route_keys`
(9 ``WORKER_ROUTES`` + 2 ``WORKER_SEAM_ROUTES`` + 9 ``WORKER_COMPUTE_ROUTES``,
with ``GRAPH_WRITER_ROUTES`` retired after native write cutover). Two reasons
for filtering the assembled app rather than maintaining a separate phase list:

* the kept product routes (model lifecycle, agent LLM/tool doors, embedding
  status, and ``/health``) are wired by the same assembly phases as the rest of
  the runtime, avoiding duplicated wiring;
* ``rust/fixtures/worker_allowlist.json`` is generated directly from
  ``worker_route_keys()``, so the allowlist and the worker surface stay a single
  contract checked from both ends.

The host launches it as an ASGI factory::

    uvicorn latticeai.worker_app:create_worker_app --factory --host 127.0.0.1 --port 8000

and gates the worker's readiness on ``GET /health``, whose ``access`` block —
``require_auth`` and ``externally_reachable`` — the supervisor reads to decide
whether its own native lanes may talk to this process.
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
