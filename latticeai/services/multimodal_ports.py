from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional

from lattice_brain.ingestion import ALLOW_MULTIMODAL_ENV
from lattice_brain.multimodal import MODALITY_IMAGE, MultimodalPorts
from latticeai.core.embedding_providers import (
    VISION_CAPTION_TARGET_ENV,
    resolve_vision_captioner,
    resolve_vision_embedder,
    vision_caption_port,
)

VISION_PROVIDER_ENV = "LATTICEAI_VISION_PROVIDER"
VISION_MODEL_ENV = "LATTICEAI_VISION_MODEL"
VISION_SPACE_ENV = "LATTICEAI_VISION_SPACE"
VISION_CAPTION_PROVIDER_ENV = "LATTICEAI_VISION_CAPTION_PROVIDER"
VISION_CAPTION_MODEL_ENV = "LATTICEAI_VISION_CAPTION_MODEL"

_TRUE = {"1", "true", "yes", "on"}


def multimodal_enabled() -> bool:
    return os.getenv(ALLOW_MULTIMODAL_ENV, "0").strip().lower() in _TRUE


def build_multimodal_ports(
    *, transcriber: Optional[Callable[[str], str]] = None
) -> MultimodalPorts:
    resolved = resolve_vision_embedder(
        os.getenv(VISION_PROVIDER_ENV, ""),
        model=os.getenv(VISION_MODEL_ENV, ""),
        space=os.getenv(VISION_SPACE_ENV, MODALITY_IMAGE),
    )
    captioner = resolve_vision_captioner(
        os.getenv(VISION_CAPTION_PROVIDER_ENV, ""),
        model=os.getenv(VISION_CAPTION_MODEL_ENV, ""),
        target=os.getenv(VISION_CAPTION_TARGET_ENV, ""),
    )
    provider = resolved.provider
    return MultimodalPorts(
        captioner=vision_caption_port(captioner),
        vision_embedder=resolved.as_port(),
        transcriber=transcriber,
        text_to_image_embedder=text_to_image_port(provider),
        vision_model_id=provider.model_id if provider is not None else "",
        vision_space=resolved.space,
    )


def text_to_image_port(
    provider: Any,
) -> Optional[Callable[[str], List[float]]]:
    if provider is None or not getattr(provider, "shares_text_space", False):
        return None

    def _embed(query: str) -> List[float]:
        vectors = provider.embed_batch([str(query or "")])
        return [float(value) for value in (vectors[0] if vectors else [])]

    return _embed


def describe_multimodal(ports: MultimodalPorts) -> Dict[str, Any]:
    return {"enabled": multimodal_enabled(), **ports.describe()}


__all__ = [
    "VISION_CAPTION_MODEL_ENV",
    "VISION_CAPTION_PROVIDER_ENV",
    "VISION_MODEL_ENV",
    "VISION_PROVIDER_ENV",
    "VISION_SPACE_ENV",
    "build_multimodal_ports",
    "describe_multimodal",
    "multimodal_enabled",
    "text_to_image_port",
]
