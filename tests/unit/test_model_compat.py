"""Unit tests for latticeai.core.model_compat (피드백 #3)."""

from latticeai.core.model_compat import (
    detect_model_family,
    ensure_profile,
    fast_postprocess,
    friendly_model_runtime_error,
    model_runtime_compatibility,
    validate_smoke_response,
    classify_smoke_response,
    record_smoke_result,
    list_cached_profiles,
    strip_role_tokens,
)


def test_detect_family_basic():
    assert detect_model_family("mlx-community/gemma-4-12b-it-4bit") == "gemma"
    assert detect_model_family("Qwen/Qwen3-VL-8B-Instruct") == "qwen"
    assert detect_model_family("meta-llama/Llama-4-Scout-17B-16E-Instruct") == "llama"
    assert detect_model_family("something-unknown-xyz") == "unknown"


def test_ensure_profile_caches_per_engine():
    p1 = ensure_profile("mlx-community/gemma-4-12b-it-4bit", "local_mlx")
    assert p1.family == "gemma"
    assert p1.supports_vision is True
    assert any(stop == "<end_of_turn>" for stop in p1.stop)

    p2 = ensure_profile("mlx-community/gemma-4-12b-it-4bit", "local_mlx")
    assert p2 is p1  # cached


def test_strip_role_tokens_removes_special_markers():
    raw = "<|im_start|>assistant: 답은 4입니다.<|im_end|>"
    assert "답은 4입니다." in strip_role_tokens(raw)
    assert "<|im_start|>" not in strip_role_tokens(raw)


def test_validate_smoke_response_ok():
    ok, reason = validate_smoke_response("2+2는 4입니다.")
    assert ok
    assert reason == "ok"


def test_validate_smoke_response_role_token_leakage():
    ok, reason = validate_smoke_response("<|im_end|> something")
    assert not ok
    assert "role token leakage" in reason


def test_validate_smoke_response_empty():
    ok, reason = validate_smoke_response("")
    assert not ok
    assert reason == "empty response"


def test_record_smoke_result_updates_cache():
    profile = record_smoke_result("model-a", "local_mlx", True, "ok")
    assert profile.chat_compatible is True
    assert profile.quality_status == "ok"
    assert profile.loaded is True

    profile2 = record_smoke_result("model-a", "local_mlx", False, "role token leakage")
    assert profile2.chat_compatible is False
    assert profile2.quality_status == "degraded"
    assert profile2.last_test_error == "role token leakage"


def test_fast_postprocess_uses_profile_steps():
    profile = ensure_profile("Qwen/Qwen3-VL-8B-Instruct", "ollama")
    cleaned = fast_postprocess(
        "<|im_start|>assistant: 답은 4입니다.<|im_end|>\n<|user|> 다음",
        profile.to_dict(),
    )
    assert "<|im_start|>" not in cleaned
    assert "<|im_end|>" not in cleaned


def test_classify_smoke_ok():
    status, reason = classify_smoke_response("2+2는 4입니다.")
    assert status == "ok"
    assert reason == "ok"


def test_classify_smoke_failed_on_role_token():
    status, reason = classify_smoke_response("<|im_end|> 어쩌고")
    assert status == "failed"
    assert "leakage" in reason


def test_classify_smoke_failed_on_severe_repetition():
    status, _ = classify_smoke_response("같은 문장. " * 6)
    assert status == "failed"


def test_classify_smoke_failed_on_runaway_repetition():
    status, _ = classify_smoke_response("안녕" * 30)
    assert status == "failed"


def test_classify_smoke_failed_on_too_long():
    status, _ = classify_smoke_response("4 " + "가" * 5000)
    assert status == "failed"


def test_classify_smoke_degraded_when_no_answer():
    # 형식은 멀쩡하지만 기대한 정답(4/네/사)이 없음 → degraded.
    status, reason = classify_smoke_response("잘 모르겠어요.")
    assert status == "degraded"
    assert "expected result" in reason


def test_validate_wrapper_treats_degraded_as_chattable():
    ok, _ = validate_smoke_response("잘 모르겠어요.")
    assert ok is True  # degraded 도 채팅 가능


def test_record_smoke_result_explicit_failed_status():
    profile = record_smoke_result("model-f", "local_mlx", False, "severe repetition", status="failed")
    assert profile.chat_compatible is False
    assert profile.quality_status == "failed"


def test_list_cached_profiles_returns_dicts():
    record_smoke_result("model-z", "ollama", True, "ok")
    items = list_cached_profiles()
    assert any(item.get("model_id") == "model-z" for item in items)


def test_gemma4_12b_unified_requires_runtime_update(monkeypatch):
    def fake_find_spec(name):
        if name in {"mlx", "mlx_vlm", "mlx_lm", "mlx_lm.models.gemma4"}:
            return object()
        return None

    monkeypatch.setattr("latticeai.core.model_compat.importlib.util.find_spec", fake_find_spec)
    monkeypatch.setattr("latticeai.core.model_compat._local_model_type", lambda _model_id: "gemma4_unified")
    result = model_runtime_compatibility("mlx-community/gemma-4-12b-it-4bit", "local_mlx")

    assert result["supported"] is False
    assert result["status"] == "runtime_update_needed"
    assert result["action"] == "Runtime update needed"
    assert result["missing_components"] == ["mlx_vlm.models.gemma4_unified"]
    assert result["alternatives"]


def test_gemma4_26b_standard_is_supported_without_unified_module(monkeypatch):
    def fake_find_spec(name):
        if name in {"mlx", "mlx_vlm", "mlx_lm", "mlx_lm.models.gemma4"}:
            return object()
        return None

    monkeypatch.setattr("latticeai.core.model_compat.importlib.util.find_spec", fake_find_spec)
    monkeypatch.setattr("latticeai.core.model_compat._local_model_type", lambda _model_id: "gemma4")
    result = model_runtime_compatibility("mlx-community/gemma-4-26b-a4b-it-4bit", "local_mlx")

    assert result["supported"] is True
    assert result["status"] == "supported"
    assert result["preferred_runtime"] == "MLX-VLM"


def test_gemma4_runtime_error_is_friendly(monkeypatch):
    def fake_find_spec(name):
        if name in {"mlx", "mlx_vlm", "mlx_lm", "mlx_lm.models.gemma4_text"}:
            return object()
        return None

    monkeypatch.setattr("latticeai.core.model_compat.importlib.util.find_spec", fake_find_spec)
    monkeypatch.setattr("latticeai.core.model_compat._local_model_type", lambda _model_id: "gemma4_unified")
    detail = friendly_model_runtime_error(
        "Model type gemma4_unified not supported.",
        model_id="mlx-community/gemma-4-12b-it-4bit",
        engine="local_mlx",
    )

    assert detail["status"] == "runtime_update_needed"
    assert "No module named" not in detail["user_message"]
    assert detail["recovery_guidance"]
    assert detail["alternatives"]


def test_gemma4_unified_error_without_model_id_is_runtime_update(monkeypatch):
    def fake_find_spec(name):
        if name in {"mlx", "mlx_vlm", "mlx_lm", "mlx_lm.models.gemma4"}:
            return object()
        return None

    monkeypatch.setattr("latticeai.core.model_compat.importlib.util.find_spec", fake_find_spec)

    detail = friendly_model_runtime_error("Model type gemma4_unified not supported.", engine="local_mlx")

    assert detail["status"] == "runtime_update_needed"
    assert detail["reason_code"] == "mlx_vlm_missing_gemma4_unified_model"
    assert detail["missing_components"] == ["mlx_vlm.models.gemma4_unified"]
