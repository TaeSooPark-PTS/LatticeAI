"""Configuration runtime assembly for the FastAPI composition root."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from latticeai.runtime.stages import RuntimeStage

if TYPE_CHECKING:
    from latticeai.core.config import Config


@dataclass(frozen=True)
class ConfigRuntime(RuntimeStage):
    CONFIG: Any
    APP_MODE: str
    IS_PUBLIC_MODE: bool
    DEFAULT_HOST: str
    DEFAULT_PORT: int
    NETWORK_EXPOSED: bool
    ENABLE_TELEGRAM: bool
    ENABLE_GRAPH: bool
    AUTOLOAD_MODELS: bool
    MODEL_IDLE_UNLOAD_SECONDS: int
    ALLOW_LOCAL_MODELS: bool
    REQUIRE_AUTH: bool
    ALLOW_PLAINTEXT_API_KEYS: bool
    CORS_ALLOW_NETWORK: bool
    CORS_EXTRA_ORIGINS: Any
    PUBLIC_MODEL: str
    LOCAL_MODEL: str
    LOCAL_DRAFT_MODEL: str
    TIMEZONE: str
    MAX_LOCAL_MODELS: int
    ALLOW_MODEL_DOWNLOADS: bool
    MODEL_DOWNLOAD_TIMEOUT: int


def build_config_runtime(config: "Optional[Config]" = None) -> ConfigRuntime:
    """Build app configuration values without importing model/runtime code."""

    from latticeai.core.config import Config

    cfg = config if config is not None else Config.from_env()
    return ConfigRuntime(
        CONFIG=cfg,
        APP_MODE=cfg.app_mode,
        IS_PUBLIC_MODE=cfg.is_public,
        DEFAULT_HOST=cfg.host,
        DEFAULT_PORT=cfg.port,
        NETWORK_EXPOSED=cfg.network_exposed,
        ENABLE_TELEGRAM=cfg.enable_telegram,
        ENABLE_GRAPH=cfg.enable_graph,
        AUTOLOAD_MODELS=cfg.autoload_models,
        MODEL_IDLE_UNLOAD_SECONDS=cfg.model_idle_unload_seconds,
        ALLOW_LOCAL_MODELS=cfg.allow_local_models,
        REQUIRE_AUTH=cfg.require_auth,
        ALLOW_PLAINTEXT_API_KEYS=cfg.allow_plaintext_api_keys,
        CORS_ALLOW_NETWORK=cfg.cors_allow_network,
        CORS_EXTRA_ORIGINS=cfg.cors_extra_origins,
        PUBLIC_MODEL=cfg.public_model,
        LOCAL_MODEL=cfg.local_model,
        LOCAL_DRAFT_MODEL=cfg.local_draft_model,
        TIMEZONE=cfg.timezone,
        MAX_LOCAL_MODELS=cfg.max_local_models,
        ALLOW_MODEL_DOWNLOADS=cfg.allow_model_downloads,
        MODEL_DOWNLOAD_TIMEOUT=cfg.model_download_timeout,
    )


__all__ = ["ConfigRuntime", "build_config_runtime"]
