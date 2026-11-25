from __future__ import annotations

from typing import Any, Dict

from latticeai.runtime.runtime_context import RuntimeContext


def phase_features(ctx: RuntimeContext) -> None:
    ctx.enter("features")

    from latticeai.api.agent_worker_seam import create_agent_worker_seam_router
    from latticeai.api.health import create_health_router
    from latticeai.api.models import create_models_router
    from latticeai.api.search import create_search_router
    from latticeai.core.model_compat import list_cached_profiles
    from latticeai.runtime.access_runtime import is_externally_reachable
    from latticeai.runtime.platform_services_runtime import build_model_service
    from latticeai.services.model_runtime import (
        ENGINE_MODEL_CATALOG,
        MODEL_ENGINE_ALIASES,
        filter_lower_family_versions,
        sse_event,
    )
    from latticeai.services.search_service import SearchService
    from latticeai.services.tool_dispatch import DEFAULT_TOOL_DISPATCH_SERVICE
    from latticeai.tools import execute_tool

    app = ctx.app
    service = ctx.model_runtime_service
    model_service = build_model_service(
        model_router=ctx.model_router,
        runtime_features=service.runtime_features,
        is_public=ctx.IS_PUBLIC_MODE,
    )
    ctx.set(model_service=model_service)

    app.include_router(
        create_health_router(
            model_service=model_service,
            engine_status=service.engine_status,
            get_current_user=ctx.get_current_user,
            require_auth=ctx.REQUIRE_AUTH,
            externally_reachable=is_externally_reachable(ctx.CONFIG),
            app_version=ctx.APP_VERSION,
            app_mode=ctx.APP_MODE,
        )
    )

    app.include_router(
        create_models_router(
            model_router=ctx.model_router,
            require_user=ctx.require_user,
            require_admin=ctx.require_admin,
            prepare_and_load_model=service.prepare_and_load_model,
            prepare_and_load_model_stream=service.prepare_and_load_model_stream,
            sse_event=sse_event,
            engine_status=service.engine_status,
            filter_lower_family_versions=filter_lower_family_versions,
            list_compat_profiles=list_cached_profiles,
            engine_model_catalog=ENGINE_MODEL_CATALOG,
            model_engine_aliases=MODEL_ENGINE_ALIASES,
            is_public_mode=ctx.IS_PUBLIC_MODE,
            allow_local_models=ctx.ALLOW_LOCAL_MODELS,
            require_auth=ctx.REQUIRE_AUTH,
        )
    )

    def _embedding_info() -> Dict[str, Any]:
        from latticeai.core.embedding_providers import (
            PROVIDER_TYPES,
            embedding_provider_profiles,
        )

        info = ctx.EMBEDDER.as_dict()
        info["available_providers"] = list(PROVIDER_TYPES)
        info["profile"] = ctx.CONFIG.embedding_profile or ""
        info["profiles"] = embedding_provider_profiles()
        return info

    ctx.set(_embedding_info=_embedding_info)
    app.include_router(
        create_search_router(
            service=SearchService(embedder=ctx.EMBEDDER),
            require_user=ctx.require_user,
            embedding_info=_embedding_info,
        )
    )

    app.include_router(
        create_agent_worker_seam_router(
            model_router=ctx.model_router,
            dispatch_service=DEFAULT_TOOL_DISPATCH_SERVICE,
            execute_tool=execute_tool,
            hooks=None,
            require_user=ctx.require_user,
            enforce_rate_limit=ctx.enforce_rate_limit,
        )
    )
