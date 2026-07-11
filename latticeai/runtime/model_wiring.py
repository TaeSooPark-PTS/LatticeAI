"""Model/runtime wiring seam for ``latticeai.app_factory``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from latticeai.runtime.platform_services_runtime import build_model_service


@dataclass(frozen=True)
class ModelRuntime:
    """Typed model-provider stage registered with the HTTP application."""

    router: Any
    runtime_service: Any
    service: Any
    runtime_features: Any
    is_public: bool


def configure_model_runtime_from_context(**kwargs: Any) -> Any:
    """Build and return an isolated model runtime from app-owned values."""

    build_model_runtime = kwargs.pop("build_model_runtime")
    return build_model_runtime(**kwargs)


def register_model_runtime_routers(
    *,
    app: Any,
    create_health_router: Any,
    create_models_router: Any,
    register_health_and_model_routers: Any,
    model_router: Any,
    runtime_service: Any,
    runtime_features: Any,
    is_public_mode: bool,
    **kwargs: Any,
) -> ModelRuntime:
    model_service = build_model_service(
        model_router=model_router,
        runtime_features=runtime_features,
        is_public=is_public_mode,
    )
    register_health_and_model_routers(
        app,
        create_health_router=create_health_router,
        model_service=model_service,
        create_models_router=create_models_router,
        model_router=model_router,
        is_public_mode=is_public_mode,
        **kwargs,
    )
    return ModelRuntime(
        router=model_router,
        runtime_service=runtime_service,
        service=model_service,
        runtime_features=runtime_features,
        is_public=is_public_mode,
    )


__all__ = [
    "ModelRuntime",
    "configure_model_runtime_from_context",
    "register_model_runtime_routers",
]
