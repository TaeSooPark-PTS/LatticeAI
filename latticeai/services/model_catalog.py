from __future__ import annotations

import re
import sys
from typing import Any, Dict, List, Optional

from latticeai.services.model_capability_registry import (
    build_engine_model_catalog as _build_engine_model_catalog,
)
from latticeai.services.model_capability_registry import (
    get_all_capabilities as _get_all_capabilities,
)
from latticeai.services.model_capability_registry import (
    get_capability as _get_capability,
)
from latticeai.services.model_capability_registry import (
    get_embedding_capabilities as _get_embedding_capabilities,
)
from latticeai.services.model_capability_registry import (
    get_verified_models as _get_verified_models,
)
from latticeai.services.model_capability_registry import (
    recommend_embedding_for_hardware as _recommend_embedding_for_hardware,
)

ENGINE_INSTALLERS: Dict[str, Dict[str, Any]] = {
    "local_mlx": {
        "command": [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "mlx-vlm>=0.6.3",
            "mlx-lm",
            "huggingface_hub[cli]",
        ],
        "label": "Install MLX runtime",
    },
    "openai": {
        "command": [sys.executable, "-m", "pip", "install", "openai"],
        "label": "Install OpenAI-compatible SDK",
    },
    "openrouter": {
        "command": [sys.executable, "-m", "pip", "install", "openai"],
        "label": "Install OpenAI-compatible SDK",
    },
    "groq": {
        "command": [sys.executable, "-m", "pip", "install", "openai"],
        "label": "Install OpenAI-compatible SDK",
    },
    "together": {
        "command": [sys.executable, "-m", "pip", "install", "openai"],
        "label": "Install OpenAI-compatible SDK",
    },
    "xai": {
        "command": [sys.executable, "-m", "pip", "install", "openai"],
        "label": "Install OpenAI-compatible SDK",
    },
    "ollama": {
        "command": ["brew", "install", "ollama"],
        "label": "Install Ollama",
        "requires_binary": "brew",
    },
    "vllm": {
        "command": [
            sys.executable,
            "-m",
            "pip",
            "install",
            "vllm",
            "huggingface_hub[cli]",
        ],
        "label": "Install vLLM runtime",
    },
    "lmstudio": {
        "command": ["brew", "install", "--cask", "lm-studio"],
        "label": "Install LM Studio",
        "requires_binary": "brew",
    },
    "llamacpp": {
        "command": ["brew", "install", "llama.cpp"],
        "label": "Install llama.cpp",
        "requires_binary": "brew",
    },
    "omlx": {
        "command": [
            "/bin/bash",
            "-lc",
            "brew tap jundot/omlx https://github.com/jundot/omlx && brew install jundot/omlx/omlx",
        ],
        "label": "Install oMLX",
    },
}

_RAW_ENGINE_MODEL_CATALOG: Dict[str, List[Dict[str, Any]]] = (
    _build_engine_model_catalog()
)
ENGINE_MODEL_CATALOG: Dict[str, List[Dict[str, Any]]] = {}


def _gguf_routes(
    mlx_repo: str,
    gguf_repo: str,
    hf_repo: str,
    *,
    ollama_tag: str = "Q4_K_M",
) -> Dict[str, str]:
    return {
        "local_mlx": mlx_repo,
        "omlx": mlx_repo.replace("/", "--"),
        "ollama": f"hf.co/{gguf_repo}:{ollama_tag}",
        "vllm": hf_repo,
        "lmstudio": gguf_repo,
        "llamacpp": gguf_repo,
    }


