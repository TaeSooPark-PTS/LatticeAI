"""Multi-modal ingestion, both ways round: flag off and flag on.

The flag is the contract. With ``allow_multimodal=False`` a folder scan must
produce byte-identical work to the release before this one — same files
matched, same node ids, same node types — and with it on the same folder must
additionally yield ``Image`` nodes that a query can actually find. Both
directions are asserted against a real ``KnowledgeGraphStore`` over SQLite.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph.store import KnowledgeGraphStore  # noqa: E402
from lattice_brain.ingestion import (  # noqa: E402
    ALLOW_MULTIMODAL_ENV,
    DEFAULT_FOLDER_EXTENSIONS,
    IngestionItem,
    IngestionPipeline,
)
from lattice_brain.multimodal import VIDEO_OUT_OF_SCOPE, MultimodalPorts  # noqa: E402


@pytest.fixture()
def store(tmp_path: Path) -> KnowledgeGraphStore:
    return KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")


def _png(path: Path, colour="blue") -> Path:
    from PIL import Image

    Image.new("RGB", (48, 32), colour).save(path)
    return path


def _fake_ocr(monkeypatch, text: str):
    module = types.ModuleType("pytesseract")
    module.image_to_string = lambda image: text
    monkeypatch.setitem(sys.modules, "pytesseract", module)


def _no_ocr(monkeypatch):
    monkeypatch.setitem(sys.modules, "pytesseract", None)


# ── off means unchanged ──────────────────────────────────────────────────────
def test_with_the_flag_off_an_image_ingests_exactly_as_before(store, tmp_path):
    path = _png(tmp_path / "photo.png")
    pipe = IngestionPipeline(store)

    result = pipe.ingest(
        IngestionItem(source_type="file", path=str(path), title="photo.png"),
        user_email="me@local",
    )

    assert result.status == "ok"
    assert result.node_id.startswith("file:")  # the Document door, as before
    assert store.get_node(result.node_id)["type"] == "Document"
    assert pipe.multimodal_status()["enabled"] is False


def test_with_the_flag_off_a_folder_scan_skips_pictures_and_recordings(store, tmp_path):
    folder = tmp_path / "mixed"
    folder.mkdir()
    (folder / "notes.md").write_text("# Roadmap\nShip the thing.", encoding="utf-8")
    _png(folder / "photo.png")
    (folder / "memo.m4a").write_bytes(b"not really audio")

    summary = IngestionPipeline(store).ingest_folder(folder, owner="me@local")

    assert summary["matched"] == 1  # only notes.md
    assert summary["skipped"]["extension"] == 2


def test_with_the_flag_off_a_video_is_just_another_file(store, tmp_path):
    path = tmp_path / "clip.mov"
    path.write_bytes(b"fake movie bytes")

    result = IngestionPipeline(store).ingest(
        IngestionItem(source_type="file", path=str(path), title="clip.mov"),
        user_email="me@local",
    )

    assert result.status == "ok"


# ── on means pictures are memories ───────────────────────────────────────────
def test_an_ingested_image_becomes_a_findable_image_node(monkeypatch, store, tmp_path):
    _fake_ocr(monkeypatch, "Q3 roadmap\n제주 워크숍 회의록")
    path = _png(tmp_path / "whiteboard.png")
    ports = MultimodalPorts(
        captioner=lambda _p: "A whiteboard photographed in a meeting room",
        vision_embedder=lambda _p: [0.6, 0.8],
        vision_model_id="clip:2",
    )
    pipe = IngestionPipeline(store, allow_multimodal=True, multimodal=ports)

    result = pipe.ingest(
        IngestionItem(source_type="file", path=str(path), title="whiteboard.png"),
        user_email="me@local",
    )

    assert result.status == "ok"
    assert result.node_id.startswith("image:")
    node = store.get_node(result.node_id)
    assert node["type"] == "Image"
    assert node["metadata"]["caption"].startswith("A whiteboard")
    assert "제주 워크숍" in node["metadata"]["ocr_text"]
    assert node["metadata"]["thumbnail"].startswith("data:image/png;base64,")
    assert result.indexing_status == "indexed"
    assert result.embedded is True

    # The picture is reachable by a text query about what is written on it.
    hits = store.hybrid_search("제주 워크숍 회의록", top_k=5)
    assert result.node_id in {match["node_id"] for match in hits["matches"]}
    assert hits["multimodal"]["images"] >= 1


def test_image_quality_reflects_what_was_actually_extracted(monkeypatch, store, tmp_path):
    _no_ocr(monkeypatch)
    path = _png(tmp_path / "photo.png")
    pipe = IngestionPipeline(store, allow_multimodal=True)

    result = pipe.ingest(
        IngestionItem(source_type="file", path=str(path)), user_email="me@local"
    )

    assert result.extraction_quality["reasons"] == [
        "ocr_unavailable",
        "no_vision_caption",
    ]
    assert result.extraction_quality["level"] == "low"
    assert result.warnings  # the honest "this capture is thin" warning


def test_an_explicit_image_source_type_routes_without_an_extension(
    monkeypatch, store, tmp_path
):
    _no_ocr(monkeypatch)
    # A clipboard grab saved without a suffix: nothing about the name says
    # "picture", so the caller's source_type has to be what routes it.
    path = tmp_path / "clipboard-grab"
    _png(tmp_path / "tmp.png").replace(path)
    pipe = IngestionPipeline(store, allow_multimodal=True)

    result = pipe.ingest(
        IngestionItem(source_type="screenshot", path=str(path)), user_email="me@local"
    )

    assert result.node_id.startswith("image:")
    assert result.source_type == "screenshot"


def test_a_folder_of_photos_is_ingested_and_searchable(monkeypatch, store, tmp_path):
    _fake_ocr(monkeypatch, "receipt total 42000")
    folder = tmp_path / "album"
    folder.mkdir()
    (folder / "notes.md").write_text("# Trip", encoding="utf-8")
    _png(folder / "receipt.png")
    _png(folder / "sunset.jpg", colour="orange")

    pipe = IngestionPipeline(store, allow_multimodal=True)
    summary = pipe.ingest_folder(folder, owner="me@local")

    assert summary["matched"] == 3
    assert summary["ingested"] == 3
    assert summary["failed"] == 0
    images = [
        node for node in store.graph(limit=100)["nodes"] if node["type"] == "Image"
    ]
    assert len(images) == 2
    assert any("receipt total 42000" in (node["summary"] or "") for node in images)


def test_the_folder_allow_list_grows_only_while_multimodal_is_on(store):
    off = IngestionPipeline(store)
    on = IngestionPipeline(store, allow_multimodal=True)

    assert off._folder_extensions() == DEFAULT_FOLDER_EXTENSIONS
    assert ".png" not in off._folder_extensions()
    assert ".png" in on._folder_extensions() and ".m4a" in on._folder_extensions()
    assert DEFAULT_FOLDER_EXTENSIONS < on._folder_extensions()


def test_the_env_flag_can_turn_multimodal_on_without_a_constructor(
    monkeypatch, store, tmp_path
):
    monkeypatch.setenv(ALLOW_MULTIMODAL_ENV, "true")
    _no_ocr(monkeypatch)
    path = _png(tmp_path / "photo.png")

    result = IngestionPipeline(store).ingest(
        IngestionItem(source_type="file", path=str(path)), user_email="me@local"
    )

    assert result.node_id.startswith("image:")


# ── video is refused, out loud, when this machine cannot decode one ──────────
def test_video_is_recognized_and_refused_with_a_reason(store, tmp_path, monkeypatch):
    # 11.2.0 implements video; the refusal below is now a *runtime* answer, so
    # the decoder probe is pinned absent instead of trusting the test host.
    monkeypatch.setattr("lattice_brain.multimodal.ports._which_ffmpeg", lambda: None)
    path = tmp_path / "standup.mov"
    path.write_bytes(b"fake movie bytes")

    result = IngestionPipeline(store, allow_multimodal=True).ingest(
        IngestionItem(source_type="file", path=str(path)), user_email="me@local"
    )

    assert result.status == "unavailable"
    assert result.indexing_status == "skipped"
    assert result.detail == VIDEO_OUT_OF_SCOPE
    assert result.node_id is None
    status = IngestionPipeline(store, allow_multimodal=True).multimodal_status()
    assert status["video"] is False
    assert status["video_detail"] == VIDEO_OUT_OF_SCOPE


# ── audio ────────────────────────────────────────────────────────────────────
def test_a_recording_without_a_transcriber_is_kept_and_says_it_is_not_searchable(
    store, tmp_path
):
    path = tmp_path / "memo.m4a"
    path.write_bytes(b"fake audio bytes")

    result = IngestionPipeline(store, allow_multimodal=True).ingest(
        IngestionItem(source_type="file", path=str(path), title="Friday decision"),
        user_email="me@local",
    )

    assert result.status == "ok"
    node = store.get_node(result.node_id)
    # A recording is a recording whether or not anyone could hear it: the node
    # is `Audio` even when the transcript is the honest "nobody heard this".
    assert node["type"] == "Audio"
    assert node["metadata"]["modality"] == "audio"
    assert node["metadata"]["transcription"] == "unavailable"
    assert node["metadata"]["searchable"] is False
    assert "음성 인식기가 없어" in node["summary"]
    assert result.extraction_quality["reasons"] == ["no_transcript"]


def test_a_recording_with_a_transcriber_becomes_ordinary_searchable_text(
    store, tmp_path
):
    path = tmp_path / "memo.m4a"
    path.write_bytes(b"fake audio bytes")
    ports = MultimodalPorts(
        transcriber=lambda _p: "금요일에 배포하기로 결정했다. Ship on Friday."
    )

    result = IngestionPipeline(store, allow_multimodal=True, multimodal=ports).ingest(
        IngestionItem(source_type="file", path=str(path), title="memo"),
        user_email="me@local",
    )

    node = store.get_node(result.node_id)
    # The transcript rides the ordinary text index — chunks, concepts, search —
    # while the node itself stays a recording rather than becoming a Document.
    assert node["type"] == "Audio"
    assert node["metadata"]["transcription"] == "ok"
    assert node["metadata"]["searchable"] is True
    assert result.extraction_quality["level"] in {"medium", "high"}
    hits = store.search("금요일에 배포")
    assert result.node_id in {match["id"] for match in hits["matches"]}


def test_an_explicit_audio_source_type_routes_by_name(store, tmp_path):
    path = tmp_path / "recording"
    path.write_bytes(b"fake audio bytes")

    result = IngestionPipeline(store, allow_multimodal=True).ingest(
        IngestionItem(source_type="voice_memo", path=str(path), title="memo"),
        user_email="me@local",
    )

    node = store.get_node(result.node_id)
    assert node["type"] == "Audio"
    assert node["metadata"]["modality"] == "audio"


def test_a_recording_is_a_first_class_node_type_not_a_document(store, tmp_path):
    """The workaround this release started with was the text door.

    `AUDIO` is now a schema member, so the three places that decide what a node
    *is* must agree: the legacy table's label, the normalized `nodes_v2`
    projection, and the graph view a person actually looks at.
    """
    from lattice_brain.graph.schema import NodeType

    path = tmp_path / "standup.m4a"
    path.write_bytes(b"fake audio bytes")

    result = IngestionPipeline(store, allow_multimodal=True).ingest(
        IngestionItem(source_type="file", path=str(path), title="standup"),
        user_email="me@local",
    )

    assert store.get_node(result.node_id)["type"] == "Audio"
    assert NodeType.from_legacy("Audio") is NodeType.AUDIO
    assert NodeType.from_legacy("오디오") is NodeType.AUDIO
    with store._connect() as conn:
        row = conn.execute(
            "SELECT type, legacy_type FROM nodes_v2 WHERE id=?", (result.node_id,)
        ).fetchone()
    assert (row["type"], row["legacy_type"]) == ("AUDIO", "Audio")
    # Still visible: a first-class type that the graph view filters out would
    # be a memory the user can no longer find.
    visible = store.graph(limit=50)["nodes"]
    assert result.node_id in {node["id"] for node in visible}


def test_a_note_still_ingests_as_a_document(store):
    """`ingest_source`'s new `node_type` is additive: default behaviour holds."""
    result = IngestionPipeline(store).ingest(
        IngestionItem(source_type="note", text="a plain note about the roadmap"),
        user_email="me@local",
    )

    assert store.get_node(result.node_id)["type"] == "Document"


