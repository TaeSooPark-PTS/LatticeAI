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


__all__ = ["build_model_service"]
