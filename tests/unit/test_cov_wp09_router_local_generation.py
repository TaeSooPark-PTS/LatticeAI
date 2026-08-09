"""Local MLX generation: chat, streaming and the document pipeline.

The generator functions import ``mlx.core``, ``mlx_vlm`` and ``mlx_lm`` inside
the worker-thread body, so the fakes have to live in ``sys.modules``. That
keeps these tests about what the router asks each backend for — prompt, image,
sampler, draft model — on a machine with or without MLX installed.
"""

from __future__ import annotations

import asyncio
import base64
import io
import sys
import types

import pytest
from PIL import Image

from latticeai.models import router as router_mod

STREAM_TIMEOUT = 10.0


class _TemplateTokenizer:
    """Records the messages the router hands to the chat template."""

    def __init__(self):
        self.messages = None

    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True):
        self.messages = messages
        return "TEMPLATED PROMPT"


class _RaisingTokenizer:
    def apply_chat_template(self, *_args, **_kwargs):
        raise RuntimeError("no chat template for this checkpoint")


def _install_fake_mlx(monkeypatch, *, vlm=None, lm=None):
    devices = []
    core = types.ModuleType("mlx.core")
    core.gpu = object()
    core.set_default_device = devices.append
    mlx = types.ModuleType("mlx")
    mlx.core = core
    monkeypatch.setitem(sys.modules, "mlx", mlx)
    monkeypatch.setitem(sys.modules, "mlx.core", core)

    fake_vlm = types.ModuleType("mlx_vlm")
    fake_vlm.apply_chat_template = lambda *_a, **_k: "VLM PROMPT"
    for name, value in (vlm or {}).items():
        setattr(fake_vlm, name, value)
    monkeypatch.setitem(sys.modules, "mlx_vlm", fake_vlm)

    fake_lm = types.ModuleType("mlx_lm")
    for name, value in (lm or {}).items():
        setattr(fake_lm, name, value)
    monkeypatch.setitem(sys.modules, "mlx_lm", fake_lm)
    return devices


def _png_base64(size=(4, 3)) -> str:
    buffer = io.BytesIO()
    Image.new("RGB", size, (10, 20, 30)).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _collect(stream_factory, timeout: float = STREAM_TIMEOUT) -> list:
    chunks: list = []

    async def _scenario() -> None:
        async def _drain() -> None:
            async for chunk in stream_factory():
                chunks.append(chunk)

        await asyncio.wait_for(_drain(), timeout)

    asyncio.run(_scenario())
    return chunks


def _client(create):
    return types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
    )


class _AsyncEvents:
    def __init__(self, events):
        self._events = list(events)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)


def _delta_event(content):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content=content))]
    )


def _vlm_model():
    """A vision checkpoint: ``mlx_vlm.apply_chat_template`` reads ``.config``."""
    return types.SimpleNamespace(config={"model_type": "gemma4"})


def _router_with(entry, key: str = "local"):
    router = router_mod.LLMRouter()
    router._cache = {key: entry}
    router._current = key
    return router


# ── Chat generation on the text (mlx-lm) path ────────────────────────────


def test_generate_runs_the_mlx_lm_text_path_and_rebrands_the_answer(monkeypatch):
    seen = {}

    def lm_generate(model, tokenizer, **kwargs):
        seen.update(model=model, tokenizer=tokenizer, **kwargs)
        return "커넥트 AI 답변"

    devices = _install_fake_mlx(monkeypatch, lm={"generate": lm_generate})
    model, tokenizer, draft = object(), _TemplateTokenizer(), object()
    router = _router_with((model, tokenizer, draft, "mlx_lm"))

    result = asyncio.run(router.generate("질문", "커넥트 AI 문서", max_tokens=64, temperature=0.7))

    assert result == "Lattice AI 답변"
    assert seen["model"] is model
    assert seen["tokenizer"] is tokenizer
    assert seen["prompt"] == "TEMPLATED PROMPT"
    assert seen["max_tokens"] == 64
    assert seen["draft_model"] is draft
    # mlx-lm has no multimodal or MTP arguments; passing them would be a
    # TypeError against the real package.
    assert "image" not in seen
    assert "draft_kind" not in seen
    # The router defers to the backend's bundled sampler.
    assert seen["sampler"] is None
    assert "Context:\nLattice AI 문서" in tokenizer.messages[0]["content"]
    assert tokenizer.messages[1] == {"role": "user", "content": "질문"}
    # Generation always pins the GPU stream first.
    assert devices == [sys.modules["mlx.core"].gpu]


