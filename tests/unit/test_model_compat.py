"""Unit tests for latticeai.core.model_compat (피드백 #3)."""

from latticeai.core.model_compat import (
    detect_model_family,
    ensure_profile,
    fast_postprocess,
    validate_smoke_response,
    record_smoke_result,
    list_cached_profiles,
    strip_role_tokens,
)


def test_detect_family_basic():
    assert detect_model_family("gpt-oss-20b") == "gpt-oss"
    assert detect_model_family("ollama:gpt-oss:20b") == "gpt-oss"
    assert detect_model_family("mlx-community/gemma-2-9b-it-4bit") == "gemma"
    assert detect_model_family("Qwen/Qwen2.5-Coder-32B-Instruct") == "qwen"
    assert detect_model_family("meta-llama/Llama-3.2-1B") == "llama"
    assert detect_model_family("something-unknown-xyz") == "unknown"


def test_ensure_profile_caches_per_engine():
    p1 = ensure_profile("mlx-community/gpt-oss-20b-MXFP4-Q8", "local_mlx")
    assert p1.family == "gpt-oss"
    assert p1.disable_draft is True
    assert any(stop.startswith("<|im_end") for stop in p1.stop)

    p2 = ensure_profile("mlx-community/gpt-oss-20b-MXFP4-Q8", "local_mlx")
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
    profile = ensure_profile("openai/gpt-oss-20b", "ollama")
    cleaned = fast_postprocess(
        "<|im_start|>assistant: 답은 4입니다.<|im_end|>\n<|user|> 다음",
        profile.to_dict(),
    )
    assert "<|im_start|>" not in cleaned
    assert "다음" not in cleaned  # trim_after_user_marker


def test_list_cached_profiles_returns_dicts():
    record_smoke_result("model-z", "ollama", True, "ok")
    items = list_cached_profiles()
    assert any(item.get("model_id") == "model-z" for item in items)
