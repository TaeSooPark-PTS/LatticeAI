"""The production embedding profiles the setup and admin surfaces offer.

A literal table, deliberately: a profile is a *named, supported* combination of
provider, model and dimensionality, so the UI can offer a short list of things
known to work instead of asking the user to assemble one.
"""

from __future__ import annotations

from typing import Any, Dict, List

PRODUCTION_PROVIDER_PROFILES: Dict[str, Dict[str, Any]] = {
    # ── one-click local profiles (v12.0.0) ────────────────────────────────
    # These three carry `hf_repo_id` and `download_gb` because they are the
    # ones a setup surface can *offer to fetch*: the id is what
    # `huggingface_hub.snapshot_download` takes and what
    # `autodetect.detect_local_mlx` looks for in the cache, so "offer",
    # "download" and "detect" all name the same string. The rest of the table
    # describes providers the user has to bring themselves (an Ollama server,
    # an API key), which is why they have no repo id.
    "local:multilingual-e5-small": {
        "id": "local:multilingual-e5-small",
        "provider": "mlx",
        "model": "mlx-community/multilingual-e5-small-mlx",
        "hf_repo_id": "mlx-community/multilingual-e5-small-mlx",
        "download_gb": 0.24,
        "dimensions": 384,
        "grade": "production",
        "family": "local",
        "label": "Multilingual E5 Small (로컬)",
        "detail": "한국어를 포함한 100여 개 언어. 240MB, 해시 임베더와 같은 384차원.",
    },
    "local:multilingual-e5-base": {
        "id": "local:multilingual-e5-base",
        "provider": "mlx",
        "model": "mlx-community/multilingual-e5-base-mlx",
        "hf_repo_id": "mlx-community/multilingual-e5-base-mlx",
        "download_gb": 1.1,
        "dimensions": 768,
        "grade": "production",
        "family": "local",
        "label": "Multilingual E5 Base (로컬)",
        "detail": "더 정확한 다국어 임베딩. 768차원이라 기존 색인은 다시 만들어야 합니다.",
    },
    "local:arctic-embed-l-v2": {
        "id": "local:arctic-embed-l-v2",
        "provider": "mlx",
        "model": "mlx-community/snowflake-arctic-embed-l-v2.0-8bit",
        "hf_repo_id": "mlx-community/snowflake-arctic-embed-l-v2.0-8bit",
        "download_gb": 0.6,
        "dimensions": 1024,
        "grade": "production",
        "family": "local",
        "label": "Arctic Embed L v2 (로컬)",
        "detail": "다국어 고품질 검색용 임베딩. 1024차원.",
    },
    "local:bge-m3": {
        "id": "local:bge-m3",
        "provider": "mlx",
        "model": "bge-m3",
        "dimensions": 1024,
        "grade": "production",
        "family": "local",
        "label": "BGE-M3 local",
        "detail": "Multilingual semantic embeddings for local retrieval.",
    },
    "local:nomic-embed-text": {
        "id": "local:nomic-embed-text",
        "provider": "ollama",
        "model": "nomic-embed-text",
        "dimensions": 768,
        "grade": "production",
        "family": "local",
        "label": "Nomic Embed Text local",
        "detail": "General-purpose local semantic embeddings.",
    },
    "local:e5-large": {
        "id": "local:e5-large",
        "provider": "mlx",
        "model": "e5-large",
        "dimensions": 1024,
        "grade": "production",
        "family": "local",
        "label": "E5 Large local",
        "detail": "High-recall local retrieval profile.",
    },
    "local:gte-large": {
        "id": "local:gte-large",
        "provider": "mlx",
        "model": "gte-large",
        "dimensions": 1024,
        "grade": "production",
        "family": "local",
        "label": "GTE Large local",
        "detail": "Large local semantic embedding profile.",
    },
    "ollama:nomic-embed-text": {
        "id": "ollama:nomic-embed-text",
        "provider": "ollama",
        "model": "nomic-embed-text",
        "dimensions": 768,
        "grade": "production",
        "family": "ollama",
        "label": "Ollama Nomic Embed Text",
        "detail": "Production semantic embeddings through Ollama.",
    },
    "ollama:mxbai-embed-large": {
        "id": "ollama:mxbai-embed-large",
        "provider": "ollama",
        "model": "mxbai-embed-large",
        "dimensions": 1024,
        "grade": "production",
        "family": "ollama",
        "label": "Ollama MXBAI Embed Large",
        "detail": "High-quality local semantic embeddings through Ollama.",
    },
    "ollama:bge-m3": {
        "id": "ollama:bge-m3",
        "provider": "ollama",
        "model": "bge-m3",
        "dimensions": 1024,
        "grade": "production",
        "family": "ollama",
        "label": "Ollama BGE-M3-compatible",
        "detail": "BGE-M3-compatible providers exposed through Ollama.",
    },
    "mlx:bge-m3": {
        "id": "mlx:bge-m3",
        "provider": "mlx",
        "model": "bge-m3",
        "dimensions": 1024,
        "grade": "production",
        "family": "mlx",
        "label": "MLX BGE-M3",
        "detail": "Apple Silicon optimized local embeddings.",
    },
    "openai:text-embedding-3-small": {
        "id": "openai:text-embedding-3-small",
        "provider": "openai",
        "model": "text-embedding-3-small",
        "dimensions": 1536,
        "grade": "production",
        "family": "openai-compatible",
        "label": "OpenAI-compatible small",
        "detail": "OpenAI-compatible /v1/embeddings endpoint.",
    },
    "openai:text-embedding-3-large": {
        "id": "openai:text-embedding-3-large",
        "provider": "openai",
        "model": "text-embedding-3-large",
        "dimensions": 3072,
        "grade": "production",
        "family": "openai-compatible",
        "label": "OpenAI-compatible large",
        "detail": "Highest-dimensional OpenAI-compatible embedding profile.",
    },
}


def embedding_provider_profiles() -> List[Dict[str, Any]]:
    return [dict(PRODUCTION_PROVIDER_PROFILES[key]) for key in sorted(PRODUCTION_PROVIDER_PROFILES)]


def resolve_embedding_profile(profile: str) -> Dict[str, Any]:
    if not profile:
        return {}
    key = str(profile).strip().lower()
    if key in PRODUCTION_PROVIDER_PROFILES:
        return dict(PRODUCTION_PROVIDER_PROFILES[key])
    raise ValueError(f"unknown embedding profile: {profile!r}")
