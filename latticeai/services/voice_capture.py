"""Voice memo capture — the fastest path from a thought to the Brain (v9.9.7).

Typing is the slowest part of capture. A 15-second voice memo on a phone or a
laptop is the shortest distance between "I should remember this" and the Brain
actually having it.

The hard constraint is local-first: a voice memo must never leave the machine
to become text. So transcription is an **injected, optional port**:

* when a local transcriber is available, the memo is transcribed and ingested
  exactly like a typed note — same pipeline, same provenance, same quality
  signals;
* when it is not, the audio is still stored and indexed by whatever the user
  gave us (title, tags), and the result says ``transcription="unavailable"``
  with the reason. **A missing transcriber never produces an invented
  transcript**, and the memo is never silently dropped either.

Nothing here reaches for a cloud speech API, and nothing here installs a model
behind the user's back — an absent transcriber is a reported state, not a
prompt to download something.

v11.1.0: that same injected port is now the *only* transcription seam in the
product. When multi-modal ingestion is enabled, a ``.m4a`` found by a folder
scan is transcribed by whatever this service was given — see
:meth:`VoiceCaptureService.multimodal_ports`. One transcriber, one honesty
story: if a voice memo cannot become text here, a scanned recording cannot
either, and both say so the same way.

v11.6.0 took the *ingest* half away: ``POST /api/capture/voice`` is a native
route that stores the memo itself and asks ``POST /worker/asr`` for the words.
What is left is the transcriber and the two questions only this process can
answer about it — can it hear, and what will it accept.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

if TYPE_CHECKING:  # import-time isolation: the port is a callable, not a class
    from lattice_brain.multimodal import MultimodalPorts

LOGGER = logging.getLogger(__name__)

__all__ = ["VoiceCaptureService", "TranscriptionUnavailable", "SUPPORTED_AUDIO_EXTENSIONS"]

# Container formats a local transcriber can plausibly open. Anything else is
# refused with a clear reason rather than handed to a transcriber that will
# fail obscurely.
SUPPORTED_AUDIO_EXTENSIONS = frozenset({
    ".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".webm", ".mp4",
})

# A memo is short by nature; a huge file is a recording session, not a memo,
# and would block the request for minutes.
MAX_AUDIO_BYTES = 50 * 1024 * 1024


class TranscriptionUnavailable(RuntimeError):
    """No local transcriber could turn this audio into text."""


class VoiceCaptureService:
    """The local transcriber, and the honest report of whether there is one."""

    def __init__(
        self,
        *,
        transcriber: Optional[Callable[[str], str]] = None,
        max_bytes: int = MAX_AUDIO_BYTES,
    ) -> None:
        self._transcriber = transcriber
        self._max_bytes = max(1, int(max_bytes))

    # ── capability ───────────────────────────────────────────────────────
    def status(self) -> Dict[str, Any]:
        """What this install can actually do with a voice memo, honestly."""
        return {
            # Storing the memo is native (``POST /api/capture/voice`` in
            # lattice-platform), so capture is always available to a caller
            # that reached this answer through the gateway. Only the
            # transcriber is a fact about this process.
            "capture": True,
            "transcription": self._transcriber is not None,
            "supported_extensions": sorted(SUPPORTED_AUDIO_EXTENSIONS),
            "max_bytes": self._max_bytes,
            "detail": (
                "음성 메모를 글로 바꿔 Brain에 저장합니다."
                if self._transcriber is not None
                else "이 컴퓨터에는 음성 인식기가 없어서, 메모는 저장되지만 글로 바뀌지는 않습니다."
            ),
        }

    def multimodal_ports(self) -> "MultimodalPorts":
        """This service's transcriber, shaped for the ingestion pipeline.

        The pipeline lives in Brain Core and must not import ``latticeai``, so
        the capability travels as a plain callable rather than as this class.
        Handing over ``None`` when no transcriber is configured is the point:
        the pipeline then degrades exactly as :meth:`capture` does instead of
        inventing a transcript of its own.
        """
        from lattice_brain.multimodal import MultimodalPorts

        return MultimodalPorts(transcriber=self._transcriber)

    # ── transcription ────────────────────────────────────────────────────
    def _transcribe(self, path: Path) -> str:
        if self._transcriber is None:
            raise TranscriptionUnavailable("no local transcriber is configured")
        text = self._transcriber(str(path))
        cleaned = str(text or "").strip()
        if not cleaned:
            # An empty transcript is not text: reporting it as one would put an
            # empty note in the Brain and call it a memory.
            raise TranscriptionUnavailable("the transcriber returned no text")
        return cleaned


__all__ = ["VoiceCaptureService", "TranscriptionUnavailable", "SUPPORTED_AUDIO_EXTENSIONS"]
