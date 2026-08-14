"""The ordered phases that build the Lattice AI AI-Worker application.

Each phase reads what earlier phases published on the :class:`RuntimeContext`
and publishes its own results. The order below *is* the dependency order and is
fixed by ``tests/unit/test_runtime_context.py``:

1. ``platform`` — MLX/GPU device selection (the only step that touches hardware)
2. ``config``   — configuration, paths, the data directory
3. ``identity`` — the seam gate: users read, sessions, access control, rate limit
4. ``brain``    — the embedder and the multi-modal ports
5. ``domain``   — the LLM router and the tool-dispatch policy
6. ``web``      — lifespan, the FastAPI object, the model runtime service
7. ``features`` — the worker's compute routers

``worker_profile.phase_worker_routes`` runs *after* this list (from
:func:`latticeai.worker_app.create_worker_app`): it mounts the ``/worker/*``
seams and then asserts the served surface is exactly the worker contract.

v11.6.0 (WP-P1) cut the product's ten phases to these seven. The three that
disappeared — ``services`` (the typed ``AppContext``), ``foundation_routes``
(auth/admin/security/static/workspace) and ``platform_features`` (~34 product
routers) — built the surface ``lattice-host`` now serves natively.

Every heavy import lives *inside* a phase, never at module scope: importing
this module must stay free of GPU init, singleton construction, and filesystem
writes (``tests/unit/test_app_factory.py`` enforces that).

Why closures still appear here: several handlers must resolve a dependency at
call time rather than at construction time, because the dependency is built by
a later phase. Those read through ``ctx``, which is exactly the late binding
the original single function got from Python's closure rules.
"""

from __future__ import annotations

from latticeai.runtime.build_phases.features import phase_features as phase_features
from latticeai.runtime.build_phases.foundation import phase_brain as phase_brain
from latticeai.runtime.build_phases.foundation import phase_config as phase_config
from latticeai.runtime.build_phases.foundation import phase_identity as phase_identity
from latticeai.runtime.build_phases.foundation import phase_platform as phase_platform
from latticeai.runtime.build_phases.web import phase_domain as phase_domain
from latticeai.runtime.build_phases.web import phase_web as phase_web

#: The build order. Exported so the ordering test reads the same list the
#: factory runs, rather than a copy that can drift.
BUILD_PHASES = (
    phase_platform,
    phase_config,
    phase_identity,
    phase_brain,
    phase_domain,
    phase_web,
    phase_features,
)


__all__ = [
    "BUILD_PHASES",
    "phase_brain",
    "phase_config",
    "phase_domain",
    "phase_features",
    "phase_identity",
    "phase_platform",
    "phase_web",
]
