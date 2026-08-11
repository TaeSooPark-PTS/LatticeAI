"""v11.2.0 F3 — video ingestion: keyframes through the image door, plus subtitles.

11.1.0 recognized a video and refused it, and the reason it gave was *scope*:
keyframe extraction was not implemented. It is now, and the promises that
matter are the ones a "we support video" claim usually gets wrong:

* **No decoder, no pretending.** ffmpeg is looked up on PATH, never bundled.
  Without it the refusal is unchanged in shape and its reason becomes runtime,
  not scope. Nothing is fabricated to fill the gap.
* **Not a second retrieval path.** A keyframe becomes an ordinary ``Image``
  node — OCR, caption, vector, thumbnail — joined to its video by
  ``CONTAINS_IMAGE``. Subtitles become ordinary chunks. Video adds a node type,
  not a pipeline.
* **Still off by default.** Video sits behind ``allow_multimodal``, whose
  default is unchanged, plus its own sub-switch.

Every ffmpeg interaction is seamed: this suite never runs a subprocess.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# ``_which_ffmpeg`` / ``_run_ffmpeg`` / ``subprocess`` are globals of the
# ports submodule, which is where the code that calls them reads them from.
# Patching the package would rebind a copy nothing looks at.
import lattice_brain.multimodal.ports as mm  # noqa: E402
from lattice_brain.graph.store import KnowledgeGraphStore  # noqa: E402
from lattice_brain.ingestion import (  # noqa: E402
    ALLOW_VIDEO_ENV,
    MULTIMODAL_GATE,
    VIDEO_GATE,
    IngestionItem,
    IngestionPipeline,
)
from lattice_brain.multimodal import (  # noqa: E402
    VIDEO_UNAVAILABLE_DETAIL,
    MultimodalPorts,
    VideoFacts,
    extract_keyframes,
    ffmpeg_available,
    find_subtitle,
    parse_subtitles,
    read_video_facts,
    video_frame_dir,
    video_node_id,
    video_quality_score,
)

SRT = """1
00:00:01,000 --> 00:00:04,000
안녕하세요, 오늘 회의를 시작하겠습니다.

2
00:00:04,500 --> 00:00:08,000
<i>첫 안건은</i> 릴리스 일정입니다.
안녕하세요, 오늘 회의를 시작하겠습니다.
"""

VTT = """WEBVTT

NOTE this is a comment

