"""Descriptions of an image — what a vision-language model said, or nothing.

Every "clever" fallback here would be a lie: ``Image IMG_2381.png (JPEG
3024x4032)`` is metadata wearing a caption's clothes, and once it is in the
graph nothing downstream can tell it from a model's actual description. So the
base :class:`VisionCaptioner` returns ``None`` and reports itself unavailable,
and ``vision_caption_port`` hands Brain Core ``None`` rather than a decoy.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, Optional, Tuple

from .base import EmbeddingUnavailable, _load_dotted

VISION_CAPTION_TARGET_ENV = "LATTICEAI_VISION_CAPTION_TARGET"


class VisionCaptioner:
    """Describes an image — the null implementation, which describes nothing.

    Every "clever" fallback here is a lie: ``Image IMG_2381.png (JPEG
    3024x4032)`` is metadata wearing a caption's clothes, and once it is in the
    graph nothing downstream can tell it from a model's actual description. So
    the base class returns ``None`` and :meth:`available` says ``False``.
    """

    provider = "none"
    model_id = ""

    def available(self) -> bool:
        return False

    def caption(self, path: str, *, prompt: str = "") -> Optional[str]:
        return None

    def health(self) -> Dict[str, Any]:
        return {"status": "unavailable", "detail": "no vision-language model is loaded"}

    def metadata(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model_id,
            "available": self.available(),
        }


#: Short, literal instruction — a caption is a description, not an essay.
DEFAULT_CAPTION_PROMPT = "Describe this image in one factual sentence."


class MLXVisionCaptioner(VisionCaptioner):
    """Caption through a locally loaded ``mlx_vlm`` model (guarded import)."""

    provider = "mlx-vlm"

    def __init__(self, model: str, *, prompt: str = DEFAULT_CAPTION_PROMPT, max_tokens: int = 64):
        self.model_id = str(model or "")
        self._prompt = prompt or DEFAULT_CAPTION_PROMPT
        self._max_tokens = max(1, int(max_tokens))
        self._loaded: Optional[Tuple[Any, Any]] = None

    def _load(self) -> Tuple[Any, Any]:
        if self._loaded is not None:
            return self._loaded
        try:  # optional dependency (`pip install "ltcai[local]"`)
            from mlx_vlm import load as vlm_load  # type: ignore

            model, processor = vlm_load(self.model_id)
            self._loaded = (model, processor)
        except Exception as exc:
            raise EmbeddingUnavailable(f"vision-language model unavailable: {exc}") from exc
        return self._loaded

    def available(self) -> bool:
        try:
            self._load()
            return True
        except EmbeddingUnavailable:
            return False

    def caption(self, path: str, *, prompt: str = "") -> Optional[str]:
        try:
            model, processor = self._load()
            from mlx_vlm import generate as vlm_generate  # type: ignore

            text = vlm_generate(
                model,
                processor,
                str(path),
                prompt or self._prompt,
                max_tokens=self._max_tokens,
            )
        except Exception:
            # A caption the model did not produce is not a caption. Absence is
            # the honest answer, and every caller already handles it.
            return None
        cleaned = str(text or "").strip()
        return cleaned or None

    def health(self) -> Dict[str, Any]:
        try:
            self._load()
            return {"status": "ok", "detail": f"VLM {self.model_id} loaded"}
        except EmbeddingUnavailable as exc:
            return {"status": "unavailable", "detail": str(exc)}


class CustomVisionCaptioner(VisionCaptioner):
    """A user-supplied ``module:callable`` that captions an image path."""

    provider = "custom-vlm"

    def __init__(self, target: str = ""):
        self._target_ref = str(target or os.getenv(VISION_CAPTION_TARGET_ENV, ""))
        self.model_id = self._target_ref
        self._fn: Optional[Callable[..., Any]] = None

    def _load(self) -> Callable[..., Any]:
        if self._fn is None:
            self._fn = _load_dotted(self._target_ref, VISION_CAPTION_TARGET_ENV, "vision caption")
        return self._fn

    def available(self) -> bool:
        try:
            self._load()
            return True
        except EmbeddingUnavailable:
            return False

    def caption(self, path: str, *, prompt: str = "") -> Optional[str]:
        try:
            text = self._load()(str(path), prompt or DEFAULT_CAPTION_PROMPT)
        except Exception:
            return None
        cleaned = str(text or "").strip()
        return cleaned or None

    def health(self) -> Dict[str, Any]:
        try:
            self._load()
            return {"status": "ok", "detail": f"custom captioner {self._target_ref} loaded"}
        except EmbeddingUnavailable as exc:
            return {"status": "unavailable", "detail": str(exc)}


def resolve_vision_captioner(
    provider: str = "", *, model: str = "", target: str = ""
) -> VisionCaptioner:
    """Build a captioner, or the null one that honestly captions nothing."""
    kind = str(provider or "").strip().lower()
    if kind in {"mlx", "mlx-vlm", "mlx_vlm"} and model:
        return MLXVisionCaptioner(model)
    if kind in {"custom", "custom-vlm"}:
        return CustomVisionCaptioner(target)
    return VisionCaptioner()


def vision_caption_port(captioner: VisionCaptioner) -> Optional[Callable[[str], Optional[str]]]:
    """The caption seam Brain Core injects — ``None`` when no VLM is loaded."""
    return captioner.caption if captioner.available() else None
