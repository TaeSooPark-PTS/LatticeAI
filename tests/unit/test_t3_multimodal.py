"""Brain Core's multi-modal layer: what it observed, and what it did not.

Every assertion here is about the difference between *knowing* something about
a picture or a recording and *appearing to*. Real PNGs are written to
``tmp_path`` (Pillow is a core dependency), OCR arrives as a fake
``pytesseract`` module, and captions/vectors/transcripts arrive as injected
callables — the same shape the app layer uses, with no model anywhere.
"""

from __future__ import annotations

import random
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.multimodal import (  # noqa: E402
    MAX_THUMBNAIL_CHARS,
    MODALITY_AUDIO,
    MODALITY_IMAGE,
    MODALITY_TEXT,
    MODALITY_VIDEO,
    AudioFacts,
    ImageFacts,
    MultimodalPorts,
    audio_quality_score,
    detect_modality,
    extract_image_facts,
    image_node_id,
    image_quality_score,
    transcribe_audio,
    write_image_memory,
)


def _png(path: Path, size=(40, 24), colour="blue") -> Path:
    from PIL import Image

    Image.new("RGB", size, colour).save(path)
    return path


def _noisy_png(path: Path) -> Path:
    """A 400x400 noise image — its 96px thumbnail will not compress."""
    from PIL import Image

    rng = random.Random(11)
    image = Image.new("RGB", (400, 400))
    image.putdata([
        (rng.randrange(256), rng.randrange(256), rng.randrange(256))
        for _ in range(400 * 400)
    ])
    image.save(path)
    return path


def _fake_ocr(monkeypatch, text="회의록 초안\nQ3 roadmap review", error: str = ""):
    module = types.ModuleType("pytesseract")

    def _image_to_string(image):
        if error:
            raise RuntimeError(error)
        return text

    module.image_to_string = _image_to_string
    monkeypatch.setitem(sys.modules, "pytesseract", module)


def _no_ocr(monkeypatch):
    monkeypatch.setitem(sys.modules, "pytesseract", None)


# ── modality routing ─────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("path", "mime", "expected"),
    [
        ("photo.png", None, MODALITY_IMAGE),
        ("photo.bin", "image/heic", MODALITY_IMAGE),
        ("memo.m4a", None, MODALITY_AUDIO),
        ("memo.bin", "audio/webm; codecs=opus", MODALITY_AUDIO),
        ("clip.mov", None, MODALITY_VIDEO),
        ("clip.bin", "video/mp4", MODALITY_VIDEO),
        ("notes.md", None, MODALITY_TEXT),
        ("notes.md", "text/markdown", MODALITY_TEXT),
        ("mystery", None, MODALITY_TEXT),
        # MIDI is in this module's audio table on purpose: CPython's built-in
        # mime map has no `.mid`, so a host without a system mime file would
        # otherwise call a MIDI file text.
        ("theme.mid", None, MODALITY_AUDIO),
        ("theme.midi", None, MODALITY_AUDIO),
        # Not in this module's tables — the stdlib guess is the last resort.
        ("diagram.svg", None, MODALITY_IMAGE),
    ],
)
def test_modality_is_decided_by_mime_first_and_extension_second(path, mime, expected):
    assert detect_modality(path, mime) == expected


def test_an_mp4_is_video_by_extension_but_audio_when_declared_so():
    # A voice memo really can arrive in an mp4 container — but only the caller
    # who saw the bytes may say so.
    assert detect_modality("recording.mp4") == MODALITY_VIDEO
    assert detect_modality("recording.mp4", "audio/mp4") == MODALITY_AUDIO


def test_modality_of_nothing_at_all_is_text():
    assert detect_modality() == MODALITY_TEXT


