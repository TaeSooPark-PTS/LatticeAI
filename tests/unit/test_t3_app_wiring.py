"""Turning configuration into capability — and, mostly, into honest absence.

The default install has no CLIP, no VLM and no transcriber, so the interesting
assertion is that building the ports in that state costs nothing and claims
nothing. The configured state is driven with fake modules and dotted callables,
never a real model.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.ingestion import ALLOW_MULTIMODAL_ENV  # noqa: E402
from latticeai.services.memory_service import (  # noqa: E402
    MAX_RECALL_THUMBNAIL_CHARS,
    _visual_fields,
)
from latticeai.services.multimodal_ports import (  # noqa: E402
    VISION_CAPTION_PROVIDER_ENV,
    VISION_MODEL_ENV,
    VISION_PROVIDER_ENV,
    VISION_SPACE_ENV,
    build_multimodal_ports,
    describe_multimodal,
    multimodal_enabled,
)

PIXEL = "data:image/png;base64,iVBORw0KGgo="


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        ALLOW_MULTIMODAL_ENV,
        VISION_PROVIDER_ENV,
        VISION_MODEL_ENV,
        VISION_SPACE_ENV,
        VISION_CAPTION_PROVIDER_ENV,
        "LATTICEAI_VISION_CAPTION_MODEL",
        "LATTICEAI_VISION_CAPTION_TARGET",
        "LATTICEAI_VISION_EMBEDDING_TARGET",
    ):
        monkeypatch.delenv(name, raising=False)


# ── the default install ──────────────────────────────────────────────────────
def test_by_default_nothing_is_enabled_and_nothing_is_claimed(monkeypatch):
    # ffmpeg belongs to the machine, not the config, so it is pinned absent —
    # otherwise this assertion would read differently on a host that has it.
    monkeypatch.setattr("lattice_brain.multimodal._which_ffmpeg", lambda: None)
    ports = build_multimodal_ports()

    assert multimodal_enabled() is False
    assert ports.captioner is None
    assert ports.vision_embedder is None
    assert ports.transcriber is None
    assert ports.text_to_image_embedder is None
    assert ports.vision_model_id == ""
    assert describe_multimodal(ports) == {
        "enabled": False,
        "caption": False,
        "vision_embedding": False,
        "transcription": False,
        "keyframes": False,
        "text_to_image_query": False,
        "vision_model_id": "",
        "vision_space": "image",
    }


def test_the_flag_reads_the_same_env_the_pipeline_reads(monkeypatch):
    monkeypatch.setenv(ALLOW_MULTIMODAL_ENV, "on")
    assert multimodal_enabled() is True
    monkeypatch.setenv(ALLOW_MULTIMODAL_ENV, "nope")
    assert multimodal_enabled() is False


def test_the_transcriber_is_passed_through_from_voice_capture():
    ports = build_multimodal_ports(transcriber=lambda _p: "transcribed")

    assert ports.transcriber is not None
    assert ports.describe()["transcription"] is True


# ── a configured install ─────────────────────────────────────────────────────
def _install_mlx_clip(monkeypatch):
    module = types.ModuleType("mlx_clip")

    class _Encoder:
        def encode_image(self, paths):
            return [[1.0, 0.0] for _ in paths]

    module.load = lambda model: _Encoder()
    monkeypatch.setitem(sys.modules, "mlx_clip", module)


def test_a_configured_vision_model_becomes_a_working_port(monkeypatch):
    _install_mlx_clip(monkeypatch)
    monkeypatch.setenv(VISION_PROVIDER_ENV, "mlx")
    monkeypatch.setenv(VISION_MODEL_ENV, "clip-vit-base-patch32")
    monkeypatch.setenv(VISION_SPACE_ENV, "shared")

    ports = build_multimodal_ports()

    assert ports.vision_embedder("/photos/a.png") == [1.0, 0.0]
    assert ports.vision_model_id == "mlx-vision:clip-vit-base-patch32:512"
    assert ports.vision_space == "shared"


def test_a_vision_model_that_will_not_load_leaves_the_port_empty(monkeypatch):
    monkeypatch.setitem(sys.modules, "mlx_clip", None)
    monkeypatch.setenv(VISION_PROVIDER_ENV, "mlx")
    monkeypatch.setenv(VISION_MODEL_ENV, "clip-vit-base-patch32")

    ports = build_multimodal_ports()

    assert ports.vision_embedder is None
    assert ports.vision_model_id == ""


def _caption(path, prompt):
    return f"a picture at {path}"


def test_a_configured_captioner_becomes_a_working_port(monkeypatch):
    monkeypatch.setenv(VISION_CAPTION_PROVIDER_ENV, "custom")
    monkeypatch.setenv("LATTICEAI_VISION_CAPTION_TARGET", f"{__name__}:_caption")

    ports = build_multimodal_ports()

    assert ports.captioner("/photos/a.png") == "a picture at /photos/a.png"
    assert describe_multimodal(ports)["caption"] is True


# ── one transcription seam, shared ───────────────────────────────────────────
def test_voice_capture_hands_its_transcriber_to_the_ingestion_pipeline():
    from latticeai.services.voice_capture import VoiceCaptureService

    def _transcribe(path):
        return "ship on Friday"

    with_model = VoiceCaptureService(pipeline=None, transcriber=_transcribe)
    without = VoiceCaptureService(pipeline=None)

    assert with_model.multimodal_ports().transcriber is _transcribe
    # No transcriber means the pipeline degrades exactly as `capture` does.
    assert without.multimodal_ports().transcriber is None


# ── the app wiring survives a graph-less install ─────────────────────────────
def test_a_store_that_refuses_new_attributes_never_fails_startup(tmp_path):
    from latticeai.runtime.persistence_runtime import build_persistence_runtime

    class _Frozen:
        """A graph object with no ``__dict__`` — attaching ports must not crash."""

        __slots__ = ()

    runtime = build_persistence_runtime(
        data_dir=tmp_path,
        base_dir=tmp_path,
        enable_graph=False,
        knowledge_graph=_Frozen(),
        hooks_registry=None,
        history_file=tmp_path / "history.json",
        conversations=None,
        user_id_for_email=lambda email: email,
        audit=lambda action, detail, user: None,
    )

    assert runtime["MULTIMODAL_PORTS"].vision_embedder is None
    assert runtime["INGESTION_PIPELINE"].multimodal_status()["enabled"] is False


# ── what a recall row is allowed to carry ────────────────────────────────────
def test_a_row_without_metadata_carries_nothing_extra():
    assert _visual_fields({"id": "a"}) == {}
    assert _visual_fields({"id": "a", "metadata": "not a dict"}) == {}


def test_an_image_row_carries_its_caption_and_thumbnail():
    fields = _visual_fields(
        {"metadata": {"caption": "  A whiteboard  ", "thumbnail": PIXEL}}
    )

    assert fields == {"caption": "A whiteboard", "thumbnail": PIXEL}


def test_a_picture_with_no_caption_carries_only_the_thumbnail():
    assert _visual_fields({"metadata": {"thumbnail": PIXEL, "caption": "  "}}) == {
        "thumbnail": PIXEL
    }


def test_a_remote_thumbnail_is_refused():
    # An evidence card must never become an outbound request.
    assert _visual_fields({"metadata": {"thumbnail": "https://x.example/p.gif"}}) == {}


def test_an_oversized_thumbnail_is_dropped_rather_than_shipped():
    huge = "data:image/png;base64," + "A" * MAX_RECALL_THUMBNAIL_CHARS

    assert _visual_fields({"metadata": {"thumbnail": huge}}) == {}


def test_a_long_caption_is_trimmed_not_dropped():
    fields = _visual_fields({"metadata": {"caption": "긴" * 900}})

    assert len(fields["caption"]) == 400
