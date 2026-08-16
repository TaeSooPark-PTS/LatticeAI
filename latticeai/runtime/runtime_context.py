"""The assembly state the build phases share.

``app_factory._build`` was one 1,300-line function. That was not an accident:
its sections genuinely depend on each other, and several of them close over
names bound *further down* — ``save_to_history`` calls ``append_audit_event``,
which is built ~150 lines later. Inside a single function that works, because a
closure resolves a free variable when it is called, not when it is defined.
Any naive extraction breaks exactly there (10.3.0 hit this and documented it).

``RuntimeContext`` is what makes the extraction safe. It is a mutable object
that every phase writes into and reads from, so a closure created in phase 2
that calls ``ctx.append_audit_event`` still resolves it at call time — the same
late binding the single function had, now with the shared state named instead
of implicit.

Two properties keep it honest:

* **Attributes are declared, not defaulted.** Reading one before its phase has
  run raises ``AttributeError`` naming the attribute, rather than silently
  yielding ``None`` and failing somewhere unrelated.
* **The phase order is a contract.** ``tests/unit/test_runtime_context.py``
  fixes which phase produces which attribute, so reordering the phases in a way
  that breaks a dependency fails a test instead of production.
"""

from __future__ import annotations

from typing import Any, Dict, Set


class RuntimeContext:
    """Mutable state carried across the ordered application build phases."""

    # ── phase 1: platform ────────────────────────────────────────────────
    mx: Any

    # ── phase 2: configuration and paths ─────────────────────────────────
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

    # ── phase 3: the seam gate ───────────────────────────────────────────
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

    # ── phase 4: the compute ports ───────────────────────────────────────
    EMBEDDING_PROFILE: Any
    EMBEDDER: Any
    MULTIMODAL_PORTS: Any

    # ── phase 5: domain singletons ───────────────────────────────────────
    model_router: Any

    # ── phase 6: the web shell ───────────────────────────────────────────
    _spawn: Any
    lifespan: Any
    app: Any
    CORS_ALLOWED_ORIGINS: Any
    CSRF_ALLOWED_ORIGINS: Any
    model_runtime_service: Any

    # ── phase 7: the compute routers ─────────────────────────────────────
    model_service: Any
    _embedding_info: Any

    def __init__(self, config: Any = None) -> None:
        self.config_arg = config
        self._produced: Dict[str, str] = {}
        self._phase: str = "<none>"

    # ── phase bookkeeping ────────────────────────────────────────────────
    def enter(self, phase: str) -> None:
        """Mark which phase subsequent ``set`` calls belong to."""
        self._phase = phase

    def set(self, **values: Any) -> None:
        """Publish values produced by the current phase.

        Recording the producing phase is what lets the ordering test assert a
        contract instead of a comment.
        """
        for name, value in values.items():
            setattr(self, name, value)
            self._produced.setdefault(name, self._phase)

    def adopt(self, stage: Any, *names: str) -> None:
        """Publish selected keys of a runtime-stage mapping onto the context."""
        self.set(**{name: stage[name] for name in names})

    def require(self, name: str) -> Any:
        """Read an attribute, failing with the phase contract if it is unset.

        The bare ``AttributeError`` is already specific; this adds which phase
        was supposed to have produced the value, which is the actual question
        when a phase gets reordered.
        """
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

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<RuntimeContext phase={self._phase!r} names={len(self._produced)}>"


__all__ = ["RuntimeContext"]
