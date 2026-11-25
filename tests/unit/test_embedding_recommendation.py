from __future__ import annotations

from latticeai.services.embedding_recommendation import (
    DEFAULT_HF_ENDPOINT,
    build_gated_fallback_metadata,
    get_public_mirror_repos,
    has_hf_auth_token,
    recommend_embedding_for_hardware,
    resolve_hf_endpoint,
    select_public_fallback_repo,
)


def test_has_hf_auth_token_and_resolve_hf_endpoint():
    assert has_hf_auth_token({}) is False
    assert has_hf_auth_token({"HF_TOKEN": "   "}) is False
    assert has_hf_auth_token({"HF_TOKEN": "hf_abc123"}) is True
    assert has_hf_auth_token({"HUGGING_FACE_HUB_TOKEN": "hf_xyz"}) is True
    assert has_hf_auth_token({"HUGGINGFACE_TOKEN": "hf_legacy"}) is True

    assert resolve_hf_endpoint({}) == DEFAULT_HF_ENDPOINT
    assert (
        resolve_hf_endpoint({"HF_ENDPOINT": "https://hf-mirror.com/"})
        == "https://hf-mirror.com"
    )
    assert (
        resolve_hf_endpoint(
            {"HUGGINGFACE_HUB_ENDPOINT": "https://custom.mirror.local/"}
        )
        == "https://custom.mirror.local"
    )


def test_public_mirrors_and_fallback_selection_for_embeddinggemma():
    mirrors_v2 = get_public_mirror_repos("google/embeddinggemma-2", "local_mlx")
    assert "mlx-community/embeddinggemma-2-4bit" in mirrors_v2
    assert "mlx-community/embeddinggemma-2-8bit" in mirrors_v2
    assert "ggml-org/embeddinggemma-2-GGUF" in mirrors_v2
    assert "onnx-community/embeddinggemma-300m-ONNX" in mirrors_v2
    assert mirrors_v2[0] == "mlx-community/embeddinggemma-2-4bit"

    mirrors_gguf = get_public_mirror_repos("google/embeddinggemma-2", "llamacpp")
    assert mirrors_gguf[0] == "ggml-org/embeddinggemma-2-GGUF"

    mirrors_onnx = get_public_mirror_repos("google/embeddinggemma-2", "onnx")
    assert mirrors_onnx[0] == "onnx-community/embeddinggemma-300m-ONNX"

    assert (
        select_public_fallback_repo("google/embeddinggemma-2", "local_mlx")
        == "mlx-community/embeddinggemma-2-4bit"
    )
    assert (
        select_public_fallback_repo("google/embeddinggemma-2", "llamacpp")
        == "ggml-org/embeddinggemma-2-GGUF"
    )
    assert (
        select_public_fallback_repo("google/embeddinggemma-300m", "onnx")
        == "onnx-community/embeddinggemma-300m-ONNX"
    )
    assert get_public_mirror_repos("org/unrelated-model", "local_mlx") == []
    assert select_public_fallback_repo("org/unrelated-model", "local_mlx") is None
    assert (
        select_public_fallback_repo(
            "google/embeddinggemma-2", "local_mlx", prefer_8bit=True
        )
        == "mlx-community/embeddinggemma-2-8bit"
    )


def test_build_gated_fallback_metadata_unauthenticated_vs_authenticated():
    unauth = build_gated_fallback_metadata(
        "google/embeddinggemma-2",
        provider="local_mlx",
        env={"HF_ENDPOINT": "https://hf-mirror.com"},
    )
    assert unauth["primary_repo"] == "google/embeddinggemma-2"
    assert unauth["requires_hf_auth"] is True
    assert unauth["authenticated"] is False
    assert unauth["active"] is True
    assert unauth["fallback_repo"] == "mlx-community/embeddinggemma-2-4bit"
    assert unauth["hf_endpoint"] == "https://hf-mirror.com"
    assert unauth["custom_mirror_active"] is True
    assert "ggml-org/embeddinggemma-2-GGUF" in unauth["public_mirror_repos"]
    assert unauth["quantized_distributions"]["gguf"] == "ggml-org/embeddinggemma-2-GGUF"

    authed = build_gated_fallback_metadata(
        "google/embeddinggemma-2",
        provider="llamacpp",
        env={"HF_TOKEN": "hf_valid_token"},
    )
    assert authed["authenticated"] is True
    assert authed["active"] is False
    assert authed["fallback_repo"] == "ggml-org/embeddinggemma-2-GGUF"
    assert authed["hf_endpoint"] == DEFAULT_HF_ENDPOINT
    assert authed["custom_mirror_active"] is False


def test_recommend_embedding_for_hardware_includes_mirrors_and_fallback(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
    monkeypatch.setenv("HF_ENDPOINT", "https://hf-mirror.com")

    rec_high = recommend_embedding_for_hardware(ram_gb=16.0, apple_silicon=True)
    assert rec_high["id"] == "google/embeddinggemma-2"
    assert rec_high["mlx_repo_id"] == "mlx-community/embeddinggemma-2-4bit"
    assert rec_high["mlx_8bit_repo_id"] == "mlx-community/embeddinggemma-2-8bit"
    assert rec_high["gguf_repo_id"] == "ggml-org/embeddinggemma-2-GGUF"
    assert rec_high["onnx_repo_id"] == "onnx-community/embeddinggemma-300m-ONNX"
    assert "mlx-community/embeddinggemma-2-4bit" in rec_high["public_mirror_repos"]
    assert "ggml-org/embeddinggemma-2-GGUF" in rec_high["public_mirror_repos"]
    assert rec_high["gated_fallback"]["active"] is True
    assert rec_high["gated_fallback"]["hf_endpoint"] == "https://hf-mirror.com"

    rec_low = recommend_embedding_for_hardware(ram_gb=4.0, apple_silicon=False)
    assert rec_low["id"] == "google/embeddinggemma-300m"
    assert rec_low["gguf_repo_id"] == "ggml-org/embeddinggemma-300m-GGUF"
    assert rec_low["onnx_repo_id"] == "onnx-community/embeddinggemma-300m-ONNX"
    assert rec_low["gated_fallback"]["active"] is True
    assert (
        rec_low["gated_fallback"]["fallback_repo"]
        == "mlx-community/embeddinggemma-300m-4bit"
    )
