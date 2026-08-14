"""Voice memo capture (v9.9.7).

Review follow-up: "음성/모바일 캡처 루프 — 로컬 우선을 해치지 않는 선에서, 짧은
음성 메모 → 바로 Brain 인제스트". House rules verified here: a missing
transcriber never invents a transcript, an empty transcript is treated as no
text, and status() reports what this install can actually do.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from latticeai.api.voice_capture import create_voice_capture_router
from latticeai.services.voice_capture import (
    MAX_AUDIO_BYTES,
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


def test_status_reports_what_this_install_can_really_do():
    with_transcriber = VoiceCaptureService(transcriber=lambda _path: "x").status()
    assert with_transcriber["capture"] is True
    assert with_transcriber["transcription"] is True

    without = VoiceCaptureService().status()
    assert without["transcription"] is False
    assert "음성 인식기가 없어서" in without["detail"]
    assert ".m4a" in without["supported_extensions"]
    assert without["max_bytes"] == MAX_AUDIO_BYTES

    tiny = VoiceCaptureService(transcriber=lambda _path: "x", max_bytes=4)
    assert tiny.status()["max_bytes"] == 4


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(
        create_voice_capture_router(
            service=VoiceCaptureService(transcriber=lambda _path: "받아쓴 내용"),
            require_user=lambda request: "u@x.com",
        )
    )
    return TestClient(app)


def test_router_reports_capability_before_a_user_records_anything(client):
    payload = client.get("/api/capture/voice/status").json()
    assert payload["capture"] is True
    assert payload["transcription"] is True


def test_router_status_requires_a_signed_in_user():
    app = FastAPI()

    def refuse(_request):
        from fastapi import HTTPException

        raise HTTPException(status_code=401, detail="auth required")

    app.include_router(
        create_voice_capture_router(
            service=VoiceCaptureService(),
            require_user=refuse,
        )
    )

    response = TestClient(app).get("/api/capture/voice/status")
    assert response.status_code == 401
