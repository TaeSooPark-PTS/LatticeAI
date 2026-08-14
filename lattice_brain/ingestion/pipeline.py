"""What this machine will do with a picture, a recording, or a video.

    Source → normalize → content hash → (file | text) ingest → provenance

That was this module: :meth:`IngestionPipeline.ingest` owned normalization, the
gate reads, idempotency reporting, provenance capture, the advisory quality
annotation, and routing every ingest through the shared ``dispatch_tool``
lifecycle. The per-source doors lived in ``routing.py``, the folder walk in
``folders.py`` and background scheduling in ``jobs_api.py``.

v11.6.0 §Wave 2.5 made Rust the single writer of the Brain, so the whole write
side moved to ``lattice-core``'s graph write engine and ``lattice-jobs``. What
a stateless worker still owns is the *capability question* behind
``GET /api/ingestion/multimodal``: which gates are on, which model-backed ports
actually resolved, and — if a video would be refused — which of the three
reasons applies. The answer is derived from this process's environment and its
injected ports, which is why it is still answered here and not natively.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..multimodal import (
    DEFAULT_KEYFRAMES,
    VIDEO_UNAVAILABLE_DETAIL,
    MultimodalPorts,
    ffmpeg_available,
)
from .constants import (
    ALLOW_MULTIMODAL_ENV,
    ALLOW_VIDEO_ENV,
    MULTIMODAL_GATE,
    VIDEO_GATE,
)


class IngestionPipeline:
    """The multi-modal capability probe. Writes nothing, holds no store."""

    def __init__(
        self,
        *,
        allow_multimodal: bool = False,
        multimodal: Optional[MultimodalPorts] = None,
    ) -> None:
        # Multi-modal routing. Off unless the caller asks for it *or* the gate
        # says yes — the env behind that gate is the escape hatch for an
        # install with no code path to the constructor (CLI, background
        # worker), and the gate is asked per call so a runtime toggle can reach
        # it. A constructor ``True`` is still a permanent yes.
        self._multimodal_opt_in = bool(allow_multimodal)
        self._multimodal = multimodal or MultimodalPorts()
        self._keyframes = DEFAULT_KEYFRAMES

    @property
    def _allow_multimodal(self) -> bool:
        """Whether pictures and recordings route by modality, asked *now*."""
        return self._multimodal_opt_in or MULTIMODAL_GATE.enabled()

    @property
    def _allow_video(self) -> bool:
        """Video needs multi-modal on, its own sub-switch on, and a decoder."""
        return self._allow_multimodal and VIDEO_GATE.enabled() and self._can_decode_video()

    def _can_decode_video(self) -> bool:
        """An injected keyframe port counts as a decoder; otherwise, ffmpeg."""
        return self._multimodal.keyframe_extractor is not None or ffmpeg_available()

    def multimodal_status(self) -> Dict[str, Any]:
        """What this pipeline will do with a picture or a recording, honestly.

        ``enabled`` is the flag; the rest is which model-backed capabilities
        were actually injected. Video reports whether it can really run — the
        answer is no on a machine with no ffmpeg, and it says which of the two
        reasons applies rather than leaving the surface to guess.
        """
        allowed = self._allow_multimodal
        video = self._allow_video
        return {
            "enabled": allowed,
            "image": allowed,
            "audio": allowed,
            "video": video,
            "video_detail": None if video else self._video_refusal(),
            "gates": {
                "multimodal": MULTIMODAL_GATE.describe(),
                "video": VIDEO_GATE.describe(),
            },
            **self._multimodal.describe(),
        }

    def _video_refusal(self) -> str:
        """Why a video would be refused right now — never a stale reason."""
        if not self._allow_multimodal:
            return (
                "multi-modal ingestion is off; pictures, recordings and videos "
                f"are only stored when {ALLOW_MULTIMODAL_ENV} is on"
            )
        if not VIDEO_GATE.enabled():
            return (
                "video ingestion is turned off for this install "
                f"({ALLOW_VIDEO_ENV}); pictures and recordings are unaffected"
            )
        return VIDEO_UNAVAILABLE_DETAIL


__all__ = ["IngestionPipeline"]
