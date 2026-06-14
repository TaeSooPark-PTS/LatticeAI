"""Configuration runtime assembly for the FastAPI composition root."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from latticeai.core.config import Config


def build_config_runtime(config: "Optional[Config]" = None) -> Dict[str, Any]:
    """Build app configuration values without importing model/runtime code."""

    from latticeai.core.config import Config

    cfg = config if config is not None else Config.from_env()
    return {
        "CONFIG": cfg,
        "APP_MODE": cfg.app_mode,
        "IS_PUBLIC_MODE": cfg.is_public,
        "DEFAULT_HOST": cfg.host,
        "DEFAULT_PORT": cfg.port,
        "NETWORK_EXPOSED": cfg.network_exposed,
        "ENABLE_TELEGRAM": cfg.enable_telegram,
        "ENABLE_GRAPH": cfg.enable_graph,
        "AUTOLOAD_MODELS": cfg.autoload_models,
        "MODEL_IDLE_UNLOAD_SECONDS": cfg.model_idle_unload_seconds,
        "ALLOW_LOCAL_MODELS": cfg.allow_local_models,
        "REQUIRE_AUTH": cfg.require_auth,
        "ALLOW_PLAINTEXT_API_KEYS": cfg.allow_plaintext_api_keys,
        "CORS_ALLOW_NETWORK": cfg.cors_allow_network,
        "CORS_EXTRA_ORIGINS": cfg.cors_extra_origins,
        "PUBLIC_MODEL": cfg.public_model,
        "LOCAL_MODEL": cfg.local_model,
        "LOCAL_DRAFT_MODEL": cfg.local_draft_model,
    }
