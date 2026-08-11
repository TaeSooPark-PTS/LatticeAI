"""The immutable dependency set one model runtime is configured with.

``ModelRuntimeState`` replaced the process-wide module ``STATE`` object: every
value the runtime needs — router, mode, paths, download/auth policy, the
app-owned callables — is passed in explicitly and never mutated afterwards, so
a second ASGI application in the same interpreter cannot inherit the first
one's credentials or configuration.

Also home to the two consent gates (:func:`_download_block`,
:func:`_engine_install_block`): presence of a token or a model name never
authorises network or installer activity on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Callable, Optional

from latticeai.core.model_compat import (
    SMOKE_PROMPT as _SMOKE_PROMPT,
)
from latticeai.core.model_compat import (
    friendly_model_runtime_error as _friendly_model_runtime_error,
)
from latticeai.core.model_compat import (
    model_runtime_compatibility as _model_runtime_compatibility,
)
from latticeai.services.model_errors import ModelRuntimeError

# ``model_loading._get_model_runtime_deps`` imports these private names from
# this module to preserve the historical model_runtime wiring surface.
_MODEL_LOADING_COMPAT_EXPORTS = (
    _friendly_model_runtime_error,
    _model_runtime_compatibility,
    _SMOKE_PROMPT,
)


def _missing_current_user(_request: Any) -> Optional[str]:
    return None


def _missing_user_api_key(_email: Optional[str], _provider: str) -> Optional[str]:
    return None


@dataclass(frozen=True, slots=True)
class ModelRuntimeState:
    """Immutable application-owned dependencies for one model runtime.

    Upper-case configuration field names intentionally match the long-standing
    composition-root vocabulary.  Unlike the former module ``STATE`` object,
    instances are explicit, immutable, and safe to create more than once in a
    process (for example in isolated tests or multiple ASGI applications).
    """

    router: Any = None
    APP_MODE: str = "local"
    DEFAULT_HOST: str = "127.0.0.1"
    DEFAULT_PORT: int = 4825
    DATA_DIR: Path = field(default_factory=lambda: Path.home() / ".latticeai")
    BASE_DIR: Path = field(default_factory=Path.cwd)
    ENABLE_TELEGRAM: bool = False
    ENABLE_GRAPH: bool = True
    AUTOLOAD_MODELS: bool = False
    MODEL_IDLE_UNLOAD_SECONDS: int = 0
    ALLOW_MODEL_DOWNLOADS: bool = False
    MODEL_DOWNLOAD_TIMEOUT: int = 300
    ALLOW_LOCAL_MODELS: bool = True
    REQUIRE_AUTH: bool = False
    INVITE_GATE_ENABLED: bool = False
    ALLOW_PLAINTEXT_API_KEYS: bool = False
    CORS_ALLOW_NETWORK: bool = False
    PUBLIC_MODEL: str = "openai:gpt-4o-mini"
    LOCAL_MODEL: str = "mlx-community/gemma-4-12B-it-4bit"
    IS_PUBLIC_MODE: bool = False
    keyring: Any = None
    get_current_user: Callable[[Any], Optional[str]] = _missing_current_user
    get_user_api_key: Callable[[Optional[str], str], Optional[str]] = _missing_user_api_key


def create_model_runtime_state(**deps: Any) -> ModelRuntimeState:
    """Create an immutable runtime dependency set with strict key validation."""

    known = {item.name for item in fields(ModelRuntimeState)}
    unknown = sorted(set(deps) - known)
    if unknown:
        raise TypeError(f"unknown model runtime dependencies: {', '.join(unknown)}")
    return ModelRuntimeState(**deps)

def _download_allowed(
    allow_download: bool = False, *, state: ModelRuntimeState
) -> bool:
    autoload = state.AUTOLOAD_MODELS
    configured = state.ALLOW_MODEL_DOWNLOADS
    return bool(allow_download) or bool(configured) or bool(autoload)


def _download_block(provider: str, model_name: str) -> None:
    raise ModelRuntimeError(
        status_code=409,
        detail={
            "status": "unavailable",
            "capability": "model_download",
            "provider": provider,
            "model": model_name,
            "reason": (
                "Model files are not present locally. Lattice AI does not start "
                "outbound model downloads by default, and token/model presence "
                "alone never authorizes network activity."
            ),
            "action": "Use the explicit pull/prepare flow with download consent, or set LATTICEAI_ALLOW_MODEL_DOWNLOADS=true.",
        },
    )


def _engine_install_block(engine: str) -> None:
    raise ModelRuntimeError(
        status_code=409,
        detail={
            "status": "unavailable",
            "capability": "engine_install",
            "engine": engine,
            "reason": (
                "The requested local runtime is not installed. Lattice AI does not "
                "run package-manager or installer commands from Model Load by default."
            ),
            "action": "Install the runtime explicitly from Library/System setup, or enable explicit download/install consent for this request.",
        },
    )
