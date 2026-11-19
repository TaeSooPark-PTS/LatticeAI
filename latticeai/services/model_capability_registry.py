"""Structured Model Capability Registry for Lattice AI.

User-focused, transparent model catalog with:
- HF repo provenance (exact-case repo id, pinned against the HF API)
- Modality / vision support and the HF config architecture (``model_type``)
- Quantization, size, download/load strategies
- Hardware notes (RAM estimates, Apple Silicon affinity)
- License / safety notes
- Verification status (populated by scripts/verify_hf_model_registry.py or runtime light probe)

This replaces the flat ENGINE_MODEL_CATALOG construction with a richer,
queryable source of truth while preserving exact legacy shapes for
model_catalog / recommendation / API / frontend consumers.

11.2.0 — two lists, one registry
================================
A model can be *offered* or merely *understood*, and conflating the two either
pushes dead downloads at people or orphans the weights already on their disk.
Every entry therefore carries a :attr:`ModelCapability.lifecycle`:

``RECOMMENDED``
    Current generation. Appears in ENGINE_MODEL_CATALOG, the recommendation
    tiers, and the download paths.
``LEGACY``
    Superseded, but real and still loadable. **Never** offered for download and
    never recommended — it exists so a model a user already downloaded keeps its
    name, size, family and runtime profile instead of showing up as an unknown
    blob. See :func:`get_legacy_capabilities` / :func:`is_recognized_model`.

Models that no longer exist on the Hub, or that cannot be fetched without
credentials (gated), are **deleted outright** rather than demoted: pretending to
recognise something nobody can obtain is not compatibility, it is noise.

Verification is honest: hf_exists + metadata/siblings presence measured through
the public HF REST API; full weights are never auto-fetched by the verifier and
no model is ever loaded to produce these flags. Large models (>12GB) explicitly
note "local load practical only on high-RAM Apple Silicon or CUDA; expect long
download".
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .embedding_recommendation import (
    recommend_embedding_for_hardware as _rec_embedding_for_hardware,
)
from .model_capability_catalog import _LEGACY_REGISTRY, _REGISTRY
from .model_capability_types import (
    LEGACY,
    RECOMMENDED,
    HardwareProfile,
    ModelCapability,
    VerificationStatus,
)


def get_all_capabilities() -> List[ModelCapability]:
    """Every entry the registry knows — recommended *and* recognised-only.

    This is the verification surface: ``scripts/verify_hf_model_registry.py``
    re-measures all of it, because a legacy entry that quietly disappeared from
    the Hub should be reported rather than kept as a comforting fiction.
    """
    return [*_REGISTRY, *_LEGACY_REGISTRY]


def get_legacy_capabilities() -> List[ModelCapability]:
    """Recognised-only entries: never offered, never recommended, still named."""
    return list(_LEGACY_REGISTRY)


def get_capability(model_id: str) -> Optional[ModelCapability]:
    """Look an id up across both lists — recognition covers legacy weights too."""
    for m in get_all_capabilities():
        if m.id == model_id or m.hf_repo_id == model_id:
            return m
    return None


def is_recognized_model(model_id: str) -> bool:
    """True when the registry can name this model, whatever its lifecycle.

    Used by the load path: a model already on disk must keep working even after
    its generation stops being offered.
    """
    return get_capability(model_id) is not None


def build_engine_model_catalog() -> Dict[str, List[Dict[str, Any]]]:
    """Return legacy ENGINE_MODEL_CATALOG shape, enriched with rich fields.

    Built from the **recommended** list only: the catalog is what the product
    offers, and offering a superseded generation is how users end up downloading
    something we would not stand behind.
    """
    from collections import defaultdict
    by_engine: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    engine_map = {
        "local_mlx": ["local_mlx"],
        "ollama": ["ollama"],
        "vllm": ["vllm"],
        "lmstudio": ["lmstudio"],
        "llamacpp": ["llamacpp"],
    }

    for cap in _REGISTRY:
        for eng_key, hints in engine_map.items():
            if any(h in cap.provider_hints for h in hints) or eng_key in cap.provider_hints:
                legacy = cap.to_legacy_dict()
                # Adapt id for non-mlx engines. These are provisional prefixed
                # ids; `model_catalog._normalize_engine_entry` then replaces them
                # with the verified per-engine repo from MODEL_ENGINE_ALIASES.
                # (Until 11.2.0 the ollama branch *fabricated* a repo path from
                # the family name — `ggml-org/<family>-12B-it-GGUF` — which
                # resolved to a 404 for anything but Gemma 4 12B.)
                if eng_key == "ollama" and not legacy["id"].startswith("ollama:"):
                    legacy["id"] = f"ollama:{cap.hf_repo_id.split('/')[-1].lower()}"
                elif eng_key == "vllm" and not legacy["id"].startswith("vllm:"):
                    legacy["id"] = f"vllm:{cap.hf_repo_id}"
                elif eng_key == "lmstudio" and not legacy["id"].startswith("lmstudio:"):
                    legacy["id"] = f"lmstudio:{cap.hf_repo_id}"
                elif eng_key == "llamacpp" and not legacy["id"].startswith("llamacpp:"):
                    legacy["id"] = f"llamacpp:{cap.hf_repo_id}-GGUF"
                by_engine[eng_key].append(legacy)

    return dict(by_engine)


def get_verified_models() -> List[Dict[str, Any]]:
    """Return only load-verified recommended entries with rich fields (API/UI)."""
    return [
        c.to_legacy_dict() for c in _REGISTRY
        if c.verification.hf_exists and c.verification.has_config and c.verification.has_tokenizer
    ]


def get_embedding_capabilities() -> List[ModelCapability]:
    """Return recommended embedding model capabilities (Google EmbeddingGemma 2 / 300M)."""
    return [c for c in _REGISTRY if c.category == "embedding"]


def recommend_embedding_for_hardware(
    *,
    ram_gb: Optional[float] = None,
    apple_silicon: bool = False,
    cuda: bool = False,
) -> Dict[str, Any]:
    """Return a PC-tailored embedding model recommendation accounting for RAM & accelerator."""
    return _rec_embedding_for_hardware(
        ram_gb=ram_gb,
        apple_silicon=apple_silicon,
        cuda=cuda,
        get_capability_fn=get_capability,
    )



__all__ = [
    "HardwareProfile",
    "LEGACY",
    "ModelCapability",
    "RECOMMENDED",
    "VerificationStatus",
    "_LEGACY_REGISTRY",
    "_REGISTRY",
    "build_engine_model_catalog",
    "get_all_capabilities",
    "get_capability",
    "get_embedding_capabilities",
    "get_legacy_capabilities",
    "get_verified_models",
    "is_recognized_model",
    "recommend_embedding_for_hardware",
]
