from __future__ import annotations

from typing import Any, Dict, Set


class RuntimeContext:
    mx: Any

    config_arg: Any
    config_runtime: Any
    security_runtime: Any
    CONFIG: Any
    APP_VERSION: str
    APP_MODE: str
    IS_PUBLIC_MODE: bool
    DEFAULT_HOST: str
    DEFAULT_PORT: int
    ENABLE_GRAPH: bool
    AUTOLOAD_MODELS: bool
    MODEL_IDLE_UNLOAD_SECONDS: Any
    ALLOW_MODEL_DOWNLOADS: bool
    MODEL_DOWNLOAD_TIMEOUT: Any
    ALLOW_LOCAL_MODELS: bool
    REQUIRE_AUTH: bool
    ALLOW_PLAINTEXT_API_KEYS: bool
    CORS_ALLOW_NETWORK: bool
    CORS_EXTRA_ORIGINS: Any
    CSRF_TRUSTED_ORIGINS: Any
    PUBLIC_MODEL: str
    LOCAL_MODEL: Any
    LOCAL_DRAFT_MODEL: Any
    RATE_LIMIT_ENABLED: bool
    _RATE_LIMIT_ENABLED: bool
    BASE_DIR: Any
    DATA_DIR: Any
    STATIC_DIR: Any
    USERS_FILE: Any
    keyring: Any

    _session_store: Any
    get_session_email: Any
    load_users: Any
    user_id_for_email: Any
    get_user_role: Any
    _extract_bearer_token: Any
    get_current_user: Any
    require_user: Any
    require_admin: Any
    enforce_rate_limit: Any

    EMBEDDING_PROFILE: Any
    EMBEDDER: Any
    MULTIMODAL_PORTS: Any

    model_router: Any

    _spawn: Any
    lifespan: Any
    app: Any
    CORS_ALLOWED_ORIGINS: Any
    CSRF_ALLOWED_ORIGINS: Any
    model_runtime_service: Any

    model_service: Any
    _embedding_info: Any

    def __init__(self, config: Any = None) -> None:
        self.config_arg = config
        self._produced: Dict[str, str] = {}
        self._phase: str = "<none>"

    def enter(self, phase: str) -> None:
        self._phase = phase

    def set(self, **values: Any) -> None:
        for name, value in values.items():
            setattr(self, name, value)
            self._produced.setdefault(name, self._phase)

    def adopt(self, stage: Any, *names: str) -> None:
        self.set(**{name: stage[name] for name in names})

    def require(self, name: str) -> Any:
        try:
            return getattr(self, name)
        except AttributeError:
            producer = self._produced.get(name)
            hint = f" (produced by phase {producer!r})" if producer else ""
            raise RuntimeError(
                f"RuntimeContext.{name} was read before it was built{hint}"
            ) from None

    def names(self) -> Set[str]:
        return set(self._produced)

    def __repr__(self) -> str:
        return f"<RuntimeContext phase={self._phase!r} names={len(self._produced)}>"


__all__ = ["RuntimeContext"]
