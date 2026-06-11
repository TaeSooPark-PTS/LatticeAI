"""Deprecation shim — the LLM router moved to ``latticeai.models.router`` in v4.

This root module remains importable for the deprecation window and will be
removed in a future major release. Import from ``latticeai.models.router``.
"""

from latticeai.models.router import *  # noqa: F401,F403
from latticeai.models.router import (  # noqa: F401 — explicit key surface
    AsyncOpenAI,
    BRAND_NAME,
    HF_MODELS_ROOT,
    LLMRouter,
    OPENAI_COMPATIBLE_PROVIDERS,
    SYSTEM_PROMPT,
    ensure_mlx_runtime,
    hf_model_dir,
    normalize_branding,
    parse_model_ref,
)

__all__ = [
    "AsyncOpenAI",
    "BRAND_NAME",
    "HF_MODELS_ROOT",
    "LLMRouter",
    "OPENAI_COMPATIBLE_PROVIDERS",
    "SYSTEM_PROMPT",
    "ensure_mlx_runtime",
    "hf_model_dir",
    "normalize_branding",
    "parse_model_ref",
]
