"""Pure helpers of the LLM router: prompts, model refs, paths, images.

Nothing here touches a GPU, a network or a real model. The MLX chat-template
helper is the one import that would otherwise depend on what is installed, so
it is injected through ``sys.modules``.
"""

from __future__ import annotations

import base64
import io
import sys
import types

import pytest
from PIL import Image

from latticeai.models import router as router_mod

# ``hf_model_dir`` reads the root from its own module globals, so after the
# v11.3.0 split the temp-dir stand-in lands on ``.local_models``.
from latticeai.models.router import local_models as router_local_models


class _RaisingTokenizer:
    """A tokenizer whose chat template rejects the router's message shape."""

    def apply_chat_template(self, *_args, **_kwargs):
        raise RuntimeError("no chat template for this checkpoint")


def _png_base64(size=(4, 3)) -> str:
    buffer = io.BytesIO()
    Image.new("RGB", size, (10, 20, 30)).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


# ── _compose_system: context is appended only when there is context ──────


def test_compose_system_is_byte_identical_to_the_base_prompt_without_context():
    assert router_mod._compose_system("BASE", "") == "BASE"


def test_compose_system_appends_the_context_block_and_citation_rules():
    composed = router_mod._compose_system("BASE", "문서 1")

    assert composed == "BASE\n\nContext:\n문서 1\n\n" + router_mod.CITATION_INSTRUCTION
    assert "[1], [2]" in composed


# ── _system_for: which caller sent the context decides what it is ─────────


def test_system_for_a_chat_caller_is_the_product_prompt_and_the_citation_rules():
    from latticeai.models.router.generation import _system_for

    system = _system_for("커넥트 AI 문서", cite_sources=True)

    assert system.startswith(router_mod.SYSTEM_PROMPT)
    # Legacy branding is rewritten wherever the context came from.
    assert "Context:\nLattice AI 문서" in system
    assert router_mod.CITATION_INSTRUCTION in system


def test_system_for_a_worker_caller_is_the_callers_own_prompt_alone():
    """The agent seam sends its whole prompt, and gets exactly that.

    The chat persona used to be prepended to it, and six lines about being
    Lattice AI, a Vision-Language Model on Apple Silicon sat in front of the
    micro-turn that asks a model to write a file's contents. Two live models
    wrote that subject into the user's files.
    """
    from latticeai.models.router.generation import _system_for

    system = _system_for("You are the executor of an agent loop.", cite_sources=False)

    assert system == "You are the executor of an agent loop."
    assert router_mod.SYSTEM_PROMPT not in system
    assert router_mod.CITATION_INSTRUCTION not in system


def test_system_for_a_worker_caller_with_no_prompt_still_knows_what_it_is():
    """The one case where the identity is the only thing there is to say."""
    from latticeai.models.router.generation import _system_for

    assert _system_for("", cite_sources=False) == router_mod.SYSTEM_PROMPT
    assert _system_for(None, cite_sources=False) == router_mod.SYSTEM_PROMPT


# ── source_metadata_for_model: the provenance card shown next to a model ──


def test_source_metadata_marks_a_local_server_model_as_offline_capable():
    meta = router_mod.source_metadata_for_model(
        "lmstudio", {"family": "Qwen", "name": "Qwen3-VL"}, local_server=True
    )

    assert meta["source_country"] == "중국"
    assert meta["source_company"] == "Alibaba"
    assert meta["execution_method"] == "내 컴퓨터에서만 실행"
    assert meta["internet_requirement"].startswith("모델을 다운로드할 때만")
    assert meta["model_name"] == "Qwen3-VL"
    assert meta["source_display_order"] == [
        "source_country",
        "source_company",
        "execution_method",
        "internet_requirement",
        "model_name",
    ]


def test_source_metadata_warns_that_a_hosted_model_sends_files_out():
    meta = router_mod.source_metadata_for_model(
        "openai", {"family": "GPT", "id": "gpt-4o"}, local_server=False
    )

    assert meta["source_country"] == "미국"
    assert meta["source_company"] == "OpenAI"
    assert meta["execution_method"] == "인터넷 연결 후 사용"
    assert meta["internet_requirement"] == "내 파일이 인터넷으로 전송될 수 있음"
    # No display name in the catalog entry: the id stands in for it.
    assert meta["model_name"] == "gpt-4o"


def test_source_metadata_falls_back_to_the_provider_for_an_unknown_family():
    meta = router_mod.source_metadata_for_model("together", {}, local_server=False)

    assert meta["source_country"] == "미상"
    assert meta["source_company"] == "Together"
    assert meta["model_name"] == ""


