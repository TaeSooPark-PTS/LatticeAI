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
    return [*_REGISTRY, *_LEGACY_REGISTRY]


def get_legacy_capabilities() -> List[ModelCapability]:
    return list(_LEGACY_REGISTRY)


def get_capability(model_id: str) -> Optional[ModelCapability]:
    for m in get_all_capabilities():
        if m.id == model_id or m.hf_repo_id == model_id:
            return m
    return None


def is_recognized_model(model_id: str) -> bool:
    return get_capability(model_id) is not None


def build_engine_model_catalog() -> Dict[str, List[Dict[str, Any]]]:
    from collections import defaultdict

    by_engine: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    engine_map = {
        "local_mlx": ["local_mlx"],
        "omlx": ["omlx"],
        "ollama": ["ollama"],
        "vllm": ["vllm"],
        "lmstudio": ["lmstudio"],
        "llamacpp": ["llamacpp"],
    }

    for cap in _REGISTRY:
        for eng_key, hints in engine_map.items():
            if (
                any(h in cap.provider_hints for h in hints)
                or eng_key in cap.provider_hints
            ):
                legacy = cap.to_legacy_dict()
                if eng_key == "omlx" and not legacy["id"].startswith("omlx:"):
                    legacy["id"] = f"omlx:{cap.hf_repo_id.replace('/', '--')}"
                elif eng_key == "ollama" and not legacy["id"].startswith("ollama:"):
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
    return [
        c.to_legacy_dict()
        for c in _REGISTRY
        if c.verification.hf_exists
        and c.verification.has_config
        and c.verification.has_tokenizer
    ]


def get_embedding_capabilities() -> List[ModelCapability]:
    return [c for c in _REGISTRY if c.category == "embedding"]


def recommend_embedding_for_hardware(
    *,
    ram_gb: Optional[float] = None,
    apple_silicon: bool = False,
    cuda: bool = False,
) -> Dict[str, Any]:
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
