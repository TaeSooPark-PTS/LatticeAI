"""The vision seam: a real model, an honest absence, and nothing in between.

CI has no CLIP and no VLM, which is exactly the state most installs are in.
Both branches are driven here — the loaded model through fake ``mlx_clip`` /
``mlx_vlm`` modules in ``sys.modules``, the unloaded one by leaving them out —
because the interesting failure is not "the model is missing" but "the product
quietly behaved as if it weren't".
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from latticeai.core.embedding_providers import (  # noqa: E402
    DEFAULT_VISION_DIM,
    VISION_SPACE_IMAGE,
    VISION_SPACE_SHARED,
    CustomVisionCaptioner,
    CustomVisionEmbeddingProvider,
    EmbeddingUnavailable,
    MLXVisionCaptioner,
    MLXVisionEmbeddingProvider,
    VisionCaptioner,
    build_vision_provider,
    resolve_vision_captioner,
    resolve_vision_embedder,
    vision_caption_port,
)


class _Encoder:
    """The contract a vision module must satisfy: encode_image (+ encode_text)."""

    def __init__(self, *, text: bool = True, boom: bool = False):
        self.text = text
        self.boom = boom

    def encode_image(self, paths):
        if self.boom:
            raise RuntimeError("GPU out of memory")
        return [[3.0, 4.0] for _ in paths]

    def encode_text(self, texts):
        if not self.text:
            raise AttributeError("no text tower")
        return [[0.0, 5.0] for _ in texts]


def _install_mlx_clip(monkeypatch, encoder=None, *, load_error: str = "") -> None:
    module = types.ModuleType("mlx_clip")

    def _load(model):
        if load_error:
            raise RuntimeError(load_error)
        return encoder if encoder is not None else _Encoder()

    module.load = _load
    monkeypatch.setitem(sys.modules, "mlx_clip", module)


# ── the model is there ───────────────────────────────────────────────────────
def test_a_loaded_vision_model_embeds_an_image_and_normalizes_it(monkeypatch):
    _install_mlx_clip(monkeypatch)
    provider = build_vision_provider("mlx", model="clip-vit-base-patch32")

    assert provider.dim == 512  # known width before the first call
    vector = provider.embed_image("/photos/whiteboard.png")

    # 3-4-5 triangle: normalization is real, not decorative.
    assert vector == pytest.approx([0.6, 0.8])
    # The index identity follows the width the model actually returned.
    assert provider.dim == 2
    assert provider.model_id == "mlx-vision:clip-vit-base-patch32:2"
    assert provider.metadata()["modality"] == "image"
    assert provider.health()["status"] == "ok"


def test_an_explicit_dim_pins_the_index_identity_before_any_call(monkeypatch):
    _install_mlx_clip(monkeypatch)
    provider = build_vision_provider("mlx", model="some-private-model", dim=768)

    # No guessing when the caller already knows the width.
    assert provider.dim == 768
    assert provider.model_id == "mlx-vision:some-private-model:768"


def test_an_image_only_space_refuses_to_embed_text(monkeypatch):
    _install_mlx_clip(monkeypatch)
    provider = build_vision_provider("mlx", model="clip-vit-base-patch32")

    assert provider.space == VISION_SPACE_IMAGE
    assert provider.shares_text_space is False
    with pytest.raises(EmbeddingUnavailable, match="late fusion"):
        provider.embed_batch(["a photo of a whiteboard"])


def test_a_shared_space_model_may_embed_the_query_too(monkeypatch):
    _install_mlx_clip(monkeypatch)
    provider = build_vision_provider(
        "mlx", model="clip-vit-base-patch32", space=VISION_SPACE_SHARED
    )

    assert provider.shares_text_space is True
    assert provider.embed_batch(["a whiteboard"]) == [pytest.approx([0.0, 1.0])]
    assert provider.metadata()["shares_text_space"] is True


def test_a_shared_space_without_a_text_tower_says_so(monkeypatch):
    _install_mlx_clip(monkeypatch, _Encoder(text=False))
    provider = build_vision_provider("mlx", model="mystery", space=VISION_SPACE_SHARED)
    provider._load().encode_text = None  # the module exposes no text encoder

    with pytest.raises(EmbeddingUnavailable, match="no text encoder"):
        provider.embed_batch(["query"])


def test_a_text_tower_that_raises_is_reported_not_swallowed(monkeypatch):
    _install_mlx_clip(monkeypatch, _Encoder(text=False))
    provider = build_vision_provider("mlx", model="mystery", space=VISION_SPACE_SHARED)

    with pytest.raises(EmbeddingUnavailable, match="text embedding failed"):
        provider.embed_batch(["query"])


# ── the model is not there ───────────────────────────────────────────────────
def test_a_missing_vision_module_is_unavailable_never_a_hash_fallback(monkeypatch):
    monkeypatch.setitem(sys.modules, "mlx_clip", None)
    provider = build_vision_provider("mlx", model="clip-vit-base-patch32")

    assert provider.health()["status"] == "unavailable"
    with pytest.raises(EmbeddingUnavailable, match="unavailable"):
        provider.embed_image("/photos/whiteboard.png")


def test_a_model_that_fails_to_load_reports_the_reason(monkeypatch):
    _install_mlx_clip(monkeypatch, load_error="weights not downloaded")
    resolved = resolve_vision_embedder("mlx", model="clip-vit-base-patch32")

    assert resolved.available is False
    assert resolved.as_port() is None
    assert "weights not downloaded" in resolved.health["detail"]
    assert "unavailable" in resolved.detail
    assert resolved.space == VISION_SPACE_IMAGE
    assert resolved.as_dict()["available"] is False


def test_an_embedding_call_that_explodes_becomes_embedding_unavailable(monkeypatch):
    _install_mlx_clip(monkeypatch, _Encoder(boom=True))
    provider = build_vision_provider("mlx", model="clip-vit-base-patch32")

    with pytest.raises(EmbeddingUnavailable, match="GPU out of memory"):
        provider.embed_images(["/photos/a.png"])


def test_an_empty_vector_is_a_failure_not_a_zero_vector(monkeypatch):
    _install_mlx_clip(monkeypatch)
    provider = build_vision_provider("mlx", model="clip-vit-base-patch32")
    provider._load().encode_image = lambda paths: [[]]

    with pytest.raises(EmbeddingUnavailable, match="empty vector"):
        provider.embed_image("/photos/a.png")


def test_a_provider_returning_no_rows_is_a_failure(monkeypatch):
    _install_mlx_clip(monkeypatch)
    provider = build_vision_provider("mlx", model="clip-vit-base-patch32")
    provider._load().encode_image = lambda paths: []

    with pytest.raises(EmbeddingUnavailable, match="no vector"):
        provider.embed_image("/photos/a.png")


# ── resolution ───────────────────────────────────────────────────────────────
def test_no_configured_vision_provider_is_a_state_not_a_failure():
    resolved = resolve_vision_embedder("")

    assert resolved.available is False
    assert resolved.requested == ""
    assert "off" in resolved.detail
    assert resolved.as_dict()["requested_provider"] == ""


def test_an_unknown_vision_provider_name_is_refused():
    with pytest.raises(ValueError, match="unknown vision embedding provider"):
        build_vision_provider("stable-diffusion")

    resolved = resolve_vision_embedder("stable-diffusion")
    assert resolved.available is False
    assert "could not construct" in resolved.detail


def test_resolution_can_skip_the_probe(monkeypatch):
    _install_mlx_clip(monkeypatch, load_error="not downloaded")
    resolved = resolve_vision_embedder("mlx", model="clip", probe=False)

    # Not probed means not claimed: available, but health says "unknown".
    assert resolved.available is True
    assert resolved.health["status"] == "unknown"


def test_a_provider_whose_health_check_raises_never_crashes_startup(monkeypatch):
    _install_mlx_clip(monkeypatch)

    def _boom(self):
        raise RuntimeError("probe blew up")

    monkeypatch.setattr(MLXVisionEmbeddingProvider, "health", _boom)
    resolved = resolve_vision_embedder("mlx", model="clip")

    assert resolved.available is False
    assert "probe blew up" in resolved.health["detail"]


def test_a_healthy_provider_hands_over_a_one_argument_port(monkeypatch):
    _install_mlx_clip(monkeypatch)
    resolved = resolve_vision_embedder("mlx", model="clip-vit-base-patch32")
    port = resolved.as_port()

    assert port is not None
    assert port("/photos/a.png") == pytest.approx([0.6, 0.8])
    assert resolved.as_dict()["modality"] == "image"


# ── the dotted-callable provider ─────────────────────────────────────────────
def _embed_two(paths):
    return [[0.0, 1.0] for _ in paths]


def test_a_custom_dotted_vision_target_is_loaded_and_used(monkeypatch):
    monkeypatch.setenv(
        "LATTICEAI_VISION_EMBEDDING_TARGET",
        f"{__name__}:_embed_two",
    )
    provider = build_vision_provider("custom", dim=2)

    assert provider.embed_image("/photos/a.png") == pytest.approx([0.0, 1.0])
    assert provider.health()["status"] == "ok"
    assert DEFAULT_VISION_DIM == 512  # the configured default
    assert provider.dim == 2  # locked to the width the callable really returned


def test_a_custom_vision_target_is_image_only(monkeypatch):
    monkeypatch.setenv("LATTICEAI_VISION_EMBEDDING_TARGET", f"{__name__}:_embed_two")
    provider = build_vision_provider("custom", space=VISION_SPACE_SHARED)

    with pytest.raises(EmbeddingUnavailable, match="image-only callable"):
        provider.embed_batch(["query"])


def test_an_unconfigured_custom_vision_target_says_which_env_var(monkeypatch):
    monkeypatch.delenv("LATTICEAI_VISION_EMBEDDING_TARGET", raising=False)
    provider = CustomVisionEmbeddingProvider.__new__(CustomVisionEmbeddingProvider)
    provider.__init__(_cfg())

    assert provider.health()["status"] == "unavailable"
    assert "LATTICEAI_VISION_EMBEDDING_TARGET" in provider.health()["detail"]


def test_a_custom_vision_target_without_a_module_path_is_refused(monkeypatch):
    monkeypatch.setenv("LATTICEAI_VISION_EMBEDDING_TARGET", "notdotted")
    provider = build_vision_provider("custom")

    assert "invalid vision embedding target" in provider.health()["detail"]


def test_a_custom_vision_target_that_does_not_import_is_reported(monkeypatch):
    monkeypatch.setenv("LATTICEAI_VISION_EMBEDDING_TARGET", "no.such.module:fn")
    provider = build_vision_provider("custom")

    assert provider.health()["status"] == "unavailable"
    with pytest.raises(EmbeddingUnavailable, match="target unavailable"):
        provider.embed_image("/photos/a.png")


def _explode(paths):
    raise RuntimeError("the callable failed")


def test_a_custom_vision_callable_that_raises_becomes_unavailable(monkeypatch):
    monkeypatch.setenv("LATTICEAI_VISION_EMBEDDING_TARGET", f"{__name__}:_explode")
    provider = build_vision_provider("custom")

    with pytest.raises(EmbeddingUnavailable, match="custom vision embedding failed"):
        provider.embed_image("/photos/a.png")


def _cfg():
    from latticeai.core.embedding_providers import _RemoteConfig

    return _RemoteConfig(model="", extra={"space": "image"})


# ── captions: a model said it, or nobody did ─────────────────────────────────
def _install_mlx_vlm(monkeypatch, *, caption="A whiteboard covered in sticky notes",
                     load_error: str = "", generate_error: str = "") -> None:
    module = types.ModuleType("mlx_vlm")

    def _load(model):
        if load_error:
            raise RuntimeError(load_error)
        return ("model", "processor")

    def _generate(model, processor, path, prompt, max_tokens=64):
        if generate_error:
            raise RuntimeError(generate_error)
        return caption

    module.load = _load
    module.generate = _generate
    monkeypatch.setitem(sys.modules, "mlx_vlm", module)


def test_with_no_vlm_there_is_simply_no_caption():
    captioner = VisionCaptioner()

    assert captioner.available() is False
    assert captioner.caption("/photos/a.png") is None
    assert captioner.health()["status"] == "unavailable"
    assert captioner.metadata()["available"] is False
    assert vision_caption_port(captioner) is None


def test_a_loaded_vlm_captions_the_image(monkeypatch):
    _install_mlx_vlm(monkeypatch)
    captioner = resolve_vision_captioner("mlx", model="qwen2-vl-2b")

    assert isinstance(captioner, MLXVisionCaptioner)
    assert captioner.available() is True
    assert captioner.caption("/photos/a.png") == "A whiteboard covered in sticky notes"
    assert captioner.health()["status"] == "ok"
    port = vision_caption_port(captioner)
    assert port is not None and port("/photos/a.png")


def test_a_vlm_that_will_not_load_captions_nothing(monkeypatch):
    _install_mlx_vlm(monkeypatch, load_error="model not downloaded")
    captioner = MLXVisionCaptioner("qwen2-vl-2b")

    assert captioner.available() is False
    assert captioner.caption("/photos/a.png") is None
    assert "model not downloaded" in captioner.health()["detail"]


def test_a_vlm_that_returns_nothing_captions_nothing(monkeypatch):
    _install_mlx_vlm(monkeypatch, caption="   ")
    captioner = MLXVisionCaptioner("qwen2-vl-2b")

    assert captioner.caption("/photos/a.png") is None


def test_a_vlm_that_raises_mid_generation_captions_nothing(monkeypatch):
    _install_mlx_vlm(monkeypatch, generate_error="context overflow")
    captioner = MLXVisionCaptioner("qwen2-vl-2b")

    assert captioner.caption("/photos/a.png") is None


def _caption_target(path, prompt):
    return f"caption of {Path(path).name} for {prompt[:8]}"


def test_a_custom_dotted_captioner_is_used_when_configured(monkeypatch):
    monkeypatch.setenv("LATTICEAI_VISION_CAPTION_TARGET", f"{__name__}:_caption_target")
    captioner = resolve_vision_captioner("custom")

    assert isinstance(captioner, CustomVisionCaptioner)
    assert captioner.available() is True
    assert captioner.caption("/photos/a.png") == "caption of a.png for Describe"
    assert captioner.health()["status"] == "ok"


def _blank_caption(path, prompt):
    return ""


def test_a_custom_captioner_returning_blank_captions_nothing(monkeypatch):
    monkeypatch.setenv("LATTICEAI_VISION_CAPTION_TARGET", f"{__name__}:_blank_caption")
    captioner = CustomVisionCaptioner()

    assert captioner.caption("/photos/a.png") is None


def test_an_unconfigured_custom_captioner_is_unavailable(monkeypatch):
    monkeypatch.delenv("LATTICEAI_VISION_CAPTION_TARGET", raising=False)
    captioner = CustomVisionCaptioner()

    assert captioner.available() is False
    assert captioner.caption("/photos/a.png") is None
    assert "LATTICEAI_VISION_CAPTION_TARGET" in captioner.health()["detail"]


def test_an_mlx_captioner_without_a_model_name_falls_back_to_no_captions():
    # "mlx" with nothing to load is not a captioner; saying so beats a stub.
    assert type(resolve_vision_captioner("mlx", model="")) is VisionCaptioner
    assert type(resolve_vision_captioner("")) is VisionCaptioner