# ── what an image memory actually contains ───────────────────────────────────
def test_an_image_with_ocr_a_caption_and_a_vector_records_all_three(monkeypatch, tmp_path):
    _fake_ocr(monkeypatch)
    path = _png(tmp_path / "whiteboard.png")
    ports = MultimodalPorts(
        captioner=lambda _p: "A whiteboard with sticky notes",
        vision_embedder=lambda _p: [0.6, 0.8],
        vision_model_id="clip:2",
    )

    facts = extract_image_facts(str(path), ports=ports)

    assert facts.readable is True
    assert (facts.width, facts.height) == (40, 24)
    assert facts.image_format == "PNG"
    assert facts.ocr_status == "ok"
    assert "회의록 초안" in facts.ocr_text
    assert facts.caption == "A whiteboard with sticky notes"
    assert facts.caption_status == "ok"
    assert facts.embedding == [0.6, 0.8]
    assert facts.embedding_status == "ok"
    assert facts.thumbnail.startswith("data:image/png;base64,")
    # The caption leads: it describes the picture, OCR only quotes it.
    assert facts.index_text().startswith("A whiteboard with sticky notes")
    meta = facts.as_metadata()
    assert meta["modality"] == MODALITY_IMAGE
    assert meta["ocr_chars"] == len(facts.ocr_text)
    assert meta["vision_embedding"] == "ok"


def test_without_a_vlm_there_is_no_caption_key_at_all(monkeypatch, tmp_path):
    _no_ocr(monkeypatch)
    path = _png(tmp_path / "photo.png")

    facts = extract_image_facts(str(path))

    assert facts.caption is None
    assert facts.caption_status == "unavailable"
    assert facts.ocr_status == "unavailable"
    meta = facts.as_metadata()
    assert "caption" not in meta
    assert meta["ocr_status"] == "unavailable"
    assert meta["ocr_detail"]
    assert facts.index_text() == ""


def test_a_captioner_that_raises_produces_no_caption(monkeypatch, tmp_path):
    _no_ocr(monkeypatch)

    def _boom(_path):
        raise RuntimeError("VLM crashed")

    facts = extract_image_facts(
        str(_png(tmp_path / "photo.png")), ports=MultimodalPorts(captioner=_boom)
    )

    assert facts.caption is None
    assert facts.caption_status == "unavailable"


def test_a_captioner_returning_whitespace_produces_no_caption(monkeypatch, tmp_path):
    _no_ocr(monkeypatch)

    facts = extract_image_facts(
        str(_png(tmp_path / "photo.png")),
        ports=MultimodalPorts(captioner=lambda _p: "   "),
    )

    assert facts.caption is None


def test_a_vision_embedder_that_raises_is_recorded_as_failed(monkeypatch, tmp_path):
    _no_ocr(monkeypatch)

    def _boom(_path):
        raise RuntimeError("no CLIP weights")

    facts = extract_image_facts(
        str(_png(tmp_path / "photo.png")), ports=MultimodalPorts(vision_embedder=_boom)
    )

    assert facts.embedding is None
    assert facts.embedding_status == "failed"
    assert "no CLIP weights" in facts.as_metadata()["vision_embedding_detail"]


def test_a_vision_embedder_returning_nothing_is_recorded_as_failed(monkeypatch, tmp_path):
    _no_ocr(monkeypatch)

    facts = extract_image_facts(
        str(_png(tmp_path / "photo.png")),
        ports=MultimodalPorts(vision_embedder=lambda _p: []),
    )

    assert facts.embedding_status == "failed"
    assert "empty vector" in facts.embedding_detail


def test_ocr_can_be_switched_off_and_says_it_was_skipped(monkeypatch, tmp_path):
    _fake_ocr(monkeypatch)

    facts = extract_image_facts(str(_png(tmp_path / "photo.png")), ocr=False)

    assert facts.ocr_status == "skipped"
    assert facts.ocr_text == ""


def test_ocr_that_finds_nothing_says_empty_rather_than_ok(monkeypatch, tmp_path):
    _fake_ocr(monkeypatch, text="   ")

    facts = extract_image_facts(str(_png(tmp_path / "photo.png")))

    assert facts.ocr_status == "empty"
    assert facts.ocr_detail == "no text found in the image"


