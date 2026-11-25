from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from latticeai.services.voice_capture import (
    TranscriptionUnavailable,
    VoiceCaptureService,
)


def test_transcribe_returns_cleaned_text(tmp_path):
    audio = tmp_path / "memo.m4a"
    audio.write_bytes(b"fake audio bytes")
    service = VoiceCaptureService(transcriber=lambda _path: "  내일 예산안 공유하기  ")

    assert service._transcribe(audio) == "내일 예산안 공유하기"


def test_no_transcriber_raises_instead_of_inventing_text(tmp_path):
    audio = tmp_path / "memo.m4a"
    audio.write_bytes(b"fake audio bytes")

    with pytest.raises(TranscriptionUnavailable, match="no local transcriber"):
        VoiceCaptureService()._transcribe(audio)


def test_an_empty_transcript_is_not_treated_as_text(tmp_path):
    audio = tmp_path / "memo.m4a"
    audio.write_bytes(b"fake audio bytes")

    with pytest.raises(TranscriptionUnavailable, match="no text"):
        VoiceCaptureService(transcriber=lambda _path: "   ")._transcribe(audio)


def test_a_broken_transcriber_is_not_swallowed(tmp_path):
    audio = tmp_path / "memo.m4a"
    audio.write_bytes(b"fake audio bytes")

    def boom(_path):
        raise RuntimeError("model file corrupt")

    with pytest.raises(RuntimeError, match="model file corrupt"):
        VoiceCaptureService(transcriber=boom)._transcribe(audio)
