from __future__ import annotations

from typing import Any, AsyncIterator, Dict, Optional

from latticeai.core.model_resolution import ModelResolution as _ModelResolution
from latticeai.core.sse import sse_frame
from latticeai.models.router import OPENAI_COMPATIBLE_PROVIDERS, ensure_mlx_runtime
from latticeai.services.engine_adapters import (
    default_local_engine_for_host,
    is_apple_silicon,
)
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
    if engine_hint == "auto":
        engine_hint = default_local_engine_for_host(
            engine_installed_fn=engine_installed
        )
    provider: Optional[str] = None
    model_name = raw
    if ":" in raw:
        prefix, rest = raw.split(":", 1)
        prefix = prefix.strip().lower()
        if prefix in {
            "omlx",
            "ollama",
            "vllm",
            "lmstudio",
            "llamacpp",
            "local_mlx",
            "mlx",
        }:
            provider = "local_mlx" if prefix in {"local_mlx", "mlx"} else prefix
            model_name = rest.strip()
    provider = provider or (
        "local_mlx" if engine_hint in {"", "local_mlx", "mlx"} else engine_hint
    )
    lookup_name = (
        model_name.replace("--", "/", 1)
        if "/" not in model_name and "--" in model_name
        else model_name
    )
    aliases = MODEL_ENGINE_ALIASES.get(model_name.lower()) or MODEL_ENGINE_ALIASES.get(
        lookup_name.lower()
    )
    if not aliases:
        return raw
    mapped = aliases.get(provider)
    if not mapped:
        return raw
    return mapped if provider == "local_mlx" else f"{provider}:{mapped}"


def resolve_cross_platform_model_request(
    model_id: str,
    engine: Optional[str] = None,
) -> tuple[str, Optional[str]]:
    engine_hint = (engine or "").strip().lower()
    if engine_hint == "auto":
        chosen = default_local_engine_for_host(engine_installed_fn=engine_installed)
        return normalize_local_model_request(model_id, chosen), chosen
    if is_apple_silicon():
        return normalize_local_model_request(model_id, engine), engine
    raw = model_id.strip()
    raw_prefix = raw.split(":", 1)[0].lower() if ":" in raw else ""
    bare_name = (
        raw.split(":", 1)[1].strip()
        if raw_prefix in {"local_mlx", "mlx", "omlx"}
        else raw
    )
    lookup_name = (
        bare_name.replace("--", "/", 1)
        if "/" not in bare_name and "--" in bare_name
        else bare_name
    )
    aliases = MODEL_ENGINE_ALIASES.get(bare_name.lower()) or MODEL_ENGINE_ALIASES.get(
        lookup_name.lower()
    )
    if (
        aliases
        and engine_hint in {"", "local_mlx", "mlx", "omlx"}
        and not engine_installed("local_mlx")
    ):
        chosen = default_local_engine_for_host(
            engine_installed_fn=engine_installed, apple_silicon=False
        )
        mapped = aliases.get(chosen)
        if mapped:
            return f"{chosen}:{mapped}", chosen
    return normalize_local_model_request(model_id, engine), engine


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
        raise ModelRuntimeError(
            status_code=400, detail=f"지원하지 않는 엔진입니다: {engine}"
        )
    support = engine_support_status(engine)
    if not support["supported"]:
        raise ModelRuntimeError(status_code=400, detail=str(support["reason"]))

    if engine_installed(engine):
        if engine == "local_mlx":
            ensure_mlx_runtime()
        return {"engine": engine, "installed": True, "installed_now": False}

    if engine not in ENGINE_INSTALLERS:
        raise ModelRuntimeError(
            status_code=400, detail=f"{engine} 엔진 설치 방법이 등록되어 있지 않습니다."
        )

    result = install_engine(engine, state=state)
    if result.get("returncode") not in (0, None) or not engine_installed(engine):
        detail = (
            result.get("stderr")
            or result.get("stdout")
            or f"{engine} 설치에 실패했습니다."
        )
        raise ModelRuntimeError(status_code=500, detail=str(detail)[-2000:])

    if engine == "local_mlx":
        ensure_mlx_runtime()
    return {
        "engine": engine,
        "installed": True,
        "installed_now": True,
        "install": result,
    }


def build_model_resolution(
    input_id: str,
    engine: Optional[str],
    *,
    user_email: Optional[str] = None,
    display_name: Optional[str] = None,
) -> _ModelResolution:
    normalized = normalize_local_model_request(input_id, engine)
    return _ModelResolution.from_request(
        normalized,
        engine=engine,
        user_email=user_email,
        display_name=display_name or input_id,
        engine_aliases=MODEL_ENGINE_ALIASES,
    )


_LOCAL_SMOKE_ENGINES = {"local_mlx", "omlx", "ollama", "vllm", "lmstudio", "llamacpp"}


async def _smoke_test_loaded_model(
    resolution: _ModelResolution,
    *,
    api_key_override: Optional[str] = None,
    state: ModelRuntimeState,
) -> Dict[str, Any]:
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

    resolved_id, resolved_engine = resolve_cross_platform_model_request(
        model_id, engine
    )
    return await _impl(
        resolved_id,
        request,
        engine=resolved_engine,
        user_email=user_email,
        adapter_path=adapter_path,
        draft_model_id=draft_model_id,
        allow_download=allow_download,
        runtime_state=state,
    )


def sse_event(event: str, data: Dict[str, Any]) -> str:
    return sse_frame(event, data)


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

    resolved_id, resolved_engine = resolve_cross_platform_model_request(
        model_id, engine
    )
    async for event in _impl(
        resolved_id,
        request,
        engine=resolved_engine,
        user_email=user_email,
        allow_download=allow_download,
        runtime_state=state,
    ):
        yield event