def test_a_broken_ocr_runtime_is_reported_not_hidden(monkeypatch, tmp_path):
    _fake_ocr(monkeypatch, error="tesseract not on PATH")

    facts = extract_image_facts(str(_png(tmp_path / "photo.png")))

    assert facts.ocr_status == "failed"
    assert "tesseract not on PATH" in facts.ocr_detail


def test_a_file_that_is_not_an_image_is_a_state_not_a_crash(tmp_path):
    path = tmp_path / "broken.png"
    path.write_bytes(b"definitely not a png")

    facts = extract_image_facts(str(path))

    assert facts.readable is False
    assert facts.error
    assert facts.as_metadata()["image_error"]


# ── thumbnails ───────────────────────────────────────────────────────────────
def test_the_thumbnail_can_be_turned_off(monkeypatch, tmp_path):
    _no_ocr(monkeypatch)

    facts = extract_image_facts(str(_png(tmp_path / "photo.png")), thumbnail=False)

    assert facts.thumbnail is None
    assert "thumbnail" not in facts.as_metadata()


def test_a_palette_image_is_converted_before_thumbnailing(monkeypatch, tmp_path):
    from PIL import Image

    _no_ocr(monkeypatch)
    path = tmp_path / "palette.png"
    Image.new("P", (30, 30)).save(path)

    facts = extract_image_facts(str(path))

    assert facts.mode == "P"
    assert facts.thumbnail.startswith("data:image/png;base64,")


def test_a_thumbnail_that_will_not_compress_is_dropped_rather_than_stored(
    monkeypatch, tmp_path
):
    _no_ocr(monkeypatch)

    facts = extract_image_facts(str(_noisy_png(tmp_path / "noise.png")))

    # Noise at 96px still encodes past the budget, so no thumbnail is kept.
    assert facts.thumbnail is None


def test_a_thumbnail_failure_never_fails_the_extraction():
    from lattice_brain.multimodal import _thumbnail_data_uri

    class _Unthumbnailable:
        def copy(self):
            raise RuntimeError("decoder gave up")

    assert _thumbnail_data_uri(_Unthumbnailable()) is None


def test_the_thumbnail_budget_is_a_real_number():
    assert MAX_THUMBNAIL_CHARS == 24_000


# ── extraction quality for pictures ──────────────────────────────────────────
def test_quality_rewards_what_can_actually_be_retrieved():
    rich = ImageFacts(
        path="/a.png",
        ocr_text="x" * 400,
        ocr_status="ok",
        caption="a diagram",
        caption_status="ok",
        embedding=[0.1],
        embedding_status="ok",
    )
    bare = ImageFacts(path="/a.png", ocr_status="unavailable")

    assert image_quality_score(rich)["score"] == pytest.approx(1.0)
    assert image_quality_score(rich)["reasons"] == [
        "ocr_text",
        "vision_caption",
        "vision_embedding",
    ]
    assert image_quality_score(bare)["score"] == pytest.approx(0.15)
    assert image_quality_score(bare)["reasons"] == ["ocr_unavailable", "no_vision_caption"]


def test_quality_distinguishes_ocr_skipped_from_ocr_empty():
    skipped = image_quality_score(ImageFacts(path="/a.png", ocr_status="skipped"))
    empty = image_quality_score(ImageFacts(path="/a.png", ocr_status="empty"))

    assert "ocr_skipped" in skipped["reasons"]
    assert "no_ocr_text" in empty["reasons"]


def test_an_unreadable_image_scores_zero():
    verdict = image_quality_score(ImageFacts(path="/a.png", error="cannot identify"))

    assert verdict == {"score": 0.0, "reasons": ["image_unreadable"]}


