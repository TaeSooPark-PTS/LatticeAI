"""Unit tests for cross-platform oMLX / GGUF / Ollama / llama.cpp setup and Qwen3.8-27B-4bit."""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from latticeai.core.model_resolution import ModelResolution
from latticeai.models.model_providers import OPENAI_COMPATIBLE_PROVIDERS
from latticeai.services.embedding_recommendation import get_public_mirror_repos
from latticeai.services.engine_adapters import (
    ENGINE_ADAPTERS,
    default_local_engine_for_host,
    host_available_ram_gb,
    llamacpp_serve_args,
    omlx_serve_args,
    resolve_engine_installer,
    windows_binary_candidates,
)
from latticeai.services.model_capability_registry import get_capability
from latticeai.services.model_catalog import (
    ENGINE_INSTALLERS,
    ENGINE_MODEL_CATALOG,
    MODEL_ENGINE_ALIASES,
)
from latticeai.services.model_errors import ModelRuntimeError
from latticeai.services.model_loading import _get_model_runtime_deps
from latticeai.services.model_runtime import ModelRuntimeState
from latticeai.services.model_runtime import loading as loading_mod
from latticeai.services.model_runtime import status as status_mod
from latticeai.services.model_runtime.download import (
    _call_hf_hub_download,
    hf_model_ready,
)


def test_omlx_registered_in_providers_adapters_and_installers() -> None:
    assert "omlx" in OPENAI_COMPATIBLE_PROVIDERS
    assert (
        OPENAI_COMPATIBLE_PROVIDERS["omlx"]["default_model"]
        == "mlx-community--Qwen3.8-27B-4bit"
    )
    assert "omlx" in ENGINE_INSTALLERS
    assert (
        ENGINE_ADAPTERS["omlx"].to_served_model_id("mlx-community/Qwen3.8-27B-4bit")
        == "mlx-community--Qwen3.8-27B-4bit"
    )
    assert (
        ENGINE_ADAPTERS["omlx"].to_hf_repo_id("mlx-community--Qwen3.8-27B-4bit")
        == "mlx-community/Qwen3.8-27B-4bit"
    )
    deps = _get_model_runtime_deps(ModelRuntimeState())
    assert callable(deps["ensure_omlx_server"])


def test_qwen38_27b_in_all_cross_platform_catalogs_and_mirrors() -> None:
    mlx_ids = {m["id"] for m in ENGINE_MODEL_CATALOG["local_mlx"]}
    omlx_ids = {m["id"] for m in ENGINE_MODEL_CATALOG["omlx"]}
    ollama_ids = {m["id"] for m in ENGINE_MODEL_CATALOG["ollama"]}
    llamacpp_ids = {m["id"] for m in ENGINE_MODEL_CATALOG["llamacpp"]}
    assert "mlx-community/Qwen3.8-27B-4bit" in mlx_ids
    assert "omlx:mlx-community--Qwen3.8-27B-4bit" in omlx_ids
    assert "ollama:hf.co/ggml-org/Qwen3.8-27B-GGUF:Q4_K_M" in ollama_ids
    assert "llamacpp:ggml-org/Qwen3.8-27B-GGUF" in llamacpp_ids

    cap = get_capability("mlx-community/Qwen3.8-27B-4bit")
    assert cap is not None
    assert cap.hardware.recommended_ram_gb == 32.0
    assert len(cap.provider_hints) == len(set(cap.provider_hints))
    assert get_public_mirror_repos("ggml-org/Qwen3.8-27B-GGUF", "llamacpp") == [
        "unsloth/Qwen3.8-27B-GGUF"
    ]


