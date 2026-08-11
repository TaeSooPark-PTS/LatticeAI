"""Model identity, engine readiness, and the load entrypoints.

The path from a string a user clicked to a loaded model: resolve engine
aliases into one canonical identity, make sure the engine that identity needs
is installed, then hand off to ``latticeai.services.model_loading`` for the
load itself (blocking and streaming forms) and to
``latticeai.services.model_engines`` for the post-load smoke test.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Dict, Optional

from latticeai.core.model_resolution import ModelResolution as _ModelResolution
from latticeai.models.router import OPENAI_COMPATIBLE_PROVIDERS, ensure_mlx_runtime
from latticeai.services.model_catalog import ENGINE_INSTALLERS, MODEL_ENGINE_ALIASES
from latticeai.services.model_errors import ModelRuntimeError
from latticeai.services.model_runtime.engines import (
    engine_installed,
    engine_support_status,
)
from latticeai.services.model_runtime.state import ModelRuntimeState
from latticeai.services.model_runtime.status import install_engine


def _resolve_model_alias(model_id: str, engine: Optional[str] = None) -> str:
    raw = model_id.strip()
    engine_hint = (engine or "").strip().lower()
    provider: Optional[str] = None
    model_name = raw
    if ":" in raw:
        prefix, rest = raw.split(":", 1)
        prefix = prefix.strip().lower()
        if prefix in {"ollama", "vllm", "lmstudio", "llamacpp", "local_mlx", "mlx"}:
            provider = "local_mlx" if prefix in {"local_mlx", "mlx"} else prefix
            model_name = rest.strip()
    provider = provider or ("local_mlx" if engine_hint in {"", "local_mlx", "mlx"} else engine_hint)
    aliases = MODEL_ENGINE_ALIASES.get(model_name.lower())
    if not aliases:
        return raw
    mapped = aliases.get(provider)
    if not mapped:
        return raw
    return mapped if provider == "local_mlx" else f"{provider}:{mapped}"


def normalize_local_model_request(model_id: str, engine: Optional[str] = None) -> str:
    model_id = _resolve_model_alias(model_id, engine)
    engine = (engine or "").strip().lower()
    if engine in {"local_mlx", "mlx"} and model_id.startswith(("local_mlx:", "mlx:")):
        return model_id.split(":", 1)[1].strip()
    if engine and engine not in {"local_mlx", "mlx"} and ":" not in model_id:
        return f"{engine}:{model_id}"
    return model_id


def ensure_engine_ready(engine: str, *, state: ModelRuntimeState) -> Dict[str, Any]:
    engine = "local_mlx" if engine == "mlx" else engine
    if engine not in ENGINE_INSTALLERS and engine not in OPENAI_COMPATIBLE_PROVIDERS:
        raise ModelRuntimeError(status_code=400, detail=f"지원하지 않는 엔진입니다: {engine}")
    support = engine_support_status(engine)
    if not support["supported"]:
        raise ModelRuntimeError(status_code=400, detail=str(support["reason"]))

    if engine_installed(engine):
        if engine == "local_mlx":
            ensure_mlx_runtime()
        return {"engine": engine, "installed": True, "installed_now": False}

    if engine not in ENGINE_INSTALLERS:
        raise ModelRuntimeError(status_code=400, detail=f"{engine} 엔진 설치 방법이 등록되어 있지 않습니다.")

    result = install_engine(engine, state=state)
    if result.get("returncode") not in (0, None) or not engine_installed(engine):
        detail = result.get("stderr") or result.get("stdout") or f"{engine} 설치에 실패했습니다."
        raise ModelRuntimeError(status_code=500, detail=str(detail)[-2000:])

    if engine == "local_mlx":
        ensure_mlx_runtime()
    return {"engine": engine, "installed": True, "installed_now": True, "install": result}


def build_model_resolution(
    input_id: str,
    engine: Optional[str],
    *,
    user_email: Optional[str] = None,
    display_name: Optional[str] = None,
) -> _ModelResolution:
    """피드백 #1/#2 공용 ModelResolution 생성기.

    사용자가 클릭한 input_id + engine 힌트를 받아 모든 단계가 공유할
    canonical identity를 만든다.
    """
    normalized = normalize_local_model_request(input_id, engine)
    return _ModelResolution.from_request(
        normalized,
        engine=engine,
        user_email=user_email,
        display_name=display_name or input_id,
        engine_aliases=MODEL_ENGINE_ALIASES,
    )


_LOCAL_SMOKE_ENGINES = {"local_mlx", "ollama", "vllm", "lmstudio", "llamacpp"}


async def _smoke_test_loaded_model(
    resolution: _ModelResolution,
    *,
    api_key_override: Optional[str] = None,
    state: ModelRuntimeState,
) -> Dict[str, Any]:
    # Delegated to model_engines for server decomp
    # Absolute since v11.3.0: this file moved one level down into the
    # model_runtime package, so ``.model_engines`` would resolve inside it.
    from latticeai.services.model_engines import (
        _smoke_test_loaded_model as _impl_smoke,
    )
    return await _impl_smoke(
        resolution,
        api_key_override=api_key_override,
        model_router=state.router,
    )


async def prepare_and_load_model(
    model_id: str,
    request: Any,
    engine: Optional[str] = None,
    user_email: Optional[str] = None,
    adapter_path: Optional[str] = None,
    draft_model_id: Optional[str] = None,
    allow_download: bool = False,
    *,
    state: ModelRuntimeState,
) -> Dict[str, Any]:
    from latticeai.services.model_loading import prepare_and_load_model as _impl

    return await _impl(
        model_id,
        request,
        engine=engine,
        user_email=user_email,
        adapter_path=adapter_path,
        draft_model_id=draft_model_id,
        allow_download=allow_download,
        runtime_state=state,
    )


def sse_event(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def prepare_and_load_model_stream(
    model_id: str,
    request: Any,
    engine: Optional[str] = None,
    user_email: Optional[str] = None,
    allow_download: bool = False,
    *,
    state: ModelRuntimeState,
) -> AsyncIterator[str]:
    from latticeai.services.model_loading import (
        prepare_and_load_model_stream as _impl,
    )

    async for event in _impl(
        model_id,
        request,
        engine=engine,
        user_email=user_email,
        allow_download=allow_download,
        runtime_state=state,
    ):
        yield event
