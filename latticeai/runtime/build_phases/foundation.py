from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from latticeai.core.quiet import quiet
from latticeai.runtime.runtime_context import RuntimeContext


def phase_platform(ctx: RuntimeContext) -> None:
    ctx.enter("platform")
    try:
        import mlx.core as mx

        mx.set_default_device(mx.gpu)
        print("✅ MLX Metal context initialized in main thread.")
    except (ImportError, OSError, RuntimeError, AttributeError) as exc:
        print(f"⚠️ MLX Metal context unavailable: {exc}")
        mx = None
    ctx.set(mx=mx)


def phase_config(ctx: RuntimeContext) -> None:
    ctx.enter("config")

    from latticeai import __version__ as app_version
    from latticeai.runtime.config_runtime import build_config_runtime
    from latticeai.runtime.security_runtime import build_security_runtime

    keyring: Any
    try:
        import keyring
    except (ImportError, OSError, RuntimeError):
        keyring = None

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


def phase_identity(ctx: RuntimeContext) -> None:
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


def phase_brain(ctx: RuntimeContext) -> None:
    ctx.enter("brain")

    from latticeai.core.embedding_providers import (
        resolve_embedder,
        resolve_embedding_profile,
    )
    from latticeai.runtime.brain_runtime import build_embedder_runtime
    from latticeai.services.multimodal_ports import build_multimodal_ports
    from latticeai.services.voice_capture import VoiceCaptureService

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

    voice_capture = VoiceCaptureService(transcriber=None)
    multimodal_ports = build_multimodal_ports(
        transcriber=voice_capture.multimodal_ports().transcriber
    )
    ctx.set(MULTIMODAL_PORTS=multimodal_ports)