def test_omlx_serve_args_tuned_by_ram_memory_pressure_and_env(monkeypatch) -> None:
    args_16 = omlx_serve_args(ram_gb=16.0, port=8000)
    assert args_16[args_16.index("--memory-guard") + 1] == "safe"
    assert args_16[args_16.index("--hot-cache-max-size") + 1] == "0"
    assert args_16[args_16.index("--max-concurrent-requests") + 1] == "2"

    args_32 = omlx_serve_args(ram_gb=32.0, port=18090)
    assert args_32[args_32.index("--port") + 1] == "18090"
    assert args_32[args_32.index("--memory-guard") + 1] == "balanced"
    assert args_32[args_32.index("--hot-cache-max-size") + 1] == "2GB"
    assert args_32[args_32.index("--max-concurrent-requests") + 1] == "2"

    # Concurrent-app memory pressure on 32GB Mac (only 17GB reclaimable for 16.1GB 27B weights)
    args_tight = omlx_serve_args(ram_gb=32.0, available_ram_gb=17.0, model_size_gb=16.1)
    assert args_tight[args_tight.index("--memory-guard") + 1] == "safe"
    assert args_tight[args_tight.index("--hot-cache-max-size") + 1] == "1GB"
    assert args_tight[args_tight.index("--max-concurrent-requests") + 1] == "1"

    args_64 = omlx_serve_args(ram_gb=64.0, port=8000)
    assert args_64[args_64.index("--hot-cache-max-size") + 1] == "4GB"
    assert args_64[args_64.index("--max-concurrent-requests") + 1] == "4"

    monkeypatch.setenv("LATTICEAI_OMLX_HOT_CACHE_MAX_SIZE", "6GB")
    monkeypatch.setenv("LATTICEAI_OMLX_MEMORY_GUARD", "safe")
    args_env = omlx_serve_args(ram_gb=32.0)
    assert args_env[args_env.index("--hot-cache-max-size") + 1] == "6GB"
    assert args_env[args_env.index("--memory-guard") + 1] == "safe"

    llama_args = llamacpp_serve_args(
        Path("/tmp/qwen3.8.gguf"), "ggml-org/Qwen3.8-27B-GGUF", ram_gb=32.0
    )
    assert "--cache-type-k" in llama_args
    assert llama_args[llama_args.index("-c") + 1] == "16384"
    assert isinstance(host_available_ram_gb(32.0), float)


def test_cross_platform_installers_for_macos_windows_and_linux() -> None:
    mac_sys = types.SimpleNamespace(platform="darwin", executable="/usr/bin/python3")
    mac_plat = types.SimpleNamespace(machine=lambda: "arm64")
    win_sys = types.SimpleNamespace(
        platform="win32", executable="C:\\Python\\python.exe"
    )
    win_plat = types.SimpleNamespace(machine=lambda: "AMD64")
    lin_sys = types.SimpleNamespace(platform="linux", executable="/usr/bin/python3")
    lin_plat = types.SimpleNamespace(machine=lambda: "x86_64")

    # macOS oMLX: brew when available, GitHub release universal2 wheel fallback when brew is absent
    cmd_brew = resolve_engine_installer(
        "omlx",
        ENGINE_INSTALLERS,
        sys_mod=mac_sys,
        platform_mod=mac_plat,
        which_fn=lambda b: "/opt/homebrew/bin/brew" if b == "brew" else None,
    )
    assert "brew install jundot/omlx/omlx" in cmd_brew[2]
    cmd_wheel = resolve_engine_installer(
        "omlx",
        ENGINE_INSTALLERS,
        sys_mod=mac_sys,
        platform_mod=mac_plat,
        which_fn=lambda _b: None,
    )
    assert "jundot/omlx/releases/latest" in cmd_wheel[2]

    # Windows: winget for Ollama & LM Studio & llama.cpp, pip fallback for llama.cpp
    cmd_win_ollama = resolve_engine_installer(
        "ollama",
        ENGINE_INSTALLERS,
        sys_mod=win_sys,
        platform_mod=win_plat,
        which_fn=lambda b: "C:\\winget.exe" if b == "winget" else None,
    )
    assert cmd_win_ollama[:4] == ["winget", "install", "--id", "Ollama.Ollama"]
    cmd_win_llama_pip = resolve_engine_installer(
        "llamacpp",
        ENGINE_INSTALLERS,
        sys_mod=win_sys,
        platform_mod=win_plat,
        which_fn=lambda _b: None,
    )
    assert "llama-cpp-python[server]" in cmd_win_llama_pip
    cmd_win_lms = resolve_engine_installer(
        "lmstudio",
        ENGINE_INSTALLERS,
        sys_mod=win_sys,
        platform_mod=win_plat,
        which_fn=lambda b: "C:\\winget.exe" if b == "winget" else None,
    )
    assert "ElementLabs.LMStudio" in cmd_win_lms

    with pytest.raises(ModelRuntimeError):
        resolve_engine_installer(
            "omlx",
            ENGINE_INSTALLERS,
            sys_mod=win_sys,
            platform_mod=win_plat,
            which_fn=lambda _b: None,
        )

    # Linux: curl script for Ollama, pip fallback for llama.cpp
    cmd_lin_ollama = resolve_engine_installer(
        "ollama",
        ENGINE_INSTALLERS,
        sys_mod=lin_sys,
        platform_mod=lin_plat,
        which_fn=lambda b: "/usr/bin/curl" if b == "curl" else None,
    )
    assert "ollama.com/install.sh" in cmd_lin_ollama[2]
    cmd_lin_llama = resolve_engine_installer(
        "llamacpp",
        ENGINE_INSTALLERS,
        sys_mod=lin_sys,
        platform_mod=lin_plat,
        which_fn=lambda _b: None,
    )
    assert "llama-cpp-python[server]" in cmd_lin_llama

    win_bins = windows_binary_candidates(
        "llama-server",
        env={
            "LOCALAPPDATA": "C:\\Users\\u\\AppData\\Local",
            "ProgramFiles": "C:\\Program Files",
        },
    )
    assert any(p.name == "llama-server.exe" for p in win_bins)


