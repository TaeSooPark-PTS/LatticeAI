from __future__ import annotations

import logging
from typing import List

from latticeai.runtime.runtime_context import RuntimeContext


def phase_domain(ctx: RuntimeContext) -> None:
    ctx.enter("domain")

    from lattice_brain.graph.runtime import set_llm_router
    from latticeai.models.router import LLMRouter
    from latticeai.services.tool_dispatch import configure_tool_dispatch

    model_router = LLMRouter()
    set_llm_router(model_router)
    configure_tool_dispatch(load_users=ctx.load_users, get_user_role=ctx.get_user_role)
    ctx.set(model_router=model_router)


def phase_web(ctx: RuntimeContext) -> None:
    ctx.enter("web")

    from latticeai.runtime.lifespan_runtime import build_lifespan_runtime
    from latticeai.services.model_runtime import (
        LOCAL_SERVER_PROCESSES,
        build_model_runtime,
    )
    from latticeai.tools import ensure_agent_root

    lifespan_runtime = build_lifespan_runtime(
        app_mode=ctx.APP_MODE,
        autoload_models=ctx.AUTOLOAD_MODELS,
        is_public_mode=ctx.IS_PUBLIC_MODE,
        public_model=ctx.PUBLIC_MODEL,
        allow_local_models=ctx.ALLOW_LOCAL_MODELS,
        local_model=ctx.LOCAL_MODEL,
        local_draft_model=ctx.LOCAL_DRAFT_MODEL,
        model_idle_unload_seconds=ctx.MODEL_IDLE_UNLOAD_SECONDS,
        model_router=ctx.model_router,
        local_server_processes=LOCAL_SERVER_PROCESSES,
        logger=logging,
    )
    ctx.adopt(lifespan_runtime, "_spawn", "lifespan")

    ctx.set(app=build_worker_app_shell(ctx))
    ensure_agent_root()

    ctx.set(
        model_runtime_service=build_model_runtime(
            router=ctx.model_router,
            APP_MODE=ctx.APP_MODE,
            DEFAULT_HOST=ctx.DEFAULT_HOST,
            DEFAULT_PORT=ctx.DEFAULT_PORT,
            DATA_DIR=ctx.DATA_DIR,
            BASE_DIR=ctx.BASE_DIR,
            ENABLE_GRAPH=ctx.ENABLE_GRAPH,
            AUTOLOAD_MODELS=ctx.AUTOLOAD_MODELS,
            MODEL_IDLE_UNLOAD_SECONDS=ctx.MODEL_IDLE_UNLOAD_SECONDS,
            ALLOW_MODEL_DOWNLOADS=ctx.ALLOW_MODEL_DOWNLOADS,
            MODEL_DOWNLOAD_TIMEOUT=ctx.MODEL_DOWNLOAD_TIMEOUT,
            ALLOW_LOCAL_MODELS=ctx.ALLOW_LOCAL_MODELS,
            REQUIRE_AUTH=ctx.REQUIRE_AUTH,
            ALLOW_PLAINTEXT_API_KEYS=ctx.ALLOW_PLAINTEXT_API_KEYS,
            CORS_ALLOW_NETWORK=ctx.CORS_ALLOW_NETWORK,
            PUBLIC_MODEL=ctx.PUBLIC_MODEL,
            LOCAL_MODEL=ctx.LOCAL_MODEL,
            IS_PUBLIC_MODE=ctx.IS_PUBLIC_MODE,
            keyring=ctx.keyring,
            get_current_user=ctx.get_current_user,
        )
    )


def build_worker_app_shell(ctx: RuntimeContext):
    from fastapi import FastAPI

    from latticeai.core.csrf import CSRFOriginGuardMiddleware, CSRFOriginPolicy
    from latticeai.core.security import host_is_loopback

    app = FastAPI(
        title=f"Lattice AI Server ({ctx.APP_MODE})",
        version=ctx.APP_VERSION,
        lifespan=ctx.lifespan,
    )

    cors_allowed_origins = [
        f"http://localhost:{ctx.DEFAULT_PORT}",
        f"http://127.0.0.1:{ctx.DEFAULT_PORT}",
        *ctx.CORS_EXTRA_ORIGINS,
    ]
    if ctx.CORS_ALLOW_NETWORK:
        cors_allowed_origins = cors_allowed_origins + [
            f"http://{ctx.DEFAULT_HOST}:{ctx.DEFAULT_PORT}",
            f"https://{ctx.DEFAULT_HOST}:{ctx.DEFAULT_PORT}",
        ]

    csrf_allowed_origins: List[str] = [
        *cors_allowed_origins,
        *ctx.CSRF_TRUSTED_ORIGINS,
    ]
    csrf_policy = CSRFOriginPolicy(
        trusted_origins=csrf_allowed_origins,
        server_host=ctx.DEFAULT_HOST,
        server_port=ctx.DEFAULT_PORT,
        bind_is_loopback=host_is_loopback(ctx.DEFAULT_HOST),
    )

    app.add_middleware(CSRFOriginGuardMiddleware, policy=csrf_policy)
    _add_cors(app, cors_allowed_origins)

    ctx.set(
        CORS_ALLOWED_ORIGINS=cors_allowed_origins,
        CSRF_ALLOWED_ORIGINS=csrf_allowed_origins,
    )
    return app


def _add_cors(app, origins: List[str]) -> None:
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )
