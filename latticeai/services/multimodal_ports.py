"""Where the app decides what it can actually see and hear (v11.1.0).

Brain Core owns the *shape* of a multi-modal memory; it deliberately owns none
of the models. This module is the one place that turns configuration into the
plain callables :class:`lattice_brain.multimodal.MultimodalPorts` accepts, so
the ingestion pipeline never learns that ``latticeai`` exists.

Everything here is off by default and costs nothing when off: with no vision
provider configured, :func:`build_multimodal_ports` does no model loading, no
imports of optional packages, and no network calls — it returns a bundle whose
every capability is honestly ``None``.
"""

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

#: ``mlx`` | ``custom`` | "" (off). Never defaulted to something that loads.
VISION_PROVIDER_ENV = "LATTICEAI_VISION_PROVIDER"
VISION_MODEL_ENV = "LATTICEAI_VISION_MODEL"
#: ``image`` (own index + late fusion) or ``shared`` (same space as text).
VISION_SPACE_ENV = "LATTICEAI_VISION_SPACE"
VISION_CAPTION_PROVIDER_ENV = "LATTICEAI_VISION_CAPTION_PROVIDER"
VISION_CAPTION_MODEL_ENV = "LATTICEAI_VISION_CAPTION_MODEL"

_TRUE = {"1", "true", "yes", "on"}


def multimodal_enabled() -> bool:
    """Whether pictures and recordings may be ingested at all (default no)."""
    return os.getenv(ALLOW_MULTIMODAL_ENV, "0").strip().lower() in _TRUE


def build_multimodal_ports(
    *, transcriber: Optional[Callable[[str], str]] = None
) -> MultimodalPorts:
    """Resolve the vision/audio capabilities this install really has.

    ``transcriber`` comes from :class:`~latticeai.services.voice_capture.
    VoiceCaptureService` so a voice memo and a scanned ``.m4a`` are transcribed
    by the same thing — or, far more often, by the same nothing.
    """
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
    """A *query-text* → image-space vector port, only from a shared-space model.

    This is what lets a typed question reach a picture through the image index
    instead of only through its OCR text. It exists as its own port precisely
    because most vision models cannot do it: a CLIP image vector and a BGE text
    vector are not comparable, and `VisionEmbeddingProvider.embed_batch` refuses
    outright unless the model declares a shared space. An image-space model
    therefore yields ``None`` here — the honest answer, and the one the search
    surface reports rather than silently ranking on a meaningless number.
    """
    if provider is None or not getattr(provider, "shares_text_space", False):
        return None

    def _embed(query: str) -> List[float]:
        vectors = provider.embed_batch([str(query or "")])
        return [float(value) for value in (vectors[0] if vectors else [])]

    return _embed


def describe_multimodal(ports: MultimodalPorts) -> Dict[str, Any]:
    """Operator-facing status: enabled, plus what is actually wired."""
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