def test_generate_as_reads_the_text_attribute_of_a_generation_result(monkeypatch):
    def lm_generate(*_args, **_kwargs):
        return types.SimpleNamespace(text="커넥트 AI 결과", token_count=7)

    _install_fake_mlx(monkeypatch, lm={"generate": lm_generate})
    router = _router_with((object(), _TemplateTokenizer(), None, "mlx_lm"), key="text-model")

    assert asyncio.run(router.generate_as("text-model", "질문")) == "Lattice AI 결과"
    # A request-scoped model never changes the process-wide default.
    assert router.current_model_id == "text-model"


# ── Chat generation on the vision (mlx-vlm) path ─────────────────────────


def test_generate_as_sends_a_decoded_image_down_the_vlm_path(monkeypatch):
    seen = {}

    def vlm_generate(model, processor, **kwargs):
        seen.update(model=model, processor=processor, **kwargs)
        return types.SimpleNamespace(text="사진 설명")

    _install_fake_mlx(monkeypatch, vlm={"generate": vlm_generate})
    model, processor = _vlm_model(), _TemplateTokenizer()
    router = _router_with((model, processor, None, "mlx_vlm"), key="vision")

    result = asyncio.run(
        router.generate_as("vision", "이 사진은?", None, 32, 0.1, _png_base64((4, 3)))
    )

    assert result == "사진 설명"
    assert seen["prompt"] == "VLM PROMPT"
    assert seen["max_tokens"] == 32
    assert seen["draft_kind"] == "mtp"
    assert seen["draft_model"] is None
    assert seen["image"] is not None
    assert (seen["image"].width, seen["image"].height) == (4, 3)
    # The VLM prompt builder owns the template, so the tokenizer is not used.
    assert processor.messages is None


def test_generate_on_the_vlm_path_without_an_image_passes_none(monkeypatch):
    seen = {}

    def vlm_generate(_model, _processor, **kwargs):
        seen.update(kwargs)
        return "커넥트 AI"

    _install_fake_mlx(monkeypatch, vlm={"generate": vlm_generate})
    router = _router_with((_vlm_model(), _TemplateTokenizer(), None, "mlx_vlm"))

    assert asyncio.run(router.generate("질문")) == "Lattice AI"
    assert seen["image"] is None
    assert seen["prompt"] == "VLM PROMPT"


# ── Chat streaming on the vision path ────────────────────────────────────


def test_stream_generate_as_streams_the_vlm_path_in_every_chunk_shape(monkeypatch):
    seen = {}

    def vlm_stream(model, processor, **kwargs):
        seen.update(model=model, processor=processor, **kwargs)
        yield types.SimpleNamespace(text="커넥트 AI ")
        yield ("응답",)
        yield 42

    _install_fake_mlx(monkeypatch, vlm={"stream_generate": vlm_stream})
    router = _router_with((_vlm_model(), _TemplateTokenizer(), None, "mlx_vlm"), key="vision")

    chunks = _collect(
        lambda: router.stream_generate_as("vision", "이 사진은?", None, 16, 0.5, _png_base64())
    )

    assert chunks == ["Lattice AI ", "응답", "42"]
    assert seen["prompt"] == "VLM PROMPT"
    assert seen["max_tokens"] == 16
    assert seen["draft_kind"] == "mtp"
    assert seen["image"] is not None


# ── Document generation ──────────────────────────────────────────────────