_ENGINE_ROUTES: Dict[str, Dict[str, str]] = {
    "mlx-community/LFM2.5-2.6B-4bit": _gguf_routes(
        "mlx-community/LFM2.5-2.6B-4bit",
        "LiquidAI/LFM2.5-2.6B-GGUF",
        "LiquidAI/LFM2.5-2.6B",
    ),
    "mlx-community/gemma-4-e2b-it-4bit": _gguf_routes(
        "mlx-community/gemma-4-e2b-it-4bit",
        "ggml-org/gemma-4-E2B-it-GGUF",
        "google/gemma-4-E2B-it",
        ollama_tag="Q4_0",
    ),
    "mlx-community/gemma-4-e4b-it-4bit": _gguf_routes(
        "mlx-community/gemma-4-e4b-it-4bit",
        "ggml-org/gemma-4-E4B-it-GGUF",
        "google/gemma-4-E4B-it",
        ollama_tag="Q4_0",
    ),
    "mlx-community/gemma-4-12B-it-4bit": _gguf_routes(
        "mlx-community/gemma-4-12B-it-4bit",
        "ggml-org/gemma-4-12B-it-GGUF",
        "google/gemma-4-12B-it",
        ollama_tag="Q4_0",
    ),
    "mlx-community/gemma-4-26B-A4B-it-qat-4bit": _gguf_routes(
        "mlx-community/gemma-4-26B-A4B-it-qat-4bit",
        "google/gemma-4-26B-A4B-it-qat-q4_0-gguf",
        "google/gemma-4-26B-A4B-it-qat-q4_0-unquantized",
        ollama_tag="Q4_0",
    ),
    "mlx-community/gemma-4-26b-a4b-it-4bit": _gguf_routes(
        "mlx-community/gemma-4-26b-a4b-it-4bit",
        "ggml-org/gemma-4-26B-A4B-it-GGUF",
        "google/gemma-4-26B-A4B-it",
        ollama_tag="Q4_0",
    ),
    "mlx-community/gemma-4-26B-A4B-it-qat-OptiQ-4bit": _gguf_routes(
        "mlx-community/gemma-4-26B-A4B-it-qat-OptiQ-4bit",
        "unsloth/gemma-4-26B-A4B-it-GGUF",
        "google/gemma-4-26B-A4B-it-qat-q4_0-unquantized",
        ollama_tag="Q4_0",
    ),
    "mlx-community/gemma-4-31b-it-4bit": _gguf_routes(
        "mlx-community/gemma-4-31b-it-4bit",
        "ggml-org/gemma-4-31B-it-GGUF",
        "google/gemma-4-31B-it",
        ollama_tag="Q4_0",
    ),
    "mlx-community/Qwen3.8-27B-4bit": _gguf_routes(
        "mlx-community/Qwen3.8-27B-4bit",
        "ggml-org/Qwen3.8-27B-GGUF",
        "Qwen/Qwen3.8-27B",
    ),
    "mlx-community/Qwen3.6-27B-4bit": _gguf_routes(
        "mlx-community/Qwen3.6-27B-4bit",
        "ggml-org/Qwen3.6-27B-GGUF",
        "Qwen/Qwen3.6-27B",
    ),
    "mlx-community/gpt-oss-20b-MXFP4-Q8": _gguf_routes(
        "mlx-community/gpt-oss-20b-MXFP4-Q8",
        "ggml-org/gpt-oss-20b-GGUF",
        "openai/gpt-oss-20b",
        ollama_tag="MXFP4",
    ),
    "mlx-community/Qwen3.5-9B-MLX-4bit": _gguf_routes(
        "mlx-community/Qwen3.5-9B-MLX-4bit",
        "unsloth/Qwen3.5-9B-GGUF",
        "Qwen/Qwen3.5-9B",
    ),
    "mlx-community/Qwen3.6-35B-A3B-4bit": _gguf_routes(
        "mlx-community/Qwen3.6-35B-A3B-4bit",
        "ggml-org/Qwen3.6-35B-A3B-GGUF",
        "Qwen/Qwen3.6-35B-A3B",
    ),
    "google/embeddinggemma-2": _gguf_routes(
        "google/embeddinggemma-2",
        "ggml-org/embeddinggemma-2-GGUF",
        "google/embeddinggemma-2",
    ),
    "mlx-community/embeddinggemma-2-4bit": _gguf_routes(
        "mlx-community/embeddinggemma-2-4bit",
        "mlx-community/embeddinggemma-2-4bit-GGUF",
        "mlx-community/embeddinggemma-2-4bit",
    ),
    "google/embeddinggemma-300m": _gguf_routes(
        "google/embeddinggemma-300m",
        "ggml-org/embeddinggemma-300m-GGUF",
        "google/embeddinggemma-300m",
    ),
    "mlx-community/embeddinggemma-300m-4bit": _gguf_routes(
        "mlx-community/embeddinggemma-300m-4bit",
        "mlx-community/embeddinggemma-300m-4bit-GGUF",
        "mlx-community/embeddinggemma-300m-4bit",
    ),
}

_COMMON_MISNAMES: Dict[str, str] = {
    "gemma-4-26b-it-4bit": "mlx-community/gemma-4-26b-a4b-it-4bit",
    "gemma-4-26b-qat-4bit": "mlx-community/gemma-4-26B-A4B-it-qat-4bit",
    "gemma-4-26b-a4b-qat-4bit": "mlx-community/gemma-4-26B-A4B-it-qat-4bit",
    "gemma-4-26b-optiq-4bit": "mlx-community/gemma-4-26B-A4B-it-qat-OptiQ-4bit",
    "qwen-3.8-27b-4bit": "mlx-community/Qwen3.8-27B-4bit",
    "qwen3.8-27b": "mlx-community/Qwen3.8-27B-4bit",
    "qwen3.8:27b": "mlx-community/Qwen3.8-27B-4bit",
    "qwen3.8-27b-gguf": "mlx-community/Qwen3.8-27B-4bit",
    "mlx-community--qwen3.8-27b-4bit": "mlx-community/Qwen3.8-27B-4bit",
    "embeddinggemma2": "google/embeddinggemma-2",
    "embeddinggemma-2-hf": "google/embeddinggemma-2",
}

