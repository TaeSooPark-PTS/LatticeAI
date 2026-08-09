"""wp05 — ② RECOMMEND and ③ INSTALL-plan of ``latticeai/setup/auto_setup.py``.

``plan`` only ever *builds* commands, and the one function that would run them
(``apply_plan``) is exercised with ``subprocess.run`` replaced, so no installer
is ever launched.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Callable, Dict, List, Optional

import pytest

from latticeai.services.process_audit import CommandConfirmationError
from latticeai.setup import auto_setup


def _which_stub(found: Dict[str, str]) -> Callable[[str], Optional[str]]:
    def _which(binary: str) -> Optional[str]:
        return found.get(binary)

    return _which


def _rec(
    runtime: str = "llama.cpp",
    backend: str = "cpu",
    model_id: str = "Qwen/Qwen3-VL-4B-Instruct",
    quantization: str = "q4_K_M",
) -> auto_setup.Recommendation:
    return auto_setup.Recommendation(
        runtime=runtime,
        backend=backend,
        model_id=model_id,
        quantization=quantization,
        rationale=["fixture"],
    )


# ── ② RECOMMEND ────────────────────────────────────────────────────────────
def test_recommend_apple_silicon_prefers_mlx(monkeypatch: pytest.MonkeyPatch) -> None:
    prof = auto_setup.SystemProfile(
        os="darwin",
        arch="arm64",
        ram_mb=64 * 1024,
        cpu_cores=12,
        gpu=auto_setup.GPUInfo(vendor="apple", model="Apple M3 Max", sdk=["metal", "mlx"]),
    )

    monkeypatch.setattr(auto_setup, "_has_module", lambda name: name == "mlx_vlm")
    rec = auto_setup.recommend(prof)

    assert (rec.backend, rec.runtime) == ("metal+mlx", "mlx")
    assert rec.model_id == "mlx-community/gemma-4-31b-it-4bit"
    assert rec.quantization == "4bit"
    assert rec.estimated_tokens_per_sec == pytest.approx(64 * 0.7)
    assert any("Apple Silicon" in line for line in rec.rationale)
    assert any("최신 멀티모달" in line for line in rec.rationale)
    assert rec.to_json()["model_id"] == rec.model_id

    monkeypatch.setattr(auto_setup, "_has_module", lambda name: False)
    assert auto_setup.recommend(prof).runtime == "llama.cpp"


def test_recommend_linux_cuda_uses_vllm_and_f16(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auto_setup, "_has_module", lambda name: False)
    prof = auto_setup.SystemProfile(
        os="linux",
        ram_mb=64 * 1024,
        cpu_cores=16,
        cuda_available=True,
        gpu=auto_setup.GPUInfo(vendor="nvidia", vram_mb=24 * 1024, sdk=["cuda"]),
    )

    rec = auto_setup.recommend(prof)

    assert (rec.backend, rec.runtime) == ("cuda", "vllm")
    assert rec.quantization == "f16"
    assert rec.model_id == "mlx-community/gemma-4-31b-it-4bit"
    assert rec.estimated_tokens_per_sec == pytest.approx(24 * 1024 / 800)


def test_recommend_wsl_small_cuda_gpu_uses_llama_cpp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auto_setup, "_has_module", lambda name: False)
    prof = auto_setup.SystemProfile(
        os="windows",
        ram_mb=16 * 1024,
        cpu_cores=8,
        cuda_available=True,
        is_wsl=True,
        gpu=auto_setup.GPUInfo(vendor="nvidia", vram_mb=8 * 1024, sdk=["cuda"]),
    )

    rec = auto_setup.recommend(prof)

    assert (rec.backend, rec.runtime) == ("cuda", "llama.cpp")
    assert rec.model_id == "mlx-community/gemma-4-12b-it-4bit"


@pytest.mark.parametrize(
    ("cuda", "tools", "backend", "runtime"),
    [
        (True, {"lms": "lms.exe"}, "cuda", "lmstudio"),
        (False, {"ollama": "ollama.exe"}, "vulkan", "ollama"),
        (False, {}, "vulkan", "llama.cpp"),
    ],
)
def test_recommend_windows_nvidia_prefers_desktop_runtimes(
    monkeypatch: pytest.MonkeyPatch,
    cuda: bool,
    tools: Dict[str, str],
    backend: str,
    runtime: str,
) -> None:
    monkeypatch.setattr(auto_setup, "_has_module", lambda name: False)
    prof = auto_setup.SystemProfile(
        os="windows",
        ram_mb=32 * 1024,
        cpu_cores=8,
        cuda_available=cuda,
        tools=dict(tools),
        gpu=auto_setup.GPUInfo(vendor="nvidia", vram_mb=16 * 1024),
    )

    rec = auto_setup.recommend(prof)

    assert (rec.backend, rec.runtime) == (backend, runtime)
    assert (rec.estimated_tokens_per_sec is None) is (backend != "cuda")
    assert rec.model_id == "mlx-community/gemma-4-26b-a4b-it-4bit"
    assert any("LM Studio/Ollama" in line for line in rec.rationale)


@pytest.mark.parametrize("vendor", ["amd", "intel"])
def test_recommend_windows_amd_intel_uses_directml(
    monkeypatch: pytest.MonkeyPatch, vendor: str
) -> None:
    monkeypatch.setattr(auto_setup, "_has_module", lambda name: False)
    prof = auto_setup.SystemProfile(
        os="windows",
        ram_mb=32 * 1024,
        cpu_cores=8,
        tools={"lms": "lms.exe"} if vendor == "amd" else {},
        gpu=auto_setup.GPUInfo(vendor=vendor, vram_mb=16 * 1024, sdk=["directml", "vulkan"]),
    )

    rec = auto_setup.recommend(prof)

    assert rec.backend == "directml/vulkan"
    assert rec.runtime == ("lmstudio" if vendor == "amd" else "llama.cpp")
    assert rec.estimated_tokens_per_sec is None


@pytest.mark.parametrize(
    ("sdk", "tools", "backend", "runtime"),
    [
        (["rocm", "vulkan"], {"ollama": "/usr/bin/ollama"}, "rocm", "ollama"),
        (["vulkan"], {}, "vulkan", "llama.cpp"),
    ],
)
def test_recommend_linux_amd(
    monkeypatch: pytest.MonkeyPatch,
    sdk: List[str],
    tools: Dict[str, str],
    backend: str,
    runtime: str,
) -> None:
    monkeypatch.setattr(auto_setup, "_has_module", lambda name: False)
    prof = auto_setup.SystemProfile(
        os="linux",
        ram_mb=24 * 1024,
        cpu_cores=8,
        tools=dict(tools),
        gpu=auto_setup.GPUInfo(vendor="amd", vram_mb=12 * 1024, sdk=list(sdk)),
    )

    rec = auto_setup.recommend(prof)

    assert (rec.backend, rec.runtime) == (backend, runtime)
    assert any("Linux + AMD GPU" in line for line in rec.rationale)


def test_recommend_cpu_only_falls_back_to_smallest_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auto_setup, "_has_module", lambda name: False)
    prof = auto_setup.SystemProfile(
        os="linux",
        ram_mb=0,
        cpu_cores=4,
        cpu_logical_cores=8,
        cpu_instructions=["avx2"],
        tools={"ollama": "/usr/bin/ollama"},
    )

    rec = auto_setup.recommend(prof)

    assert (rec.backend, rec.runtime) == ("cpu", "ollama")
    assert rec.model_id == auto_setup._MODEL_CATALOG[-1]["id"]
    assert rec.estimated_tokens_per_sec == pytest.approx(2.4)
    assert any("8 threads, avx2" in line for line in rec.rationale)

    bare = auto_setup.SystemProfile(os="linux", ram_mb=8 * 1024, cpu_cores=1)
    bare_rec = auto_setup.recommend(bare)
    assert bare_rec.runtime == "llama.cpp"
    assert bare_rec.estimated_tokens_per_sec == pytest.approx(1.5)
    assert any("명령어 미감지" in line for line in bare_rec.rationale)


# ── ③ INSTALL plan ─────────────────────────────────────────────────────────
def test_plan_maps_missing_dependencies_to_apt_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auto_setup, "_which", _which_stub({}))
    monkeypatch.setattr(auto_setup, "_has_module", lambda name: False)
    prof = auto_setup.SystemProfile(
        os="linux",
        package_manager="apt",
        gpu=auto_setup.GPUInfo(vendor="nvidia", vram_mb=8 * 1024),
    )

    result = auto_setup.plan(prof, _rec(runtime="llama.cpp"))

    assert [step.name for step in result.steps] == [
        "node20",
        "ollama",
        "huggingface-cli",
        "weights:Qwen/Qwen3-VL-4B-Instruct",
    ]
    assert result.steps[0].command == ["apt-get", "install", "-y", "nodejs"]
    assert result.steps[0].requires_admin is True
    assert result.steps[1].requires_admin is False
    assert result.steps[-1].command == [
        "huggingface-cli",
        "download",
        "Qwen/Qwen3-VL-4B-Instruct",
        "--quiet",
    ]
    assert any("CUDA/nvidia-smi" in note for note in result.notes)

    payload = result.to_json()
    assert payload["package_manager"] == "apt"
    assert payload["confirmation_token"] == payload["command_plan"]["confirmation_token"]


def test_plan_without_package_manager_only_leaves_notes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auto_setup, "_which", _which_stub({}))
    monkeypatch.setattr(auto_setup, "_has_module", lambda name: False)
    prof = auto_setup.SystemProfile(os="linux", package_manager=None)

    result = auto_setup.plan(prof, _rec(runtime="ollama"))

    assert [step.name for step in result.steps] == ["weights:Qwen/Qwen3-VL-4B-Instruct"]
    assert len([note for note in result.notes if "수동 설치 필요" in note]) == 3
    assert result.steps[0].command == ["ollama", "pull", "qwen3-vl:4b"]


def test_plan_is_empty_when_everything_is_already_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        auto_setup,
        "_which",
        _which_stub(
            {
                "node": "/usr/bin/node",
                "ollama": "/usr/bin/ollama",
                "lms": "/usr/bin/lms",
                "huggingface-cli": "/usr/bin/huggingface-cli",
            }
        ),
    )
    monkeypatch.setattr(auto_setup, "_has_module", lambda name: True)
    prof = auto_setup.SystemProfile(
        os="darwin",
        package_manager="brew",
        cuda_available=True,
        gpu=auto_setup.GPUInfo(vendor="apple"),
    )

    result = auto_setup.plan(
        prof, _rec(runtime="mlx", backend="metal+mlx", model_id="mlx-community/gemma-4-31b-it-4bit")
    )

    assert [step.name for step in result.steps] == ["weights:mlx-community/gemma-4-31b-it-4bit"]
    assert result.notes == []


def test_plan_adds_pip_runtimes_when_modules_are_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auto_setup, "_which", _which_stub({"node": "/usr/bin/node"}))
    monkeypatch.setattr(auto_setup, "_has_module", lambda name: False)
    prof = auto_setup.SystemProfile(os="darwin", package_manager="brew")

    mlx_plan = auto_setup.plan(prof, _rec(runtime="mlx", backend="metal+mlx"))
    assert mlx_plan.steps[0].name == "mlx-vlm"
    assert mlx_plan.steps[0].command == ["pip3", "install", "--upgrade", "mlx-vlm"]

    vllm_plan = auto_setup.plan(prof, _rec(runtime="vllm", backend="cuda"))
    assert vllm_plan.steps[0].name == "vllm"
    assert vllm_plan.steps[0].command == [
        "pip3",
        "install",
        "--upgrade",
        "vllm",
        "huggingface_hub",
    ]


def test_plan_lmstudio_notes_missing_cli_and_uses_lms_get(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        auto_setup,
        "_which",
        _which_stub({"node": "/usr/bin/node", "huggingface-cli": "/usr/bin/huggingface-cli"}),
    )
    monkeypatch.setattr(auto_setup, "_has_module", lambda name: False)
    prof = auto_setup.SystemProfile(
        os="windows",
        package_manager="winget",
        gpu=auto_setup.GPUInfo(vendor="nvidia", vram_mb=16 * 1024),
        cuda_available=True,
    )

    result = auto_setup.plan(
        prof, _rec(runtime="lmstudio", model_id="Qwen/Qwen3-VL-8B-Instruct")
    )

    assert any("lmstudio.ai/download" in note for note in result.notes)
    assert any("WSL2/Linux" in note for note in result.notes)
    assert result.steps[-1].command == ["lms", "get", "Qwen/Qwen3-VL-8B-Instruct"]


def test_plan_requires_python_upgrade_on_old_interpreters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auto_setup, "_which", _which_stub({}))
    monkeypatch.setattr(auto_setup, "_has_module", lambda name: False)
    monkeypatch.setattr(auto_setup.sys, "version_info", (3, 10, 0, "final", 0))
    prof = auto_setup.SystemProfile(os="windows", package_manager="winget")

    result = auto_setup.plan(prof, _rec(runtime="llama.cpp"))

    assert result.steps[0].name == "python3.11+"
    assert result.steps[0].command == ["winget", "install", "-e", "--id", "Python.Python.3.11"]
    assert result.steps[0].requires_admin is False


@pytest.mark.parametrize(
    ("model_id", "command"),
    [
        (
            "mlx-community/gemma-4-31b-it-4bit",
            ["ollama", "pull", "hf.co/ggml-org/gemma-4-31B-it-GGUF:Q4_K_M"],
        ),
        (
            "mlx-community/gemma-4-12b-it-4bit",
            ["ollama", "pull", "hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_K_M"],
        ),
        (
            "mlx-community/Llama-4-Scout-17B-16E-Instruct-4bit",
            ["ollama", "pull", "hf.co/ggml-org/Llama-4-Scout-17B-16E-Instruct-GGUF:Q4_K_M"],
        ),
        ("Qwen/Qwen3-VL-8B-Instruct", ["ollama", "pull", "qwen3-vl:8b"]),
        ("Qwen/Qwen3-VL-4B-Instruct", ["ollama", "pull", "qwen3-vl:4b"]),
        (
            "some/unmapped-model",
            ["huggingface-cli", "download", "some/unmapped-model", "--quiet"],
        ),
    ],
)
def test_plan_maps_ollama_weight_pulls(
    monkeypatch: pytest.MonkeyPatch, model_id: str, command: List[str]
) -> None:
    monkeypatch.setattr(
        auto_setup,
        "_which",
        _which_stub(
            {
                "node": "/usr/bin/node",
                "ollama": "/usr/bin/ollama",
                "huggingface-cli": "/usr/bin/huggingface-cli",
            }
        ),
    )
    monkeypatch.setattr(auto_setup, "_has_module", lambda name: False)
    prof = auto_setup.SystemProfile(os="linux", package_manager="apt")

    result = auto_setup.plan(prof, _rec(runtime="ollama", model_id=model_id))

    assert result.steps[-1].name == "weights:" + model_id
    assert result.steps[-1].command == command


# ── apply_plan ─────────────────────────────────────────────────────────────
def test_apply_plan_refuses_without_confirm_flag() -> None:
    plan_obj = auto_setup.InstallPlan(package_manager=None, steps=[])

    with pytest.raises(RuntimeError, match="refuse to apply"):
        auto_setup.apply_plan(plan_obj)


def test_apply_plan_records_step_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    step = auto_setup.InstallStep(name="weights:demo", why="test", command=["echo", "ok"])
    plan_obj = auto_setup.InstallPlan(package_manager="apt", steps=[step])
    events: List[Dict[str, object]] = []

    monkeypatch.setattr(
        auto_setup, "append_process_audit_event", lambda *_a, **kwargs: events.append(kwargs)
    )

    def timed_out(*_args, **_kwargs):
        raise auto_setup.subprocess.TimeoutExpired(cmd=["echo", "ok"], timeout=300)

    monkeypatch.setattr(auto_setup.subprocess, "run", timed_out)

    results = auto_setup.apply_plan(
        plan_obj,
        confirm=True,
        confirmation_token=plan_obj.to_json()["confirmation_token"],
    )

    assert results[0]["name"] == "weights:demo"
    assert "error" in results[0]
    assert results[0]["command_hash"]
    assert [event["status"] for event in events] == ["started", "error"]

    with pytest.raises(CommandConfirmationError):
        auto_setup.apply_plan(plan_obj, confirm=True, confirmation_token="not-the-token")

    monkeypatch.setattr(
        auto_setup.subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(returncode=0, stdout="installed", stderr=""),
    )
    ok = auto_setup.apply_plan(
        plan_obj,
        confirm=True,
        confirmation_token=plan_obj.to_json()["confirmation_token"],
    )
    assert ok[0]["returncode"] == 0
    assert ok[0]["stdout_tail"] == "installed"