def test_generate_document_reports_when_no_model_is_loaded():
    assert asyncio.run(router_mod.LLMRouter().generate_document("보고서", "DOC")) == (
        "No model loaded."
    )


def test_stream_generate_document_reports_when_no_model_is_loaded():
    router = router_mod.LLMRouter()

    assert _collect(lambda: router.stream_generate_document("보고서", "DOC")) == [
        "No model loaded."
    ]


def test_generate_document_routes_a_cloud_model_to_the_cloud_backend():
    seen = {}

    async def create(**kwargs):
        seen.update(kwargs)
        message = types.SimpleNamespace(content="# 커넥트 AI 보고서")
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    cloud = router_mod.CloudModel(
        provider="openai", model="gpt-4o", client=_client(create), cache_key="cloud"
    )
    router = _router_with(cloud, key="cloud")

    result = asyncio.run(router.generate_document("보고서 써줘", "DOC SYSTEM"))

    assert result == "# Lattice AI 보고서"
    assert seen["messages"][0] == {"role": "system", "content": "DOC SYSTEM"}
    assert seen["max_tokens"] == 8192
    assert seen["temperature"] == 0.3


def test_stream_generate_document_routes_a_cloud_model_to_the_cloud_backend():
    async def create(**_kwargs):
        return _AsyncEvents([_delta_event("# 커넥트 AI"), _delta_event(" 보고서")])

    cloud = router_mod.CloudModel(
        provider="openai", model="gpt-4o", client=_client(create), cache_key="cloud"
    )
    router = _router_with(cloud, key="cloud")

    chunks = _collect(lambda: router.stream_generate_document("보고서", "DOC SYSTEM"))

    assert chunks == ["# Lattice AI", " 보고서"]


def test_generate_document_as_uses_the_vlm_path_with_the_document_prompt(monkeypatch):
    seen = {}

    def vlm_generate(model, processor, **kwargs):
        seen.update(model=model, processor=processor, **kwargs)
        return types.SimpleNamespace(text="# 커넥트 AI 보고서")

    _install_fake_mlx(monkeypatch, vlm={"generate": vlm_generate})
    tokenizer = _TemplateTokenizer()
    router = _router_with((object(), tokenizer, None, "mlx_vlm"), key="vision")

    result = asyncio.run(
        router.generate_document_as("vision", "보고서 써줘", "DOC SYSTEM", max_tokens=2048)
    )

    assert result == "# Lattice AI 보고서"
    # Documents are text-only even on a vision checkpoint.
    assert seen["image"] is None
    assert seen["draft_kind"] == "mtp"
    assert seen["max_tokens"] == 2048
    assert seen["prompt"] == "TEMPLATED PROMPT"
    # The document system prompt replaces the chat identity prompt entirely.
    assert tokenizer.messages == [
        {"role": "system", "content": "DOC SYSTEM"},
        {"role": "user", "content": "보고서 써줘"},
    ]


def test_generate_document_as_falls_back_to_chatml_when_the_template_raises(monkeypatch):
    seen = {}

    def lm_generate(_model, _tokenizer, **kwargs):
        seen.update(kwargs)
        return "보고서 본문"

    _install_fake_mlx(monkeypatch, lm={"generate": lm_generate})
    router = _router_with((object(), _RaisingTokenizer(), None, "mlx_lm"))

    result = asyncio.run(router.generate_document_as("local", "보고서 써줘", "DOC SYSTEM"))

    assert result == "보고서 본문"
    assert seen["prompt"] == (
        "<|im_start|>system\nDOC SYSTEM<|im_end|>\n"
        "<|im_start|>user\n보고서 써줘<|im_end|>\n<|im_start|>assistant\n"
    )
    assert "draft_kind" not in seen


