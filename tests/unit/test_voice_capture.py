"""Voice memo capture (v9.9.7).

Review follow-up: "음성/모바일 캡처 루프 — 로컬 우선을 해치지 않는 선에서, 짧은
음성 메모 → 바로 Brain 인제스트". House rules verified here: a missing
transcriber never invents a transcript and never silently drops the memo, an
empty transcript is treated as no text (not as an empty memory), and every
refusal names its reason.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from latticeai.api.voice_capture import create_voice_capture_router
from latticeai.services.voice_capture import MAX_AUDIO_BYTES, VoiceCaptureService


class FakePipeline:
    def __init__(self):
        self.items = []

    def ingest(self, item, *, user_email=None):
        self.items.append(item)
        return {
            "status": "ok",
            "source_type": item.source_type,
            "node_id": "node-1",
            "title": item.title,
        }


@pytest.fixture()
def audio(tmp_path):
    path = tmp_path / "memo.m4a"
    path.write_bytes(b"fake audio bytes")
    return path


def test_a_transcribed_memo_becomes_a_searchable_note(audio):
    pipeline = FakePipeline()
    service = VoiceCaptureService(
        pipeline=pipeline, transcriber=lambda p: "내일 예산안 공유하기"
    )
    result = service.capture(str(audio), title="예산 메모", user_email="u@x.com")

    assert result["status"] == "ok"
    assert result["transcription"] == "ok"
    assert result["searchable"] is True
    item = pipeline.items[0]
    assert item.text == "내일 예산안 공유하기"
    assert item.metadata["capture"] == "voice"
    assert item.metadata["transcription"] == "ok"


def test_no_transcriber_stores_the_memo_without_inventing_text(audio):
    pipeline = FakePipeline()
    service = VoiceCaptureService(pipeline=pipeline, transcriber=None)
    result = service.capture(str(audio), title="예산 메모")

    # Recorded, not dropped — and explicitly not searchable.
    assert result["status"] == "ok"
    assert result["transcription"] == "unavailable"
    assert result["searchable"] is False
    assert result["transcription_detail"]
    body = pipeline.items[0].text
    assert "예산 메모" in body
    assert "글로 바뀌지 않았습니다" in body


def test_an_empty_transcript_is_not_treated_as_text(audio):
    pipeline = FakePipeline()
    service = VoiceCaptureService(pipeline=pipeline, transcriber=lambda p: "   ")
    result = service.capture(str(audio))
    assert result["transcription"] == "unavailable"
    assert result["searchable"] is False


def test_a_broken_transcriber_is_a_reported_state_not_a_crash(audio):
    pipeline = FakePipeline()

    def boom(path):
        raise RuntimeError("model file corrupt")

    result = VoiceCaptureService(pipeline=pipeline, transcriber=boom).capture(str(audio))
    assert result["status"] == "ok"
    assert result["transcription"] == "failed"
    assert "model file corrupt" in result["transcription_detail"]


def test_a_client_supplied_transcript_skips_local_transcription(audio):
    pipeline = FakePipeline()
    calls = []
    service = VoiceCaptureService(
        pipeline=pipeline, transcriber=lambda p: calls.append(p) or "local"
    )
    result = service.capture(str(audio), transcript="폰에서 받아쓴 내용")
    assert result["transcription"] == "supplied"
    assert result["searchable"] is True
    assert calls == [], "a supplied transcript must not re-run local transcription"
    assert pipeline.items[0].text == "폰에서 받아쓴 내용"


def test_every_refusal_names_its_reason(tmp_path, audio):
    pipeline = FakePipeline()
    service = VoiceCaptureService(pipeline=pipeline, transcriber=lambda p: "x")

    missing = service.capture(str(tmp_path / "nope.m4a"))
    assert missing["error"] == "FILE_NOT_FOUND" and missing["message"]

    doc = tmp_path / "notes.txt"
    doc.write_text("not audio", encoding="utf-8")
    unsupported = service.capture(str(doc))
    assert unsupported["error"] == "UNSUPPORTED_FORMAT"

    tiny = VoiceCaptureService(pipeline=pipeline, transcriber=lambda p: "x", max_bytes=4)
    assert tiny.capture(str(audio))["error"] == "SIZE_LIMIT"

    no_pipeline = VoiceCaptureService(pipeline=None, transcriber=lambda p: "x")
    assert no_pipeline.capture(str(audio))["error"] == "UNAVAILABLE"

    assert pipeline.items == [], "a refused memo must never reach the pipeline"


def test_status_reports_what_this_install_can_really_do():
    with_transcriber = VoiceCaptureService(pipeline=FakePipeline(), transcriber=lambda p: "x").status()
    assert with_transcriber["capture"] is True
    assert with_transcriber["transcription"] is True

    without = VoiceCaptureService(pipeline=FakePipeline()).status()
    assert without["transcription"] is False
    assert "음성 인식기가 없어서" in without["detail"]
    assert ".m4a" in without["supported_extensions"]
    assert without["max_bytes"] == MAX_AUDIO_BYTES


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(
        create_voice_capture_router(
            service=VoiceCaptureService(pipeline=FakePipeline(), transcriber=lambda p: "받아쓴 내용"),
            require_user=lambda request: "u@x.com",
            gate_write=lambda request: None,
            append_audit_event=lambda *a, **k: None,
        )
    )
    return TestClient(app)


def test_router_accepts_a_memo_upload(client):
    response = client.post(
        "/api/capture/voice",
        files={"file": ("memo.m4a", b"fake audio", "audio/m4a")},
        data={"title": "예산 메모"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["transcription"] == "ok"


def test_router_reports_capability_before_a_user_records_anything(client):
    payload = client.get("/api/capture/voice/status").json()
    assert payload["capture"] is True
    assert payload["transcription"] is True