# ── the file-path guards the modality doors share ────────────────────────────
def test_the_image_door_refuses_a_missing_file(store, tmp_path):
    result = IngestionPipeline(store, allow_multimodal=True).ingest(
        IngestionItem(source_type="image", path=str(tmp_path / "gone.png")),
        user_email="me@local",
    )

    assert result.status == "failed"
    assert "File not found" in result.detail


def test_the_image_door_refuses_a_directory(store, tmp_path):
    folder = tmp_path / "album"
    folder.mkdir()

    result = IngestionPipeline(store, allow_multimodal=True).ingest(
        IngestionItem(source_type="image", path=str(folder)), user_email="me@local"
    )

    assert result.status == "failed"
    assert "got a directory" in result.detail


def test_the_image_door_refuses_an_item_with_no_path(store):
    result = IngestionPipeline(store, allow_multimodal=True).ingest(
        IngestionItem(source_type="image"), user_email="me@local"
    )

    assert result.status == "failed"
    assert "requires a path" in result.detail


def test_a_pathless_item_stays_on_the_text_door_when_multimodal_is_on(store):
    result = IngestionPipeline(store, allow_multimodal=True).ingest(
        IngestionItem(source_type="note", text="a plain note"), user_email="me@local"
    )

    assert result.status == "ok"
    assert store.get_node(result.node_id)["type"] == "Document"


