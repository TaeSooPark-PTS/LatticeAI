"""Lattice AI AI-Worker application factory.

``build_context`` performs *all* construction the worker process needs: MLX/GPU
device init, config parsing, the seam gate, the embedder and multi-modal ports,
the LLM router, and the compute routers. Importing this module has **no side
effects**: nothing heavy is imported and no file is created until
``build_context`` is called.

The assembly itself lives in :mod:`latticeai.runtime.build_phases` as seven
ordered phases sharing a
:class:`~latticeai.runtime.runtime_context.RuntimeContext`. This module is now
only the *orchestrator*: run the phases and hand back the context.

``create_app`` used to live here and built the 464-route product server.
v11.6.0 moved that whole surface into ``lattice-host`` (plan §설계 결정 1) and
WP-P1 deleted its Python side, so there is exactly one application in this
package now — :func:`latticeai.worker_app.create_worker_app`, which is
``build_context`` plus the worker profile.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from latticeai.runtime.build_phases import BUILD_PHASES
from latticeai.runtime.runtime_context import RuntimeContext

if TYPE_CHECKING:  # imports for annotations only — keep module import light
    from latticeai.core.config import Config


def build_context(config: "Optional[Config]" = None) -> RuntimeContext:
    """Run every build phase in order and return the populated context.

    Exposed separately from :func:`latticeai.worker_app.create_worker_app` so
    tests can inspect the assembly (which phase produced what) without building
    an application.
    """
    ctx = RuntimeContext(config)
    for phase in BUILD_PHASES:
        phase(ctx)
    return ctx


__all__ = ["build_context"]
