"""The production embedding profiles the setup and admin surfaces offer.

A literal table, deliberately: a profile is a *named, supported* combination of
provider, model and dimensionality, so the UI can offer a short list of things
known to work instead of asking the user to assemble one.
"""

from __future__ import annotations

from typing import Any, Dict, List

PRODUCTION_PROVIDER_PROFILES: Dict[str, Dict[str, Any]] = {
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
