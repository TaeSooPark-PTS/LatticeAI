"""The image vector space stays separate, and joins by late fusion.

Two claims are under test. First, that image vectors never mix with text
vectors: a different model or a different width is simply not compared.
Second, that a caller *can* still combine them — by ranking each side on its
own and blending at the end — and that a failure on the image side never costs
the text answer.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph.image_vectors import (  # noqa: E402
    DEFAULT_IMAGE_FUSION_WEIGHT,
    IMAGE_VECTOR_TABLE,
    decode_vector,
    encode_vector,
    fuse_image_scores,
    image_index_status,
    image_similarity_search,
    record_image_vector,
)
from lattice_brain.graph.retrieval import (  # noqa: E402
    context_quality_signal,
    multimodal_signal,
)
from lattice_brain.graph.store import KnowledgeGraphStore  # noqa: E402
from lattice_brain.ingestion import IngestionItem, IngestionPipeline  # noqa: E402
from lattice_brain.multimodal import MultimodalPorts  # noqa: E402


@pytest.fixture()
def store(tmp_path: Path) -> KnowledgeGraphStore:
    return KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")


class _BrokenStore:
    def _connect(self):
        raise RuntimeError("database is locked")


def _png(path: Path, colour="blue") -> Path:
    from PIL import Image

    Image.new("RGB", (48, 32), colour).save(path)
    return path


# ── the codec and the table ──────────────────────────────────────────────────
def test_vectors_round_trip_through_the_float32_codec():
    assert decode_vector(encode_vector([0.5, -0.25, 0.0])) == [0.5, -0.25, 0.0]


def test_recording_needs_a_node_a_vector_and_a_model(store):
    assert record_image_vector(store, node_id="", vector=[1.0], model_id="clip") is False
    assert record_image_vector(store, node_id="n", vector=[], model_id="clip") is False
    assert record_image_vector(store, node_id="n", vector=[1.0], model_id="") is False
    assert image_index_status(store)["vectors"] == 0


def test_recording_the_same_node_twice_replaces_the_vector(store):
    assert record_image_vector(store, node_id="image:a", vector=[1.0, 0.0], model_id="clip:2")
    assert record_image_vector(store, node_id="image:a", vector=[0.0, 1.0], model_id="clip:2")

    found = image_similarity_search(store, [0.0, 1.0])
    assert found["matches"] == [{"node_id": "image:a", "score": 1.0}]
    assert image_index_status(store)["vectors"] == 1


def test_a_store_that_cannot_be_written_never_fails_the_ingest():
    assert record_image_vector(
        _BrokenStore(), node_id="image:a", vector=[1.0], model_id="clip"
    ) is False


def test_index_status_reports_a_broken_store_instead_of_raising():
    status = image_index_status(_BrokenStore())

    assert status["vectors"] == 0
    assert "database is locked" in status["detail"]


def test_the_table_name_is_stable(store):
    record_image_vector(store, node_id="image:a", vector=[1.0], model_id="clip:1")
    with store._connect() as conn:
        rows = conn.execute(f"SELECT node_id FROM {IMAGE_VECTOR_TABLE}").fetchall()
    assert [row["node_id"] for row in rows] == ["image:a"]


# ── a different model is never compared ──────────────────────────────────────
def test_vectors_of_a_different_width_are_skipped_not_truncated(store):
    record_image_vector(store, node_id="image:wide", vector=[1.0, 0.0, 0.0], model_id="a:3")
    record_image_vector(store, node_id="image:narrow", vector=[1.0, 0.0], model_id="b:2")

    found = image_similarity_search(store, [1.0, 0.0])

    assert [m["node_id"] for m in found["matches"]] == ["image:narrow"]
    assert found["candidates"] == 1


def test_a_search_can_be_pinned_to_one_model(store):
    record_image_vector(store, node_id="image:a", vector=[1.0, 0.0], model_id="clip:2")
    record_image_vector(store, node_id="image:b", vector=[1.0, 0.0], model_id="siglip:2")

    assert image_similarity_search(store, [1.0, 0.0])["candidates"] == 2
    pinned = image_similarity_search(store, [1.0, 0.0], model_id="siglip:2")
    assert [m["node_id"] for m in pinned["matches"]] == ["image:b"]


def test_a_score_floor_applies_to_the_image_index(store):
    record_image_vector(store, node_id="image:near", vector=[1.0, 0.0], model_id="c:2")
    record_image_vector(store, node_id="image:far", vector=[0.0, 1.0], model_id="c:2")

    found = image_similarity_search(store, [1.0, 0.0], min_score=0.5)

    assert [m["node_id"] for m in found["matches"]] == ["image:near"]


def test_an_image_query_without_a_vector_says_so(store):
    found = image_similarity_search(store, [])

    assert found["matches"] == []
    assert found["detail"] == "an image query needs an image vector"


def test_an_unreadable_image_index_degrades_with_a_reason():
    found = image_similarity_search(_BrokenStore(), [1.0, 0.0])

    assert found["matches"] == []
    assert "image vector index unavailable" in found["detail"]


def test_the_image_index_reports_which_backend_scored(store):
    record_image_vector(store, node_id="image:a", vector=[1.0, 0.0], model_id="c:2")

    found = image_similarity_search(store, [1.0, 0.0])

    assert found["index"]["approx"] is False
    assert found["index"]["exhaustive"] is True


# ── late fusion ──────────────────────────────────────────────────────────────
def test_late_fusion_blends_two_rankings_without_mixing_their_spaces():
    matches = [
        {"node_id": "image:a", "score": 0.2, "scores": {"lexical": 0.2, "vector": 0.0}},
        {"node_id": "doc:b", "score": 0.9, "scores": {"lexical": 0.9, "vector": 0.0}},
    ]

    touched = fuse_image_scores(matches, {"image:a": 1.0}, weight=0.5)

    assert touched == 1
    assert matches[0]["scores"]["image"] == 1.0
    assert matches[0]["score"] == pytest.approx(0.6)
    assert matches[1]["score"] == 0.9  # untouched: the image channel never saw it
    assert "image" not in matches[1]["scores"]


def test_the_fusion_weight_is_clamped_and_defaults_to_a_half():
    matches = [{"node_id": "image:a", "score": 0.0}]
    fuse_image_scores(matches, {"image:a": 1.0}, weight=7.0)
    assert matches[0]["score"] == 1.0

    other = [{"node_id": "image:a", "score": 1.0}]
    fuse_image_scores(other, {"image:a": 0.0}, weight=-3.0)
    assert other[0]["score"] == 1.0
    assert DEFAULT_IMAGE_FUSION_WEIGHT == 0.5


def test_hybrid_search_late_fuses_a_supplied_image_vector(monkeypatch, store, tmp_path):
    monkeypatch.setitem(sys.modules, "pytesseract", None)
    ports = MultimodalPorts(
        captioner=lambda _p: "A blue rectangle", vision_embedder=lambda _p: [1.0, 0.0],
        vision_model_id="clip:2",
    )
    pipe = IngestionPipeline(store, allow_multimodal=True, multimodal=ports)
    result = pipe.ingest(
        IngestionItem(source_type="file", path=str(_png(tmp_path / "a.png"))),
        user_email="me@local",
    )

    fused = store.hybrid_search("rectangle", top_k=5, image_vector=[1.0, 0.0])

    report = fused["multimodal"]["image_fusion"]
    assert report["candidates"] == 1
    assert report["fused"] == 1
    assert report["weight"] == 0.5
    match = next(m for m in fused["matches"] if m["node_id"] == result.node_id)
    assert match["scores"]["image"] == 1.0


def test_a_pinned_fusion_weight_is_honored(monkeypatch, store, tmp_path):
    monkeypatch.setitem(sys.modules, "pytesseract", None)
    ports = MultimodalPorts(vision_embedder=lambda _p: [1.0, 0.0], vision_model_id="clip:2")
    IngestionPipeline(store, allow_multimodal=True, multimodal=ports).ingest(
        IngestionItem(source_type="file", path=str(_png(tmp_path / "a.png"))),
        user_email="me@local",
    )

    fused = store.hybrid_search(
        "a.png", top_k=5, image_vector=[1.0, 0.0], image_fusion_weight=0.25
    )

    assert fused["multimodal"]["image_fusion"]["weight"] == 0.25


def test_a_broken_image_index_never_costs_the_text_answer(monkeypatch, store):
    store.ingest_source(source_type="note", title="Roadmap", text="Ship on Friday.")

    def _explode(*args, **kwargs):
        raise RuntimeError("image index on fire")

    monkeypatch.setattr(
        "lattice_brain.graph.image_vectors.image_similarity_search", _explode
    )
    fused = store.hybrid_search("roadmap", top_k=5, image_vector=[1.0, 0.0])

    assert fused["matches"]  # the text answer survived
    assert "image index on fire" in fused["multimodal"]["image_fusion"]["detail"]
    assert fused["multimodal"]["image_fusion"]["fused"] == 0


# ── honesty in the context signal ────────────────────────────────────────────
def test_an_all_text_result_carries_no_multimodal_key():
    assert multimodal_signal([{"type": "Document"}, {"type": "Chunk"}]) is None
    signal = context_quality_signal("hybrid", 4)
    assert set(signal) == {"mode", "nodes", "limited", "reason"}


def test_a_result_with_pictures_says_so():
    assert multimodal_signal(
        [{"type": "Image"}, {"type": "ImageText"}, {"type": "Image"}, {"type": None}]
    ) == {"images": 3, "types": ["Image", "ImageText"]}

    signal = context_quality_signal("hybrid", 4, multimodal={"images": 3, "types": ["Image"]})
    assert signal["multimodal"] == {"images": 3, "types": ["Image"]}


def test_context_for_query_reports_multimodal_inclusion(monkeypatch, store, tmp_path):
    monkeypatch.setitem(sys.modules, "pytesseract", None)
    ports = MultimodalPorts(captioner=lambda _p: "A whiteboard in a meeting room")
    IngestionPipeline(store, allow_multimodal=True, multimodal=ports).ingest(
        IngestionItem(source_type="file", path=str(_png(tmp_path / "board.png"))),
        user_email="me@local",
    )

    assembled = store.context_for_query(
        "whiteboard meeting", 4, use_hybrid=True, with_meta=True
    )

    assert assembled["quality"]["multimodal"]["images"] >= 1
    assert "[Image]" in assembled["context"]


def test_a_text_only_context_keeps_the_historical_quality_shape(store):
    store.ingest_source(source_type="note", title="Roadmap", text="Ship on Friday soon.")

    assembled = store.context_for_query("roadmap", 4, use_hybrid=True, with_meta=True)

    assert "multimodal" not in assembled["quality"]
