"""Model registry, memory policy and the local load path of ``LLMRouter``.

The registry is the mutable state every request reads: which model is the
default, which are resident, and which one gets evicted when the memory policy
bites. These tests drive it with fake cache entries — a local entry is the
``(model, tokenizer, draft, loader_kind)`` tuple, a cloud entry is a
``CloudModel`` — so the invariants are checked without loading weights.

``router_mod.CloudModel`` is resolved at call time rather than imported by
name: the MLX import-contract tests reload this module during the full suite,
which rebinds the class object.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from latticeai.models import router as router_mod

# The optional-backend bindings (``mx`` / ``vlm_load`` / ``lm_load`` /
# ``VLM_AVAILABLE`` / ``LM_AVAILABLE`` / ``AsyncOpenAI``) and the two callables
# the load path reaches through them are read in the submodule that defines
# them, so after the v11.3.0 split the stand-ins land on ``.loading`` — a name
# rebound on the package ``__init__`` would leave those reads untouched.
from latticeai.models.router import loading as router_loading

# ``hf_model_dir`` reads the root from its own module globals, so after the
# v11.3.0 split the temp-dir stand-in lands on ``.local_models``.
from latticeai.models.router import local_models as router_local_models


class _FakeMx:
    """Stands in for ``mlx.core`` so memory release is deterministic."""

    gpu = object()

    def __init__(self):
        self.devices = []
        self.cache_clears = 0

    def set_default_device(self, device):
        self.devices.append(device)

    def clear_cache(self):
        self.cache_clears += 1


class _TemplateTokenizer:
    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True):
        self.messages = messages
        return "PROMPT"


def _local_entry(loader_kind: str = "mlx_vlm"):
    return (object(), _TemplateTokenizer(), None, loader_kind)


def _cloud_entry(name: str):
    return router_mod.CloudModel(
        provider="openai", model=name, client=object(), cache_key=name
    )


def _collect(stream_factory, timeout: float = 10.0) -> list:
    chunks: list = []

    async def _scenario() -> None:
        async def _drain() -> None:
            async for chunk in stream_factory():
                chunks.append(chunk)

        await asyncio.wait_for(_drain(), timeout)

    asyncio.run(_scenario())
    return chunks


# ── Registry reads ───────────────────────────────────────────────────────


def test_loaded_model_ids_reports_the_resident_set_in_load_order():
    router = router_mod.LLMRouter()
    router._cache = {"first": _local_entry(), "second": _cloud_entry("second")}

    assert router.loaded_model_ids == ["first", "second"]
    assert router.current_model_id is None


def test_switch_model_refuses_a_model_that_is_not_resident():
    router = router_mod.LLMRouter()
    router._cache = {"loaded": _local_entry()}

    with pytest.raises(KeyError, match="absent"):
        router.switch_model("absent")

    assert router.current_model_id is None


def test_switch_model_sets_the_default_and_marks_it_used():
    router = router_mod.LLMRouter()
    router._cache = {"loaded": _local_entry()}

    router.switch_model("loaded")

    assert router.current_model_id == "loaded"
    assert "loaded" in router.model_memory_policy()["last_used"]


def test_only_non_cloud_entries_count_towards_the_local_memory_budget():
    router = router_mod.LLMRouter()
    router._cache = {"local": _local_entry(), "cloud": _cloud_entry("cloud")}

    assert router._is_local_model("local") is True
    assert router._is_local_model("cloud") is False
    assert router._is_local_model("never-loaded") is False


# ── Unloading ────────────────────────────────────────────────────────────


def test_unload_model_promotes_a_survivor_as_the_new_default(monkeypatch):
    fake_mx = _FakeMx()
    monkeypatch.setattr(router_loading, "mx", fake_mx)
    router = router_mod.LLMRouter()
    router._cache = {"a": _local_entry(), "b": _local_entry()}
    router._current = "a"
    router._touch("a")
    router._touch("b")

    router.unload_model("a")

    assert router.loaded_model_ids == ["b"]
    assert router.current_model_id == "b"
    assert "a" not in router.model_memory_policy()["last_used"]
    assert fake_mx.cache_clears == 1


def test_unload_model_leaves_no_default_when_the_last_model_goes(monkeypatch):
    monkeypatch.setattr(router_loading, "mx", _FakeMx())
    router = router_mod.LLMRouter()
    router._cache = {"only": _local_entry()}
    router._current = "only"

    router.unload_model("only")
    # Unloading something that was never resident is a no-op, not an error.
    router.unload_model("never-loaded")

    assert router.loaded_model_ids == []
    assert router.current_model_id is None


def test_unload_model_keeps_the_default_when_another_model_is_evicted(monkeypatch):
    monkeypatch.setattr(router_loading, "mx", _FakeMx())
    router = router_mod.LLMRouter()
    router._cache = {"keep": _local_entry(), "drop": _local_entry()}
    router._current = "keep"

    router.unload_model("drop")

    assert router.current_model_id == "keep"


def test_unload_all_empties_the_registry(monkeypatch):
    fake_mx = _FakeMx()
    monkeypatch.setattr(router_loading, "mx", fake_mx)
    router = router_mod.LLMRouter()
    router._cache = {"a": _local_entry(), "b": _cloud_entry("b")}
    router._current = "a"
    router._touch("a")

    router.unload_all()

    assert router.loaded_model_ids == []
    assert router.current_model_id is None
    assert router.model_memory_policy()["last_used"] == {}
    assert fake_mx.cache_clears == 1


def test_unload_idle_models_ignores_a_non_positive_threshold():
    router = router_mod.LLMRouter()
    router._cache = {"a": _local_entry()}
    router._last_used = {"a": 0.0}

    assert router.unload_idle_models(0) == []
    assert router.unload_idle_models(-30) == []
    assert router.loaded_model_ids == ["a"]


def test_unload_idle_models_evicts_only_the_models_past_the_threshold(monkeypatch):
    monkeypatch.setattr(router_loading, "mx", _FakeMx())
    router = router_mod.LLMRouter()
    router._cache = {"stale": _local_entry(), "fresh": _local_entry()}
    router._current = "stale"
    now = time.monotonic()
    router._last_used = {"stale": now - 600.0, "fresh": now}

    unloaded = router.unload_idle_models(60)

    assert unloaded == ["stale"]
    assert router.loaded_model_ids == ["fresh"]
    assert router.current_model_id == "fresh"


def test_model_memory_policy_reports_the_budget_and_the_residents():
    router = router_mod.LLMRouter()
    router._max_local_models = 3
    router._cache = {"a": _local_entry(), "b": _local_entry()}
    router._touch("a")
    snapshot = router.model_memory_policy()

    assert snapshot["max_local_models"] == 3
    assert snapshot["loaded_count"] == 2
    assert list(snapshot["last_used"]) == ["a"]

    # The report is a copy: mutating it cannot corrupt the registry.
    snapshot["last_used"]["a"] = -1.0
    assert router.model_memory_policy()["last_used"]["a"] != -1.0


def test_max_local_models_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("LATTICEAI_MAX_LOCAL_MODELS", "4")
    assert router_mod.LLMRouter()._max_local_models == 4

    # A nonsensical budget is clamped, never zero.
    monkeypatch.setenv("LATTICEAI_MAX_LOCAL_MODELS", "0")
    assert router_mod.LLMRouter()._max_local_models == 1


# ── Memory policy enforcement ────────────────────────────────────────────


def test_enforce_local_model_limit_evicts_the_least_recently_used_local_model(monkeypatch):
    monkeypatch.setattr(router_loading, "mx", _FakeMx())
    router = router_mod.LLMRouter()
    router._max_local_models = 2
    router._cache = {
        "old": _local_entry(),
        "new": _local_entry(),
        "cloud": _cloud_entry("cloud"),
    }
    now = time.monotonic()
    router._last_used = {"old": now - 600.0, "new": now, "cloud": now - 900.0}

    router._enforce_local_model_limit("incoming")

    # Exactly one eviction: enough headroom for the incoming model.
    assert router.loaded_model_ids == ["new", "cloud"]
    # A cloud handle costs no local memory, so the budget never evicts one —
    # even though it is the least recently used entry of all.
    assert router._is_local_model("cloud") is False


def test_enforce_local_model_limit_never_evicts_the_incoming_model(monkeypatch):
    monkeypatch.setattr(router_loading, "mx", _FakeMx())
    router = router_mod.LLMRouter()
    router._max_local_models = 1
    router._cache = {"same": _local_entry()}
    router._last_used = {"same": time.monotonic()}

    router._enforce_local_model_limit("same")

    assert router.loaded_model_ids == ["same"]


def test_release_memory_ignores_a_backend_whose_cache_clear_fails(monkeypatch):
    class _AngryMx:
        def clear_cache(self):
            raise RuntimeError("metal command queue busy")

    monkeypatch.setattr(router_loading, "mx", _AngryMx())
    router = router_mod.LLMRouter()
    router._cache = {"a": _local_entry()}
    router._current = "a"

    router.unload_all()

    assert router.loaded_model_ids == []


def test_release_memory_skips_a_backend_without_a_cache_api(monkeypatch):
    monkeypatch.setattr(router_loading, "mx", object())
    router = router_mod.LLMRouter()
    router._cache = {"a": _local_entry()}

    router.unload_all()

    assert router.loaded_model_ids == []


# ── _model_snapshot and the "nothing loaded" answers ─────────────────────


def test_model_snapshot_reports_nothing_when_no_model_is_selected():
    assert router_mod.LLMRouter()._model_snapshot() == (None, None)


def test_generate_without_a_loaded_model_answers_with_the_no_model_marker():
    assert asyncio.run(router_mod.LLMRouter().generate("질문")) == "No model."


def test_stream_generate_without_a_loaded_model_yields_the_no_model_marker():
    router = router_mod.LLMRouter()

    assert _collect(lambda: router.stream_generate("질문")) == ["No model."]


# ── load_model ───────────────────────────────────────────────────────────


def test_load_model_hands_a_cloud_reference_to_the_cloud_loader(monkeypatch):
    def _never(*_args, **_kwargs):
        raise AssertionError("a cloud reference must not touch the MLX runtime")

    monkeypatch.setattr(router_loading, "ensure_mlx_runtime", _never)
    router = router_mod.LLMRouter()
    seen = {}

    def fake_load_cloud(provider, model, api_key_override=None, owner=None):
        seen.update(
            provider=provider, model=model, api_key_override=api_key_override, owner=owner
        )
        return "Cloud provider ready: openai:gpt-4o::alice"

    router._load_cloud_model = fake_load_cloud

    result = asyncio.run(
        router.load_model("cloud:openai:gpt-4o", api_key_override="sk-test", owner="alice")
    )

    assert result == "Cloud provider ready: openai:gpt-4o::alice"
    assert seen == {
        "provider": "openai",
        "model": "gpt-4o",
        "api_key_override": "sk-test",
        "owner": "alice",
    }


def test_load_model_refuses_a_local_model_when_mlx_never_bound(monkeypatch):
    """The guard after ``ensure_mlx_runtime`` is what stops an ``NoneType``
    crash deep inside the worker thread."""
    calls = []
    monkeypatch.setattr(router_loading, "ensure_mlx_runtime", lambda: calls.append("ensure"))
    monkeypatch.setattr(router_loading, "mx", None)
    router = router_mod.LLMRouter()

    with pytest.raises(RuntimeError, match="MLX is not available in this process"):
        asyncio.run(router.load_model("mlx-community/gemma-4-12b-it"))

    assert calls == ["ensure"]
    assert router.loaded_model_ids == []


def test_load_model_returns_the_cached_stack_without_reloading(monkeypatch):
    def _never(*_args, **_kwargs):
        raise AssertionError("a cached stack must not be loaded again")

    monkeypatch.setattr(router_loading, "ensure_mlx_runtime", lambda: None)
    monkeypatch.setattr(router_loading, "mx", _FakeMx())
    monkeypatch.setattr(router_loading, "vlm_load", _never)
    monkeypatch.setattr(router_loading, "lm_load", None)
    router = router_mod.LLMRouter()
    # The cache key folds in the draft model: the same target with a different
    # assistant is a different stack.
    router._cache = {"target_draft": _local_entry()}

    result = asyncio.run(router.load_model("target", draft_model_id="draft"))

    assert result == "Cached: target_draft"
    assert router.current_model_id == "target_draft"
    assert "target_draft" in router.model_memory_policy()["last_used"]


def _prepare_local_load(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(router_local_models, "HF_MODELS_ROOT", tmp_path / "hf-models")
    monkeypatch.setattr(router_loading, "ensure_mlx_runtime", lambda: None)
    monkeypatch.setattr(router_loading, "mx", _FakeMx())


def test_load_model_without_mlx_vlm_still_loads_gemma4_through_mlx_lm(
    monkeypatch, tmp_path
):
    """No mlx-vlm installed is a load *failure* the Gemma 4 path recovers from."""
    _prepare_local_load(monkeypatch, tmp_path)
    monkeypatch.setattr(router_loading, "vlm_load", None)
    loaded = []

    def fake_lm_load(model_id):
        loaded.append(model_id)
        return object(), _TemplateTokenizer()

    monkeypatch.setattr(router_loading, "lm_load", fake_lm_load)
    router = router_mod.LLMRouter()

    result = asyncio.run(router.load_model("mlx-community/gemma-4-12b-it"))

    assert result == "Success: mlx-community/gemma-4-12b-it (mlx_lm)"
    assert loaded == ["mlx-community/gemma-4-12b-it"]
    assert router._cache["mlx-community/gemma-4-12b-it"][3] == "mlx_lm"


def test_load_model_without_mlx_vlm_refuses_a_model_that_declares_vision(
    monkeypatch, tmp_path
):
    """A checkpoint that says it is multimodal is never downgraded to text.

    v12.0.0 generalised the mlx-lm retry from "the id looks like Gemma 4" to
    "the checkpoint's own config declares no vision", because the old rule was
    a one-family roster that failed every plain text model MLX-VLM cannot open.
    What did **not** change is this: answering an image request from a
    text-only load is worse than refusing it, so a config carrying
    ``vision_config`` still surfaces the original failure.
    """
    _prepare_local_load(monkeypatch, tmp_path)
    monkeypatch.setattr(router_loading, "vlm_load", None)
    monkeypatch.setattr(
        router_loading, "lm_load", lambda _model_id: (object(), _TemplateTokenizer())
    )
    monkeypatch.setattr(router_loading, "_declares_vision", lambda _model_id: True)
    router = router_mod.LLMRouter()

    with pytest.raises(RuntimeError, match="MLX-VLM is not installed"):
        asyncio.run(router.load_model("mlx-community/Qwen3-VL-8B"))

    assert router.loaded_model_ids == []


def test_load_model_falls_back_to_text_for_any_model_without_vision(
    monkeypatch, tmp_path
):
    """The v12.0.0 generalisation: "every small model works" needs this.

    A 0.5B Qwen, an AWQ checkpoint, any fine-tune with a text-only
    architecture — MLX-VLM cannot open them and MLX-LM can. Before this the
    load failed outright unless the *name* matched Gemma 4.
    """
    _prepare_local_load(monkeypatch, tmp_path)
    monkeypatch.setattr(router_loading, "vlm_load", None)
    loaded: list = []

    def fake_lm_load(model_id):
        loaded.append(model_id)
        return object(), _TemplateTokenizer()

    monkeypatch.setattr(router_loading, "lm_load", fake_lm_load)
    monkeypatch.setattr(router_loading, "_declares_vision", lambda _model_id: False)
    router = router_mod.LLMRouter()

    result = asyncio.run(router.load_model("Qwen/Qwen2.5-0.5B-Instruct-AWQ"))

    assert result.endswith("(mlx_lm)")
    assert loaded == ["Qwen/Qwen2.5-0.5B-Instruct-AWQ"]


def test_a_failed_text_retry_reports_the_original_loader_failure(
    monkeypatch, tmp_path
):
    """The text path is a fallback, so the useful diagnosis is the first one.

    Raising the second failure would tell an operator their model is not a
    text model, which they knew.
    """
    _prepare_local_load(monkeypatch, tmp_path)

    def broken_vlm_load(_model_id):
        raise RuntimeError("VLM said no")

    def broken_lm_load(_model_id):
        raise RuntimeError("LM said no")

    monkeypatch.setattr(router_loading, "vlm_load", broken_vlm_load)
    monkeypatch.setattr(router_loading, "lm_load", broken_lm_load)
    monkeypatch.setattr(router_loading, "_declares_vision", lambda _model_id: False)
    router = router_mod.LLMRouter()

    with pytest.raises(RuntimeError, match="VLM said no"):
        asyncio.run(router.load_model("some/text-model"))


def test_load_model_loads_a_draft_model_on_the_vlm_path(monkeypatch, tmp_path):
    _prepare_local_load(monkeypatch, tmp_path)
    target, draft = object(), object()
    requested = []

    def fake_vlm_load(model_id):
        requested.append(model_id)
        return (draft if "draft" in model_id else target), _TemplateTokenizer()

    monkeypatch.setattr(router_loading, "vlm_load", fake_vlm_load)
    monkeypatch.setattr(router_loading, "lm_load", None)
    router = router_mod.LLMRouter()

    result = asyncio.run(
        router.load_model("mlx-community/target", draft_model_id="mlx-community/draft")
    )

    assert result == "Success: mlx-community/target_mlx-community/draft (mlx_vlm)"
    assert requested == ["mlx-community/target", "mlx-community/draft"]
    cached = router._cache["mlx-community/target_mlx-community/draft"]
    assert cached[0] is target
    assert cached[2] is draft
    assert cached[3] == "mlx_vlm"
    assert router.current_model_id == "mlx-community/target_mlx-community/draft"


def test_load_model_loads_a_draft_model_on_the_text_path(monkeypatch, tmp_path):
    _prepare_local_load(monkeypatch, tmp_path)
    target, draft = object(), object()
    requested = []

    def fake_vlm_load(_model_id):
        raise RuntimeError("mlx-vlm cannot open this checkpoint")

    def fake_lm_load(model_id):
        requested.append(model_id)
        return (draft if "draft" in model_id else target), _TemplateTokenizer()

    monkeypatch.setattr(router_loading, "vlm_load", fake_vlm_load)
    monkeypatch.setattr(router_loading, "lm_load", fake_lm_load)
    router = router_mod.LLMRouter()

    result = asyncio.run(
        router.load_model("mlx-community/gemma-4-12b", draft_model_id="mlx-community/draft")
    )

    assert result == "Success: mlx-community/gemma-4-12b_mlx-community/draft (mlx_lm)"
    assert requested == ["mlx-community/gemma-4-12b", "mlx-community/draft"]
    cached = router._cache["mlx-community/gemma-4-12b_mlx-community/draft"]
    assert cached[2] is draft


def test_load_model_evicts_a_resident_local_model_to_stay_in_budget(monkeypatch, tmp_path):
    _prepare_local_load(monkeypatch, tmp_path)
    monkeypatch.setattr(
        router_loading, "vlm_load", lambda _model_id: (object(), _TemplateTokenizer())
    )
    monkeypatch.setattr(router_loading, "lm_load", None)
    router = router_mod.LLMRouter()
    router._max_local_models = 1
    router._cache = {"resident": _local_entry()}
    router._current = "resident"
    router._last_used = {"resident": time.monotonic()}

    asyncio.run(router.load_model("mlx-community/incoming"))

    assert router.loaded_model_ids == ["mlx-community/incoming"]
    assert router.current_model_id == "mlx-community/incoming"
