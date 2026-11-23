"""Unit tests for oMLX engine integration and Qwen3.8-27B-4bit catalog/tuning."""

from __future__ import annotations

from pathlib import Path

from latticeai.core.model_resolution import ModelResolution
from latticeai.models.model_providers import OPENAI_COMPATIBLE_PROVIDERS
from latticeai.services.model_catalog import (
    ENGINE_INSTALLERS,
    ENGINE_MODEL_CATALOG,
    MODEL_ENGINE_ALIASES,
    recommend_models_for_pc,
)
from latticeai.services.model_engines import omlx_serve_args
from latticeai.services.model_runtime.download import hf_model_ready


def test_omlx_registered_in_providers_and_installers() -> None:
    assert "omlx" in OPENAI_COMPATIBLE_PROVIDERS
    assert OPENAI_COMPATIBLE_PROVIDERS["omlx"]["default_model"] == "mlx-community--Qwen3.8-27B-4bit"
    assert "omlx" in ENGINE_INSTALLERS


def test_qwen38_27b_in_recommended_and_download_catalogs() -> None:
    mlx_ids = {m["id"] for m in ENGINE_MODEL_CATALOG["local_mlx"]}
    omlx_ids = {m["id"] for m in ENGINE_MODEL_CATALOG["omlx"]}
    assert "mlx-community/Qwen3.8-27B-4bit" in mlx_ids
    assert "omlx:mlx-community--Qwen3.8-27B-4bit" in omlx_ids

    for engine, expected_id in (
        ("local_mlx", "mlx-community/Qwen3.8-27B-4bit"),
        ("omlx", "omlx:mlx-community--Qwen3.8-27B-4bit"),
    ):
        rec = recommend_models_for_pc(ram_gb=32.0, apple_silicon=True, engine=engine)
        matches = [m for m in rec["models"] if m["id"] == expected_id]
        assert len(matches) == 1
        assert matches[0]["compatibility"]["status"] == "recommended"


def test_omlx_serve_args_tuned_by_ram_and_env(monkeypatch) -> None:
    args_16 = omlx_serve_args(ram_gb=16.0, port=8000)
    assert "--memory-guard" in args_16
    assert args_16[args_16.index("--memory-guard") + 1] == "safe"
    assert args_16[args_16.index("--hot-cache-max-size") + 1] == "0"
    assert args_16[args_16.index("--max-concurrent-requests") + 1] == "2"

    args_32 = omlx_serve_args(ram_gb=32.0, port=18090)
    assert args_32[args_32.index("--port") + 1] == "18090"
    assert args_32[args_32.index("--memory-guard") + 1] == "balanced"
    assert args_32[args_32.index("--hot-cache-max-size") + 1] == "2GB"
    assert args_32[args_32.index("--max-concurrent-requests") + 1] == "2"

    args_64 = omlx_serve_args(ram_gb=64.0, port=8000)
    assert args_64[args_64.index("--hot-cache-max-size") + 1] == "4GB"
    assert args_64[args_64.index("--max-concurrent-requests") + 1] == "4"

    monkeypatch.setenv("LATTICEAI_OMLX_HOT_CACHE_MAX_SIZE", "6GB")
    monkeypatch.setenv("LATTICEAI_OMLX_MEMORY_GUARD", "safe")
    args_env = omlx_serve_args(ram_gb=32.0)
    assert args_env[args_env.index("--hot-cache-max-size") + 1] == "6GB"
    assert args_env[args_env.index("--memory-guard") + 1] == "safe"


def test_omlx_model_resolution_and_hf_cache_normalization(tmp_path, monkeypatch) -> None:
    res = ModelResolution.from_request(
        "mlx-community/Qwen3.8-27B-4bit",
        engine="omlx",
        engine_aliases=MODEL_ENGINE_ALIASES,
    )
    assert res.provider == "omlx"
    assert res.resolved_model == "mlx-community--Qwen3.8-27B-4bit"

    fake_home = tmp_path / "home"
    snap = fake_home / ".cache" / "huggingface" / "hub" / "models--mlx-community--Qwen3.8-27B-4bit" / "snapshots" / "rev1"
    snap.mkdir(parents=True)
    (snap / "config.json").write_text('{"model_type":"qwen3_5"}', encoding="utf-8")
    (snap / "model.safetensors").write_bytes(b"weights")
    (snap / "tokenizer.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    assert hf_model_ready("mlx-community--Qwen3.8-27B-4bit", "omlx") is True
