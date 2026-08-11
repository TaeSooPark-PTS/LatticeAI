"""The ordered phases that build the Lattice AI application.

Each phase reads what earlier phases published on the :class:`RuntimeContext`
and publishes its own results. The order below *is* the dependency order and is
fixed by ``tests/unit/test_runtime_context.py``:

1.  ``platform``   — MLX/GPU device selection (the only step that touches hardware)
2.  ``config``     — configuration, security settings, paths, filesystem layout
3.  ``identity``   — users, sessions, audit, access control, API keys, SSO/VPC config
4.  ``brain``      — embedder, knowledge graph, conversations, hooks, persistence, history
5.  ``domain``     — model router, garden, chat service (needed by the web phase)
6.  ``web``        — lifespan, the FastAPI app, model runtime, static + foundation routers
7.  ``services``   — retrieval/context, chat agent runtime, the typed AppContext
8.  ``foundation_routes`` — mount the foundation routers now that AppContext exists
9.  ``platform_features`` — workspace platform, automation, review, command centre
10. ``interaction`` — model/chat/search/tools routers and the brain tail routers

Every heavy import lives *inside* a phase, never at module scope: importing
this module must stay free of GPU init, singleton construction, and filesystem
writes (``tests/unit/test_app_factory.py`` enforces that).

Why closures still appear here: several handlers must resolve a dependency at
call time rather than at construction time, because the dependency is built by
a later phase. Those read through ``ctx``, which is exactly the late binding
the original single function got from Python's closure rules.

v11.3.0 split the 1,450-line module into three stage submodules — ``foundation``
(1-4), ``web`` (5-8) and ``features`` (9-10). Nothing else moved: this package
re-exports every phase under its historical name and :data:`BUILD_PHASES` stays
defined here, in one place, so the order test and the factory read the same
tuple rather than a copy that can drift.
"""

from __future__ import annotations

from latticeai.runtime.build_phases.features import (
    phase_interaction as phase_interaction,
)
from latticeai.runtime.build_phases.features import (
    phase_platform_features as phase_platform_features,
)
from latticeai.runtime.build_phases.foundation import phase_brain as phase_brain
from latticeai.runtime.build_phases.foundation import phase_config as phase_config
from latticeai.runtime.build_phases.foundation import phase_identity as phase_identity
from latticeai.runtime.build_phases.foundation import phase_platform as phase_platform
from latticeai.runtime.build_phases.web import phase_domain as phase_domain
from latticeai.runtime.build_phases.web import (
    phase_foundation_routes as phase_foundation_routes,
)
from latticeai.runtime.build_phases.web import phase_services as phase_services
from latticeai.runtime.build_phases.web import phase_web as phase_web
from latticeai.runtime.build_phases.web import self_model_port as self_model_port

#: The build order. Exported so the ordering test reads the same list the
#: factory runs, rather than a copy that can drift.
BUILD_PHASES = (
    phase_platform,
    phase_config,
    phase_identity,
    phase_brain,
    phase_domain,
    phase_web,
    phase_services,
    phase_foundation_routes,
    phase_platform_features,
    phase_interaction,
)


__all__ = [
    "BUILD_PHASES",
    "phase_brain",
    "phase_domain",
    "phase_config",
    "phase_foundation_routes",
    "phase_identity",
    "phase_interaction",
    "phase_platform",
    "phase_platform_features",
    "phase_services",
    "phase_web",
]
