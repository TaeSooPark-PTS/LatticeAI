from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from lattice_brain.multimodal import MultimodalPorts

LOGGER = logging.getLogger(__name__)

__all__ = [
    "VoiceCaptureService",
    "TranscriptionUnavailable",
    "SUPPORTED_AUDIO_EXTENSIONS",
]

SUPPORTED_AUDIO_EXTENSIONS = frozenset(
    {
        ".m4a",
        ".mp3",
        ".wav",
        ".aac",
        ".flac",
        ".ogg",
        ".webm",
        ".mp4",
    }
)

MAX_AUDIO_BYTES = 50 * 1024 * 1024


class TranscriptionUnavailable(RuntimeError):
    pass


class VoiceCaptureService:
    def __init__(self, *, transcriber: Optional[Callable[[str], str]] = None) -> None:
        self._transcriber = transcriber

    def multimodal_ports(self) -> "MultimodalPorts":
        from lattice_brain.multimodal import MultimodalPorts

        return MultimodalPorts(transcriber=self._transcriber)

    def _transcribe(self, path: Path) -> str:
        if self._transcriber is None:
            raise TranscriptionUnavailable("no local transcriber is configured")
        text = self._transcriber(str(path))
        cleaned = str(text or "").strip()
        if not cleaned:
            raise TranscriptionUnavailable("the transcriber returned no text")
        return cleaned


__all__ = [
    "VoiceCaptureService",
    "TranscriptionUnavailable",
    "SUPPORTED_AUDIO_EXTENSIONS",
]
