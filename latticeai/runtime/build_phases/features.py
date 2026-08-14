"""Phase 7 of the AI-Worker build: the compute routers.

The tail of the build order — everything that mounts a router on the finished
application. WP-P1 replaced two phases (``platform_features`` and
``interaction``, ~34 product routers between them) with this one, because the
worker mounts six things:

* ``/health`` — the posture the supervisor gates on;
* the MLX model lifecycle (``/models*``, ``/engines/*``);
* the embedder report (``/api/embeddings/{status,providers}``);
* the document parser (``POST /tools/read_document``, ``GET /tools/pdf_pages``);
* the two capability probes (``/api/ingestion/multimodal``,
  ``/api/capture/voice/status``);
* the Rust loop's seam (``POST /agent/llm``, ``POST /agent/tool``).

The ``/worker/*`` seams are not here: they are mounted by
:func:`~latticeai.runtime.build_phases.worker_profile.phase_worker_routes`,
which runs after this phase and is the module that owns the worker contract.

Every heavy import lives *inside* a phase, never at module scope.
"""

from __future__ import annotations

from typing import Any, Dict

from latticeai.runtime.runtime_context import RuntimeContext


def phase_features(ctx: RuntimeContext) -> None:
    """Mount the worker's compute routers on the application object."""
    ctx.enter("features")

    from latticeai.api.agent_worker_seam import create_agent_worker_seam_router
    from latticeai.api.health import create_health_router
    from latticeai.api.local_files import create_local_files_router
    from latticeai.api.models import create_models_router
    from latticeai.api.search import create_search_router
    from latticeai.api.tools import create_tools_router
    from latticeai.api.voice_capture import create_voice_capture_router
    from latticeai.core.model_compat import list_cached_profiles
    from latticeai.runtime.access_runtime import is_externally_reachable
    from latticeai.runtime.platform_services_runtime import build_model_service
    from latticeai.services.model_runtime import (
        ENGINE_MODEL_CATALOG,
        MODEL_ENGINE_ALIASES,
        download_hf_model,
        ensure_ollama_server,
        filter_lower_family_versions,
        local_binary,
        normalize_local_model_request,
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
            normalize_local_model_request=normalize_local_model_request,
            download_hf_model=download_hf_model,
            prepare_and_load_model=service.prepare_and_load_model,
            prepare_and_load_model_stream=service.prepare_and_load_model_stream,
            sse_event=sse_event,
            ensure_ollama_server=ensure_ollama_server,
            local_binary=local_binary,
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
        create_tools_router(require_user=ctx.require_user)
    )
    app.include_router(
        create_local_files_router(
            require_user=ctx.require_user, ingestion_pipeline=ctx.INGESTION_PIPELINE
        )
    )
    app.include_router(
        create_voice_capture_router(
            service=ctx.VOICE_CAPTURE, require_user=ctx.require_user
        )
    )

    # AI-Worker seam (v11.5.1, plan §Y1): the calls the Rust agent loop makes
    # back into Python once orchestration lives in ``lattice-agent``. The tool
    # route stays behind ``LATTICEAI_AGENT_TOOL_SEAM=1``, which only
    # lattice-host injects into a worker it started.
    app.include_router(
        create_agent_worker_seam_router(
            model_router=ctx.model_router,
            dispatch_service=DEFAULT_TOOL_DISPATCH_SERVICE,
            execute_tool=execute_tool,
            # No registry: the hooks platform (persistence, ordering, the
            # built-in runners that bound to the audit log) is native platform
            # state now, and ``dispatch_tool(None, …)`` is a transparent
            # pass-through rather than a pretend lifecycle.
            hooks=None,
            require_user=ctx.require_user,
            enforce_rate_limit=ctx.enforce_rate_limit,
        )
    )
