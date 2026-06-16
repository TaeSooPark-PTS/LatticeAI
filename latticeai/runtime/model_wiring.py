"""Model/runtime wiring seam for ``latticeai.app_factory``."""

from __future__ import annotations

from typing import Any

from latticeai.runtime.platform_services_runtime import build_model_service


def configure_model_runtime_from_context(**kwargs: Any) -> None:
    configure_model_runtime = kwargs.pop("configure_model_runtime")
    configure_model_runtime(**kwargs)


def register_model_runtime_routers(
    *,
    app: Any,
    create_health_router: Any,
    create_models_router: Any,
    register_health_and_model_routers: Any,
    model_router: Any,
    runtime_features: Any,
    is_public_mode: bool,
    **kwargs: Any,
) -> Any:
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
    return model_service
