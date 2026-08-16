"""Phases 1-4 of the AI-Worker build: platform, configuration, identity, brain.

The first half of the build order — everything that must exist before a
FastAPI application object is worth creating.

v11.6.0 (WP-P1) cut these four phases down to what a **pure compute worker**
needs. The product application built users, sessions, SSO, VPC config, the MCP
install state, the audit log, the knowledge-graph store, the conversation
store, the workspace OS and a dozen platform services here; ``lattice-host``
owns every one of those now, and Python owns no durable state at all. What
survives is the smallest set that still lets this process infer, embed, parse,
render and transcribe:

* the MLX device (phase 1),
* configuration and the data directory the model cache lives under (phase 2),
* the **seam gate** — ``require_user``, the rate limiter, the loopback posture
  (phase 3). It is a check, not a store: durable sessions and users are
  ``lattice-auth``'s,
* the resolved embedder and the multi-modal ports (phase 4), which is all the
  compute seams need.

Every heavy import lives *inside* a phase, never at module scope
(``tests/unit/test_runtime_context.py`` enforces that for each submodule).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from latticeai.core.quiet import quiet
from latticeai.runtime.runtime_context import RuntimeContext


# ── phase 1: platform ────────────────────────────────────────────────────────
def phase_platform(ctx: RuntimeContext) -> None:
    """Select the MLX Metal device in the main thread.

    Kept first and alone: it is the one step that talks to hardware, and it
    must happen on the main thread before any worker touches MLX.
    """
    ctx.enter("platform")
    try:
        import mlx.core as mx

        mx.set_default_device(mx.gpu)  # type: ignore[arg-type]
        print("✅ MLX Metal context initialized in main thread.")
    except Exception as exc:
        print(f"⚠️ MLX Metal context unavailable: {exc}")
        mx = None  # type: ignore[assignment]
    ctx.set(mx=mx)


# ── phase 2: configuration and paths ─────────────────────────────────────────
def phase_config(ctx: RuntimeContext) -> None:
    """Parse configuration once and lay out the data directory."""
    ctx.enter("config")

    from latticeai import __version__ as app_version
    from latticeai.runtime.config_runtime import build_config_runtime
    from latticeai.runtime.security_runtime import build_security_runtime

    try:
        import keyring
    except Exception:
        keyring = None  # type: ignore[assignment]

    config_runtime = build_config_runtime(ctx.config_arg)
    ctx.set(config_runtime=config_runtime, keyring=keyring)
    ctx.adopt(
        config_runtime,
        "CONFIG",
        "APP_MODE",
        "IS_PUBLIC_MODE",
        "DEFAULT_HOST",
        "DEFAULT_PORT",
        "ENABLE_GRAPH",
        "AUTOLOAD_MODELS",
        "MODEL_IDLE_UNLOAD_SECONDS",
        "ALLOW_MODEL_DOWNLOADS",
        "MODEL_DOWNLOAD_TIMEOUT",
        "ALLOW_LOCAL_MODELS",
        "REQUIRE_AUTH",
        "ALLOW_PLAINTEXT_API_KEYS",
        "CORS_ALLOW_NETWORK",
        "CORS_EXTRA_ORIGINS",
        "CSRF_TRUSTED_ORIGINS",
        "PUBLIC_MODEL",
        "LOCAL_MODEL",
        "LOCAL_DRAFT_MODEL",
    )
    # The version the worker reports on ``/health``. It used to be read off the
    # Workspace OS store's schema constant; that store is native platform state
    # now, and the package version was always the same number.
    ctx.set(APP_VERSION=app_version)

    security_runtime = build_security_runtime(ctx.CONFIG)
    ctx.set(security_runtime=security_runtime)
    ctx.adopt(security_runtime, "RATE_LIMIT_ENABLED")
    ctx.set(_RATE_LIMIT_ENABLED=ctx.RATE_LIMIT_ENABLED)

    from pathlib import Path

    base_dir = Path(__file__).resolve().parent.parent.parent
    data_dir = ctx.CONFIG.data_dir
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        data_dir.chmod(0o700)
    except OSError:
        quiet()

    ctx.set(
        BASE_DIR=base_dir,
        DATA_DIR=data_dir,
        STATIC_DIR=ctx.CONFIG.static_dir,
        USERS_FILE=data_dir / "users.json",
    )


# ── phase 3: the seam gate ───────────────────────────────────────────────────
def phase_identity(ctx: RuntimeContext) -> None:
    """Who is calling, and may they call this often.

    Not an identity *store*: the worker reads ``users.json`` to answer "what
    role does this session's email have" and holds session tokens in memory for
    the life of the process. Registration, password hashing, invitations, SSO
    and the audit log went to ``lattice-auth`` and ``lattice-platform`` with
    every route that used them.
    """
    ctx.enter("identity")

    from fastapi import HTTPException, Request

    from latticeai.core.security import enforce_rate_limit as _enforce_rate_limit
    from latticeai.core.users import load_users_file
    from latticeai.core.users import user_id_for_email as _user_id_for_email
    from latticeai.runtime.access_runtime import build_access_runtime
    from latticeai.runtime.bootstrap import build_session_runtime

    def load_users() -> Dict[str, Any]:
        return load_users_file(ctx.USERS_FILE)

    def user_id_for_email(email: Optional[str]) -> Optional[str]:
        return _user_id_for_email(load_users(), email)

    def enforce_rate_limit(email: str, bucket_key: str) -> None:
        _enforce_rate_limit(email, bucket_key, enabled=ctx._RATE_LIMIT_ENABLED)

    ctx.set(
        load_users=load_users,
        user_id_for_email=user_id_for_email,
        enforce_rate_limit=enforce_rate_limit,
    )

    # Session token lifecycle; user_id_for_email is the injected subject resolver.
    session_runtime = build_session_runtime(user_id_resolver=user_id_for_email)
    ctx.adopt(session_runtime, "_session_store", "get_session_email")

    access_runtime = build_access_runtime(
        config=ctx.CONFIG,
        require_auth=ctx.REQUIRE_AUTH,
        http_exception=HTTPException,
        request_type=Request,
        load_users=load_users,
        get_session_email=ctx.get_session_email,
        user_id_for_email=_user_id_for_email,
    )
    ctx.adopt(
        access_runtime,
        "get_user_role",
        "_extract_bearer_token",
        "get_current_user",
        "require_user",
        "require_admin",
    )


# ── phase 4: the compute ports ───────────────────────────────────────────────
def phase_brain(ctx: RuntimeContext) -> None:
    """The embedder and the multi-modal ports — the worker's whole Brain half.

    ``phase_brain`` used to open ``knowledge_graph.sqlite``, build the
    conversation store, the workspace OS, the plugin registry, the memory
    service and the ingest write door. Rust owns every one of those writes
    (§Wave 2.5), so what is left is the two *ports* the compute seams need:
    something that turns text into a vector, and something that can look at a
    picture or listen to a recording. Both degrade to an honest absence rather
    than failing construction.
    """
    ctx.enter("brain")

    from latticeai.core.embedding_providers import (
        resolve_embedder,
        resolve_embedding_profile,
    )
    from latticeai.runtime.brain_runtime import build_embedder_runtime
    from latticeai.services.multimodal_ports import build_multimodal_ports
    from latticeai.services.voice_capture import VoiceCaptureService

    # Resolve the configured embedding provider once. Degrades to the offline
    # hash fallback when unavailable, recording requested-vs-active provider.
    try:
        embedding_profile = resolve_embedding_profile(ctx.CONFIG.embedding_profile)
    except ValueError as exc:
        logging.warning("Embedding profile ignored: %s", exc)
        embedding_profile = {}

    embedder = build_embedder_runtime(
        config=ctx.CONFIG,
        profile=embedding_profile,
        resolve_embedder=resolve_embedder,
    )
    if embedder.fell_back:
        logging.warning(
            "Embedding provider %s unavailable: %s", embedder.requested, embedder.detail
        )
    ctx.set(EMBEDDING_PROFILE=embedding_profile, EMBEDDER=embedder)

    # Multi-modal capture (v11.1.0): off unless the user turned it on, and even
    # then only as capable as the models actually present. With nothing
    # configured this resolves to an empty bundle without importing or loading
    # anything.
    #
    # The transcriber is resolved through the voice service so a voice memo and
    # a scanned ``.m4a`` are transcribed by the same thing — or, far more often,
    # by the same nothing.
    #
    # The service itself is not published on the context: v11.8.0 deleted its
    # one route (``GET /api/capture/voice/status``, which nothing called), so
    # what the rest of the build needs from it is the port it resolves, and
    # that travels in ``MULTIMODAL_PORTS``.
    voice_capture = VoiceCaptureService(transcriber=None)
    multimodal_ports = build_multimodal_ports(
        transcriber=voice_capture.multimodal_ports().transcriber
    )
    ctx.set(MULTIMODAL_PORTS=multimodal_ports)
