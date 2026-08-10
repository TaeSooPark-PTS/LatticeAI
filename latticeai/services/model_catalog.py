"""Static local-model catalog, engine installers, and family-version filtering.

5.2.0: Now sources the rich ENGINE_MODEL_CATALOG from the structured
model_capability_registry (single source of truth with HF verification,
download/load strategy, hardware, license, modality). Legacy flat shapes
preserved exactly for all downstream (recommendation, api, runtime, frontend).

The old inline _model() + ENGINE_MODEL_CATALOG data has been moved into
latticeai/services/model_capability_registry.py (see there for full 5.2 fields).
"""

from __future__ import annotations

import re
import sys
from typing import Any, Dict, List, Optional

from latticeai.services.model_capability_registry import (
    LOCAL_MLX_MODELS as _LOCAL_MLX_MODELS,
)

# 5.2.0: Delegate catalog data to the structured capability registry (rich + verified).
# This keeps backward compat for every `from ...model_catalog import ENGINE_MODEL_CATALOG`.
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
    get_verified_models as _get_verified_models,
)

ENGINE_INSTALLERS: Dict[str, Dict[str, Any]] = {
    "local_mlx": {
        "command": [sys.executable, "-m", "pip", "install", "--upgrade", "mlx-vlm>=0.6.3", "mlx-lm", "huggingface_hub[cli]"],
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
        "command": [sys.executable, "-m", "pip", "install", "vllm", "huggingface_hub[cli]"],
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
}

# 5.2.0 delegation: the rich catalog (with verification, hf_repo_id, strategies, hardware, license etc)
# is defined in model_capability_registry. We build the legacy-shaped ENGINE_MODEL_CATALOG here
# at import time so every existing consumer (runtime, api, recommendation, tests) is unaffected.
#
# The *raw* registry projection keeps every capability (incl. legacy generations
# like Gemma 3 / Qwen2.5-VL / Pixtral) for transparency + HF verification. The
# user-facing ENGINE_MODEL_CATALOG below is then narrowed to the aggressive 5.2.0
# policy (latest family generations only, no text-only/legacy weights) and the
# engine-specific ids/sizes are normalised. See `_finalize_engine_catalog`.
_RAW_ENGINE_MODEL_CATALOG: Dict[str, List[Dict[str, Any]]] = _build_engine_model_catalog()
# Filled in at module end once the blocklist, alias map and family-version filter
# are all defined; declared here so the public name exists for static readers.
ENGINE_MODEL_CATALOG: Dict[str, List[Dict[str, Any]]] = {}

# Historical aliases preserved (used by _recommended_with_engine_options and resolution).
# These can be enriched later from registry if needed; kept verbatim for safety.
MODEL_ENGINE_ALIASES = {
    "gemma-4-12b-it-4bit": {
        "local_mlx": "mlx-community/gemma-4-12b-it-4bit",
        "ollama": "hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_K_M",
        "vllm": "google/gemma-4-12b-it",
        "lmstudio": "ggml-org/gemma-4-12B-it-GGUF",
        "llamacpp": "ggml-org/gemma-4-12B-it-GGUF",
    },
    "mlx-community/gemma-4-12b-it-4bit": {
        "local_mlx": "mlx-community/gemma-4-12b-it-4bit",
        "ollama": "hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_K_M",
        "vllm": "google/gemma-4-12b-it",
        "lmstudio": "ggml-org/gemma-4-12B-it-GGUF",
        "llamacpp": "ggml-org/gemma-4-12B-it-GGUF",
    },
    "gemma-4-26b-it-4bit": {
        "local_mlx": "mlx-community/gemma-4-26b-a4b-it-4bit",
    },
    "mlx-community/gemma-4-26b-a4b-it-4bit": {
        "local_mlx": "mlx-community/gemma-4-26b-a4b-it-4bit",
    },
    "gemma-4-31b-it-4bit": {
        "local_mlx": "mlx-community/gemma-4-31b-it-4bit",
        "ollama": "hf.co/ggml-org/gemma-4-31B-it-GGUF:Q4_K_M",
        "vllm": "suitch/gemma-4-31B-it-4bit",
        "lmstudio": "ggml-org/gemma-4-31B-it-GGUF",
        "llamacpp": "ggml-org/gemma-4-31B-it-GGUF",
    },
    "suitch/gemma-4-31b-it-4bit": {
        "local_mlx": "mlx-community/gemma-4-31b-it-4bit",
        "ollama": "hf.co/ggml-org/gemma-4-31B-it-GGUF:Q4_K_M",
        "vllm": "suitch/gemma-4-31B-it-4bit",
        "lmstudio": "ggml-org/gemma-4-31B-it-GGUF",
        "llamacpp": "ggml-org/gemma-4-31B-it-GGUF",
    },
    "mlx-community/gemma-4-31b-it-4bit": {
        "local_mlx": "mlx-community/gemma-4-31b-it-4bit",
        "ollama": "hf.co/ggml-org/gemma-4-31B-it-GGUF:Q4_K_M",
        "vllm": "suitch/gemma-4-31B-it-4bit",
        "lmstudio": "ggml-org/gemma-4-31B-it-GGUF",
        "llamacpp": "ggml-org/gemma-4-31B-it-GGUF",
    },
    "qwen3-vl-8b": {
        "local_mlx": "mlx-community/Qwen3-VL-8B-Instruct-4bit",
        "ollama": "qwen3-vl:8b",
        "vllm": "Qwen/Qwen3-VL-8B-Instruct",
        "lmstudio": "Qwen/Qwen3-VL-8B-Instruct",
        "llamacpp": "Qwen/Qwen3-VL-8B-Instruct-GGUF",
    },
    "llama-4-scout": {
        "local_mlx": "mlx-community/Llama-4-Scout-17B-16E-Instruct-4bit",
        "ollama": "hf.co/ggml-org/Llama-4-Scout-17B-16E-Instruct-GGUF:Q4_K_M",
        "vllm": "meta-llama/Llama-4-Scout-17B-16E-Instruct",
        "lmstudio": "meta-llama/Llama-4-Scout-17B-16E-Instruct",
        "llamacpp": "ggml-org/Llama-4-Scout-17B-16E-Instruct-GGUF",
    },
}

# Also expose registry helpers directly from here for consumers who want the rich objects
get_all_capabilities = _get_all_capabilities
get_capability = _get_capability
get_verified_models = _get_verified_models

# Convenience re-export for tests / places that did `from ...model_catalog import LOCAL_MLX_MODELS`
LOCAL_MLX_MODELS = _LOCAL_MLX_MODELS  # type: ignore[name-defined]

_VERSIONED_MODEL_PATTERNS = (
    ("gemma", re.compile(r"\bgemma[-\s]?(\d+(?:\.\d+)?)", re.IGNORECASE)),
    ("qwen", re.compile(r"\bqwen[-\s]?(\d+(?:\.\d+)?)", re.IGNORECASE)),
    ("llama", re.compile(r"\bllama[-\s]?(\d+(?:\.\d+)?)", re.IGNORECASE)),
)


def _version_tuple(raw: str) -> tuple[int, ...]:
    return tuple(int(part) for part in raw.split(".") if part.isdigit())


def _model_family_version(model: Dict[str, Any]) -> Optional[tuple[str, tuple[int, ...]]]:
    text = " ".join(str(model.get(key) or "") for key in ("family", "name", "id"))
    for family, pattern in _VERSIONED_MODEL_PATTERNS:
        match = pattern.search(text)
        if match:
            # Every pattern captures at least one decimal digit and no other
            # character but ``.``, so the parsed tuple is never empty — the
            # old ``if version:`` guard could not fire and is gone.
            return family, _version_tuple(match.group(1))
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
        model for model, version_info in detected
        if not version_info or version_info[1] >= max_versions.get(version_info[0], version_info[1])
    ]