MODEL_ENGINE_ALIASES: Dict[str, Dict[str, str]] = {
    **{
        key: routes
        for repo, routes in _ENGINE_ROUTES.items()
        for key in (repo.split("/")[-1].lower(), repo.lower())
    },
    **{alias: _ENGINE_ROUTES[repo] for alias, repo in _COMMON_MISNAMES.items()},
}

get_all_capabilities = _get_all_capabilities
get_capability = _get_capability
get_embedding_capabilities = _get_embedding_capabilities
get_verified_models = _get_verified_models
recommend_embedding_for_hardware = _recommend_embedding_for_hardware

_VERSIONED_MODEL_PATTERNS = (
    ("gemma", re.compile(r"\bgemma[-\s]?(\d+(?:\.\d+)?)", re.IGNORECASE)),
    ("qwen", re.compile(r"\bqwen[-\s]?(\d+(?:\.\d+)?)", re.IGNORECASE)),
    ("llama", re.compile(r"\bllama[-\s]?(\d+(?:\.\d+)?)", re.IGNORECASE)),
)


def _version_tuple(raw: str) -> tuple[int, ...]:
    return tuple(int(part) for part in raw.split(".") if part.isdigit())


def _model_family_version(
    model: Dict[str, Any],
) -> Optional[tuple[str, tuple[int, ...]]]:
    text = " ".join(str(model.get(key) or "") for key in ("family", "name", "id"))
    if (
        "embeddinggemma" in text.lower()
        or model.get("category") == "embedding"
        or model.get("task") == "embedding"
    ):
        return None
    for family, pattern in _VERSIONED_MODEL_PATTERNS:
        match = pattern.search(text)
        if match:
            return family, _version_tuple(match.group(1))[:1]
    return None


def filter_lower_family_versions(models: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    max_versions: Dict[str, tuple[int, ...]] = {}
    detected: List[tuple[Dict[str, Any], Optional[tuple[str, tuple[int, ...]]]]] = []
    for model in models:
        version_info = _model_family_version(model)
        detected.append((model, version_info))
        if not version_info:
            continue
        family, version = version_info
        if version > max_versions.get(family, (0,)):
            max_versions[family] = version
    return [
        model
        for model, version_info in detected
        if not version_info
        or version_info[1] >= max_versions.get(version_info[0], version_info[1])
    ]


_BLOCKED_CATALOG_FRAGMENTS = (
    "gemma-3",
    "gemma3",
    "gemma-2",
    "gemma2",
    "qwen2.5",
    "qwen-2.5",
    "qwen2-5",
    "qwen3-vl",
    "qwen3vl",
    "llama-3",
    "llama3.2",
    "llama-3.2",
    "llama-4",
    "llama4",
    "pixtral",
    "mistral",
    "smollm",
    "phi-",
    "moondream",
)


def _is_blocked_catalog_id(model: Dict[str, Any]) -> bool:
    ident = str(model.get("id") or "").lower()
    if "embeddinggemma" in ident:
        return False
    return any(fragment in ident for fragment in _BLOCKED_CATALOG_FRAGMENTS)


def _normalize_engine_entry(engine: str, model: Dict[str, Any]) -> Dict[str, Any]:
    if engine == "local_mlx":
        return model
    entry = dict(model)
    hf_repo = str(entry.get("hf_repo_id") or entry.get("id") or "")
    short = hf_repo.split("/")[-1].lower()
    aliases = MODEL_ENGINE_ALIASES.get(short) or MODEL_ENGINE_ALIASES.get(
        hf_repo.lower()
    )
    if engine == "omlx":
        mapped = aliases.get("omlx") if aliases else hf_repo.replace("/", "--")
        entry["id"] = f"omlx:{mapped}"
        entry["hf_repo_id"] = hf_repo
        return entry
    mapped = aliases.get(engine) if aliases else None
    if mapped:
        entry["id"] = f"{engine}:{mapped}"
    entry["size"] = "실행 도구에서 관리"
    return entry


def _finalize_engine_catalog(
    raw: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, List[Dict[str, Any]]]:
    final: Dict[str, List[Dict[str, Any]]] = {}
    for engine, models in raw.items():
        kept = [
            _normalize_engine_entry(engine, m)
            for m in models
            if not _is_blocked_catalog_id(m)
        ]
        final[engine] = filter_lower_family_versions(kept)
    return final


ENGINE_MODEL_CATALOG = _finalize_engine_catalog(_RAW_ENGINE_MODEL_CATALOG)
