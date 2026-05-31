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
        "command": [sys.executable, "-m", "pip", "install", "--upgrade", "mlx-lm", "mlx-vlm", "huggingface_hub[cli]"],
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

ENGINE_MODEL_CATALOG = {
    "local_mlx": [
        {"id": "mlx-community/SmolLM-1.7B-Instruct-4bit", "name": "SmolLM 1.7B", "family": "SmolLM", "tag": "local-light", "size": "963MB", "pullable": True},
        {"id": "mlx-community/gemma-3-1b-it-4bit", "name": "Gemma 3 1B", "family": "Gemma 3", "tag": "local-light", "size": "733MB", "pullable": True},
        {"id": "mlx-community/Llama-3.2-1B-Instruct-4bit", "name": "Llama 3.2 1B", "family": "Llama 3.x", "tag": "local-light", "size": "1.3GB", "pullable": True},
        {"id": "mlx-community/gemma-2-2b-it-4bit", "name": "Gemma 2 2B", "family": "Gemma 2", "tag": "local-light", "size": "1.6GB", "pullable": True},
        {"id": "mlx-community/gemma-4-e2b-4bit", "name": "Gemma 4 E2B Base", "family": "Gemma 4", "tag": "local-vlm", "size": "3.6GB", "pullable": True},
        {"id": "mlx-community/gemma-4-e2b-it-4bit", "name": "Gemma 4 E2B Instruct", "family": "Gemma 4", "tag": "local-vlm", "size": "3.6GB", "pullable": True},
        {"id": "mlx-community/gemma-4-e4b-4bit", "name": "Gemma 4 E4B Base", "family": "Gemma 4", "tag": "local-vlm", "size": "5.2GB", "pullable": True},
        {"id": "mlx-community/gemma-4-e4b-it-4bit", "name": "Gemma 4 E4B Instruct", "family": "Gemma 4", "tag": "local-vlm", "size": "5.2GB", "pullable": True},
        {"id": "mlx-community/Qwen3-VL-4B-Instruct-4bit", "name": "Qwen3-VL 4B", "family": "Qwen3-VL", "tag": "local-vlm", "size": "2.7GB", "pullable": True},
        {"id": "mlx-community/Qwen3-VL-8B-Instruct-4bit", "name": "Qwen3-VL 8B", "family": "Qwen3-VL", "tag": "local-vlm", "size": "4.8GB", "pullable": True},
        {"id": "mlx-community/Qwen2.5-VL-7B-Instruct-4bit", "name": "Qwen2.5-VL 7B", "family": "Qwen2.5-VL", "tag": "local-vlm", "size": "4.4GB", "pullable": True},
        {"id": "mlx-community/gemma-3-4b-it-4bit", "name": "Gemma 3 4B", "family": "Gemma 3", "tag": "local-vlm", "size": "3.3GB", "pullable": True},
        {"id": "mlx-community/Llama-3.2-3B-Instruct-4bit", "name": "Llama 3.2 3B", "family": "Llama 3.x", "tag": "local-general", "size": "2.0GB", "pullable": True},
        {"id": "mlx-community/Llama-3.1-8B-Instruct-4bit", "name": "Llama 3.1 8B", "family": "Llama 3.1", "tag": "local-general", "size": "4.7GB", "pullable": True},
        {"id": "mlx-community/gemma-2-9b-it-4bit", "name": "Gemma 2 9B", "family": "Gemma 2", "tag": "local-general", "size": "5.4GB", "pullable": True},
        {"id": "mlx-community/gemma-3-12b-it-4bit", "name": "Gemma 3 12B", "family": "Gemma 3", "tag": "local-vlm", "size": "8.0GB", "pullable": True},
        {"id": "mlx-community/Phi-3.5-mini-instruct-4bit", "name": "Phi 3.5 Mini", "family": "Phi", "tag": "local-coding", "size": "2.2GB", "pullable": True},
        {"id": "mlx-community/Phi-4-mini-instruct-4bit", "name": "Phi 4 Mini", "family": "Phi", "tag": "local-coding", "size": "2.2GB", "pullable": True},
        {"id": "mlx-community/phi-4-4bit", "name": "Phi 4", "family": "Phi", "tag": "local-coding", "size": "8.3GB", "pullable": True},
        {"id": "mlx-community/Mistral-7B-Instruct-v0.3-4bit", "name": "Mistral 7B Instruct v0.3", "family": "Mistral", "tag": "local-general", "size": "4.1GB", "pullable": True},
        {"id": "mlx-community/Ministral-8B-Instruct-2410-4bit", "name": "Ministral 8B Instruct", "family": "Mistral", "tag": "local-general", "size": "4.5GB", "pullable": True},
        {"id": "mlx-community/Mistral-Small-24B-Instruct-2501-4bit", "name": "Mistral Small 24B", "family": "Mistral", "tag": "local-large", "size": "13.3GB", "pullable": True},
        {"id": "mlx-community/Qwen2.5-Coder-32B-Instruct-4bit", "name": "Qwen2.5 Coder 32B", "family": "Qwen2.5", "tag": "local-coding", "size": "18.5GB", "pullable": True},
        {"id": "mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit", "name": "Qwen3-VL 30B A3B", "family": "Qwen3-VL", "tag": "local-vlm", "size": "18GB", "pullable": True},
        {"id": "mlx-community/gemma-3-27b-it-4bit", "name": "Gemma 3 27B", "family": "Gemma 3", "tag": "local-vlm", "size": "17GB", "pullable": True},
        {"id": "mlx-community/gemma-4-26b-a4b-it-4bit", "name": "Gemma 4 26B A4B Instruct", "family": "Gemma 4", "tag": "local-vlm", "size": "15.6GB", "pullable": True},
        {"id": "mlx-community/gemma-4-31b-it-4bit", "name": "Gemma 4 31B Instruct", "family": "Gemma 4", "tag": "local-vlm", "size": "18.4GB", "pullable": True},
        {"id": "mlx-community/gpt-oss-20b-MXFP4-Q8", "name": "GPT-OSS 20B", "family": "GPT-OSS", "tag": "local-reasoning", "size": "12.1GB", "pullable": True},
        {"id": "mlx-community/gpt-oss-120b-MXFP4-Q4", "name": "GPT-OSS 120B", "family": "GPT-OSS", "tag": "local-large", "size": "62.3GB", "pullable": True},
        {"id": "mlx-community/Llama-3.3-70B-Instruct-4bit", "name": "Llama 3.3 70B", "family": "Llama 3.x", "tag": "local-general", "size": "40GB+", "pullable": True},
        {"id": "mlx-community/Llama-3.1-70B-Instruct-4bit", "name": "Llama 3.1 70B", "family": "Llama 3.1", "tag": "local-general", "size": "40GB+", "pullable": True},
    ],
    "ollama": [
        {"id": "ollama:qwen3-vl:4b", "name": "Qwen3-VL 4B via Ollama", "family": "Qwen3-VL", "tag": "local-vlm", "size": "pull required", "pullable": True},
        {"id": "ollama:qwen3-vl:8b", "name": "Qwen3-VL 8B via Ollama", "family": "Qwen3-VL", "tag": "local-vlm", "size": "pull required", "pullable": True},
        {"id": "ollama:qwen3-vl:30b", "name": "Qwen3-VL 30B via Ollama", "family": "Qwen3-VL", "tag": "local-vlm", "size": "pull required", "pullable": True},
        {"id": "ollama:gpt-oss:20b", "name": "GPT-OSS 20B via Ollama", "family": "GPT-OSS", "tag": "local-reasoning", "size": "pull required", "pullable": True},
        {"id": "ollama:gpt-oss:120b", "name": "GPT-OSS 120B via Ollama", "family": "GPT-OSS", "tag": "local-large", "size": "pull required", "pullable": True},
        {"id": "ollama:hf.co/ggml-org/gemma-4-31B-it-GGUF:Q4_K_M", "name": "Gemma 4 31B Q4 via Ollama", "family": "Gemma 4", "tag": "local-vlm", "size": "18.7GB", "pullable": True},
        {"id": "ollama:qwen3:8b", "name": "Qwen3 8B via Ollama", "family": "Qwen", "tag": "local-server", "size": "pull required", "pullable": True},
        {"id": "ollama:qwen2.5-coder:14b", "name": "Qwen2.5 Coder 14B via Ollama", "family": "Qwen", "tag": "local-coding", "size": "pull required", "pullable": True},
        {"id": "ollama:gemma3:1b", "name": "Gemma 3 1B via Ollama", "family": "Gemma", "tag": "local-light", "size": "pull required", "pullable": True},
        {"id": "ollama:gemma3:4b", "name": "Gemma 3 4B via Ollama", "family": "Gemma", "tag": "local-server", "size": "pull required", "pullable": True},
        {"id": "ollama:gemma3:4b-it-q4_K_M", "name": "Gemma 3 4B q4_K_M via Ollama", "family": "Gemma", "tag": "quantized", "size": "pull required", "pullable": True},
        {"id": "ollama:gemma3:12b", "name": "Gemma 3 12B via Ollama", "family": "Gemma", "tag": "local-server", "size": "pull required", "pullable": True},
        {"id": "ollama:gemma3:12b-it-q4_K_M", "name": "Gemma 3 12B q4_K_M via Ollama", "family": "Gemma", "tag": "quantized", "size": "pull required", "pullable": True},
        {"id": "ollama:gemma3:27b", "name": "Gemma 3 27B via Ollama", "family": "Gemma", "tag": "local-large", "size": "pull required", "pullable": True},
        {"id": "ollama:llama3.2:1b", "name": "Llama 3.2 1B via Ollama", "family": "Llama 3.x", "tag": "local-light", "size": "pull required", "pullable": True},
        {"id": "ollama:llama3.2:3b", "name": "Llama 3.2 3B via Ollama", "family": "Llama 3.x", "tag": "local-server", "size": "pull required", "pullable": True},
        {"id": "ollama:llama3.1:8b", "name": "Llama 3.1 8B via Ollama", "family": "Llama 3.1", "tag": "local-server", "size": "pull required", "pullable": True},
        {"id": "ollama:llama3.1:8b-instruct-q4_0", "name": "Llama 3.1 8B q4_0 via Ollama", "family": "Llama 3.1", "tag": "quantized", "size": "pull required", "pullable": True},
        {"id": "ollama:llama3.1:8b-instruct-q8_0", "name": "Llama 3.1 8B q8_0 via Ollama", "family": "Llama 3.1", "tag": "quantized", "size": "pull required", "pullable": True},
        {"id": "ollama:llama3.1:70b", "name": "Llama 3.1 70B via Ollama", "family": "Llama 3.1", "tag": "local-server", "size": "pull required", "pullable": True},
        {"id": "ollama:llama3.3:70b", "name": "Llama 3.3 70B via Ollama", "family": "Llama 3.x", "tag": "local-large", "size": "pull required", "pullable": True},
        {"id": "ollama:mistral:7b", "name": "Mistral 7B via Ollama", "family": "Mistral", "tag": "local-server", "size": "pull required", "pullable": True},
        {"id": "ollama:mixtral:8x7b", "name": "Mixtral 8x7B via Ollama", "family": "Mistral", "tag": "local-large", "size": "pull required", "pullable": True},
        {"id": "ollama:phi4-mini", "name": "Phi 4 Mini via Ollama", "family": "Phi", "tag": "local-coding", "size": "pull required", "pullable": True},
        {"id": "ollama:phi4", "name": "Phi 4 via Ollama", "family": "Phi", "tag": "local-coding", "size": "pull required", "pullable": True},
        {"id": "ollama:smollm2:1.7b", "name": "SmolLM2 1.7B via Ollama", "family": "SmolLM", "tag": "local-light", "size": "pull required", "pullable": True},
        {"id": "ollama:deepseek-r1:1.5b", "name": "DeepSeek-R1 1.5B via Ollama", "family": "DeepSeek", "tag": "local-light", "size": "pull required", "pullable": True},
        {"id": "ollama:deepseek-r1:7b", "name": "DeepSeek-R1 7B via Ollama", "family": "DeepSeek", "tag": "local-reasoning", "size": "pull required", "pullable": True},
        {"id": "ollama:deepseek-r1:8b", "name": "DeepSeek-R1 8B via Ollama", "family": "DeepSeek", "tag": "local-reasoning", "size": "pull required", "pullable": True},
        {"id": "ollama:deepseek-r1:14b", "name": "DeepSeek-R1 14B via Ollama", "family": "DeepSeek", "tag": "local-reasoning", "size": "pull required", "pullable": True},
        {"id": "ollama:deepseek-r1:32b", "name": "DeepSeek-R1 32B via Ollama", "family": "DeepSeek", "tag": "local-large", "size": "pull required", "pullable": True},
        {"id": "ollama:deepseek-coder-v2:16b", "name": "DeepSeek-Coder-V2 16B via Ollama", "family": "DeepSeek", "tag": "local-coding", "size": "pull required", "pullable": True},
    ],
    "vllm": [
        {"id": "vllm:openai/gpt-oss-20b", "name": "GPT-OSS 20B via vLLM", "family": "GPT-OSS", "tag": "local-reasoning", "size": "server model", "pullable": True},
        {"id": "vllm:openai/gpt-oss-120b", "name": "GPT-OSS 120B via vLLM", "family": "GPT-OSS", "tag": "local-large", "size": "server model", "pullable": True},
        {"id": "vllm:Qwen/Qwen3-VL-4B-Instruct", "name": "Qwen3-VL 4B via vLLM", "family": "Qwen3-VL", "tag": "local-vlm", "size": "server model", "pullable": True},
        {"id": "vllm:Qwen/Qwen3-VL-8B-Instruct", "name": "Qwen3-VL 8B via vLLM", "family": "Qwen3-VL", "tag": "local-vlm", "size": "server model", "pullable": True},
        {"id": "vllm:Qwen/Qwen3-VL-30B-A3B-Instruct", "name": "Qwen3-VL 30B A3B via vLLM", "family": "Qwen3-VL", "tag": "local-vlm", "size": "server model", "pullable": True},
        {"id": "vllm:Qwen/Qwen2.5-VL-7B-Instruct", "name": "Qwen2.5-VL 7B via vLLM", "family": "Qwen2.5-VL", "tag": "local-vlm", "size": "server model", "pullable": True},
        {"id": "vllm:google/gemma-2-2b", "name": "Gemma 2 2B Base via vLLM", "family": "Gemma", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "vllm:google/gemma-2-2b-it", "name": "Gemma 2 2B via vLLM", "family": "Gemma", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "vllm:google/gemma-2-9b", "name": "Gemma 2 9B Base via vLLM", "family": "Gemma", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "vllm:google/gemma-2-9b-it", "name": "Gemma 2 9B via vLLM", "family": "Gemma", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "vllm:google/gemma-3-4b-it", "name": "Gemma 3 4B via vLLM", "family": "Gemma", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "vllm:google/gemma-3-12b-it", "name": "Gemma 3 12B via vLLM", "family": "Gemma", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "vllm:microsoft/Phi-3.5-mini-instruct", "name": "Phi 3.5 Mini via vLLM", "family": "Phi", "tag": "local-coding", "size": "server model", "pullable": True},
        {"id": "vllm:microsoft/Phi-4-mini-instruct", "name": "Phi 4 Mini via vLLM", "family": "Phi", "tag": "local-coding", "size": "server model", "pullable": True},
        {"id": "vllm:microsoft/phi-4", "name": "Phi 4 via vLLM", "family": "Phi", "tag": "local-coding", "size": "server model", "pullable": True},
        {"id": "vllm:mistralai/Mistral-7B-Instruct-v0.3", "name": "Mistral 7B via vLLM", "family": "Mistral", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "vllm:mistralai/Ministral-8B-Instruct-2410", "name": "Ministral 8B via vLLM", "family": "Mistral", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "vllm:mistralai/Mistral-Small-24B-Instruct-2501", "name": "Mistral Small 24B via vLLM", "family": "Mistral", "tag": "local-large", "size": "server model", "pullable": True},
        {"id": "vllm:meta-llama/Llama-3.2-3B-Instruct", "name": "Llama 3.2 3B via vLLM", "family": "Llama 3.x", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "vllm:meta-llama/Llama-3.1-8B-Instruct", "name": "Llama 3.1 8B via vLLM", "family": "Llama 3.1", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "vllm:meta-llama/Llama-3.3-70B-Instruct", "name": "Llama 3.3 70B via vLLM", "family": "Llama 3.x", "tag": "local-large", "size": "server model", "pullable": True},
        {"id": "vllm:meta-llama/Llama-3.1-70B-Instruct", "name": "Llama 3.1 70B via vLLM", "family": "Llama 3.1", "tag": "local-server", "size": "server model", "pullable": True},
    ],
    "lmstudio": [
        {"id": "lmstudio:openai/gpt-oss-20b", "name": "GPT-OSS 20B via LM Studio", "family": "GPT-OSS", "tag": "local-reasoning", "size": "server model", "pullable": True},
        {"id": "lmstudio:openai/gpt-oss-120b", "name": "GPT-OSS 120B via LM Studio", "family": "GPT-OSS", "tag": "local-large", "size": "server model", "pullable": True},
        {"id": "lmstudio:ggml-org/gemma-4-31B-it-GGUF", "name": "Gemma 4 31B 4-bit via LM Studio", "family": "Gemma 4", "tag": "local-vlm", "size": "server model", "pullable": True},
        {"id": "lmstudio:Qwen/Qwen3-VL-4B-Instruct", "name": "Qwen3-VL 4B via LM Studio", "family": "Qwen3-VL", "tag": "local-vlm", "size": "server model", "pullable": True},
        {"id": "lmstudio:Qwen/Qwen3-VL-8B-Instruct", "name": "Qwen3-VL 8B via LM Studio", "family": "Qwen3-VL", "tag": "local-vlm", "size": "server model", "pullable": True},
        {"id": "lmstudio:Qwen/Qwen3-VL-30B-A3B-Instruct", "name": "Qwen3-VL 30B A3B via LM Studio", "family": "Qwen3-VL", "tag": "local-vlm", "size": "server model", "pullable": True},
        {"id": "lmstudio:Qwen/Qwen2.5-VL-7B-Instruct", "name": "Qwen2.5-VL 7B via LM Studio", "family": "Qwen2.5-VL", "tag": "local-vlm", "size": "server model", "pullable": True},
        {"id": "lmstudio:google/gemma-2-2b-it", "name": "Gemma 2 2B via LM Studio", "family": "Gemma", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "lmstudio:google/gemma-2-9b-it", "name": "Gemma 2 9B via LM Studio", "family": "Gemma", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "lmstudio:google/gemma-3-4b-it", "name": "Gemma 3 4B via LM Studio", "family": "Gemma", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "lmstudio:google/gemma-3-12b-it", "name": "Gemma 3 12B via LM Studio", "family": "Gemma", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "lmstudio:microsoft/Phi-3.5-mini-instruct", "name": "Phi 3.5 Mini via LM Studio", "family": "Phi", "tag": "local-coding", "size": "server model", "pullable": True},
        {"id": "lmstudio:microsoft/Phi-4-mini-instruct", "name": "Phi 4 Mini via LM Studio", "family": "Phi", "tag": "local-coding", "size": "server model", "pullable": True},
        {"id": "lmstudio:microsoft/phi-4", "name": "Phi 4 via LM Studio", "family": "Phi", "tag": "local-coding", "size": "server model", "pullable": True},
        {"id": "lmstudio:mistralai/Mistral-7B-Instruct-v0.3", "name": "Mistral 7B via LM Studio", "family": "Mistral", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "lmstudio:mistralai/Ministral-8B-Instruct-2410", "name": "Ministral 8B via LM Studio", "family": "Mistral", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "lmstudio:mistralai/Mistral-Small-24B-Instruct-2501", "name": "Mistral Small 24B via LM Studio", "family": "Mistral", "tag": "local-large", "size": "server model", "pullable": True},
        {"id": "lmstudio:meta-llama/Llama-3.2-3B-Instruct", "name": "Llama 3.2 3B via LM Studio", "family": "Llama 3.x", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "lmstudio:meta-llama/Llama-3.1-8B-Instruct", "name": "Llama 3.1 8B via LM Studio", "family": "Llama 3.1", "tag": "local-server", "size": "server model", "pullable": True},
        {"id": "lmstudio:meta-llama/Llama-3.3-70B-Instruct", "name": "Llama 3.3 70B via LM Studio", "family": "Llama 3.x", "tag": "local-large", "size": "server model", "pullable": True},
        {"id": "lmstudio:meta-llama/Llama-3.1-70B-Instruct", "name": "Llama 3.1 70B via LM Studio", "family": "Llama 3.1", "tag": "local-server", "size": "server model", "pullable": True},
    ],
    "llamacpp": [
        {"id": "llamacpp:ggml-org/gpt-oss-20b-GGUF", "name": "GPT-OSS 20B GGUF via llama.cpp", "family": "GPT-OSS", "tag": "gguf-q4", "size": "gguf", "pullable": True},
        {"id": "llamacpp:ggml-org/gpt-oss-120b-GGUF", "name": "GPT-OSS 120B GGUF via llama.cpp", "family": "GPT-OSS", "tag": "gguf-q4", "size": "gguf", "pullable": True},
        {"id": "llamacpp:ggml-org/gemma-4-31B-it-GGUF", "name": "Gemma 4 31B GGUF via llama.cpp", "family": "Gemma 4", "tag": "gguf-q4", "size": "gguf", "pullable": True},
        {"id": "llamacpp:Qwen/Qwen3-VL-4B-Instruct-GGUF", "name": "Qwen3-VL 4B GGUF via llama.cpp", "family": "Qwen3-VL", "tag": "gguf-vlm", "size": "gguf", "pullable": True},
        {"id": "llamacpp:Qwen/Qwen3-VL-8B-Instruct-GGUF", "name": "Qwen3-VL 8B GGUF via llama.cpp", "family": "Qwen3-VL", "tag": "gguf-vlm", "size": "gguf", "pullable": True},
        {"id": "llamacpp:unsloth/gemma-2-2b-it-GGUF", "name": "Gemma 2 2B GGUF via llama.cpp", "family": "Gemma", "tag": "gguf-q4", "size": "gguf", "pullable": True},
        {"id": "llamacpp:unsloth/gemma-2-9b-it-GGUF", "name": "Gemma 2 9B GGUF via llama.cpp", "family": "Gemma", "tag": "gguf-q4", "size": "gguf", "pullable": True},
        {"id": "llamacpp:unsloth/gemma-3-4b-it-GGUF", "name": "Gemma 3 4B GGUF via llama.cpp", "family": "Gemma", "tag": "gguf-q4", "size": "gguf", "pullable": True},
        {"id": "llamacpp:bartowski/Mistral-7B-Instruct-v0.3-GGUF", "name": "Mistral 7B GGUF via llama.cpp", "family": "Mistral", "tag": "gguf-q4", "size": "gguf", "pullable": True},
        {"id": "llamacpp:bartowski/Phi-3.5-mini-instruct-GGUF", "name": "Phi 3.5 Mini GGUF via llama.cpp", "family": "Phi", "tag": "gguf-q4", "size": "gguf", "pullable": True},
        {"id": "llamacpp:bartowski/phi-4-GGUF", "name": "Phi 4 GGUF via llama.cpp", "family": "Phi", "tag": "gguf-q4", "size": "gguf", "pullable": True},
        {"id": "llamacpp:bartowski/Llama-3.2-3B-Instruct-GGUF", "name": "Llama 3.2 3B GGUF via llama.cpp", "family": "Llama 3.x", "tag": "gguf-q4", "size": "gguf", "pullable": True},
        {"id": "llamacpp:bartowski/Llama-3.1-8B-Instruct-GGUF", "name": "Llama 3.1 8B GGUF via llama.cpp", "family": "Llama 3.1", "tag": "local-server", "size": "gguf", "pullable": True},
        {"id": "llamacpp:bartowski/Llama-3.3-70B-Instruct-GGUF", "name": "Llama 3.3 70B GGUF via llama.cpp", "family": "Llama 3.x", "tag": "local-large", "size": "gguf", "pullable": True},
        {"id": "llamacpp:bartowski/Llama-3.1-70B-Instruct-GGUF", "name": "Llama 3.1 70B GGUF via llama.cpp", "family": "Llama 3.1", "tag": "local-server", "size": "gguf", "pullable": True},
        {"id": "llamacpp:unsloth/DeepSeek-R1-GGUF", "name": "DeepSeek-R1 GGUF via llama.cpp", "family": "DeepSeek", "tag": "gguf-q4", "size": "gguf", "pullable": True},
        {"id": "llamacpp:bartowski/DeepSeek-Coder-V2-Lite-Instruct-GGUF", "name": "DeepSeek-Coder-V2 Lite GGUF via llama.cpp", "family": "DeepSeek", "tag": "gguf-q4", "size": "gguf", "pullable": True},
    ],
}