def test_generate_document_as_uses_chatml_for_a_tokenizer_without_a_template(monkeypatch):
    seen = {}

    def lm_generate(_model, _tokenizer, **kwargs):
        seen.update(kwargs)
        return "보고서 본문"

    _install_fake_mlx(monkeypatch, lm={"generate": lm_generate})
    router = _router_with((object(), object(), None, "mlx_lm"))

    assert asyncio.run(router.generate_document_as("local", "질문", "DOC SYSTEM")) == "보고서 본문"
    assert seen["prompt"].startswith("<|im_start|>system\nDOC SYSTEM<|im_end|>")


# ── Document streaming ───────────────────────────────────────────────────


def test_stream_generate_document_as_streams_the_vlm_path_after_a_template_failure(
    monkeypatch,
):
    seen = {}

    def vlm_stream(_model, _processor, **kwargs):
        seen.update(kwargs)
        yield types.SimpleNamespace(text="# 커넥트 AI")
        yield types.SimpleNamespace(text=" 보고서")

    _install_fake_mlx(monkeypatch, vlm={"stream_generate": vlm_stream})
    router = _router_with((object(), _RaisingTokenizer(), None, "mlx_vlm"), key="vision")

    chunks = _collect(
        lambda: router.stream_generate_document_as(
            "vision", "보고서 써줘", "DOC SYSTEM", max_tokens=256, temperature=0.9
        )
    )

    assert chunks == ["# Lattice AI", " 보고서"]
    assert seen["image"] is None
    assert seen["draft_kind"] == "mtp"
    assert seen["max_tokens"] == 256
    assert seen["prompt"].startswith("<|im_start|>system\nDOC SYSTEM<|im_end|>")


def test_stream_generate_document_as_uses_chatml_without_a_chat_template(monkeypatch):
    seen = {}

    def lm_stream(_model, _tokenizer, **kwargs):
        seen.update(kwargs)
        yield types.SimpleNamespace(text="본문")

    _install_fake_mlx(monkeypatch, lm={"stream_generate": lm_stream})
    router = _router_with((object(), object(), None, "mlx_lm"))

    chunks = _collect(lambda: router.stream_generate_document_as("local", "질문", "DOC SYSTEM"))

    assert chunks == ["본문"]
    assert seen["prompt"].startswith("<|im_start|>system\nDOC SYSTEM<|im_end|>")
    assert "draft_kind" not in seen


def test_stream_generate_document_as_templates_the_document_messages(monkeypatch):
    def lm_stream(_model, _tokenizer, **kwargs):
        assert kwargs["prompt"] == "TEMPLATED PROMPT"
        yield types.SimpleNamespace(text="본문")

    _install_fake_mlx(monkeypatch, lm={"stream_generate": lm_stream})
    tokenizer = _TemplateTokenizer()
    router = _router_with((object(), tokenizer, None, "mlx_lm"))

    assert _collect(lambda: router.stream_generate_document("보고서 써줘", "DOC SYSTEM")) == [
        "본문"
    ]
    assert tokenizer.messages == [
        {"role": "system", "content": "DOC SYSTEM"},
        {"role": "user", "content": "보고서 써줘"},
    ]


def test_local_stream_leaves_the_default_model_untouched(monkeypatch):
    def lm_stream(*_args, **_kwargs):
        yield types.SimpleNamespace(text="답변")

    _install_fake_mlx(monkeypatch, lm={"stream_generate": lm_stream})
    router = router_mod.LLMRouter()
    router._cache = {
        "default": (object(), _TemplateTokenizer(), None, "mlx_lm"),
        "requested": (object(), _TemplateTokenizer(), None, "mlx_lm"),
    }
    router._current = "default"

    assert _collect(lambda: router.stream_generate_as("requested", "질문")) == ["답변"]
    assert router.current_model_id == "default"


def test_requesting_an_unloaded_model_names_the_missing_model():
    router = router_mod.LLMRouter()
    router._cache = {"loaded": (object(), _TemplateTokenizer(), None, "mlx_lm")}
    router._current = "loaded"

    with pytest.raises(ValueError, match="Model 'absent' is not loaded"):
        asyncio.run(router.generate_document_as("absent", "질문", "DOC"))
