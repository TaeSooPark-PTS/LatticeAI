"""Regression coverage for Gemma 4 MLX loader routing."""

import asyncio

from latticeai.models import router as router_mod


class _FakeMx:
    gpu = object()

    def set_default_device(self, _device):
        return None

    def clear_cache(self):
        return None


class _FakeTokenizer:
    def apply_chat_template(self, _messages, tokenize=False, add_generation_prompt=True):
        return "prompt"


def test_gemma4_load_retries_mlx_lm_when_mlx_vlm_rejects_unified_metadata(monkeypatch):
    calls = []

    def fake_vlm_load(model_id):
        calls.append(("mlx_vlm", model_id))
        raise ModuleNotFoundError("No module named mlx_vlm.speculative.drafters.gemma4_unified")

    def fake_lm_load(model_id):
        calls.append(("mlx_lm", model_id))
        return object(), _FakeTokenizer()

    monkeypatch.setattr(router_mod, "mx", _FakeMx())
    monkeypatch.setattr(router_mod, "vlm_load", fake_vlm_load)
    monkeypatch.setattr(router_mod, "lm_load", fake_lm_load)
    monkeypatch.setattr(router_mod, "VLM_AVAILABLE", True)
    monkeypatch.setattr(router_mod, "LM_AVAILABLE", True)

    llm = router_mod.LLMRouter()
    result = asyncio.run(llm.load_model("mlx-community/gemma-4-12b-it-4bit"))

    assert result == "Success: mlx-community/gemma-4-12b-it-4bit (mlx_lm)"
    assert [runtime for runtime, _model_id in calls] == ["mlx_vlm", "mlx_lm"]
    assert calls[0][1] == calls[1][1]
    assert "gemma-4-12b-it-4bit" in calls[0][1]
    assert llm._cache["mlx-community/gemma-4-12b-it-4bit"][3] == "mlx_lm"
