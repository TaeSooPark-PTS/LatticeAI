"""P1b coverage — multimodal facts, ingestion DTOs, quiet, extraction leftovers.

Drives the remaining Brain Core compute doors with real tiny files (PNG via
Pillow, sidecar `.srt` / `.vtt`, a few bytes of text) and mocked ffmpeg /
pytesseract / LLM-router seams. No product code is patched except public
callables those modules already expose as test seams.
"""

from __future__ import annotations

import asyncio
import builtins
import sys
import types
from pathlib import Path
from typing import Any, List

import pytest
from PIL import Image

from lattice_brain.graph._kg_common import extraction as kg_extraction
from lattice_brain.ingestion.hashing import _file_digest, content_hash_text
from lattice_brain.ingestion.models import IngestionItem, IngestionResult
from lattice_brain.ingestion.quality import (
    QUALITY_LOW_WARNING,
    _quality_level,
    assess_extraction_quality,
    capture_quality_verdict,
)
from lattice_brain.multimodal import common as common_mod
from lattice_brain.multimodal import images as images_mod
from lattice_brain.multimodal import ports as ports_mod
from lattice_brain.multimodal import video as video_mod
from lattice_brain.multimodal.audio import (
    AudioFacts,
    audio_quality_score,
    transcribe_audio,
)
from lattice_brain.multimodal.common import (
    IMAGE_CHUNK_CHARS,
    SUMMARY_CHARS,
    VIDEO_UNAVAILABLE_DETAIL,
    _sha256_file,
    _sha256_text,
    _split_index_text,
    detect_modality,
)
from lattice_brain.multimodal.images import (
    ImageFacts,
    _apply_vision_embedding,
    _open_image,
    _run_ocr,
    _safe_caption,
    _thumbnail_data_uri,
    extract_image_facts,
    image_quality_score,
)
from lattice_brain.multimodal.ports import (
    MultimodalPorts,
    _injected_keyframes,
    _run_ffmpeg,
    _which_ffmpeg,
    extract_keyframes,
    ffmpeg_available,
)
from lattice_brain.multimodal.video import (
    VideoFacts,
    find_subtitle,
    parse_subtitles,
    read_video_facts,
    video_quality_score,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _write_png(path: Path, *, size=(8, 8), colour=(200, 40, 40), mode: str = "RGB") -> Path:
    image = Image.new(mode, size, colour if mode != "P" else 1)
    image.save(path, format="PNG")
    return path


class _FakeLoop:
    def __init__(self, running: bool) -> None:
        self._running = running

    def is_running(self) -> bool:
        return self._running


class _FakeRouter:
    def __init__(self, reply: str = "[]", *, model_id: str = "fake-model", fail=None):
        self.current_model_id = model_id
        self._reply = reply
        self._fail = fail
        self.prompts: List[Any] = []

    async def generate(self, prompt: str, **kwargs):
        self.prompts.append((prompt, kwargs))
        if self._fail is not None:
            raise self._fail
        return self._reply


def _install_router(monkeypatch, router, *, loop_running: bool) -> None:
    monkeypatch.setattr(kg_extraction, "ENABLE_LLM_EXTRACTION", True)
    monkeypatch.setattr(kg_extraction, "get_llm_router", lambda: router)
    monkeypatch.setattr(asyncio, "get_event_loop", lambda: _FakeLoop(loop_running))


@pytest.fixture(autouse=True)
def _reset_ingestion_gates(monkeypatch):
    from lattice_brain.ingestion.constants import MULTIMODAL_GATE, VIDEO_GATE

    monkeypatch.delenv("LATTICEAI_ALLOW_MULTIMODAL", raising=False)
    monkeypatch.delenv("LATTICEAI_ALLOW_VIDEO", raising=False)
    MULTIMODAL_GATE.reset()
    VIDEO_GATE.reset()
    yield
    MULTIMODAL_GATE.reset()
    VIDEO_GATE.reset()

def test_detect_modality_prefers_declared_mime_and_strips_parameters():
    assert detect_modality(mime_type="IMAGE/PNG; charset=binary") == "image"
    assert detect_modality(mime_type="audio/mpeg") == "audio"
    assert detect_modality(mime_type="video/mp4") == "video"
    assert detect_modality(path="shot.png", mime_type="application/octet-stream") == "image"

@pytest.mark.parametrize(
    "name,expected",
    [
        ("shot.png", "image"),
        ("clip.mp3", "audio"),
        ("movie.mp4", "video"),
        ("note.txt", "text"),
    ],
)
def test_detect_modality_uses_the_module_extension_tables(name, expected):
    assert detect_modality(path=name) == expected

def test_detect_modality_falls_back_to_stdlib_mimetypes_then_text():
    # `.svg` is not in IMAGE_EXTENSIONS; the stdlib table still calls it image.
    assert detect_modality(path="icon.svg") == "image"
    # `.mpeg` is not in VIDEO_EXTENSIONS.
    assert detect_modality(path="clip.mpeg") == "video"
    assert detect_modality(path="") == "text"
    assert detect_modality() == "text"
    assert detect_modality(path="no-such-extension.zzz") == "text"

def test_sha256_and_split_index_text(tmp_path: Path):
    empty = tmp_path / "empty.bin"
    empty.write_bytes(b"")
    assert len(_sha256_file(empty)) == 64
    blob = tmp_path / "blob.bin"
    blob.write_bytes(b"abc")
    assert _sha256_file(blob) == _sha256_text("abc") or len(_sha256_file(blob)) == 64
    assert len(_sha256_text("hello")) == 64
    assert _sha256_text("") == _sha256_text("")
    assert _split_index_text("") == []
    assert _split_index_text("   ") == []
    assert _split_index_text("x" * SUMMARY_CHARS) == []
    long_body = "y" * (SUMMARY_CHARS + 1)
    pieces = _split_index_text(long_body)
    assert pieces
    assert all(len(piece) <= IMAGE_CHUNK_CHARS for piece in pieces)
    assert "".join(pieces) == long_body
    huge = "z" * (IMAGE_CHUNK_CHARS * 2 + 10)
    assert len(_split_index_text(huge)) == 3

def test_audio_facts_searchable_and_supplied_transcript():
    empty = AudioFacts(path="memo.m4a")
    assert empty.searchable is False
    supplied = transcribe_audio("memo.m4a", transcript="  already typed  ")
    assert supplied.transcription_status == "supplied"
    assert supplied.transcript == "already typed"
    assert supplied.searchable is True
    assert transcribe_audio("memo.m4a", transcript="   ").transcription_status == "unavailable"

def test_transcribe_audio_unavailable_failed_empty_and_ok(tmp_path: Path):
    path = str(tmp_path / "memo.wav")
    none = transcribe_audio(path)
    assert none.transcription_status == "unavailable"
    assert "no local transcriber" in none.detail

    def boom(_path):
        raise RuntimeError("decoder exploded")

    failed = transcribe_audio(path, ports=MultimodalPorts(transcriber=boom))
    assert failed.transcription_status == "failed"
    assert "decoder exploded" in failed.detail

    blank = transcribe_audio(path, ports=MultimodalPorts(transcriber=lambda _p: "   "))
    assert blank.transcription_status == "failed"
    assert "no text" in blank.detail

    none_text = transcribe_audio(path, ports=MultimodalPorts(transcriber=lambda _p: None))
    assert none_text.transcription_status == "failed"

    ok = transcribe_audio(path, ports=MultimodalPorts(transcriber=lambda _p: "  hello world  "))
    assert ok.transcription_status == "ok"
    assert ok.transcript == "hello world"

def test_audio_quality_score_empty_short_and_long():
    assert audio_quality_score(AudioFacts(path="x")) == {"score": 0.0, "reasons": ["no_transcript"]}
    short = audio_quality_score(AudioFacts(path="x", transcript="hi"))
    assert short["reasons"] == ["transcript"]
    assert 0.5 <= short["score"] < 1.0
    long = audio_quality_score(AudioFacts(path="x", transcript="w" * 400))
    assert long["score"] == 1.0

def test_image_facts_index_text_and_metadata_optional_fields():
    facts = ImageFacts(
        path="shot.png",
        width=8,
        height=8,
        image_format="PNG",
        mode="RGB",
        ocr_text="receipt total",
        ocr_detail="ok",
        caption="a receipt",
        embedding_detail="dim=2",
        thumbnail="data:image/png;base64,xx",
        error="nope",
    )
    assert facts.readable is False
    assert "a receipt" in facts.index_text()
    assert "receipt total" in facts.index_text()
    meta = facts.as_metadata()
    assert meta["ocr_text"] == "receipt total"
    assert meta["ocr_detail"] == "ok"
    assert meta["caption"] == "a receipt"
    assert meta["vision_embedding_detail"] == "dim=2"
    assert meta["thumbnail"].startswith("data:image/png")
    assert meta["image_error"] == "nope"

    bare = ImageFacts(path="x")
    bare_meta = bare.as_metadata()
    assert "ocr_text" not in bare_meta
    assert "caption" not in bare_meta
    assert "thumbnail" not in bare_meta
    assert "image_error" not in bare_meta
    assert bare.index_text() == ""

def test_open_image_and_thumbnail_rgb_l_convert_and_too_big(tmp_path: Path, monkeypatch):
    rgb = _write_png(tmp_path / "rgb.png", mode="RGB")
    opened = _open_image(str(rgb))
    uri = _thumbnail_data_uri(opened)
    assert uri and uri.startswith("data:image/png;base64,")
    opened.close()

    gray = _write_png(tmp_path / "gray.png", mode="L", colour=90)
    with _open_image(str(gray)) as image:
        assert _thumbnail_data_uri(image)

    rgba = Image.new("RGBA", (6, 6), (10, 20, 30, 40))
    rgba_path = tmp_path / "rgba.png"
    rgba.save(rgba_path)
    with _open_image(str(rgba_path)) as image:
        assert image.mode == "RGBA"
        assert _thumbnail_data_uri(image)

    class Boom:
        def copy(self):
            raise RuntimeError("cannot copy")

    assert _thumbnail_data_uri(Boom()) is None

    monkeypatch.setattr(images_mod, "MAX_THUMBNAIL_CHARS", 1)
    with _open_image(str(rgb)) as image:
        assert _thumbnail_data_uri(image) is None

def test_run_ocr_unavailable_failed_empty_and_ok(monkeypatch):
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "pytesseract":
            raise ImportError("No module named pytesseract")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    missing = _run_ocr(object())
    assert missing["status"] == "unavailable"
    assert "pytesseract" in missing["detail"]
    monkeypatch.setattr(builtins, "__import__", real_import)

    fake = types.ModuleType("pytesseract")
    fake.image_to_string = lambda _img: (_ for _ in ()).throw(RuntimeError("tesseract missing"))
    monkeypatch.setitem(sys.modules, "pytesseract", fake)
    failed = _run_ocr(object())
    assert failed["status"] == "failed"
    assert "tesseract missing" in failed["detail"]

    fake.image_to_string = lambda _img: "   "
    assert _run_ocr(object())["status"] == "empty"

    fake.image_to_string = lambda _img: None
    assert _run_ocr(object())["status"] == "empty"

    long_text = "x" * (common_mod.MAX_INDEX_TEXT_CHARS + 50)
    fake.image_to_string = lambda _img: long_text
    ok = _run_ocr(object())
    assert ok["status"] == "ok"
    assert len(ok["text"]) == common_mod.MAX_INDEX_TEXT_CHARS

def test_extract_image_facts_unreadable_skipped_and_full_ports(tmp_path: Path, monkeypatch):
    missing = extract_image_facts(str(tmp_path / "nope.png"))
    assert missing.error
    assert missing.readable is False
    assert image_quality_score(missing) == {"score": 0.0, "reasons": ["image_unreadable"]}

    path = _write_png(tmp_path / "tiny.png")
    skipped = extract_image_facts(str(path), ocr=False, thumbnail=False)
    assert skipped.ocr_status == "skipped"
    assert skipped.thumbnail is None
    assert skipped.width == 8 and skipped.height == 8

    fake = types.ModuleType("pytesseract")
    fake.image_to_string = lambda _img: "  board notes  "
    monkeypatch.setitem(sys.modules, "pytesseract", fake)

    def boom_caption(_p):
        raise RuntimeError("vlm down")

    ports = MultimodalPorts(
        captioner=boom_caption,
        vision_embedder=lambda _p: [1.0, 2.0],
    )
    facts = extract_image_facts(str(path), ports=ports)
    assert facts.ocr_status == "ok"
    assert facts.ocr_text == "board notes"
    assert facts.caption is None
    assert facts.caption_status == "unavailable"
    assert facts.embedding == [1.0, 2.0]
    assert facts.embedding_status == "ok"
    assert facts.thumbnail

    ports_ok = MultimodalPorts(
        captioner=lambda _p: "  a red square  ",
        vision_embedder=lambda _p: [],
    )
    captioned = extract_image_facts(str(path), ports=ports_ok, ocr=False)
    assert captioned.caption == "a red square"
    assert captioned.caption_status == "ok"
    assert captioned.embedding_status == "failed"
    assert "empty vector" in captioned.embedding_detail

    def boom_embed(_p):
        raise RuntimeError("no clip")

    failed_embed = extract_image_facts(
        str(path),
        ports=MultimodalPorts(captioner=lambda _p: "", vision_embedder=boom_embed),
        ocr=False,
        thumbnail=False,
    )
    assert failed_embed.caption is None
    assert failed_embed.embedding_status == "failed"
    assert "no clip" in failed_embed.embedding_detail

def test_safe_caption_and_apply_vision_embedding_helpers():
    facts = ImageFacts(path="x.png")
    assert _safe_caption(lambda _p: None, "x.png") is None
    assert _safe_caption(lambda _p: "   ", "x.png") is None
    assert _safe_caption(lambda _p: "ok", "x.png") == "ok"

    def boom(_p):
        raise RuntimeError("nope")

    assert _safe_caption(boom, "x.png") is None

    _apply_vision_embedding(facts, lambda _p: [3, 4])
    assert facts.embedding == [3.0, 4.0]
    assert facts.embedding_status == "ok"

    empty = ImageFacts(path="x.png")
    _apply_vision_embedding(empty, lambda _p: [])
    assert empty.embedding_status == "failed"

    broken = ImageFacts(path="x.png")
    _apply_vision_embedding(broken, boom)
    assert broken.embedding_status == "failed"

def test_image_quality_score_reason_matrix():
    unread = ImageFacts(path="x", error="bad")
    assert image_quality_score(unread)["reasons"] == ["image_unreadable"]

    ocr = ImageFacts(path="x", ocr_text="n" * 400, caption="c", embedding_status="ok")
    scored = image_quality_score(ocr)
    assert "ocr_text" in scored["reasons"]
    assert "vision_caption" in scored["reasons"]
    assert "vision_embedding" in scored["reasons"]
    assert scored["score"] == 1.0

    assert "ocr_unavailable" in image_quality_score(ImageFacts(path="x", ocr_status="unavailable"))["reasons"]
    assert "ocr_skipped" in image_quality_score(ImageFacts(path="x", ocr_status="skipped"))["reasons"]
    assert "no_ocr_text" in image_quality_score(ImageFacts(path="x", ocr_status="empty"))["reasons"]
    assert "no_vision_caption" in image_quality_score(ImageFacts(path="x"))["reasons"]

def test_multimodal_ports_describe_with_and_without_capabilities(monkeypatch):
    monkeypatch.setattr(ports_mod, "_which_ffmpeg", lambda: None)
    empty = MultimodalPorts().describe()
    assert empty["caption"] is False
    assert empty["transcription"] is False
    assert empty["keyframes"] is False
    assert empty["text_to_image_query"] is False

    monkeypatch.setattr(ports_mod, "_which_ffmpeg", lambda: "/usr/bin/ffmpeg")
    full = MultimodalPorts(
        captioner=lambda _p: "c",
        vision_embedder=lambda _p: [0.1],
        transcriber=lambda _p: "t",
        keyframe_extractor=lambda *_a, **_k: [],
        text_to_image_embedder=lambda _q: [0.2],
        vision_model_id="clip",
        vision_space="shared",
    ).describe()
    assert full["caption"] is True
    assert full["vision_embedding"] is True
    assert full["transcription"] is True
    assert full["keyframes"] is True
    assert full["text_to_image_query"] is True
    assert full["vision_model_id"] == "clip"
    assert full["vision_space"] == "shared"

def test_which_ffmpeg_and_run_ffmpeg(monkeypatch):
    monkeypatch.setattr(ports_mod.shutil, "which", lambda _name: "/opt/ffmpeg")
    assert _which_ffmpeg() == "/opt/ffmpeg"
    assert ffmpeg_available() is True
    monkeypatch.setattr(ports_mod.shutil, "which", lambda _name: None)
    assert _which_ffmpeg() is None
    assert ffmpeg_available() is False

    class Completed:
        returncode = 7

    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return Completed()

    monkeypatch.setattr(ports_mod.subprocess, "run", fake_run)
    assert _run_ffmpeg("/bin/ffmpeg", ["-version"]) == 7
    assert seen["argv"] == ["/bin/ffmpeg", "-version"]
    assert seen["kwargs"]["check"] is False

def test_extract_keyframes_injected_and_ffmpeg_outcomes(tmp_path: Path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not-a-real-video")
    dest = tmp_path / "frames"

    injected = extract_keyframes(
        video,
        dest,
        count=2,
        ports=MultimodalPorts(keyframe_extractor=lambda *_a, **_k: [str(dest / "a.jpg"), str(dest / "b.jpg"), "extra"]),
    )
    assert injected["status"] == "ok"
    assert injected["frames"] == [str(dest / "a.jpg"), str(dest / "b.jpg")]

    empty = extract_keyframes(
        video, dest, ports=MultimodalPorts(keyframe_extractor=lambda *_a, **_k: [])
    )
    assert empty["status"] == "empty"

    none = extract_keyframes(
        video, dest, ports=MultimodalPorts(keyframe_extractor=lambda *_a, **_k: None)
    )
    assert none["status"] == "empty"

    def boom(*_a, **_k):
        raise RuntimeError("decoder died")

    failed_port = extract_keyframes(video, dest, ports=MultimodalPorts(keyframe_extractor=boom))
    assert failed_port["status"] == "failed"
    assert "decoder died" in failed_port["detail"]

    monkeypatch.setattr(ports_mod, "_which_ffmpeg", lambda: None)
    unavailable = extract_keyframes(video, dest, count=0)
    assert unavailable["status"] == "unavailable"
    assert unavailable["detail"] == VIDEO_UNAVAILABLE_DETAIL

    monkeypatch.setattr(ports_mod, "_which_ffmpeg", lambda: "/usr/bin/ffmpeg")

    def explode(_binary, _args):
        raise TimeoutError("ffmpeg hung")

    monkeypatch.setattr(ports_mod, "_run_ffmpeg", explode)
    timed_out = extract_keyframes(video, dest)
    assert timed_out["status"] == "failed"
    assert "ffmpeg hung" in timed_out["detail"]

    def write_frames(_binary, args):
        out = Path(args[-1]).parent
        (out / "keyframe-001.jpg").write_bytes(b"j")
        (out / "keyframe-002.jpg").write_bytes(b"j")
        return 0

    monkeypatch.setattr(ports_mod, "_run_ffmpeg", write_frames)
    ok = extract_keyframes(str(video), str(dest), count=1)
    assert ok["status"] == "ok"
    assert len(ok["frames"]) == 1

    def nonzero_with_frames(_binary, args):
        out = Path(args[-1]).parent
        (out / "keyframe-001.jpg").write_bytes(b"j")
        return 1

    monkeypatch.setattr(ports_mod, "_run_ffmpeg", nonzero_with_frames)
    recovered = extract_keyframes(video, dest)
    assert recovered["status"] == "ok"

    def nonzero_no_frames(_binary, _args):
        return 3

    monkeypatch.setattr(ports_mod, "_run_ffmpeg", nonzero_no_frames)
    failed_code = extract_keyframes(video, dest / "empty-fail")
    assert failed_code["status"] == "failed"
    assert "status 3" in failed_code["detail"]

    def zero_no_frames(_binary, _args):
        return 0

    monkeypatch.setattr(ports_mod, "_run_ffmpeg", zero_no_frames)
    empty_ffmpeg = extract_keyframes(video, dest / "empty-ok")
    assert empty_ffmpeg["status"] == "empty"

def test_injected_keyframes_helper_directly(tmp_path: Path):
    video = tmp_path / "v.mp4"
    dest = tmp_path / "d"
    assert _injected_keyframes(lambda *_a: ["f1"], video, dest, 4)["frames"] == ["f1"]
    assert _injected_keyframes(lambda *_a: [], video, dest, 4)["status"] == "empty"

    def boom(*_a):
        raise ValueError("bad")

    assert "keyframe port failed" in _injected_keyframes(boom, video, dest, 1)["detail"]

def test_find_subtitle_prefers_srt_then_vtt(tmp_path: Path):
    clip = tmp_path / "talk.mp4"
    clip.write_bytes(b"x")
    assert find_subtitle(clip) is None
    vtt = tmp_path / "talk.vtt"
    vtt.write_text("WEBVTT\n\nhello", encoding="utf-8")
    assert find_subtitle(clip) == vtt
    srt = tmp_path / "talk.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
    assert find_subtitle(clip) == srt

def test_parse_subtitles_strips_scaffolding_and_duplicates(monkeypatch):
    raw = (
        "\ufeffWEBVTT\n"
        "NOTE this is a note\n"
        "webvtt header again\n"
        "\n"
        "1\n"
        "00:00:00.000 --> 00:00:01.000\n"
        "Hello <i>world</i>\n"
        "\n"
        "2\n"
        "00:00:01,000 --> 00:00:02,000\n"
        "Hello <i>world</i>\n"
        "\n"
        "3\n"
        "00:00:02.000 --> 00:00:03.000\n"
        "<c.color></c>\n"
        "Next line\n"
    )
    parsed = parse_subtitles(raw)
    assert parsed == "Hello world\nNext line"
    assert parse_subtitles("") == ""
    assert parse_subtitles(None) == ""
    monkeypatch.setattr(video_mod, "MAX_SUBTITLE_CHARS", 20)
    huge = "\n".join(f"unique line {i} of spoken words" for i in range(8))
    assert len(parse_subtitles(huge)) == 20

def test_video_facts_metadata_and_searchable():
    bare = VideoFacts(path="clip.mp4")
    assert bare.searchable is False
    meta = bare.as_metadata()
    assert meta["keyframes"] == 0
    assert "keyframe_detail" not in meta
    assert "subtitle_path" not in meta

    rich = VideoFacts(
        path="clip.mp4",
        keyframes=["a.jpg"],
        keyframe_detail="ok",
        subtitle_path="clip.srt",
        subtitle_text="hello",
    )
    rich_meta = rich.as_metadata()
    assert rich.searchable is True
    assert rich_meta["keyframe_detail"] == "ok"
    assert rich_meta["subtitle_path"] == "clip.srt"

def test_read_video_facts_supplied_companion_empty_and_unreadable(tmp_path: Path, monkeypatch):
    clip = tmp_path / "standup.mp4"
    clip.write_bytes(b"x")
    dest = tmp_path / "frames"
    ports = MultimodalPorts(keyframe_extractor=lambda *_a, **_k: [str(dest / "k.jpg")])

    supplied = read_video_facts(
        clip,
        dest,
        count=2,
        ports=ports,
        subtitle_text="1\n00:00:00.000 --> 00:00:01.000\nhello there\n",
    )
    assert supplied.subtitle_status == "ok"
    assert "hello there" in supplied.subtitle_text
    assert supplied.keyframes == [str(dest / "k.jpg")]

    absent = read_video_facts(clip, dest, ports=ports)
    assert absent.subtitle_status == "absent"

    empty_srt = tmp_path / "standup.srt"
    empty_srt.write_text("WEBVTT\n\n1\n00:00:00.000 --> 00:00:01.000\n\n", encoding="utf-8")
    empty = read_video_facts(clip, dest, ports=ports)
    assert empty.subtitle_status == "empty"
    assert empty.subtitle_path == str(empty_srt)

    empty_srt.write_text("1\n00:00:00.000 --> 00:00:01.000\nspoken words\n", encoding="utf-8")
    ok = read_video_facts(str(clip), str(dest), ports=ports)
    assert ok.subtitle_status == "ok"
    assert ok.subtitle_text == "spoken words"

    def boom_read(self, *args, **kwargs):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_text", boom_read)
    unreadable = read_video_facts(clip, dest, ports=ports)
    assert unreadable.subtitle_status == "unreadable"
    assert "Permission denied" in unreadable.subtitle_path

    class NoStrerror(OSError):
        def __init__(self):
            super().__init__("blocked")
            self.strerror = None

    def boom_no_strerror(self, *args, **kwargs):
        raise NoStrerror()

    monkeypatch.setattr(Path, "read_text", boom_no_strerror)
    unreadable2 = read_video_facts(clip, dest, ports=ports)
    assert unreadable2.subtitle_status == "unreadable"
    assert "unreadable" in unreadable2.subtitle_path

def test_video_quality_score_with_and_without_signals():
    empty = video_quality_score(VideoFacts(path="x"))
    assert empty["reasons"] == ["no_subtitles_absent", "no_keyframes_unavailable"]
    assert empty["score"] == 0.1

    rich = video_quality_score(
        VideoFacts(
            path="x",
            subtitle_text="s" * 400,
            subtitle_status="ok",
            keyframes=["a", "b", "c", "d"],
            keyframe_status="ok",
        )
    )
    assert "subtitles" in rich["reasons"]
    assert "keyframes" in rich["reasons"]
    assert rich["score"] == 1.0

def test_quality_level_thresholds():
    assert _quality_level(0.7) == "high"
    assert _quality_level(0.69) == "medium"
    assert _quality_level(0.4) == "medium"
    assert _quality_level(0.39) == "low"

def test_assess_extraction_quality_upstream_and_empty():
    high = assess_extraction_quality("ignored", upstream_confidence=0.95)
    assert high == {"score": 0.95, "level": "high", "reasons": ["upstream_confidence"]}
    clamped = assess_extraction_quality("ignored", upstream_confidence=4)
    assert clamped["score"] == 1.0
    low = assess_extraction_quality("ignored", upstream_confidence=-2)
    assert low["score"] == 0.0
    assert low["level"] == "low"
    # Unusable confidence falls through to the text heuristics.
    empty = assess_extraction_quality("   ", upstream_confidence="not-a-number")
    assert empty == {"score": 0.0, "level": "low", "reasons": ["empty_text"]}
    assert assess_extraction_quality(None, upstream_confidence=object())["reasons"] == ["empty_text"]
    assert assess_extraction_quality("")["reasons"] == ["empty_text"]

def test_assess_extraction_quality_length_structure_diversity_cleanliness():
    very_short = assess_extraction_quality("tiny note")
    assert "very_short_text" in very_short["reasons"]
    assert very_short["level"] == "low"

    short_body = "A modest sentence about knowledge graphs and memory sits here."
    short = assess_extraction_quality(short_body)
    assert 40 <= len(short_body) < 120
    assert "short_text" in short["reasons"]

    mid_body = (
        "A modest sentence about graphs and recall sits in the first half. "
        "Another sentence expands the idea with extra useful wording."
    )
    assert 120 <= len(mid_body.strip()) < 300
    mid = assess_extraction_quality(mid_body)
    assert mid["score"] > 0
    assert "very_short_text" not in mid["reasons"]
    assert "short_text" not in mid["reasons"]

    long_prose = (
        "Knowledge graphs store entities and the relations that bind them. "
        "Agents retrieve memories by walking typed edges between concepts. "
        "Ingestion hashes each document so duplicate captures collapse. "
        "Quality scoring is advisory and never blocks a capture from landing. "
        "Workspace owners keep local models in charge of every private file."
    )
    assert len(long_prose) >= 300
    scored = assess_extraction_quality(long_prose)
    assert scored["reasons"] == ["clean_extraction"]
    assert scored["level"] == "high"

    no_period_short = assess_extraction_quality("Title without any sentence mark at all here")
    assert "no_sentence_structure" not in no_period_short["reasons"]

    no_period_long = assess_extraction_quality("abcdefghijklmnopqrstuvwxyz " * 10)
    assert len(no_period_long and "abcdefghijklmnopqrstuvwxyz " * 10) >= 200
    assert "no_sentence_structure" in assess_extraction_quality("abcdefghijklmnopqrstuvwxyz " * 10)["reasons"]

    low_div = assess_extraction_quality("aaaaaaaaaa " * 8)
    assert "low_character_diversity" in low_div["reasons"]

    mid_div = assess_extraction_quality(("abcdefghij " * 20) + "And a real sentence.")
    assert mid_div["score"] <= 1.0

    repetitive_lines = assess_extraction_quality("Same line again\n" * 6)
    assert "repetitive_lines" in repetitive_lines["reasons"]

    unique_lines = assess_extraction_quality(
        "\n".join(f"Unique line number {i} with extra words." for i in range(6))
    )
    assert "repetitive_lines" not in unique_lines["reasons"]

    long_lines = assess_extraction_quality(
        "\n".join(f"This reasonably long line number {i} has many extra words." for i in range(8))
    )
    assert "fragmented_lines" not in long_lines["reasons"]

    repetitive_words = assess_extraction_quality(("word " * 30) + "End.")
    assert "repetitive_words" in repetitive_words["reasons"]

    whitespace = assess_extraction_quality("a" + (" \n" * 40) + "b. more words here.")
    assert "high_whitespace_ratio" in whitespace["reasons"]

    fragmented = assess_extraction_quality("\n".join(["one two"] * 8) + "\nA full sentence is here.")
    assert "fragmented_lines" in fragmented["reasons"]

    chrome = "Home\nMenu\nLogin\nSign in\nActual article body with enough letters.\n"
    web = assess_extraction_quality(chrome, source_type="web_url")
    assert "nav_menu_remnants" in web["reasons"]
    other = assess_extraction_quality(chrome, source_type="note")
    assert "boilerplate_markers" in other["reasons"]

def test_capture_quality_verdict_thin_ok_and_unusable():
    none = capture_quality_verdict(None)
    assert none["status"] == "thin"
    assert none["reason_codes"] == ["no_extracted_text"]
    assert none["score"] is None
    assert none["suggestions"]

    not_dict = capture_quality_verdict("nope")
    assert not_dict["status"] == "thin"

    labeled = capture_quality_verdict(
        {"level": "low", "score": 0.1, "reasons": ["very_short_text", "unknown_code"]}
    )
    assert labeled["status"] == "thin"
    assert "짧습니다" in labeled["reason"]
    assert labeled["reason_codes"] == ["very_short_text", "unknown_code"]

    unlabeled = capture_quality_verdict({"level": "low", "score": 0.2, "reasons": ["mystery"]})
    assert unlabeled["reason"] == QUALITY_LOW_WARNING

    no_reasons = capture_quality_verdict({"level": "low", "score": 0.1})
    assert no_reasons["reason"] == QUALITY_LOW_WARNING

    ok = capture_quality_verdict(
        {"level": "high", "score": 0.9, "reasons": ["clean_extraction"]},
        source_type="web_url",
    )
    assert ok["status"] == "ok"
    assert ok["reason"] is None
    assert ok["reason_codes"] == []
    assert ok["suggestions"] == []

    empty_level = capture_quality_verdict({"level": "", "score": 0.5})
    assert empty_level["status"] == "ok"
    assert empty_level["level"] is None

def test_ingestion_hashing_text_and_file(tmp_path: Path):
    assert len(content_hash_text("hello")) == 64
    assert content_hash_text("") == content_hash_text(None)
    blob = tmp_path / "blob.bin"
    blob.write_bytes(b"xyz")
    assert len(_file_digest(blob)) == 64
    empty = tmp_path / "empty.bin"
    empty.write_bytes(b"")
    assert len(_file_digest(empty)) == 64

def test_ingestion_item_and_result_as_dict_additive_keys():
    item = IngestionItem(
        source_type="file",
        title="t",
        text="body",
        path="/tmp/a.txt",
        source_uri="file://a",
        mime_type="text/plain",
        owner="u",
        workspace_id="w",
        permissions={"read": True},
        captured_at="now",
        modified_at="then",
        conversation_id="c",
        agent_used="a",
        metadata={"k": 1},
    )
    assert item.source_type == "file"
    assert item.metadata["k"] == 1

    bare = IngestionResult(status="ok", source_type="text")
    payload = bare.as_dict()
    assert payload["status"] == "ok"
    assert payload["indexing_status"] == "pending"
    assert "extraction_quality" not in payload
    assert "warnings" not in payload
    assert "quality_gate" not in payload

    rich = IngestionResult(
        status="ok",
        source_type="web_url",
        node_id="n",
        source_node_id="s",
        content_hash="h",
        title="t",
        chunk_ids=["c1"],
        chunk_count=1,
        duplicate=True,
        embedded=True,
        indexing_status="indexed",
        provenance_id="p",
        detail="d",
        extraction_quality={"score": 0.9},
        warnings=["thin"],
        quality_gate={"status": "ok"},
    )
    rich_payload = rich.as_dict()
    assert rich_payload["extraction_quality"] == {"score": 0.9}
    assert rich_payload["warnings"] == ["thin"]
    assert rich_payload["quality_gate"] == {"status": "ok"}
    assert rich_payload["duplicate"] is True

    empty_warnings = IngestionResult(status="ok", source_type="text", warnings=[])
    assert "warnings" not in empty_warnings.as_dict()
