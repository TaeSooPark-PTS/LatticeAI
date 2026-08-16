"""Phases 5-6 of the AI-Worker build: domain singletons and the web shell.

``phase_domain`` runs before ``phase_web`` because the lifespan and the model
runtime service are both wired against the router it builds.

WP-P1 removed the product half of both phases: the chat service, the garden,
the telegram mirror, the typed ``AppContext``, the auth/admin/security/static
routers and the workspace mount all went to ``lattice-host``. What is left is
the LLM router (the thing this process exists to run), the tool-dispatch
whitelist, and the FastAPI object with the two middlewares a proxied browser
write still passes through.

Every heavy import lives *inside* a phase, never at module scope.
"""

from __future__ import annotations

import logging
from typing import List

from latticeai.runtime.runtime_context import RuntimeContext


# ── phase 5: domain singletons (must precede the web app) ────────────────────
def phase_domain(ctx: RuntimeContext) -> None:
    """The model router and the tool-dispatch policy.

    Separated from ``phase_web`` because the lifespan hooks and the model
    runtime service are both wired against the router.
    """
    ctx.enter("domain")

    from lattice_brain.graph.runtime import set_llm_router
    from latticeai.models.router import LLMRouter
    from latticeai.services.tool_dispatch import configure_tool_dispatch

    model_router = LLMRouter()
    set_llm_router(model_router)
    configure_tool_dispatch(load_users=ctx.load_users, get_user_role=ctx.get_user_role)
    ctx.set(model_router=model_router)


# ── phase 6: the web shell ───────────────────────────────────────────────────
def phase_web(ctx: RuntimeContext) -> None:
    """Lifespan, the FastAPI application object, the model runtime service."""
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
    """The FastAPI object plus the two middlewares a proxied write still meets.

    Moved here from ``runtime/web_runtime.py`` (deleted with the router
    registration it existed beside). The static mounts went with it: a worker
    serves no UI, and the worker profile dropped those mounts anyway.

    Both middlewares are load-bearing on a worker, which is not obvious:

    * **CORS** stays outside the CSRF guard so a rejected request still comes
      back with the headers a browser needs to surface the 403 rather than an
      opaque network error.
    * **The CSRF origin guard** is what makes ``LATTICEAI_CSRF_TRUSTED_ORIGINS``
      load-bearing for the gateway (v11.6.0 gateway integration §3): a proxied
      browser write — ``POST /models/load``, ``POST /engines/prepare-model``,
      ``DELETE /models/unload/{model_id}`` — arrives carrying the browser's
      session cookie *and* ``Origin: …:{gateway port}``, and without the host's
      injected trust list this guard would answer 403.
    """
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

    # An origin that CORS already lets send *credentialed* cross-origin
    # requests is, by that decision, trusted with the session cookie; listing
    # it again here would be a second place to forget. The explicit
    # LATTICEAI_CSRF_TRUSTED_ORIGINS entries are for the reverse-proxy case —
    # and, since v11.6.0, for the gateway that fronts this worker.
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

    # Registration order is stack order in reverse: the LAST middleware added
    # is the OUTERMOST.
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
