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
v11.8.0 took the report away too — ``GET /api/capture/voice/status`` had no
caller, and a capability answer nobody asks for is a second copy of the same
facts ``/worker/asr`` already returns per call. What is left is the port
itself: the transcriber, the two module constants ``/worker/asr`` enforces
against, and the conversion into the shape Brain Core accepts.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

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
    """The local transcriber port, and the honest refusal when there is none."""

    def __init__(
        self, *, transcriber: Optional[Callable[[str], str]] = None
    ) -> None:
        self._transcriber = transcriber

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
