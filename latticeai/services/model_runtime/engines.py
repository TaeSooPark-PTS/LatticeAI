"""Local engine discovery, server hand-off, and the LM Studio HTTP client.

Re-exports from ``latticeai.services.model_engines`` that keep the historical
``model_runtime`` import path alive, plus the one piece of real logic that never
belonged in the engine layer: the LM Studio native API (list / download / load)
and its short-lived model cache.

Until 11.5.2 the re-exports were fifteen hand-written one-line functions that
forwarded their arguments, next to second copies of ``_json_request`` and the
LM Studio base-URL pair. A forwarding wrapper is a place two implementations
can drift — one of the copies had already grown a different fallback — so the
names are now bound directly to the engine layer's and there is nothing left to
disagree with.

``_LMSTUDIO_MODELS_CACHE`` and ``_LMSTUDIO_MODELS_CACHE_TS`` are rebindable
module state and therefore live here and nowhere else — the package
``__init__`` deliberately does not re-export them, because a
``from … import`` copy would freeze at import time and quietly disagree with
the live value.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from latticeai.models.router import AsyncOpenAI
from latticeai.services.model_engines import (
    LOCAL_SERVER_PROCESSES as _LOCAL_SERVER_PROCESSES,
)
from latticeai.services.model_engines import (
    _json_request as _json_request,
)
from latticeai.services.model_engines import (
    engine_install_plan as _engine_install_plan,
)
from latticeai.services.model_engines import (
    engine_support_status as engine_support_status,
)
from latticeai.services.model_engines import (
    ensure_llamacpp_server as ensure_llamacpp_server,
)
from latticeai.services.model_engines import (
    ensure_lmstudio_server as ensure_lmstudio_server,
)
from latticeai.services.model_engines import (
    ensure_ollama_server as ensure_ollama_server,
)
from latticeai.services.model_engines import ensure_vllm_server as ensure_vllm_server
from latticeai.services.model_engines import find_lmstudio_cli as find_lmstudio_cli
from latticeai.services.model_engines import (
    get_ollama_pulled_models as get_ollama_pulled_models,
)
from latticeai.services.model_engines import (
    get_openai_compatible_server_models as get_openai_compatible_server_models,
)
from latticeai.services.model_engines import lmstudio_api_base as lmstudio_api_base
from latticeai.services.model_engines import (
    lmstudio_native_api_base as lmstudio_native_api_base,
)
from latticeai.services.model_engines import local_binary as local_binary
from latticeai.services.model_engines import (
    pull_ollama_model_with_progress as pull_ollama_model_with_progress,
)
from latticeai.services.model_engines import vllm_executable as vllm_executable
from latticeai.services.model_engines import vllm_metal_python as vllm_metal_python
from latticeai.services.model_engines import (
    wait_for_openai_compatible_server as wait_for_openai_compatible_server,
)
from latticeai.services.model_engines import (
    windows_binary_candidates as windows_binary_candidates,
)
from latticeai.services.model_errors import ModelRuntimeError

LOCAL_SERVER_PROCESSES = _LOCAL_SERVER_PROCESSES


_LMSTUDIO_MODELS_CACHE: List[Dict[str, Any]] = []
_LMSTUDIO_MODELS_CACHE_TS: float = 0.0
_LMSTUDIO_MODELS_CACHE_TTL: float = 10.0


def get_lmstudio_models(*, force: bool = False) -> List[Dict[str, Any]]:
    global _LMSTUDIO_MODELS_CACHE, _LMSTUDIO_MODELS_CACHE_TS
    if not force and time.monotonic() - _LMSTUDIO_MODELS_CACHE_TS < _LMSTUDIO_MODELS_CACHE_TTL:
        return _LMSTUDIO_MODELS_CACHE
    try:
        payload = _json_request(
            f"{lmstudio_native_api_base()}/api/v1/models",
            headers={"Authorization": f"Bearer {os.getenv('LMSTUDIO_API_KEY') or 'lmstudio'}"},
            timeout=2.5,
        )
    except Exception:
        return _LMSTUDIO_MODELS_CACHE
    models = payload.get("models")
    _LMSTUDIO_MODELS_CACHE = models if isinstance(models, list) else []
    _LMSTUDIO_MODELS_CACHE_TS = time.monotonic()
    return _LMSTUDIO_MODELS_CACHE


def _lmstudio_candidate_keys(model_name: str) -> List[str]:
    raw = model_name.strip()
    if not raw:
        return []
    slug = raw.split("/")[-1].lower()
    slug = slug.replace("-gguf", "").replace("-awq", "")
    parts = [p for p in slug.split("-") if p]
    candidates = [raw.lower(), slug]
    if parts:
        candidates.append("-".join(parts[: min(4, len(parts))]))
    return list(dict.fromkeys(candidates))


def _find_lmstudio_model_key(model_name: str, models: List[Dict[str, Any]]) -> Optional[str]:
    if not models:
        return None
    candidate_keys = _lmstudio_candidate_keys(model_name)
    exact = []
    fuzzy = []
    for item in models:
        key = str(item.get("key") or "").strip()
        display_name = str(item.get("display_name") or "").strip()
        haystacks = [key.lower(), display_name.lower()]
        if any(raw == key.lower() for raw in candidate_keys):
            exact.append(key)
            continue
        if any(token and token in hay for token in candidate_keys for hay in haystacks):
            fuzzy.append(key)
    return next(iter(exact or fuzzy), None)


def ensure_lmstudio_model(model_name: str) -> Dict[str, Any]:
    ensure_lmstudio_server()
    auth_header = {"Authorization": f"Bearer {os.getenv('LMSTUDIO_API_KEY') or 'lmstudio'}"}
    models = get_lmstudio_models()
    found_key = _find_lmstudio_model_key(model_name, models)
    model_key = found_key or model_name

    if not found_key:
        try:
            job = _json_request(
                f"{lmstudio_native_api_base()}/api/v1/models/download",
                method="POST",
                payload={"model": model_name},
                headers=auth_header,
                timeout=30,
            )
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[-2000:]
            raise ModelRuntimeError(status_code=500, detail=f"LM Studio 모델 다운로드 실패: {detail or e.reason}")
        except Exception as e:
            raise ModelRuntimeError(status_code=500, detail=f"LM Studio 모델 다운로드 실패: {e}")

        status = str(job.get("status") or "")
        job_id = str(job.get("job_id") or "")
        if status not in {"completed", "already_downloaded"} and job_id:
            deadline = time.time() + 3600
            while time.time() < deadline:
                polled = _json_request(
                    f"{lmstudio_native_api_base()}/api/v1/models/download/status/{job_id}",
                    headers=auth_header,
                    timeout=30,
                )
                polled_status = str(polled.get("status") or "")
                if polled_status == "completed":
                    break
                if polled_status == "failed":
                    raise ModelRuntimeError(status_code=500, detail=f"LM Studio 모델 다운로드 실패: {polled}")
                time.sleep(2)
            else:
                raise ModelRuntimeError(status_code=408, detail="LM Studio 모델 다운로드 시간이 초과되었습니다.")

        models = get_lmstudio_models(force=True)
        model_key = _find_lmstudio_model_key(model_name, models) or model_name

    target = next((item for item in models if isinstance(item, dict) and item.get("key") == model_key), None)
    loaded_instances = target.get("loaded_instances") if isinstance(target, dict) else None
    if loaded_instances:
        return {"provider": "lmstudio", "model": model_name, "resolved_model": model_key, "server_ready": True, "cached": True}

    try:
        loaded = _json_request(
            f"{lmstudio_native_api_base()}/api/v1/models/load",
            method="POST",
            payload={"model": model_key, "context_length": 4096},
            headers=auth_header,
            timeout=120,
        )
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[-2000:]
        raise ModelRuntimeError(status_code=500, detail=f"LM Studio 모델 로드 실패: {detail or e.reason}")
    except Exception as e:
        raise ModelRuntimeError(status_code=500, detail=f"LM Studio 모델 로드 실패: {e}")

    if str(loaded.get("status") or "") != "loaded":
        raise ModelRuntimeError(status_code=500, detail=f"LM Studio 모델 로드 실패: {loaded}")

    return {
        "provider": "lmstudio",
        "model": model_name,
        "resolved_model": model_key,
        "instance_id": loaded.get("instance_id"),
        "server_ready": True,
        "cached": False,
    }

def _safe_engine_install_plan(
    engine: str,
    *,
    base_dir: Path,
) -> Optional[Dict[str, Any]]:
    try:
        return _engine_install_plan(engine, base_dir=base_dir)
    except Exception:
        return None


def engine_installed(engine: str) -> bool:
    if engine == "local_mlx":
        return bool(
            importlib.util.find_spec("mlx")
            and (importlib.util.find_spec("mlx_vlm") or importlib.util.find_spec("mlx_lm"))
        )
    if engine == "ollama":
        return local_binary("ollama") is not None
    if engine == "vllm":
        return vllm_metal_python() is not None or vllm_executable() is not None or importlib.util.find_spec("vllm") is not None
    if engine == "lmstudio":
        return find_lmstudio_cli() is not None or Path("/Applications/LM Studio.app").exists()
    if engine == "llamacpp":
        return shutil.which("llama-server") is not None
    if engine in {"openai", "openrouter", "groq", "together", "xai"}:
        return AsyncOpenAI is not None
    return False
