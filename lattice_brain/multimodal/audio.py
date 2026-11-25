from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .ports import MultimodalPorts


@dataclass
class AudioFacts:
    path: str
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
    ports = ports or MultimodalPorts()
    supplied = str(transcript or "").strip()
    if supplied:
        return AudioFacts(
            path=str(path), transcription_status="supplied", transcript=supplied
        )
    if ports.transcriber is None:
        return AudioFacts(
            path=str(path),
            transcription_status="unavailable",
            detail="no local transcriber is configured",
        )
    try:
        text = str(ports.transcriber(str(path)) or "").strip()
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        return AudioFacts(
            path=str(path), transcription_status="failed", detail=str(exc)
        )
    if not text:
        return AudioFacts(
            path=str(path),
            transcription_status="failed",
            detail="the transcriber returned no text",
        )
    return AudioFacts(path=str(path), transcription_status="ok", transcript=text)


def audio_quality_score(facts: AudioFacts) -> Dict[str, Any]:
    if not facts.transcript:
        return {"score": 0.0, "reasons": ["no_transcript"]}
    score = 0.5 + 0.5 * min(1.0, len(facts.transcript) / 400.0)
    return {"score": round(score, 4), "reasons": ["transcript"]}
