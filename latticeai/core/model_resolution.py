from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class PrepareState(str, Enum):
    RESOLVING = "RESOLVING"
    ENGINE_CHECK = "ENGINE_CHECK"
    ENGINE_INSTALL = "ENGINE_INSTALL"
    DOWNLOADING = "DOWNLOADING"
    SERVER_STARTING = "SERVER_STARTING"
    MODEL_SERVING = "MODEL_SERVING"
    MODEL_LOADING = "MODEL_LOADING"
    SMOKE_TEST = "SMOKE_TEST"
    READY = "READY"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"


LOCAL_ENGINES = {"local_mlx", "omlx", "ollama", "vllm", "lmstudio", "llamacpp"}
ENGINE_ALIASES = {
    "mlx": "local_mlx",
    "local-mlx": "local_mlx",
    "openmlx": "omlx",
    "llama.cpp": "llamacpp",
    "llama-cpp": "llamacpp",
    "lm-studio": "lmstudio",
    "lmstudio:openai-compatible": "lmstudio",
}


def _canonical_engine(engine: Optional[str]) -> Optional[str]:
    if not engine:
        return None
    e = str(engine).strip().lower()
    e = ENGINE_ALIASES.get(e, e)
    return e or None


@dataclass
class ModelResolution:
    input_id: str
    engine: str
    provider: str
    resolved_model: str
    download_id: str
    load_id: str
    expected_current: str
    display_name: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_request(
        cls,
        input_id: str,
        engine: Optional[str] = None,
        *,
        display_name: Optional[str] = None,
        user_email: Optional[str] = None,
        alias_resolver=None,
        engine_aliases: Optional[Dict[str, Dict[str, str]]] = None,
    ) -> "ModelResolution":
        raw = str(input_id or "").strip()
        if not raw:
            raise ValueError("모델 식별자가 비어 있습니다.")

        engine_hint = _canonical_engine(engine)

        provider: Optional[str] = None
        model_name = raw
        if ":" in raw:
            prefix, rest = raw.split(":", 1)
            prefix_canon = _canonical_engine(prefix)
            if prefix_canon and prefix_canon in LOCAL_ENGINES.union(
                {"openai", "anthropic", "openrouter", "groq", "together"}
            ):
                provider = prefix_canon
                model_name = rest.strip()

        if not provider:
            provider = engine_hint or "local_mlx"

        resolved_model = model_name
        if engine_aliases:
            aliases = engine_aliases.get(model_name.lower())
            if aliases:
                mapped = aliases.get(provider)
                if mapped:
                    resolved_model = mapped

        if alias_resolver:
            try:
                maybe = alias_resolver(resolved_model, provider)
                if maybe:
                    if ":" in maybe and maybe.split(":", 1)[0] in LOCAL_ENGINES:
                        provider2, resolved_model = maybe.split(":", 1)
                        provider = provider2
                    else:
                        resolved_model = maybe
            except Exception:
                logger.debug(
                    "alias_resolver failed for %s", resolved_model, exc_info=True
                )

        download_id = resolved_model
        if provider == "local_mlx":
            load_id = resolved_model
        else:
            load_id = f"{provider}:{resolved_model}"

        expected_current = load_id
        if user_email and provider != "local_mlx":
            expected_current = f"{load_id}::{user_email}"

        return cls(
            input_id=raw,
            engine=provider,
            provider=provider,
            resolved_model=resolved_model,
            download_id=download_id,
            load_id=load_id,
            expected_current=expected_current,
            display_name=(display_name or raw),
            metadata={"engine_hint": engine_hint or ""},
        )

    def update_after_load(self, *, actual_current: Optional[str]) -> None:
        if not actual_current:
            return
        self.expected_current = actual_current
        if ":" in actual_current:
            head = actual_current.split("::", 1)[0]
            self.load_id = head
            if ":" in head:
                self.resolved_model = head.split(":", 1)[1]


@dataclass
class PrepareReport:
    status: str
    state: PrepareState
    resolution: ModelResolution
    current: Optional[str]
    message: Optional[str] = None
    downloaded: bool = False
    loaded: bool = False
    ready_to_chat: bool = False
    compatibility_status: str = "unknown"
    smoke_test: Optional[Dict[str, Any]] = None
    stage_logs: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[Dict[str, Any]] = None
    install_result: Dict[str, Any] = field(default_factory=dict)
    download_result: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["state"] = (
            self.state.value
            if isinstance(self.state, PrepareState)
            else str(self.state)
        )
        data["resolution"] = self.resolution.to_dict()
        return data


def transition_log(
    state: PrepareState, message: str, extra: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    log: Dict[str, Any] = {"state": state.value, "message": message}
    if extra:
        log["extra"] = extra
    return log


__all__ = [
    "ModelResolution",
    "PrepareState",
    "PrepareReport",
    "transition_log",
    "LOCAL_ENGINES",
]