# ── parse_model_ref ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("cloud:openai:gpt-4o", ("openai", "gpt-4o")),
        # Only the first two colons are separators — the model keeps its own.
        ("cloud:openrouter:anthropic/claude:beta", ("openrouter", "anthropic/claude:beta")),
        ("openai:gpt-4o", ("openai", "gpt-4o")),
        ("lmstudio:local-model", ("lmstudio", "local-model")),
        ("local_mlx:mlx-community/gemma", ("local_mlx", "mlx-community/gemma")),
        ("mlx:mlx-community/gemma", ("local_mlx", "mlx-community/gemma")),
        # An unknown prefix is not a provider, so the whole ref stays local.
        ("anthropic:claude-opus", ("local_mlx", "anthropic:claude-opus")),
        ("mlx-community/gemma-4-12b-it", ("local_mlx", "mlx-community/gemma-4-12b-it")),
    ],
)
def test_parse_model_ref_routes_each_reference_shape(ref, expected):
    assert router_mod.parse_model_ref(ref) == expected


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        ("mlx-community/gemma-4-12b-it", True),
        ("google/gemma4-27b", True),
        ("gemma 4 preview", True),
        ("mlx-community/gemma-3-12b-it", False),
        ("", False),
    ],
)
def test_is_gemma4_model_id_recognises_every_spelling(model_id, expected):
    assert router_mod._is_gemma4_model_id(model_id) is expected


# ── Hugging Face path resolution ─────────────────────────────────────────


def test_hf_model_dir_flattens_the_repo_id(monkeypatch, tmp_path):
    monkeypatch.setattr(router_local_models, "HF_MODELS_ROOT", tmp_path)

    assert router_mod.hf_model_dir("mlx-community/gemma") == tmp_path / "mlx-community__gemma"


