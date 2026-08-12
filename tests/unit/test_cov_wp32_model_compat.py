"""wp32 coverage — model compatibility: family detection, the Gemma 4 runtime
signal, output post-processing, and smoke-test classification.

Every runtime probe goes through ``module_probe.module_available``, whose
``importlib.util.find_spec`` is patched
per test: the MLX stack is darwin-only, so a test that asked the real
interpreter would pass on a laptop and skip on CI. Patching the probe makes
each branch execute identically everywhere.
"""

from __future__ import annotations

import json

import pytest

from latticeai.core import model_compat, module_probe
from latticeai.core.model_compat import (
    DEFAULT_STOP,
    _local_model_type,
    _module_available,
    classify_smoke_response,
    detect_model_family,
    fast_postprocess,
    friendly_model_runtime_error,
    get_stop_sequences,
    model_runtime_compatibility,
    normalize_generation_params,
    strip_role_tokens,
    trim_after_user_marker,
    validate_smoke_response,
)


def _find_spec(*available):
    installed = set(available)

    def fake_find_spec(name):
        return object() if name in installed else None

    return fake_find_spec


# ── family detection ────────────────────────────────────────────────────────


def test_family_detection_handles_empty_ids_and_provider_prefixes():
    assert detect_model_family("") == "unknown"
    assert detect_model_family("ollama:meta-llama-3.1-8b") == "llama"
    assert detect_model_family("local_mlx:gemma-4-12b-it") == "gemma"
    assert detect_model_family("some-unlabelled-model") == "unknown"


# ── module probing / local model type ───────────────────────────────────────


def test_module_probe_reports_false_for_a_module_whose_parent_is_missing():
    assert _module_available("no_such_parent_for_wp32.child") is False
    assert _module_available("json") is True


def test_local_model_type_reads_an_explicit_directory_config(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"model_type": "Gemma4_Unified"}), encoding="utf-8",
    )

    assert _local_model_type(str(tmp_path)) == "gemma4_unified"


def test_local_model_type_survives_an_unreadable_config(tmp_path):
    (tmp_path / "config.json").write_text("{ not json", encoding="utf-8")

    assert _local_model_type(str(tmp_path)) is None


# ── Gemma 4 runtime compatibility ───────────────────────────────────────────


def test_compatibility_normalizes_the_engine_and_strips_the_load_prefix(monkeypatch):
    monkeypatch.setattr(module_probe.importlib.util, "find_spec", _find_spec())
    monkeypatch.setattr(model_compat, "_local_model_type", lambda _model_id: None)

    payload = model_runtime_compatibility("mlx:mlx-community/gemma-4-12b-it-4bit", "mlx")

    assert payload["engine"] == "local_mlx"
    assert payload["model_id"] == "mlx-community/gemma-4-12b-it-4bit"
    assert payload["family"] == "gemma"


def test_compatibility_reports_runtime_not_installed_and_offers_gguf(monkeypatch):
    monkeypatch.setattr(module_probe.importlib.util, "find_spec", _find_spec())
    monkeypatch.setattr(model_compat, "_local_model_type", lambda _model_id: None)

    payload = model_runtime_compatibility("mlx-community/gemma-4-12b-it-4bit", "local_mlx")

    assert payload["status"] == "runtime_not_installed"
    assert payload["checked"] is False
    assert payload["supported"] is True
    assert "Install the local MLX runtime" in payload["user_message"]
    assert [alt["engine"] for alt in payload["alternatives"]] == [
        "ollama", "lmstudio", "llamacpp",
    ]


def test_compatibility_falls_back_to_mlx_lm_when_only_vlm_is_missing(monkeypatch):
    monkeypatch.setattr(
        module_probe.importlib.util, "find_spec",
        _find_spec("mlx", "mlx_lm", "mlx_lm.models.gemma4"),
    )
    monkeypatch.setattr(model_compat, "_local_model_type", lambda _model_id: "gemma4")

    payload = model_runtime_compatibility("mlx-community/gemma-4-26b-a4b-it-4bit", "local_mlx")

    assert payload["status"] == "fallback_available"
    assert payload["supported"] is True
    assert payload["reason_code"] == "mlx_vlm_missing_gemma4_standard_runtime"
    assert payload["missing_components"] == ["mlx_vlm"]
    assert payload["preferred_runtime"] == "MLX-LM fallback"
    assert all(alt["name"] != "MLX-VLM" for alt in payload["alternatives"])