00:00:01.000 --> 00:00:03.000
This is the <c.yellow>first</c> caption.
"""


@pytest.fixture(autouse=True)
def _no_real_ffmpeg(monkeypatch):
    """No test in this file may depend on the host having ffmpeg."""
    monkeypatch.setattr(mm, "_which_ffmpeg", lambda: None)
    monkeypatch.setattr(MULTIMODAL_GATE, "_override", None, raising=False)
    monkeypatch.setattr(VIDEO_GATE, "_override", None, raising=False)


@pytest.fixture
def store(tmp_path):
    return KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")


def _png(path: Path, colour=(9, 40, 60)) -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 6), colour).save(path)
    return path


def _video(tmp_path: Path, name: str = "standup.mp4") -> Path:
    path = tmp_path / name
    path.write_bytes(b"not really a movie, but it hashes")
    return path


def _frames(tmp_path: Path, count: int = 2) -> List[str]:
    # Distinct pixels per frame: an ``Image`` node is content-addressed, so two
    # identical stills would legitimately collapse into one memory.
    return [
        str(_png(tmp_path / "frames" / f"keyframe-{i:03d}.jpg", (9, 40, 60 + i * 30)))
        for i in range(count)
    ]


# ── subtitles ────────────────────────────────────────────────────────────────
def test_srt_scaffolding_is_stripped_down_to_the_words():
    parsed = parse_subtitles(SRT)
    assert parsed.splitlines() == [
        "안녕하세요, 오늘 회의를 시작하겠습니다.",
        "첫 안건은 릴리스 일정입니다.",
        "안녕하세요, 오늘 회의를 시작하겠습니다.",
    ]


def test_webvtt_headers_notes_and_inline_tags_are_dropped():
    assert parse_subtitles(VTT) == "This is the first caption."
    assert parse_subtitles("") == ""
    # A rolling caption that repeats the same line back-to-back is collapsed…
    assert parse_subtitles("same\nsame\nother") == "same\nother"
    # …and a cue that is only markup contributes nothing.
    assert parse_subtitles("<c></c>") == ""


def test_a_companion_subtitle_is_found_by_basename(tmp_path):
    video = _video(tmp_path)
    assert find_subtitle(video) is None
    vtt = tmp_path / "standup.vtt"
    vtt.write_text(VTT, encoding="utf-8")
    assert find_subtitle(video) == vtt
    srt = tmp_path / "standup.srt"
    srt.write_text(SRT, encoding="utf-8")
    assert find_subtitle(video) == srt  # .srt is preferred, deterministically


# ── keyframes ────────────────────────────────────────────────────────────────
def test_without_ffmpeg_keyframe_extraction_reports_unavailable(tmp_path):
    assert ffmpeg_available() is False
    outcome = extract_keyframes(_video(tmp_path), tmp_path / "out")
    assert outcome == {
        "status": "unavailable", "frames": [], "detail": VIDEO_UNAVAILABLE_DETAIL,
    }


def test_an_injected_extractor_wins_over_ffmpeg(tmp_path):
    ports = MultimodalPorts(keyframe_extractor=lambda *_a: _frames(tmp_path, 3))
    outcome = extract_keyframes(_video(tmp_path), tmp_path / "out", count=2, ports=ports)
    assert outcome["status"] == "ok" and len(outcome["frames"]) == 2

    empty = MultimodalPorts(keyframe_extractor=lambda *_a: [])
    assert extract_keyframes(_video(tmp_path), tmp_path / "out", ports=empty)["status"] == "empty"

    def _broken(*_a):
        raise RuntimeError("decoder exploded")

    failed = extract_keyframes(
        _video(tmp_path), tmp_path / "out", ports=MultimodalPorts(keyframe_extractor=_broken),
    )
    assert failed["status"] == "failed" and "decoder exploded" in failed["detail"]


def test_ffmpeg_is_invoked_with_an_argv_list_and_never_a_shell(tmp_path, monkeypatch):
    seen: Dict[str, Any] = {}

    def _fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        # ffmpeg writes the frames; simulate that so the glob finds them.
        Path(argv[-1]).parent.mkdir(parents=True, exist_ok=True)
        _png(Path(argv[-1]).parent / "keyframe-001.jpg")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(mm, "_which_ffmpeg", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(mm.subprocess, "run", _fake_run)
    assert ffmpeg_available() is True

    outcome = extract_keyframes(_video(tmp_path), tmp_path / "out", count=4)
    assert outcome["status"] == "ok" and len(outcome["frames"]) == 1
    assert seen["argv"][0] == "/usr/bin/ffmpeg"
    assert "-frames:v" in seen["argv"] and "4" in seen["argv"]
    assert "shell" not in seen["kwargs"]


def test_a_failing_ffmpeg_is_reported_three_different_ways(tmp_path, monkeypatch):
    monkeypatch.setattr(mm, "_which_ffmpeg", lambda: "/usr/bin/ffmpeg")

    monkeypatch.setattr(mm, "_run_ffmpeg", lambda *_a: 1)
    nonzero = extract_keyframes(_video(tmp_path), tmp_path / "a")
    assert nonzero["status"] == "failed" and "status 1" in nonzero["detail"]

    monkeypatch.setattr(mm, "_run_ffmpeg", lambda *_a: 0)
    silent = extract_keyframes(_video(tmp_path), tmp_path / "b")
    assert silent["status"] == "empty"

    def _raise(*_a):
        raise subprocess.TimeoutExpired("ffmpeg", 1)

    monkeypatch.setattr(mm, "_run_ffmpeg", _raise)
    crashed = extract_keyframes(_video(tmp_path), tmp_path / "c")
    assert crashed["status"] == "failed" and "ffmpeg failed" in crashed["detail"]


def test_frames_that_landed_despite_a_nonzero_exit_are_kept(tmp_path, monkeypatch):
    """A partial decode is still a memory; the exit code alone is not the verdict."""
    monkeypatch.setattr(mm, "_which_ffmpeg", lambda: "/usr/bin/ffmpeg")

    def _partial(_binary, args):
        _png(Path(args[-1]).parent / "keyframe-001.jpg")
        return 69

    monkeypatch.setattr(mm, "_run_ffmpeg", _partial)
    outcome = extract_keyframes(_video(tmp_path), tmp_path / "partial")
    assert outcome["status"] == "ok" and len(outcome["frames"]) == 1


# ── facts ────────────────────────────────────────────────────────────────────
def test_read_video_facts_prefers_a_supplied_transcript(tmp_path):
    ports = MultimodalPorts(keyframe_extractor=lambda *_a: _frames(tmp_path, 1))
    facts = read_video_facts(
        _video(tmp_path), tmp_path / "out", ports=ports, subtitle_text=VTT,
    )
    assert facts.subtitle_status == "ok"
    assert facts.subtitle_text == "This is the first caption."
    assert facts.subtitle_path is None
    assert facts.searchable is True


def test_read_video_facts_finds_reads_and_reports_a_companion_file(tmp_path):
    video = _video(tmp_path)
    (tmp_path / "standup.srt").write_text(SRT, encoding="utf-8")
    facts = read_video_facts(video, tmp_path / "out")
    assert facts.subtitle_status == "ok"
    assert facts.subtitle_path == str(tmp_path / "standup.srt")
    assert facts.keyframe_status == "unavailable"

    (tmp_path / "standup.srt").write_text("WEBVTT\n", encoding="utf-8")
    assert read_video_facts(video, tmp_path / "out").subtitle_status == "empty"

    (tmp_path / "standup.srt").unlink()
    assert read_video_facts(video, tmp_path / "out").subtitle_status == "absent"


def test_an_unreadable_subtitle_file_is_a_state_not_a_crash(tmp_path, monkeypatch):
    video = _video(tmp_path)
    companion = tmp_path / "standup.srt"
    companion.write_text(SRT, encoding="utf-8")

    def _boom(*_a, **_k):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_text", _boom)
    facts = read_video_facts(video, tmp_path / "out")
    assert facts.subtitle_status == "unreadable"
    assert facts.subtitle_text == ""
    assert "Permission denied" in facts.subtitle_path


def test_quality_scores_what_can_actually_be_retrieved(tmp_path):
    bare = VideoFacts(path="x.mp4")
    scored = video_quality_score(bare)
    assert scored["reasons"] == ["no_subtitles_absent", "no_keyframes_unavailable"]
    assert scored["score"] == 0.1

    rich = VideoFacts(
        path="x.mp4", subtitle_status="ok", subtitle_text="가" * 400,
        keyframe_status="ok", keyframes=["a.jpg", "b.jpg"],
    )
    verdict = video_quality_score(rich)
    assert verdict["reasons"] == ["subtitles", "keyframes"]
    assert verdict["score"] > scored["score"]

    metadata = rich.as_metadata()
    assert metadata["modality"] == "video" and metadata["keyframes"] == 2
    assert "keyframe_detail" not in metadata and "subtitle_path" not in metadata
    detailed = VideoFacts(
        path="x.mp4", keyframe_detail="slow decode", subtitle_path="x.srt",
    ).as_metadata()
    assert detailed["keyframe_detail"] == "slow decode"
    assert detailed["subtitle_path"] == "x.srt"


def test_the_frame_folder_and_node_id_are_content_addressed(tmp_path):
    assert video_node_id("abc", "ws-1") != video_node_id("abc", "ws-2")
    assert video_node_id("abc") == video_node_id("abc")
    folder = video_frame_dir(tmp_path / "blobs", "a" * 64)
    assert folder.parent.name == "video_frames" and len(folder.name) == 32


# ── the pipeline ─────────────────────────────────────────────────────────────
def test_the_refusal_names_the_reason_that_actually_applies(store, tmp_path, monkeypatch):
    path = _video(tmp_path)
    item = IngestionItem(source_type="file", path=str(path))

    off = IngestionPipeline(store)
    assert "multi-modal ingestion is off" in off.multimodal_status()["video_detail"]

    on = IngestionPipeline(store, allow_multimodal=True)
    result = on.ingest(item, user_email="me@local")
    assert result.status == "unavailable"
    assert result.detail == VIDEO_UNAVAILABLE_DETAIL

    monkeypatch.setattr(mm, "_which_ffmpeg", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setenv(ALLOW_VIDEO_ENV, "0")
    turned_off = IngestionPipeline(store, allow_multimodal=True)
    assert "video ingestion is turned off" in turned_off.multimodal_status()["video_detail"]
    assert turned_off.ingest(item, user_email="me@local").status == "unavailable"


def test_a_video_becomes_a_node_with_image_children_and_subtitle_chunks(store, tmp_path):
    video = _video(tmp_path)
    (tmp_path / "standup.srt").write_text(SRT + ("가나다 " * 400), encoding="utf-8")
    ports = MultimodalPorts(
        keyframe_extractor=lambda _v, dest, count: _frames(tmp_path, 2),
        captioner=lambda _p: "화이트보드가 있는 회의실",
        vision_embedder=lambda _p: [1.0, 0.0],
        vision_model_id="clip:2",
    )
    pipeline = IngestionPipeline(store, allow_multimodal=True, multimodal=ports)

    result = pipeline.ingest(
        IngestionItem(source_type="file", path=str(video), workspace_id="ws-1"),
        user_email="me@local",
    )

    assert result.status == "ok"
    assert result.node_id.startswith("video:")
    assert result.chunk_ids, "subtitle text should be chunked like any other text"
    assert result.extraction_quality["reasons"] == ["subtitles", "keyframes"]

    with store._connect() as conn:
        node = conn.execute(
            "SELECT type, summary FROM nodes WHERE id=?", (result.node_id,)
        ).fetchone()
        images = conn.execute(
            "SELECT to_node FROM edges WHERE from_node=? AND type='CONTAINS_IMAGE'",
            (result.node_id,),
        ).fetchall()
        v2 = conn.execute(
            "SELECT type, legacy_type FROM nodes_v2 WHERE id=?", (result.node_id,)
        ).fetchone()
    assert node["type"] == "Video"
    assert "회의를 시작" in node["summary"]
    assert len(images) == 2
    assert all(str(row["to_node"]).startswith("image:") for row in images)
    # Additive taxonomy: the canonical type is VIDEO, the label is preserved.
    assert (v2["type"], v2["legacy_type"]) == ("VIDEO", "Video")

    # Re-ingesting the same file is idempotent.
    again = pipeline.ingest(
        IngestionItem(source_type="file", path=str(video), workspace_id="ws-1"),
        user_email="me@local",
    )
    assert again.node_id == result.node_id and again.duplicate is True


def test_a_video_with_no_subtitles_says_so_instead_of_leaving_a_blank_card(store, tmp_path):
    video = _video(tmp_path, "silent.mov")
    ports = MultimodalPorts(keyframe_extractor=lambda *_a: _frames(tmp_path, 1))
    result = IngestionPipeline(store, allow_multimodal=True, multimodal=ports).ingest(
        IngestionItem(source_type="video", path=str(video), title="조용한 영상"),
        user_email="me@local",
    )
    with store._connect() as conn:
        summary = conn.execute(
            "SELECT summary FROM nodes WHERE id=?", (result.node_id,)
        ).fetchone()["summary"]
    assert "자막이 없어" in summary
    assert result.extraction_quality["reasons"][0] == "no_subtitles_absent"


def test_a_frame_pillow_cannot_open_is_skipped_not_fatal(store, tmp_path):
    good = _png(tmp_path / "frames" / "keyframe-001.jpg")
    bad = tmp_path / "frames" / "keyframe-002.jpg"
    bad.write_bytes(b"not an image at all")
    ports = MultimodalPorts(keyframe_extractor=lambda *_a: [str(good), str(bad)])

    result = IngestionPipeline(store, allow_multimodal=True, multimodal=ports).ingest(
        IngestionItem(source_type="file", path=str(_video(tmp_path))),
        user_email="me@local",
    )
    assert result.status == "ok"
    with store._connect() as conn:
        images = conn.execute(
            "SELECT to_node FROM edges WHERE from_node=? AND type='CONTAINS_IMAGE'",
            (result.node_id,),
        ).fetchall()
    assert len(images) == 1


def test_folder_scans_admit_videos_only_when_one_could_be_decoded(store, monkeypatch):
    off = IngestionPipeline(store)
    assert ".mp4" not in off._folder_extensions()

    pictures_only = IngestionPipeline(store, allow_multimodal=True)
    assert ".png" in pictures_only._folder_extensions()
    assert ".mp4" not in pictures_only._folder_extensions()

    monkeypatch.setattr(mm, "_which_ffmpeg", lambda: "/usr/bin/ffmpeg")
    with_video = IngestionPipeline(store, allow_multimodal=True)
    assert ".mp4" in with_video._folder_extensions()
    status = with_video.multimodal_status()
    assert status["video"] is True and status["video_detail"] is None
    assert status["gates"]["video"]["flag"] == ALLOW_VIDEO_ENV


def test_the_multimodal_gate_is_resolved_per_call_not_frozen_at_construction(store, tmp_path):
    """The seam the settings track needs: a live pipeline can change its mind."""
    pipeline = IngestionPipeline(store)
    image = _png(tmp_path / "shot.png")
    item = IngestionItem(source_type="file", path=str(image))

    plain = pipeline.ingest(item, user_email="me@local")
    assert not plain.node_id.startswith("image:")

    MULTIMODAL_GATE.set(True)
    try:
        routed = pipeline.ingest(item, user_email="me@local")
    finally:
        MULTIMODAL_GATE.set(None)
    assert routed.node_id.startswith("image:")
    assert pipeline.multimodal_status()["enabled"] is False