def test_hf_cache_model_dir_is_none_when_nothing_was_ever_downloaded(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    assert router_mod.hf_cache_model_dir("mlx-community/gemma") is None


def test_hf_cache_model_dir_rejects_snapshots_that_are_not_complete_models(monkeypatch, tmp_path):
    """A half-finished download must not be reported as a usable model.

    Every candidate is inspected and every one is rejected, so the walk falls
    through to ``None`` rather than handing MLX a directory without weights.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    snapshots = (
        tmp_path / ".cache" / "huggingface" / "hub" / "models--mlx-community--gemma" / "snapshots"
    )
    no_weights = snapshots / "aaa"
    no_weights.mkdir(parents=True)
    (no_weights / "config.json").write_text("{}", encoding="utf-8")
    (no_weights / "tokenizer.json").write_text("{}", encoding="utf-8")
    no_tokenizer = snapshots / "bbb"
    no_tokenizer.mkdir()
    (no_tokenizer / "config.json").write_text("{}", encoding="utf-8")
    (no_tokenizer / "model.safetensors").write_bytes(b"weights")
    # A stray file next to the snapshots is not a candidate at all.
    (snapshots / "refs.txt").write_text("main", encoding="utf-8")

    assert router_mod.hf_cache_model_dir("mlx-community/gemma") is None


def test_resolve_local_hf_model_prefers_an_existing_explicit_path(tmp_path):
    assert router_mod._resolve_local_hf_model(str(tmp_path)) == str(tmp_path)


def test_resolve_local_hf_model_returns_the_repo_id_when_nothing_is_local(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(router_local_models, "HF_MODELS_ROOT", tmp_path / "hf-models")

    assert router_mod._resolve_local_hf_model("mlx-community/gemma") == "mlx-community/gemma"


# ── _local_model_type: reads model_type out of a checkpoint's config.json ─


def _write_config(directory, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.json").write_text(body, encoding="utf-8")


def test_local_model_type_reads_an_explicit_checkpoint_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(router_local_models, "HF_MODELS_ROOT", tmp_path / "hf-models")
    checkpoint = tmp_path / "checkpoint"
    _write_config(checkpoint, '{"model_type": "Gemma4_Unified"}')

    # Normalised to lower case, because the caller compares against a literal.
    assert router_mod._local_model_type(str(checkpoint)) == "gemma4_unified"


def test_local_model_type_falls_back_to_the_managed_download_directory(monkeypatch, tmp_path):
    root = tmp_path / "hf-models"
    monkeypatch.setattr(router_local_models, "HF_MODELS_ROOT", root)
    _write_config(root / "mlx-community__gemma", '{"model_type": "gemma4"}')

    assert router_mod._local_model_type("mlx-community/gemma") == "gemma4"


def test_local_model_type_is_none_when_the_config_names_no_type(monkeypatch, tmp_path):
    root = tmp_path / "hf-models"
    monkeypatch.setattr(router_local_models, "HF_MODELS_ROOT", root)
    _write_config(root / "mlx-community__gemma", '{"model_type": ""}')

    assert router_mod._local_model_type("mlx-community/gemma") is None


def test_local_model_type_is_none_when_no_config_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(router_local_models, "HF_MODELS_ROOT", tmp_path / "hf-models")

    assert router_mod._local_model_type("mlx-community/gemma") is None


def test_local_model_type_survives_a_corrupt_config(monkeypatch, tmp_path):
    """A truncated download must not take the load path down with it."""
    root = tmp_path / "hf-models"
    monkeypatch.setattr(router_local_models, "HF_MODELS_ROOT", root)
    _write_config(root / "mlx-community__gemma", "{not json")

    assert router_mod._local_model_type("mlx-community/gemma") is None


# ── Prompt assembly ──────────────────────────────────────────────────────


def test_build_prompt_falls_back_to_chatml_when_the_template_raises():
    prompt = router_mod.LLMRouter()._build_prompt("질문", "커넥트 AI 문서", _RaisingTokenizer())

    assert prompt.startswith("<|im_start|>system\n")
    assert prompt.endswith("<|im_start|>assistant\n")
    assert "<|im_start|>user\n질문<|im_end|>" in prompt
    # The context still reaches the model, with legacy branding rewritten.
    assert "Context:\nLattice AI 문서" in prompt
    assert router_mod.CITATION_INSTRUCTION in prompt


def test_build_prompt_uses_chatml_for_a_tokenizer_without_a_chat_template():
    prompt = router_mod.LLMRouter()._build_prompt("질문", None, object())

    # No context: the system prompt is passed through untouched.
    assert prompt == (
        "<|im_start|>system\n"
        + router_mod.SYSTEM_PROMPT
        + "<|im_end|>\n<|im_start|>user\n질문<|im_end|>\n<|im_start|>assistant\n"
    )


def test_build_vlm_prompt_delegates_to_the_mlx_vlm_chat_template(monkeypatch):
    seen = {}

    def apply_chat_template(processor, config, messages, add_generation_prompt=True, num_images=0):
        seen.update(
            processor=processor,
            config=config,
            messages=messages,
            add_generation_prompt=add_generation_prompt,
            num_images=num_images,
        )
        return "VLM PROMPT"

    fake_vlm = types.ModuleType("mlx_vlm")
    fake_vlm.apply_chat_template = apply_chat_template
    monkeypatch.setitem(sys.modules, "mlx_vlm", fake_vlm)
    model = types.SimpleNamespace(config={"model_type": "gemma4"})
    processor = object()

    prompt = router_mod.LLMRouter()._build_vlm_prompt(
        model, processor, "이 사진은?", "커넥트 AI 문서", 1
    )

    assert prompt == "VLM PROMPT"
    assert seen["processor"] is processor
    assert seen["config"] == {"model_type": "gemma4"}
    assert seen["num_images"] == 1
    assert seen["add_generation_prompt"] is True
    assert "Context:\nLattice AI 문서" in seen["messages"][0]["content"]
    assert seen["messages"][1] == {"role": "user", "content": "이 사진은?"}


def test_build_vlm_prompt_falls_back_to_the_text_frame_when_the_template_fails(monkeypatch):
    fake_vlm = types.ModuleType("mlx_vlm")

    def boom(*_args, **_kwargs):
        raise RuntimeError("processor/config mismatch")

    fake_vlm.apply_chat_template = boom
    monkeypatch.setitem(sys.modules, "mlx_vlm", fake_vlm)
    model = types.SimpleNamespace(config=None)

    prompt = router_mod.LLMRouter()._build_vlm_prompt(
        model, _RaisingTokenizer(), "질문", "커넥트 AI 문서", 0
    )

    assert prompt.startswith("<|im_start|>system\n")
    assert "Context:\nLattice AI 문서" in prompt
    assert "<|im_start|>user\n질문<|im_end|>" in prompt


# ── _unpack_local_cache: tolerates the pre-loader_kind cache tuple ────────


def test_unpack_local_cache_defaults_a_legacy_three_tuple_to_the_vlm_loader():
    model, tokenizer, draft = object(), object(), object()

    assert router_mod.LLMRouter()._unpack_local_cache((model, tokenizer, draft)) == (
        model,
        tokenizer,
        draft,
        "mlx_vlm",
    )


def test_unpack_local_cache_reports_the_recorded_loader_kind():
    entry = (object(), object(), None, "mlx_lm")

    assert router_mod.LLMRouter()._unpack_local_cache(entry)[3] == "mlx_lm"


# ── _prep_image ──────────────────────────────────────────────────────────


def test_prep_image_returns_none_without_image_data():
    router = router_mod.LLMRouter()

    assert router._prep_image(None) is None
    assert router._prep_image("") is None


def test_prep_image_decodes_base64_into_an_rgb_pillow_image():
    image = router_mod.LLMRouter()._prep_image(_png_base64((4, 3)))

    assert image is not None
    assert (image.width, image.height) == (4, 3)
    assert image.mode == "RGB"


def test_prep_image_returns_none_for_data_that_is_not_an_image():
    """A broken upload degrades to a text-only turn instead of failing it."""
    payload = base64.b64encode(b"this is not an image").decode("ascii")

    assert router_mod.LLMRouter()._prep_image(payload) is None


def test_prep_image_returns_none_for_data_that_is_not_even_base64():
    assert router_mod.LLMRouter()._prep_image("not-base64!!") is None
