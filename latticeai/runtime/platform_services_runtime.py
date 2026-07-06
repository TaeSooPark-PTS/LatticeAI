"""Small platform service construction seams for app startup."""

from __future__ import annotations

from typing import Any


def build_model_service(
    *,
    model_router: Any,
    runtime_features: Any,
    is_public: bool,
) -> Any:
    """Construct the health/model summary service."""

    from latticeai.services.model_service import ModelService

    return ModelService(
        model_router=model_router,
        runtime_features=runtime_features,
        is_public=is_public,
    )


def build_brain_network(
    *,
    identity: Any,
    portability: Any,
    data_dir: Any,
) -> Any:
    """Construct peer sync/network service for brain portability routes."""

    from lattice_brain.graph.network import BrainNetwork

    return BrainNetwork(
        identity=identity,
        portability=portability,
        data_dir=data_dir,
    )
