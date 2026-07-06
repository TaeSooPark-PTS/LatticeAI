"""Cloud LLM provider catalog — static data split out of the router.

Pure config dicts (OpenAI-compatible providers, per-provider model
catalog, model→source family map). router.py re-exports these, so
``from latticeai.models.router import OPENAI_COMPATIBLE_PROVIDERS`` and the
model_runtime re-export chain are unaffected.
"""

from __future__ import annotations

OPENAI_COMPATIBLE_PROVIDERS = {
    "openai": {
        "env_key": "OPENAI_API_KEY",
        "base_url_env": "OPENAI_BASE_URL",
        "default_model": "gpt-4o-mini",
    },
    "openrouter": {
        "env_key": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "openai/gpt-4o-mini",
    },
    "groq": {
        "env_key": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "meta-llama/llama-4-scout-17b-16e-instruct",
    },
    "together": {
        "env_key": "TOGETHER_API_KEY",
        "base_url": "https://api.together.xyz/v1",
        "default_model": "Qwen/Qwen3-VL-32B-Instruct",
    },
    "xai": {
        "env_key": "XAI_API_KEY",
        "base_url": "https://api.x.ai/v1",
        "default_model": "grok-beta",
    },
    "ollama": {
        "env_key": "OLLAMA_API_KEY",
        "base_url_env": "OLLAMA_BASE_URL",
        "base_url": "http://localhost:11434/v1",
        "default_model": "hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_K_M",
        "api_key_fallback": "ollama",
    },
    "vllm": {
        "env_key": "VLLM_API_KEY",
        "base_url_env": "VLLM_BASE_URL",
        "base_url": "http://localhost:8000/v1",
        "default_model": "Qwen/Qwen3-VL-8B-Instruct",
        "api_key_fallback": "vllm",
    },
    "lmstudio": {
        "env_key": "LMSTUDIO_API_KEY",
        "base_url_env": "LMSTUDIO_BASE_URL",
        "base_url": "http://localhost:1234/v1",
        "default_model": "local-model",
        "api_key_fallback": "lmstudio",
    },
    "llamacpp": {
        "env_key": "LLAMACPP_API_KEY",
        "base_url_env": "LLAMACPP_BASE_URL",
        "base_url": "http://localhost:8080/v1",
        "default_model": "llama.cpp-model",
        "api_key_fallback": "llamacpp",
    },
}

PROVIDER_MODEL_CATALOG = {
    "openai": [
        {"id": "gpt-5.5", "name": "GPT-5.5", "family": "GPT"},
        {"id": "gpt-5.4", "name": "GPT-5.4", "family": "GPT"},
        {"id": "gpt-5.4-mini", "name": "GPT-5.4 Mini", "family": "GPT"},
        {"id": "gpt-5.4-nano", "name": "GPT-5.4 Nano", "family": "GPT"},
        {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "family": "GPT"},
        {"id": "gpt-4o", "name": "GPT-4o", "family": "GPT"},
        {"id": "gpt-4.1-mini", "name": "GPT-4.1 Mini", "family": "GPT"},
        {"id": "gpt-4.1", "name": "GPT-4.1", "family": "GPT"},
    ],
    "openrouter": [
        {"id": "openai/gpt-5.5", "name": "GPT-5.5 via OpenRouter", "family": "GPT"},
        {"id": "openai/gpt-4o-mini", "name": "GPT-4o Mini via OpenRouter", "family": "GPT"},
        {"id": "anthropic/claude-opus-4.7", "name": "Claude Opus 4.7 via OpenRouter", "family": "Claude"},
        {"id": "anthropic/claude-sonnet-4.6", "name": "Claude Sonnet 4.6 via OpenRouter", "family": "Claude"},
        {"id": "anthropic/claude-haiku-4.5", "name": "Claude Haiku 4.5 via OpenRouter", "family": "Claude"},
        {"id": "qwen/qwen3-vl-235b-a22b-instruct", "name": "Qwen3-VL 235B A22B via OpenRouter", "family": "Qwen"},
        {"id": "google/gemma-4-12b-it", "name": "Gemma 4 12B via OpenRouter", "family": "Gemma"},
        {"id": "x-ai/grok-2", "name": "Grok 2 via OpenRouter", "family": "Grok"},
        {"id": "meta-llama/llama-4-scout-17b-16e-instruct", "name": "Llama 4 Scout via OpenRouter", "family": "Llama"},
        {"id": "google/gemini-2.5-flash", "name": "Gemini 2.5 Flash via OpenRouter", "family": "Gemini"},
    ],
    "groq": [
        {"id": "meta-llama/llama-4-scout-17b-16e-instruct", "name": "Llama 4 Scout", "family": "Llama"},
    ],
    "together": [
        {"id": "Qwen/Qwen3-VL-32B-Instruct", "name": "Qwen3-VL 32B", "family": "Qwen"},
        {"id": "google/gemma-4-12b-it", "name": "Gemma 4 12B", "family": "Gemma"},
        {"id": "meta-llama/Llama-4-Scout-17B-16E-Instruct", "name": "Llama 4 Scout", "family": "Llama"},
    ],
    "xai": [
        {"id": "grok-beta", "name": "Grok Beta", "family": "Grok"},
        {"id": "grok-vision-beta", "name": "Grok Vision Beta", "family": "Grok"},
    ],
}

MODEL_SOURCE_BY_FAMILY = {
    "GPT": ("미국", "OpenAI"),
    "Claude": ("미국", "Anthropic"),
    "Qwen": ("중국", "Alibaba"),
    "Llama": ("미국", "Meta"),
    "Gemini": ("미국", "Google"),
    "Grok": ("미국", "xAI"),
}
