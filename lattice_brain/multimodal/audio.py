"""A recording as a memory — transcribed through the injected port, or not.

The whole module is three small pieces because that is all an audio memory is:
the recording's facts, one attempt at turning it into words, and an honest
score for how much of it a typed question can reach afterwards. Without a
transcriber the memory is still kept and simply says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .ports import MultimodalPorts


@dataclass
class AudioFacts:
    """A recording, its transcript, and how honestly we got one."""

    path: str
    #: ``ok`` | ``unavailable`` | ``failed`` | ``supplied``
    transcription_status: str = "unavailable"
    transcript: str = ""
    detail: str = ""
    segments: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def searchable(self) -> bool:
        return bool(self.transcript)


def transcribe_audio(
    path: str,
    *,
    ports: Optional[MultimodalPorts] = None,
    transcript: Optional[str] = None,
) -> AudioFacts:
    """Transcribe a recording through the injected port, or say why not.

    ``transcript`` lets a caller that already has text (a phone's own
    dictation, ``VoiceCaptureService``) skip the local model entirely. An
    absent transcriber yields ``transcription_status="unavailable"`` and an
    empty transcript — the recording is still remembered by title and path,
    and the result never claims it is searchable.
    """
    ports = ports or MultimodalPorts()
    supplied = str(transcript or "").strip()
    if supplied:
        return AudioFacts(path=str(path), transcription_status="supplied", transcript=supplied)
    if ports.transcriber is None:
        return AudioFacts(
            path=str(path),
            transcription_status="unavailable",
            detail="no local transcriber is configured",
        )
    try:
        text = str(ports.transcriber(str(path)) or "").strip()
    except Exception as exc:  # noqa: BLE001 — a broken transcriber is a state
        return AudioFacts(path=str(path), transcription_status="failed", detail=str(exc))
    if not text:
        return AudioFacts(
            path=str(path),
            transcription_status="failed",
            detail="the transcriber returned no text",
        )
    return AudioFacts(path=str(path), transcription_status="ok", transcript=text)


def audio_quality_score(facts: AudioFacts) -> Dict[str, Any]:
    """How much of this recording is actually retrievable later."""
    if not facts.transcript:
        return {"score": 0.0, "reasons": ["no_transcript"]}
    # A transcript is text: length is the only honest extra signal here, and
    # the text pipeline scores the wording itself downstream.
    score = 0.5 + 0.5 * min(1.0, len(facts.transcript) / 400.0)
    return {"score": round(score, 4), "reasons": ["transcript"]}