# ── the graph write ──────────────────────────────────────────────────────────
class _RecordingStore:
    """The cross-mixin write door, recorded rather than executed."""

    def __init__(self, *, source_node: bool = True, existing: bool = False):
        self.nodes: list[tuple] = []
        self.edges: list[tuple] = []
        self.chunks: list[dict] = []
        self._existing = existing
        if source_node:
            self._attach_source_node = self._attach

    def _connect(self):
        store = self

        class _Ctx:
            def __enter__(self):
                return store

            def __exit__(self, *exc):
                return False

        return _Ctx()

    def execute(self, sql, params=()):
        class _Cursor:
            def fetchone(_self):
                return (1,) if store._existing else None

        store = self
        return _Cursor()

    def _upsert_node(self, conn, node_id, node_type, title, **kwargs):
        self.nodes.append((node_id, node_type, title, kwargs))
        return node_id

    def _upsert_edge(self, conn, from_node, to_node, edge_type, **kwargs):
        self.edges.append((from_node, to_node, edge_type))
        return f"{from_node}->{to_node}"

    def _upsert_chunk(self, conn, *, chunk_id, source_node, text, metadata):
        self.chunks.append({"id": chunk_id, "source": source_node, "text": text})
        return chunk_id

    def _attach(self, conn, node_id, **kwargs):
        return f"source:{node_id}"


def test_writing_an_image_creates_an_image_node_and_its_ocr_child(tmp_path):
    store = _RecordingStore()
    path = _png(tmp_path / "board.png")
    facts = ImageFacts(path=str(path), ocr_text="Q3 roadmap", ocr_status="ok")

    result = write_image_memory(store, path=path, facts=facts, title="board.png")

    assert result["type"] == "Image"
    assert result["node_id"] == image_node_id(result["content_hash"])
    assert result["duplicate"] is False
    assert result["source_node_id"] == f"source:{result['node_id']}"
    types_written = [node[1] for node in store.nodes]
    assert types_written == ["Image", "ImageText"]
    assert store.edges == [(result["node_id"], store.nodes[1][0], "포함함")]
    assert store.nodes[0][3]["summary"] == "Q3 roadmap"


def test_an_image_nobody_could_read_still_gets_an_honest_summary(tmp_path):
    store = _RecordingStore()
    path = _png(tmp_path / "photo.png")

    result = write_image_memory(
        store, path=path, facts=ImageFacts(path=str(path)), title=""
    )

    assert store.nodes[0][3]["summary"] == "[image] photo.png"
    assert result["title"] == "photo.png"
    assert result["chunk_count"] == 0
    assert [node[1] for node in store.nodes] == ["Image"]


def test_long_ocr_text_is_chunked_so_it_stays_searchable(tmp_path):
    store = _RecordingStore()
    path = _png(tmp_path / "scan.png")
    facts = ImageFacts(path=str(path), ocr_text="회의 " * 900, ocr_status="ok")

    result = write_image_memory(store, path=path, facts=facts, title="scan.png")

    assert result["chunk_count"] == len(store.chunks) > 1
    assert result["chunk_ids"][0] == store.chunks[0]["id"]
    assert all(chunk["source"] == result["node_id"] for chunk in store.chunks)


def test_the_caption_puts_the_picture_into_the_graph_not_just_the_index(tmp_path):
    """A photo of a whiteboard should be one hop from every note on the topic.

    This is the difference between "the caption is searchable" and "the caption
    contributes to the graph": the same concept extractor every text door uses
    runs over the caption, so the image joins the existing concept nodes rather
    than sitting on an island only exact search can reach.
    """
    store = _RecordingStore()
    path = _png(tmp_path / "board.png")
    facts = ImageFacts(
        path=str(path),
        caption="화이트보드에 Kubernetes 배포 전략과 Q3 로드맵이 적혀 있다.",
        caption_status="ok",
    )

    result = write_image_memory(store, path=path, facts=facts, title="board.png")

    concept_ids = result["metadata"]["concepts"]
    assert concept_ids, "the caption produced no graph structure at all"
    # Every extracted concept hangs off the image by a "포함함" edge.
    assert all(
        (result["node_id"], concept_id, "포함함") in store.edges
        for concept_id in concept_ids
    )
    assert set(concept_ids) <= {node[0] for node in store.nodes}