def test_cross_platform_one_way_model_routing_and_xet_download(
    tmp_path, monkeypatch
) -> None:
    # Non-Apple-Silicon hosts automatically remap Qwen3.8-27B-4bit onto Ollama or installed llama.cpp
    monkeypatch.setattr(loading_mod, "is_apple_silicon", lambda: False)
    monkeypatch.setattr(loading_mod, "engine_installed", lambda eng: eng == "llamacpp")
    routed_id, routed_eng = loading_mod.resolve_cross_platform_model_request(
        "mlx-community/Qwen3.8-27B-4bit", None
    )
    assert routed_eng == "llamacpp"
    assert routed_id == "llamacpp:ggml-org/Qwen3.8-27B-GGUF"

    monkeypatch.setattr(loading_mod, "engine_installed", lambda _eng: False)
    routed_id2, routed_eng2 = loading_mod.resolve_cross_platform_model_request(
        "omlx:mlx-community--Qwen3.8-27B-4bit", "omlx"
    )
    assert routed_eng2 == "ollama"
    assert routed_id2 == "ollama:hf.co/ggml-org/Qwen3.8-27B-GGUF:Q4_K_M"

    assert (
        default_local_engine_for_host(
            engine_installed_fn=lambda e: e == "omlx",
            sys_mod=types.SimpleNamespace(platform="darwin"),
            platform_mod=types.SimpleNamespace(machine=lambda: "arm64"),
        )
        == "omlx"
    )

    # Auto confirmation token wiring for one-way consented engine install
    captured = {}
    monkeypatch.setattr(
        status_mod,
        "_safe_engine_install_plan",
        lambda *a, **k: {"confirmation_token": "tok-123"},
    )
    monkeypatch.setattr(
        status_mod,
        "_install_engine",
        lambda eng, confirmation_token=None, base_dir=None: (
            captured.update({"token": confirmation_token}) or {"ok": True}
        ),
    )
    status_mod.install_engine("omlx", state=ModelRuntimeState())
    assert captured["token"] == "tok-123"

    # HF Xet high-performance env enabled automatically on download
    monkeypatch.delenv("HF_XET_HIGH_PERFORMANCE", raising=False)
    _call_hf_hub_download(
        lambda **kw: str(tmp_path / kw["filename"]),
        repo_id="r",
        filename="f",
        local_dir=str(tmp_path),
        tqdm_class=None,
    )
    import os

    assert os.environ.get("HF_XET_HIGH_PERFORMANCE") == "1"


def test_omlx_model_resolution_and_hf_cache_normalization(
    tmp_path, monkeypatch
) -> None:
    res = ModelResolution.from_request(
        "mlx-community/Qwen3.8-27B-4bit",
        engine="omlx",
        engine_aliases=MODEL_ENGINE_ALIASES,
    )
    assert res.provider == "omlx"
    assert res.resolved_model == "mlx-community--Qwen3.8-27B-4bit"

    fake_home = tmp_path / "home"
    snap = (
        fake_home
        / ".cache"
        / "huggingface"
        / "hub"
        / "models--mlx-community--Qwen3.8-27B-4bit"
        / "snapshots"
        / "rev1"
    )
    snap.mkdir(parents=True)
    (snap / "config.json").write_text('{"model_type":"qwen3_5"}', encoding="utf-8")
    (snap / "model.safetensors").write_bytes(b"weights")
    (snap / "tokenizer.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    assert hf_model_ready("mlx-community--Qwen3.8-27B-4bit", "omlx") is True
