from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Mapping, Optional

from latticeai.core.security import host_is_loopback

__all__ = [
    "Config",
    "SSO_CALLBACK_PATH",
    "default_data_dir",
    "default_sso_redirect_uri",
    "_value",
    "_str",
    "_bool",
    "_int",
]

SSO_CALLBACK_PATH = "/auth/sso/callback"


def default_sso_redirect_uri(port: int) -> str:
    return f"http://localhost:{port}{SSO_CALLBACK_PATH}"


DEFAULT_DATA_DIR_NAME = ".ltcai"


def default_data_dir(env: Optional[Mapping[str, str]] = None) -> Path:
    source = os.environ if env is None else env
    raw = (source.get("LATTICEAI_DATA_DIR") or "").strip()
    return Path(raw) if raw else Path.home() / DEFAULT_DATA_DIR_NAME


def _value(env: Mapping[str, str], key: str, default: str = "") -> str:
    return env.get(key) or default or ""


def _str(env: Mapping[str, str], key: str, default: str = "") -> str:
    raw = env.get(key)
    return raw if raw is not None else default


def _bool(env: Mapping[str, str], key: str, default: bool = False) -> bool:
    raw = env.get(key)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        return default


def _port(env: Mapping[str, str], key: str, default: int) -> int:
    port = _int(env, key, default)
    if 1 <= port <= 65535:
        return port
    return default