def test_an_image_with_nothing_readable_contributes_no_concepts(tmp_path):
    store = _RecordingStore()
    path = _png(tmp_path / "photo.png")

    result = write_image_memory(store, path=path, facts=ImageFacts(path=str(path)), title="")

    # Nothing was read, so there is nothing to say about it.
    assert result["metadata"]["concepts"] == []


def test_a_store_without_source_nodes_still_writes_the_image(tmp_path):
    store = _RecordingStore(source_node=False)
    path = _png(tmp_path / "photo.png")

    result = write_image_memory(
        store, path=path, facts=ImageFacts(path=str(path)), title="photo.png"
    )

    assert result["source_node_id"] is None


def test_re_ingesting_the_same_image_reports_it_as_a_duplicate(tmp_path):
    store = _RecordingStore(existing=True)
    path = _png(tmp_path / "photo.png")

    result = write_image_memory(
        store, path=path, facts=ImageFacts(path=str(path)), title="photo.png"
    )

    assert result["duplicate"] is True


def test_the_image_node_id_is_scoped_to_the_workspace():
    assert image_node_id("abc") != image_node_id("abc", "team-a")
    assert image_node_id("abc", "team-a") == image_node_id("abc", "team-a")


# ── audio ────────────────────────────────────────────────────────────────────
def test_a_supplied_transcript_skips_the_local_model():
    facts = transcribe_audio(
        "/memos/a.m4a",
        ports=MultimodalPorts(transcriber=lambda _p: "should not be called"),
        transcript="  I decided to ship on Friday  ",
    )

    assert facts.transcription_status == "supplied"
    assert facts.transcript == "I decided to ship on Friday"
    assert facts.searchable is True


def test_no_transcriber_means_no_transcript_and_says_so():
    facts = transcribe_audio("/memos/a.m4a")

    assert facts.transcription_status == "unavailable"
    assert facts.transcript == ""
    assert facts.searchable is False
    assert "no local transcriber" in facts.detail


def test_a_transcriber_that_works_produces_a_searchable_memo():
    facts = transcribe_audio(
        "/memos/a.m4a", ports=MultimodalPorts(transcriber=lambda _p: " ship on Friday ")
    )

    assert facts.transcription_status == "ok"
    assert facts.transcript == "ship on Friday"


def test_a_transcriber_that_raises_is_a_reported_failure():
    def _boom(_path):
        raise RuntimeError("whisper died")

    facts = transcribe_audio("/memos/a.m4a", ports=MultimodalPorts(transcriber=_boom))

    assert facts.transcription_status == "failed"
    assert "whisper died" in facts.detail


def test_a_transcriber_returning_nothing_is_a_failure_not_an_empty_memo():
    facts = transcribe_audio(
        "/memos/a.m4a", ports=MultimodalPorts(transcriber=lambda _p: "  ")
    )

    assert facts.transcription_status == "failed"
    assert "no text" in facts.detail


def test_audio_quality_follows_whether_there_are_words_at_all():
    assert audio_quality_score(AudioFacts(path="/a.m4a")) == {
        "score": 0.0,
        "reasons": ["no_transcript"],
    }
    long_memo = AudioFacts(path="/a.m4a", transcript="x" * 400)
    assert audio_quality_score(long_memo)["score"] == pytest.approx(1.0)
    short = AudioFacts(path="/a.m4a", transcript="x" * 100)
    assert audio_quality_score(short)["score"] == pytest.approx(0.625)


# ── the port bundle ──────────────────────────────────────────────────────────
def test_the_port_bundle_describes_exactly_what_is_wired():
    empty = MultimodalPorts().describe()
    full = MultimodalPorts(
        captioner=lambda _p: "x",
        vision_embedder=lambda _p: [1.0],
        transcriber=lambda _p: "x",
        vision_model_id="clip:512",
        vision_space="shared",
    ).describe()

    assert empty == {
        "caption": False,
        "vision_embedding": False,
        "transcription": False,
        "vision_model_id": "",
        "vision_space": MODALITY_IMAGE,
    }
    assert full["caption"] and full["vision_embedding"] and full["transcription"]
    assert full["vision_space"] == "shared"