def test_a_supported_model_reports_a_plain_load_failure(monkeypatch):
    monkeypatch.setattr(module_probe.importlib.util, "find_spec", _find_spec())

    detail = friendly_model_runtime_error(
        RuntimeError("weights not found"), model_id="ollama:llama3.1", engine="ollama",
    )

    assert detail["status"] == "load_failed"
    assert detail["model_id"] == "ollama:llama3.1"
    assert "could not be loaded" in detail["user_message"]
    assert len(detail["recovery_guidance"]) == 3


# ── post-processing ─────────────────────────────────────────────────────────


def test_role_token_stripping_leaves_empty_text_alone():
    assert strip_role_tokens("") == ""
    assert strip_role_tokens("assistant: <|im_end|>hello") == "hello"


def test_trim_after_user_marker_cuts_the_hallucinated_next_turn():
    assert trim_after_user_marker("") == ""
    assert trim_after_user_marker("답은 4입니다.\nuser: 그럼 5는?") == "답은 4입니다."
    assert trim_after_user_marker("정답.<|user|>다음") == "정답."
    # a marker at position 0 is not a trailing turn, so nothing is cut
    assert trim_after_user_marker("<|user|>only") == "<|user|>only"


def test_fast_postprocess_passes_empty_text_through():
    assert fast_postprocess("", {"postprocess": ["strip_role_tokens"]}) == ""


def test_fast_postprocess_keeps_going_when_a_postprocessor_raises(monkeypatch):
    def boom(_text):
        raise RuntimeError("postprocessor blew up")

    monkeypatch.setitem(model_compat.POSTPROCESSORS, "wp32_boom", boom)

    out = fast_postprocess(
        "assistant: hello", {"postprocess": ["wp32_boom", "strip_role_tokens"]},
    )

    assert out == "hello"


# ── smoke classification ────────────────────────────────────────────────────


def test_smoke_classification_rejects_missing_and_leaking_responses():
    assert classify_smoke_response(None) == ("failed", "empty response")
    assert classify_smoke_response("   ") == ("failed", "empty response")
    assert classify_smoke_response("4 <|weird_token|>") == ("failed", "special token leakage")
    assert classify_smoke_response("assistant: 4") == ("failed", "role marker leakage")


def test_smoke_classification_rejects_an_essay():
    # Distinct sentences, so the only thing wrong with it is the length.
    essay = " ".join("항목 번호 {0} 확인.".format(index) for index in range(1, 400))
    assert len(essay) > 4000

    assert classify_smoke_response(essay) == ("failed", "response too long")


def test_smoke_classification_flags_mild_repetition_as_degraded():
    status, reason = classify_smoke_response("정답은 4. 정답은 4. 정답은 4.")

    assert status == "degraded"
    assert reason == "mild repetition"
    assert validate_smoke_response("정답은 4. 정답은 4. 정답은 4.") == (True, "mild repetition")


def test_smoke_classification_flags_a_long_but_valid_answer_as_degraded():
    text = " ".join("항목 번호 {0} 확인.".format(index) for index in range(1, 60))

    status, reason = classify_smoke_response(text)

    assert status == "degraded"
    assert "response longer than expected" in reason


# ── generation parameters ───────────────────────────────────────────────────


def test_normalize_generation_params_applies_only_non_none_overrides():
    profile = {"temperature": 0.2, "top_p": 0.9, "max_tokens": 2048, "stop_sequences": ["</s>"]}

    defaults = normalize_generation_params(profile)
    overridden = normalize_generation_params(
        profile, {"temperature": 0.7, "max_tokens": None, "stop": ["<|end|>"]},
    )

    assert defaults == {
        "temperature": 0.2, "top_p": 0.9, "max_tokens": 2048, "stop": ["</s>"],
    }
    assert overridden["temperature"] == 0.7
    assert overridden["max_tokens"] == 2048  # None never overrides
    assert overridden["stop"] == ["<|end|>"]


def test_normalize_generation_params_falls_back_to_the_default_stop_list():
    assert normalize_generation_params({})["stop"] == DEFAULT_STOP


@pytest.mark.parametrize(
    ("model_id", "expected_first"),
    [("wp32-unknown-model", DEFAULT_STOP[0]), ("wp32-gemma-4-test", "<end_of_turn>")],
)
def test_get_stop_sequences_reads_the_cached_family_profile(model_id, expected_first):
    stops = get_stop_sequences(model_id, "local_mlx")

    assert stops[0] == expected_first
    # a copy, so a caller cannot mutate the cached profile
    stops.append("mutated")
    assert "mutated" not in get_stop_sequences(model_id, "local_mlx")