@dataclass(frozen=True)
class Config:
    app_mode: str
    is_public: bool
    host: str
    port: int
    network_exposed: bool
    timezone: str

    enable_telegram: bool
    enable_graph: bool
    autoload_models: bool
    model_idle_unload_seconds: int
    allow_local_models: bool

    require_auth: bool
    allow_plaintext_api_keys: bool
    cors_allow_network: bool
    cors_extra_origins: List[str]
    csrf_trusted_origins: List[str]
    rate_limit_enabled: bool
    open_registration: bool
    invite_code: str
    invite_cookie_secret: str
    invite_gate_enabled: bool
    admin_emails: List[str]
    trusted_proxies: List[str]

    public_model: str
    local_model: str
    local_draft_model: str
    auto_read_chat_paths: bool
    max_local_models: int
    allow_model_downloads: bool
    model_download_timeout: int

    embedding_provider: str
    embedding_profile: str
    embedding_model: str
    embedding_base_url: str
    embedding_api_key: str
    embedding_dim: int
    embedding_timeout: int
    embedding_custom_target: str

    storage_engine: str
    postgres_dsn: str
    postgres_schema: str

    sso_discovery_url: str
    sso_client_id: str
    sso_client_secret: str
    sso_redirect_uri: str
    sso_provider_name: str

    discord_permission_webhook: str
    discord_bot_token: str
    discord_permission_channel: str
    permission_monitor_secret: str

    data_dir: Path
    static_dir: Path

    @classmethod
    def from_env(
        cls, env: Optional[Mapping[str, str]] = None, *, base_dir: Optional[Path] = None
    ) -> "Config":
        if env is None:
            import os

            env = os.environ
        if base_dir is None:
            base_dir = Path(__file__).resolve().parent.parent.parent

        app_mode = _value(env, "LATTICEAI_MODE", "local").lower()
        if app_mode not in {"local", "public"}:
            app_mode = "local"
        is_public = app_mode == "public"

        host = _value(env, "LATTICEAI_HOST", "127.0.0.1")
        port = _port(env, "LATTICEAI_PORT", 4825)
        network_exposed = not host_is_loopback(host)
        externally_reachable = is_public or network_exposed

        cors_extra = [
            item.strip()
            for item in _value(env, "LATTICEAI_CORS_ALLOWED_ORIGINS", "").split(",")
            if item.strip()
        ]
        csrf_trusted_origins = [
            item.strip()
            for item in _value(env, "LATTICEAI_CSRF_TRUSTED_ORIGINS", "").split(",")
            if item.strip()
        ]
        admin_emails = [
            item.strip().lower()
            for item in _value(env, "LATTICEAI_ADMIN_EMAILS", "").split(",")
            if item.strip()
        ]
        trusted_proxies = [
            item.strip()
            for item in _value(env, "LATTICEAI_TRUSTED_PROXIES", "").split(",")
            if item.strip()
        ]

        public_model = _value(
            env,
            "LATTICEAI_PUBLIC_MODEL",
            _value(env, "LATTICEAI_DEFAULT_MODEL", "openai:gpt-4o-mini"),
        )
        local_model = _value(
            env, "LATTICEAI_LOCAL_MODEL", "mlx-community/gemma-4-12B-it-4bit"
        )

        data_dir = default_data_dir(env)
        static_dir = Path(_value(env, "LATTICEAI_STATIC_DIR", str(base_dir / "static")))
        if not static_dir.exists():
            packaged_static = Path(sys.prefix) / "static"
            if packaged_static.exists():
                static_dir = packaged_static

        default_sso_redirect = default_sso_redirect_uri(port)

        return cls(
            app_mode=app_mode,
            is_public=is_public,
            host=host,
            port=port,
            network_exposed=network_exposed,
            timezone=_value(env, "LATTICE_TZ", "UTC") or "UTC",
            enable_telegram=_bool(env, "LATTICEAI_ENABLE_TELEGRAM", default=False),
            enable_graph=_bool(env, "LATTICEAI_ENABLE_GRAPH", default=True),
            autoload_models=_bool(env, "LATTICEAI_AUTOLOAD_MODELS", default=is_public),
            model_idle_unload_seconds=_int(
                env, "LATTICEAI_MODEL_IDLE_UNLOAD_SECONDS", 0
            ),
            allow_local_models=_bool(
                env, "LATTICEAI_ALLOW_LOCAL_MODELS", default=not is_public
            ),
            require_auth=(
                True
                if externally_reachable
                else _bool(env, "LATTICEAI_REQUIRE_AUTH", default=False)
            ),
            allow_plaintext_api_keys=_bool(
                env, "LATTICEAI_ALLOW_PLAINTEXT_API_KEYS", default=False
            ),
            cors_allow_network=_bool(
                env, "LATTICEAI_CORS_ALLOW_NETWORK", default=False
            ),
            cors_extra_origins=cors_extra,
            csrf_trusted_origins=csrf_trusted_origins,
            rate_limit_enabled=_str(env, "LATTICEAI_RATE_LIMIT", "1") != "0",
            open_registration=(
                False
                if externally_reachable
                else _bool(env, "LATTICEAI_OPEN_REGISTRATION", default=True)
            ),
            invite_code=_value(env, "LATTICEAI_INVITE_CODE", ""),
            invite_cookie_secret=_value(env, "LATTICEAI_INVITE_COOKIE_SECRET", ""),
            invite_gate_enabled=_bool(
                env, "LATTICEAI_INVITE_GATE_ENABLED", default=False
            ),
            admin_emails=admin_emails,
            trusted_proxies=trusted_proxies,
            public_model=public_model,
            local_model=local_model,
            local_draft_model=_value(env, "LATTICEAI_LOCAL_DRAFT_MODEL", ""),
            auto_read_chat_paths=_bool(
                env, "LATTICEAI_AUTO_READ_CHAT_PATHS", default=False
            ),
            max_local_models=_int(env, "LATTICEAI_MAX_LOCAL_MODELS", 1),
            allow_model_downloads=_bool(
                env, "LATTICEAI_ALLOW_MODEL_DOWNLOADS", default=False
            ),
            model_download_timeout=_int(env, "LATTICEAI_MODEL_DOWNLOAD_TIMEOUT", 300),
            embedding_provider=_value(env, "LATTICEAI_EMBEDDING_PROVIDER", "hash")
            .strip()
            .lower(),
            embedding_profile=_value(env, "LATTICEAI_EMBEDDING_PROFILE", "")
            .strip()
            .lower(),
            embedding_model=_value(env, "LATTICEAI_EMBEDDING_MODEL", ""),
            embedding_base_url=_value(env, "LATTICEAI_EMBEDDING_BASE_URL", ""),
            embedding_api_key=_value(env, "LATTICEAI_EMBEDDING_API_KEY", ""),
            embedding_dim=_int(env, "LATTICEAI_VECTOR_DIM", 0),
            embedding_timeout=_int(env, "LATTICEAI_EMBEDDING_TIMEOUT", 30),
            embedding_custom_target=_value(
                env, "LATTICEAI_EMBEDDING_CUSTOM_TARGET", ""
            ),
            storage_engine=_value(env, "LATTICEAI_STORAGE_ENGINE", "sqlite")
            .strip()
            .lower()
            or "sqlite",
            postgres_dsn=_value(env, "LATTICEAI_POSTGRES_DSN", ""),
            postgres_schema=_value(env, "LATTICEAI_POSTGRES_SCHEMA", "lattice_brain"),
            sso_discovery_url=_value(env, "OIDC_DISCOVERY_URL", ""),
            sso_client_id=_value(env, "OIDC_CLIENT_ID", ""),
            sso_client_secret=_value(env, "OIDC_CLIENT_SECRET", ""),
            sso_redirect_uri=_value(env, "OIDC_REDIRECT_URI", default_sso_redirect),
            sso_provider_name=_value(env, "OIDC_PROVIDER_NAME", "SSO"),
            discord_permission_webhook=_value(
                env, "LATTICEAI_DISCORD_PERMISSION_WEBHOOK", ""
            ),
            discord_bot_token=_value(env, "LATTICEAI_DISCORD_BOT_TOKEN", ""),
            discord_permission_channel=_value(
                env, "LATTICEAI_DISCORD_PERMISSION_CHANNEL", ""
            ),
            permission_monitor_secret=_value(env, "LATTICEAI_PERMISSION_SECRET", ""),
            data_dir=data_dir,
            static_dir=static_dir,
        )
