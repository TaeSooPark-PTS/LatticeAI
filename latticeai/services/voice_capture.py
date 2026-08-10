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
    """Ingest a voice memo through the unified ingestion pipeline."""

    def __init__(
        self,
        *,
        pipeline: Any,
        transcriber: Optional[Callable[[str], str]] = None,
        max_bytes: int = MAX_AUDIO_BYTES,
    ) -> None:
        self._pipeline = pipeline
        self._transcriber = transcriber
        self._max_bytes = max(1, int(max_bytes))

    # ── capability ───────────────────────────────────────────────────────
    def status(self) -> Dict[str, Any]:
        """What this install can actually do with a voice memo, honestly."""
        return {
            "capture": self._pipeline is not None,
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

    # ── capture ──────────────────────────────────────────────────────────
    def capture(
        self,
        audio_path: str,
        *,
        title: Optional[str] = None,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        transcript: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Ingest one voice memo.

        ``transcript`` lets a client that already has text (a phone's own
        dictation, for example) skip local transcription entirely — the memo
        still lands in the Brain through the same pipeline.
        """
        from lattice_brain.ingestion import IngestionItem

        path = Path(str(audio_path or "")).expanduser()
        if not path.exists() or not path.is_file():
            return self._failed("FILE_NOT_FOUND", f"audio file not found: {path}")
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_AUDIO_EXTENSIONS:
            return self._failed(
                "UNSUPPORTED_FORMAT",
                f"{suffix or 'this file'} is not a supported audio container",
            )
        try:
            size = path.stat().st_size
        except OSError as exc:
            return self._failed("FILE_NOT_FOUND", f"audio file unreadable: {exc}")
        if size > self._max_bytes:
            return self._failed(
                "SIZE_LIMIT",
                f"audio is {size} bytes; the memo limit is {self._max_bytes}",
            )
        if self._pipeline is None:
            return self._failed("UNAVAILABLE", "the ingestion pipeline is not available")

        supplied = str(transcript or "").strip()
        transcription_state = "supplied" if supplied else "ok"
        transcription_detail = ""
        text = supplied
        if not text:
            try:
                text = self._transcribe(path)
            except TranscriptionUnavailable as exc:
                transcription_state = "unavailable"
                transcription_detail = str(exc)
                text = ""
            except Exception as exc:  # noqa: BLE001 — a broken transcriber is a state
                LOGGER.exception("voice transcription failed")
                transcription_state = "failed"
                transcription_detail = str(exc)
                text = ""

        display_title = str(title or "").strip() or path.stem
        # With no transcript there is no text to index — the memo is recorded
        # by what we honestly know (title + the audio file itself), and the
        # response says so. It is never presented as a searchable note.
        body = text or (
            f"[음성 메모] {display_title}\n"
            "이 메모는 아직 글로 바뀌지 않았습니다 — 음성 인식기가 없어 내용 검색은 되지 않습니다."
        )
        item = IngestionItem(
            source_type="note",
            title=display_title,
            text=body,
            source_uri=str(path),
            mime_type=f"audio/{suffix.lstrip('.')}",
            owner=user_email,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            metadata={
                "capture": "voice",
                # Same key the multi-modal ingest door writes, so a memo and a
                # scanned recording are one kind of thing in the graph.
                "modality": "audio",
                "audio_path": str(path),
                "audio_bytes": size,
                "transcription": transcription_state,
                **({"transcription_detail": transcription_detail} if transcription_detail else {}),
            },
        )
        result = self._pipeline.ingest(item, user_email=user_email)
        payload = result.as_dict() if hasattr(result, "as_dict") else dict(result)
        payload["transcription"] = transcription_state
        payload["searchable"] = bool(text)
        if transcription_detail:
            payload["transcription_detail"] = transcription_detail
        return payload

    @staticmethod
    def _failed(error: str, message: str) -> Dict[str, Any]:
        return {
            "status": "failed",
            "source_type": "note",
            "error": error,
            "message": message,
            "transcription": "skipped",
            "searchable": False,
        }