# ── 5.2.0 user-facing catalog assembly ────────────────────────────────────────
# Legacy/text-only generations stay in the capability registry (for transparency
# and HF verification) but must never be surfaced in the model picker. Anything
# whose id contains one of these fragments is dropped from ENGINE_MODEL_CATALOG.
_BLOCKED_CATALOG_FRAGMENTS = (
    "gemma-3", "gemma3", "gemma-2", "gemma2",
    "qwen2.5", "qwen-2.5", "qwen2-5",
    "llama-3", "llama3.2", "llama-3.2",
    "pixtral", "mistral",
    "smollm", "gpt-oss", "phi-",
)


def _is_blocked_catalog_id(model: Dict[str, Any]) -> bool:
    ident = str(model.get("id") or "").lower()
    return any(fragment in ident for fragment in _BLOCKED_CATALOG_FRAGMENTS)


def _normalize_engine_entry(engine: str, model: Dict[str, Any]) -> Dict[str, Any]:
    """Apply historical engine-specific id + size conventions to a raw entry.

    * Non-MLX engines resolve to their canonical packaged id via
      :data:`MODEL_ENGINE_ALIASES` (e.g. ollama → ``hf.co/ggml-org/...GGUF``).
    * Server / tool-managed engines advertise no fixed on-disk size, so the
      execution tool validates the exact weights at pull time.
    """
    if engine == "local_mlx":
        return model
    entry = dict(model)
    hf_repo = str(entry.get("hf_repo_id") or "")
    short = hf_repo.split("/")[-1].lower()
    aliases = MODEL_ENGINE_ALIASES.get(short) or MODEL_ENGINE_ALIASES.get(hf_repo.lower())
    mapped = aliases.get(engine) if aliases else None
    if mapped:
        entry["id"] = f"{engine}:{mapped}"
    # Tool-managed engines (ollama/vllm/lmstudio/llamacpp) pull on demand; the
    # registry's MLX on-disk size does not apply to them.
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