MODEL_ENGINE_ALIASES = {
    "gpt-oss-20b": {
        "local_mlx": "mlx-community/gpt-oss-20b-MXFP4-Q8",
        "ollama": "gpt-oss:20b",
        "vllm": "openai/gpt-oss-20b",
        "lmstudio": "openai/gpt-oss-20b",
        "llamacpp": "ggml-org/gpt-oss-20b-GGUF",
    },
    "openai/gpt-oss-20b": {
        "local_mlx": "mlx-community/gpt-oss-20b-MXFP4-Q8",
        "ollama": "gpt-oss:20b",
        "vllm": "openai/gpt-oss-20b",
        "lmstudio": "openai/gpt-oss-20b",
        "llamacpp": "ggml-org/gpt-oss-20b-GGUF",
    },
    "gpt-oss-120b": {
        "local_mlx": "mlx-community/gpt-oss-120b-MXFP4-Q4",
        "ollama": "gpt-oss:120b",
        "vllm": "openai/gpt-oss-120b",
        "lmstudio": "openai/gpt-oss-120b",
        "llamacpp": "ggml-org/gpt-oss-120b-GGUF",
    },
    "openai/gpt-oss-120b": {
        "local_mlx": "mlx-community/gpt-oss-120b-MXFP4-Q4",
        "ollama": "gpt-oss:120b",
        "vllm": "openai/gpt-oss-120b",
        "lmstudio": "openai/gpt-oss-120b",
        "llamacpp": "ggml-org/gpt-oss-120b-GGUF",
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
}

_VERSIONED_MODEL_PATTERNS = (
    ("gemma", re.compile(r"\bgemma[-\s]?(\d+(?:\.\d+)?)", re.IGNORECASE)),
    ("qwen", re.compile(r"\bqwen[-\s]?(\d+(?:\.\d+)?)", re.IGNORECASE)),
    ("llama", re.compile(r"\bllama[-\s]?(\d+(?:\.\d+)?)", re.IGNORECASE)),
    ("phi", re.compile(r"\bphi[-\s]?(\d+(?:\.\d+)?)", re.IGNORECASE)),
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
