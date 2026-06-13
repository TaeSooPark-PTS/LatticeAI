"""Static local-model catalog, engine installers, and family-version filtering.

Extracted from :mod:`latticeai.services.model_runtime` so the runtime module
owns model lifecycle/loading logic while this module owns the behaviour-free
catalog data (engine installers, the per-engine model catalog, cross-engine
aliases) and the pure version-dedup helpers. Re-exported by ``model_runtime``
for backward compatibility, so existing imports such as
``from latticeai.services.model_runtime import ENGINE_MODEL_CATALOG`` keep
working unchanged.
"""

from __future__ import annotations

import re
import sys
from typing import Dict, List, Optional

ENGINE_INSTALLERS = {
    "local_mlx": {
        "command": [sys.executable, "-m", "pip", "install", "--upgrade", "mlx-vlm", "mlx-lm", "huggingface_hub[cli]"],
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

def _model(
    model_id: str,
    name: str,
    family: str,
    tag: str,
    size: str,
    *,
    source_country: str,
    source_company: str,
    execution_method: str,
    internet_requirement: str = "모델을 다운로드할 때만 인터넷 필요; 실행 중에는 필요 없음",
    pullable: bool = True,
) -> Dict[str, object]:
    clean_model_name = re.split(r"\s+via\s+", name, maxsplit=1)[0]
    return {
        "id": model_id,
        "name": name,
        "model_name": clean_model_name,
        "family": family,
        "tag": tag,
        "size": size,
        "pullable": pullable,
        "modality": "multimodal",
        "source_country": source_country,
        "source_company": source_company,
        "execution_method": execution_method,
        "run_location": "내 컴퓨터에서만 실행",
        "internet_requirement": internet_requirement,
        "source_display_order": [
            "source_country",
            "source_company",
            "execution_method",
            "internet_requirement",
            "model_name",
        ],
    }


_RUNS_ON_THIS_COMPUTER = "내 컴퓨터에서만 실행"


ENGINE_MODEL_CATALOG = {
    "local_mlx": [
        _model("mlx-community/gemma-4-e2b-4bit", "Gemma 4 E2B Base", "Gemma 4", "local-vlm", "3.6GB", source_country="미국", source_company="Google", execution_method=_RUNS_ON_THIS_COMPUTER),
        _model("mlx-community/gemma-4-e2b-it-4bit", "Gemma 4 E2B Instruct", "Gemma 4", "local-vlm", "3.6GB", source_country="미국", source_company="Google", execution_method=_RUNS_ON_THIS_COMPUTER),
        _model("mlx-community/gemma-4-e4b-4bit", "Gemma 4 E4B Base", "Gemma 4", "local-vlm", "5.2GB", source_country="미국", source_company="Google", execution_method=_RUNS_ON_THIS_COMPUTER),
        _model("mlx-community/gemma-4-e4b-it-4bit", "Gemma 4 E4B Instruct", "Gemma 4", "local-vlm", "5.2GB", source_country="미국", source_company="Google", execution_method=_RUNS_ON_THIS_COMPUTER),
        _model("mlx-community/gemma-4-12b-it-4bit", "Gemma 4 12B Instruct", "Gemma 4", "local-vlm", "7.6GB", source_country="미국", source_company="Google", execution_method=_RUNS_ON_THIS_COMPUTER),
        _model("mlx-community/gemma-4-26b-a4b-it-4bit", "Gemma 4 26B A4B Instruct", "Gemma 4", "local-vlm", "15.6GB", source_country="미국", source_company="Google", execution_method=_RUNS_ON_THIS_COMPUTER),
        _model("mlx-community/gemma-4-31b-it-4bit", "Gemma 4 31B Instruct", "Gemma 4", "local-vlm", "18.4GB", source_country="미국", source_company="Google", execution_method=_RUNS_ON_THIS_COMPUTER),
        _model("mlx-community/Qwen3-VL-4B-Instruct-4bit", "Qwen3-VL 4B", "Qwen3-VL", "local-vlm", "2.7GB", source_country="중국", source_company="Alibaba", execution_method=_RUNS_ON_THIS_COMPUTER),
        _model("mlx-community/Qwen3-VL-8B-Instruct-4bit", "Qwen3-VL 8B", "Qwen3-VL", "local-vlm", "4.8GB", source_country="중국", source_company="Alibaba", execution_method=_RUNS_ON_THIS_COMPUTER),
        _model("mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit", "Qwen3-VL 30B A3B", "Qwen3-VL", "local-vlm", "18GB", source_country="중국", source_company="Alibaba", execution_method=_RUNS_ON_THIS_COMPUTER),
        _model("mlx-community/Llama-4-Scout-17B-16E-Instruct-4bit", "Llama 4 Scout 17B 16E", "Llama 4", "local-vlm", "11.8GB", source_country="미국", source_company="Meta", execution_method=_RUNS_ON_THIS_COMPUTER),
    ],
    "ollama": [
        _model("ollama:qwen3-vl:4b", "Qwen3-VL 4B via Ollama", "Qwen3-VL", "local-vlm", "pull required", source_country="중국", source_company="Alibaba", execution_method=_RUNS_ON_THIS_COMPUTER),
        _model("ollama:qwen3-vl:8b", "Qwen3-VL 8B via Ollama", "Qwen3-VL", "local-vlm", "pull required", source_country="중국", source_company="Alibaba", execution_method=_RUNS_ON_THIS_COMPUTER),
        _model("ollama:qwen3-vl:30b", "Qwen3-VL 30B via Ollama", "Qwen3-VL", "local-vlm", "pull required", source_country="중국", source_company="Alibaba", execution_method=_RUNS_ON_THIS_COMPUTER),
        _model("ollama:hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_K_M", "Gemma 4 12B Q4 via Ollama", "Gemma 4", "local-vlm", "7.9GB", source_country="미국", source_company="Google", execution_method=_RUNS_ON_THIS_COMPUTER),
        _model("ollama:hf.co/ggml-org/gemma-4-31B-it-GGUF:Q4_K_M", "Gemma 4 31B Q4 via Ollama", "Gemma 4", "local-vlm", "18.7GB", source_country="미국", source_company="Google", execution_method=_RUNS_ON_THIS_COMPUTER),
        _model("ollama:hf.co/ggml-org/Llama-4-Scout-17B-16E-Instruct-GGUF:Q4_K_M", "Llama 4 Scout Q4 via Ollama", "Llama 4", "local-vlm", "12GB", source_country="미국", source_company="Meta", execution_method=_RUNS_ON_THIS_COMPUTER),
    ],
    "vllm": [
        _model("vllm:Qwen/Qwen3-VL-4B-Instruct", "Qwen3-VL 4B via vLLM", "Qwen3-VL", "local-vlm", "실행 도구에서 관리", source_country="중국", source_company="Alibaba", execution_method=_RUNS_ON_THIS_COMPUTER),
        _model("vllm:Qwen/Qwen3-VL-8B-Instruct", "Qwen3-VL 8B via vLLM", "Qwen3-VL", "local-vlm", "실행 도구에서 관리", source_country="중국", source_company="Alibaba", execution_method=_RUNS_ON_THIS_COMPUTER),
        _model("vllm:Qwen/Qwen3-VL-30B-A3B-Instruct", "Qwen3-VL 30B A3B via vLLM", "Qwen3-VL", "local-vlm", "실행 도구에서 관리", source_country="중국", source_company="Alibaba", execution_method=_RUNS_ON_THIS_COMPUTER),
        _model("vllm:google/gemma-4-12b-it", "Gemma 4 12B via vLLM", "Gemma 4", "local-vlm", "실행 도구에서 관리", source_country="미국", source_company="Google", execution_method=_RUNS_ON_THIS_COMPUTER),
        _model("vllm:suitch/gemma-4-31B-it-4bit", "Gemma 4 31B via vLLM", "Gemma 4", "local-vlm", "실행 도구에서 관리", source_country="미국", source_company="Google", execution_method=_RUNS_ON_THIS_COMPUTER),
        _model("vllm:meta-llama/Llama-4-Scout-17B-16E-Instruct", "Llama 4 Scout via vLLM", "Llama 4", "local-vlm", "실행 도구에서 관리", source_country="미국", source_company="Meta", execution_method=_RUNS_ON_THIS_COMPUTER),
    ],
    "lmstudio": [
        _model("lmstudio:Qwen/Qwen3-VL-4B-Instruct", "Qwen3-VL 4B via LM Studio", "Qwen3-VL", "local-vlm", "실행 도구에서 관리", source_country="중국", source_company="Alibaba", execution_method=_RUNS_ON_THIS_COMPUTER),
        _model("lmstudio:Qwen/Qwen3-VL-8B-Instruct", "Qwen3-VL 8B via LM Studio", "Qwen3-VL", "local-vlm", "실행 도구에서 관리", source_country="중국", source_company="Alibaba", execution_method=_RUNS_ON_THIS_COMPUTER),
        _model("lmstudio:Qwen/Qwen3-VL-30B-A3B-Instruct", "Qwen3-VL 30B A3B via LM Studio", "Qwen3-VL", "local-vlm", "실행 도구에서 관리", source_country="중국", source_company="Alibaba", execution_method=_RUNS_ON_THIS_COMPUTER),
        _model("lmstudio:ggml-org/gemma-4-12B-it-GGUF", "Gemma 4 12B 4-bit via LM Studio", "Gemma 4", "local-vlm", "실행 도구에서 관리", source_country="미국", source_company="Google", execution_method=_RUNS_ON_THIS_COMPUTER),
        _model("lmstudio:ggml-org/gemma-4-31B-it-GGUF", "Gemma 4 31B 4-bit via LM Studio", "Gemma 4", "local-vlm", "실행 도구에서 관리", source_country="미국", source_company="Google", execution_method=_RUNS_ON_THIS_COMPUTER),
        _model("lmstudio:meta-llama/Llama-4-Scout-17B-16E-Instruct", "Llama 4 Scout via LM Studio", "Llama 4", "local-vlm", "실행 도구에서 관리", source_country="미국", source_company="Meta", execution_method=_RUNS_ON_THIS_COMPUTER),
    ],
    "llamacpp": [
        _model("llamacpp:Qwen/Qwen3-VL-4B-Instruct-GGUF", "Qwen3-VL 4B GGUF via llama.cpp", "Qwen3-VL", "gguf-vlm", "gguf", source_country="중국", source_company="Alibaba", execution_method=_RUNS_ON_THIS_COMPUTER),
        _model("llamacpp:Qwen/Qwen3-VL-8B-Instruct-GGUF", "Qwen3-VL 8B GGUF via llama.cpp", "Qwen3-VL", "gguf-vlm", "gguf", source_country="중국", source_company="Alibaba", execution_method=_RUNS_ON_THIS_COMPUTER),
        _model("llamacpp:Qwen/Qwen3-VL-30B-A3B-Instruct-GGUF", "Qwen3-VL 30B GGUF via llama.cpp", "Qwen3-VL", "gguf-vlm", "gguf", source_country="중국", source_company="Alibaba", execution_method=_RUNS_ON_THIS_COMPUTER),
        _model("llamacpp:ggml-org/gemma-4-12B-it-GGUF", "Gemma 4 12B GGUF via llama.cpp", "Gemma 4", "gguf-vlm", "gguf", source_country="미국", source_company="Google", execution_method=_RUNS_ON_THIS_COMPUTER),
        _model("llamacpp:ggml-org/gemma-4-31B-it-GGUF", "Gemma 4 31B GGUF via llama.cpp", "Gemma 4", "gguf-vlm", "gguf", source_country="미국", source_company="Google", execution_method=_RUNS_ON_THIS_COMPUTER),
        _model("llamacpp:ggml-org/Llama-4-Scout-17B-16E-Instruct-GGUF", "Llama 4 Scout GGUF via llama.cpp", "Llama 4", "gguf-vlm", "gguf", source_country="미국", source_company="Meta", execution_method=_RUNS_ON_THIS_COMPUTER),
    ],
}

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

_VERSIONED_MODEL_PATTERNS = (
    ("gemma", re.compile(r"\bgemma[-\s]?(\d+(?:\.\d+)?)", re.IGNORECASE)),
    ("qwen", re.compile(r"\bqwen[-\s]?(\d+(?:\.\d+)?)", re.IGNORECASE)),
    ("llama", re.compile(r"\bllama[-\s]?(\d+(?:\.\d+)?)", re.IGNORECASE)),
)


def _version_tuple(raw: str) -> tuple[int, ...]:
    return tuple(int(part) for part in raw.split(".") if part.isdigit())


def _model_family_version(model: Dict[str, object]) -> Optional[tuple[str, tuple[int, ...]]]:
    text = " ".join(str(model.get(key) or "") for key in ("family", "name", "id"))
    for family, pattern in _VERSIONED_MODEL_PATTERNS:
        match = pattern.search(text)
        if match:
            version = _version_tuple(match.group(1))
            if version:
                return family, version
    return None


def filter_lower_family_versions(models: List[Dict[str, object]]) -> List[Dict[str, object]]:
    max_versions: Dict[str, tuple[int, ...]] = {}
    detected: List[tuple[Dict[str, object], Optional[tuple[str, tuple[int, ...]]]]] = []
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
