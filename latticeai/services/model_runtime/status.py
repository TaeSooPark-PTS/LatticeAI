"""What this machine can run: the engine/model status payload.

:func:`engine_status` is the single answer the Library screen renders — every
engine (local, local-server, cloud), whether it is installed, whether its
install is even supported here, and which catalogue models are already pulled.
:func:`runtime_features` is the same idea for the application itself, and
:func:`install_engine` is the one write this module owns.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from latticeai.models.router import HF_MODELS_ROOT
from latticeai.services.model_catalog import (
    ENGINE_INSTALLERS,
    ENGINE_MODEL_CATALOG,
    filter_lower_family_versions,
)
from latticeai.services.model_engines import (
    install_engine as _install_engine,
)
from latticeai.services.model_runtime.download import hf_model_ready
from latticeai.services.model_runtime.engines import (
    _safe_engine_install_plan,
    engine_installed,
    engine_support_status,
    get_lmstudio_models,
    get_ollama_pulled_models,
)
from latticeai.services.model_runtime.state import ModelRuntimeState


def engine_status(
    *,
    state: ModelRuntimeState,
    cloud_verify_cache: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict]:
    r = state.router
    verify_cache = cloud_verify_cache or {}
    cloud_models = r.detected_cloud_models() if r else []
    cloud_by_provider: Dict[str, List[Dict[str, Any]]] = {}
    for model in cloud_models:
        cloud_by_provider.setdefault(model["provider"], []).append(model)

    ollama_installed = engine_installed("ollama")
    pulled = get_ollama_pulled_models() if ollama_installed else set()
    ollama_models = []
    for m in ENGINE_MODEL_CATALOG["ollama"]:
        pull_name = m["id"].removeprefix("ollama:")
        ollama_models.append({**m, "pulled": pull_name in pulled})
    ollama_models = filter_lower_family_versions(ollama_models)

    HF_MODELS_ROOT.mkdir(parents=True, exist_ok=True)
    mlx_models = []
    for m in ENGINE_MODEL_CATALOG.get("local_mlx", []):
        repo_id = m["id"]
        mlx_models.append({**m, "pulled": hf_model_ready(repo_id, "local_mlx")})
    mlx_models = filter_lower_family_versions(mlx_models)

    vllm_models = []
    for m in ENGINE_MODEL_CATALOG.get("vllm", []):
        repo_id = m["id"].removeprefix("vllm:")
        vllm_models.append({**m, "pulled": hf_model_ready(repo_id, "vllm")})
    vllm_models = filter_lower_family_versions(vllm_models)

    lmstudio_models = []
    downloaded_lmstudio = get_lmstudio_models()
    downloaded_by_key: Dict[str, Dict[str, Any]] = {}
    for item in downloaded_lmstudio:
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        downloaded_by_key[key] = item
        loaded_instances = item.get("loaded_instances") or []
        lmstudio_models.append({
            "id": f"lmstudio:{key}",
            "name": item.get("display_name") or f"LM Studio · {key}",
            "family": item.get("architecture") or item.get("publisher") or "LM Studio",
            "tag": "loaded-server-model" if loaded_instances else "downloaded",
            "size": item.get("params_string") or item.get("format") or "LM Studio",
            "pullable": True,
            "pulled": True,
        })

    if not lmstudio_models:
        for m in ENGINE_MODEL_CATALOG.get("lmstudio", []):
            lmstudio_models.append({**m, "pulled": False})
    else:
        known_ids = {item["id"] for item in lmstudio_models}
        for m in ENGINE_MODEL_CATALOG.get("lmstudio", []):
            repo_id = m["id"].removeprefix("lmstudio:")
            if f"lmstudio:{repo_id}" not in known_ids and repo_id not in downloaded_by_key:
                lmstudio_models.append({**m, "pulled": False})
    lmstudio_models = filter_lower_family_versions(lmstudio_models)

    llamacpp_models = []
    for m in ENGINE_MODEL_CATALOG.get("llamacpp", []):
        repo_id = m["id"].removeprefix("llamacpp:")
        llamacpp_models.append({**m, "pulled": hf_model_ready(repo_id, "llamacpp")})
    llamacpp_models = filter_lower_family_versions(llamacpp_models)

    local_server_specs: List[Dict[str, Any]] = [
        {
            "id": "vllm",
            "name": "vLLM",
            "description": "vLLM OpenAI 호환 서버(예: http://localhost:8000/v1)에 연결합니다.",
            "requires": "VLLM_BASE_URL",
            "note": engine_support_status("vllm").get("reason"),
        },
        {
            "id": "lmstudio",
            "name": "LM Studio",
            "description": "LM Studio 로컬 OpenAI 호환 서버에 연결합니다.",
            "requires": "LMSTUDIO_BASE_URL",
            "note": (
                "다운로드된 모델은 자동 감지하고, 선택 시 필요하면 다운로드 후 바로 로드합니다."
                if downloaded_lmstudio else
                "LM Studio 설치 후 모델을 선택하면 Local Server 시작, 다운로드, 로드를 자동으로 진행합니다."
            ),
            "server_ready": bool(downloaded_lmstudio),
        },
        {
            "id": "llamacpp",
            "name": "llama.cpp",
            "description": "llama.cpp 서버(OpenAI 호환 /v1)에 연결합니다.",
            "requires": "LLAMACPP_BASE_URL",
        },
    ]

    engines = [
        {
            "id": "local_mlx",
            "name": "MLX",
            "kind": "local",
            "description": "Apple Silicon GPU에서 MLX-VLM 모델을 직접 실행하고, Gemma 4는 필요 시 MLX-LM 텍스트 경로로 재시도합니다.",
            "installed": engine_installed("local_mlx"),
            "installable": True,
            "install_label": ENGINE_INSTALLERS["local_mlx"]["label"],
            "install_plan": _safe_engine_install_plan("local_mlx", base_dir=state.BASE_DIR),
            "models": mlx_models,
        },
        {
            "id": "ollama",
            "name": "Ollama",
            "kind": "local-server",
            "description": "Ollama 로컬 서버를 OpenAI 호환 엔진처럼 사용합니다.",
            "installed": ollama_installed,
            "installable": True,
            "install_label": ENGINE_INSTALLERS["ollama"]["label"],
            "install_plan": _safe_engine_install_plan("ollama", base_dir=state.BASE_DIR),
            "models": ollama_models,
        },
    ]
    for spec in local_server_specs:
        support = engine_support_status(spec["id"])
        engines.append({
            "id": spec["id"],
            "name": spec["name"],
            "kind": "local-server",
            "description": spec["description"],
            "installed": engine_installed(spec["id"]),
            "supported": support["supported"],
            "support_reason": support["reason"],
            "installable": support["supported"] and spec["id"] in ENGINE_INSTALLERS,
            "install_label": ENGINE_INSTALLERS.get(spec["id"], {}).get("label"),
            "install_plan": (
                _safe_engine_install_plan(spec["id"], base_dir=state.BASE_DIR)
                if spec["id"] in ENGINE_INSTALLERS
                else None
            ),
            "requires": spec["requires"],
            "models": (
                vllm_models if spec["id"] == "vllm"
                else lmstudio_models if spec["id"] == "lmstudio"
                else llamacpp_models if spec["id"] == "llamacpp"
                else ENGINE_MODEL_CATALOG.get(spec["id"], [])
            ),
            "note": spec.get("note") or support["reason"] or f"{spec['requires']} 설정 시 활성화됩니다.",
            "server_ready": spec.get("server_ready"),
        })
    for provider in ["openai", "openrouter", "groq", "together", "xai"]:
        env_key = next((item.get("requires") for item in cloud_by_provider.get(provider, []) if item.get("requires")), None)
        provider_models = []
        for model in cloud_by_provider.get(provider, []):
            cache = verify_cache.get(str(model.get("id") or ""))
            provider_models.append({
                **model,
                "verified": cache.get("ok") if cache else None,
                "verify_reason": cache.get("reason") if cache else None,
            })
        engines.append({
            "id": provider,
            "name": provider.title(),
            "kind": "cloud",
            "description": "OpenAI 호환 Chat Completions API로 cloud LLM을 실행합니다.",
            "installed": engine_installed(provider),
            "installable": True,
            "install_label": ENGINE_INSTALLERS[provider]["label"],
            "install_plan": _safe_engine_install_plan(provider, base_dir=state.BASE_DIR),
            "requires": env_key,
            "models": provider_models,
        })
    return engines

def runtime_features(*, state: ModelRuntimeState) -> Dict:
    s = state
    r = s.router
    return {
        "mode": s.APP_MODE,
        "public": s.IS_PUBLIC_MODE,
        "host": s.DEFAULT_HOST,
        "port": s.DEFAULT_PORT,
        "data_dir": str(s.DATA_DIR),
        "telegram_enabled": s.ENABLE_TELEGRAM,
        "graph_enabled": s.ENABLE_GRAPH,
        "autoload_models": s.AUTOLOAD_MODELS,
        "model_idle_unload_seconds": s.MODEL_IDLE_UNLOAD_SECONDS,
        "allow_model_downloads": s.ALLOW_MODEL_DOWNLOADS,
        "model_download_timeout": s.MODEL_DOWNLOAD_TIMEOUT,
        "model_memory_policy": r.model_memory_policy() if r else None,
        "allow_local_models": s.ALLOW_LOCAL_MODELS,
        "security": {
            "host": s.DEFAULT_HOST,
            "require_auth": s.REQUIRE_AUTH,
            "invite_gate_enabled": s.INVITE_GATE_ENABLED,
            "keyring_available": s.keyring is not None,
            "plaintext_api_keys_allowed": s.ALLOW_PLAINTEXT_API_KEYS,
            "cors_allow_network": s.CORS_ALLOW_NETWORK,
        },
        "default_model": s.PUBLIC_MODEL if s.IS_PUBLIC_MODE else s.LOCAL_MODEL,
        "local_only_features": {
            "mlx": s.ALLOW_LOCAL_MODELS and not s.IS_PUBLIC_MODE,
            "telegram_bridge": s.ENABLE_TELEGRAM,
            "desktop_chrome_bridge": not s.IS_PUBLIC_MODE,
            "computer_use_bridge": not s.IS_PUBLIC_MODE,
        },
        "public_features": {
            "web_ui": True,
            "openai_compatible_models": True,
            "persistent_data_dir": str(s.DATA_DIR),
        },
    }

def install_engine(
    engine: str,
    confirmation_token: Optional[str] = None,
    *,
    state: ModelRuntimeState,
) -> Dict:
    return _install_engine(
        engine,
        confirmation_token=confirmation_token,
        base_dir=state.BASE_DIR,
    )