# ── the image vector lands in its own index ──────────────────────────────────
def test_an_image_vector_is_filed_in_the_image_index_not_the_text_one(
    monkeypatch, store, tmp_path
):
    from lattice_brain.graph.image_vectors import image_index_status

    _no_ocr(monkeypatch)
    ports = MultimodalPorts(
        vision_embedder=lambda _p: [0.6, 0.8], vision_model_id="clip:2"
    )
    pipe = IngestionPipeline(store, allow_multimodal=True, multimodal=ports)

    pipe.ingest(
        IngestionItem(source_type="file", path=str(_png(tmp_path / "a.png"))),
        user_email="me@local",
    )

    assert image_index_status(store) == {
        "vectors": 1,
        "models": {"clip:2": 1},
        "detail": None,
    }


def test_without_a_vision_model_the_image_index_stays_empty(monkeypatch, store, tmp_path):
    from lattice_brain.graph.image_vectors import image_index_status

    _no_ocr(monkeypatch)
    pipe = IngestionPipeline(store, allow_multimodal=True)

    pipe.ingest(
        IngestionItem(source_type="file", path=str(_png(tmp_path / "a.png"))),
        user_email="me@local",
    )

    assert image_index_status(store)["vectors"] == 0


def test_an_unnamed_vision_model_still_records_a_traceable_identity(
    monkeypatch, store, tmp_path
):
    from lattice_brain.graph.image_vectors import image_index_status

    _no_ocr(monkeypatch)
    pipe = IngestionPipeline(
        store,
        allow_multimodal=True,
        multimodal=MultimodalPorts(vision_embedder=lambda _p: [1.0, 0.0]),
    )

    pipe.ingest(
        IngestionItem(source_type="file", path=str(_png(tmp_path / "a.png"))),
        user_email="me@local",
    )

    assert image_index_status(store)["models"] == {"vision:unnamed": 1}
