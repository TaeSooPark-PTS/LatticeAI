"""Wire the feature switchboard into the running app (v11.2.0).

Same shape as ``permission_mode_wiring``: one process-wide service and an
idempotent router mount guarded on ``app.state``. The rebinding rule that shape
turns on — explicit arguments *rebind* an already-created service rather than
being dropped, so a lazy first caller cannot pin the store to a fallback data
dir with no audit sink — is shared, in
:mod:`latticeai.runtime.service_singletons`.

The one job unique to this module is :func:`bind_feature_gates`: it points each
opt-in gate's resolver at the service, which is the step that turns a persisted
preference into behaviour. Every gate is imported inside the function so
importing this module costs nothing; ``lattice_brain.synthesis`` in particular
drags in the graph layer.

Unbinding is a supported operation (:func:`unbind_feature_gates`) because these
gates are module-level singletons: a test that bound them must be able to give
them back, and "the environment answers again" has to be reachable.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

from latticeai.runtime.service_singletons import (
    rebind_singleton,
    singleton_data_dir,
)
from latticeai.services.feature_toggles import FeatureToggleService

_LOCK = threading.Lock()
_SHARED: Optional[FeatureToggleService] = None

#: ``feature id -> (module, attribute)`` for every boolean gate. Resolved lazily
#: by :func:`_boolean_gates` so the table stays readable next to the catalog it
#: mirrors, and a rename that breaks the pairing fails loudly at wiring time.
GATE_BINDINGS: Tuple[Tuple[str, str, str], ...] = (
    ("allow_multimodal", "lattice_brain.ingestion", "MULTIMODAL_GATE"),
    ("video_ingest", "lattice_brain.ingestion", "VIDEO_GATE"),
    ("auto_vector_index", "lattice_brain.ingestion", "AUTO_VECTOR_INDEX_GATE"),
    ("brain_network", "lattice_brain.portability", "BRAIN_NETWORK_GATE"),
    ("synthesis", "lattice_brain.synthesis", "SYNTHESIS_GATE"),
    ("fusion_rrf", "lattice_brain.graph.fusion", "FUSION_RRF_GATE"),
    ("graph_expansion", "lattice_brain.graph.fusion", "GRAPH_EXPANSION_GATE"),
    ("vault_watch", "latticeai.services.folder_watch", "VAULT_WATCH_GATE"),
    ("auto_late_fusion", "latticeai.services.search_service", "IMAGE_QUERY_FUSION_GATE"),
)

#: The one non-boolean seam: the vector backend is a pick-one-of-three.
CHOICE_FEATURE = "vector_backend"


def _boolean_gates() -> List[Tuple[str, Any]]:
    """``[(feature id, gate)]`` — imports are here, not at module import."""
    from importlib import import_module

    return [
        (feature_id, getattr(import_module(module), attribute))
        for feature_id, module, attribute in GATE_BINDINGS
    ]


def _bind_vector_index(resolver: Any) -> None:
    from lattice_brain.graph.vector_index.selector import bind_vector_index_resolver

    bind_vector_index_resolver(resolver)


def get_feature_toggle_service(
    *,
    data_dir: Optional[Path] = None,
    audit: Optional[Callable[..., None]] = None,
) -> FeatureToggleService:
    """Process-wide singleton; explicit arguments rebind an existing one."""
    global _SHARED
    with _LOCK:
        if _SHARED is None:
            _SHARED = FeatureToggleService(
                data_dir=singleton_data_dir(data_dir),
                audit=audit,
            )
            return _SHARED
        return rebind_singleton(_SHARED, data_dir=data_dir, audit=audit)


def bind_feature_gates(service: Optional[FeatureToggleService] = None) -> None:
    """Point every opt-in gate at the switchboard.

    After this, a persisted preference beats the environment variable for every
    feature in the catalog — which is the whole point, since the panel is the
    only control surface most people will ever use.
    """
    resolved = service or get_feature_toggle_service()
    for feature_id, gate in _boolean_gates():
        # ``gate.local`` is the fallback on purpose: the switchboard speaks only
        # for switches this person actually moved. Everything else still answers
        # from the gate's own override → env → default, so binding changes
        # nothing at all for an install that never opened the panel.
        gate.bind(resolved.resolver(feature_id, gate.local))
    _bind_vector_index(resolved.choice_resolver(CHOICE_FEATURE))


def unbind_feature_gates() -> None:
    """Hand every gate back to its environment variable."""
    for _feature_id, gate in _boolean_gates():
        gate.bind(None)
    _bind_vector_index(None)


def reset_feature_toggle_service() -> None:
    """Drop the singleton *and* the bindings that pointed at it (tests)."""
    global _SHARED
    unbind_feature_gates()
    with _LOCK:
        _SHARED = None


def register_features_router(
    app: Any,
    *,
    require_user: Callable[..., str],
    data_dir: Optional[Path] = None,
    append_audit_event: Optional[Callable[..., None]] = None,
) -> FeatureToggleService:
    """Install GET/POST /api/features on ``app`` and bind the gates. Idempotent.

    The guard is a flag on ``app.state`` rather than a scan of route paths: a
    router included through fastapi >= 0.140 has no flat ``path`` to introspect,
    so path scanning silently stopped seeing the mount (see
    ``permission_mode_wiring`` for the same note).
    """
    from latticeai.api.features import create_features_router

    service = get_feature_toggle_service(data_dir=data_dir, audit=append_audit_event)
    bind_feature_gates(service)
    state = getattr(app, "state", None)
    if state is not None and getattr(state, "_ltcai_features_mounted", False):
        return service
    app.include_router(
        create_features_router(service=service, require_user=require_user)
    )
    if state is not None:
        state._ltcai_features_mounted = True
    return service


__all__ = [
    "CHOICE_FEATURE",
    "GATE_BINDINGS",
    "bind_feature_gates",
    "get_feature_toggle_service",
    "register_features_router",
    "reset_feature_toggle_service",
    "unbind_feature_gates",
]
