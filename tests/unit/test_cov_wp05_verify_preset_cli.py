"""wp05 — ④ VERIFY, ⑤ PRESET, the orchestrator and the CLI of ``auto_setup``.

``probe`` is always replaced by a fixed profile so the CLI paths never touch
the host, and the one installer entry point reachable from ``_main`` is stubbed.
"""

from __future__ import annotations

import json
import runpy
import sys
import warnings
from types import SimpleNamespace
from typing import Callable, Dict, List, Optional

import pytest

from latticeai.setup import auto_setup


def _which_stub(found: Dict[str, str]) -> Callable[[str], Optional[str]]:
    def _which(binary: str) -> Optional[str]:
        return found.get(binary)

    return _which


def _profile(**overrides) -> auto_setup.SystemProfile:
    base: Dict[str, object] = {
        "os": "linux",
        "os_version": "6.8.0",
        "arch": "x86_64",
        "cpu_model": "Test CPU",
        "cpu_cores": 8,
        "cpu_logical_cores": 16,
        "ram_mb": 16 * 1024,
        "disk_free_mb": 100 * 1024,
        "python_version": "3.11.9",
        "tools": {"ollama": "/usr/bin/ollama"},
    }
    base.update(overrides)
    return auto_setup.SystemProfile(**base)  # type: ignore[arg-type]


def _rec(runtime: str = "ollama", backend: str = "cpu") -> auto_setup.Recommendation:
    return auto_setup.Recommendation(
        runtime=runtime,
        backend=backend,
        model_id="Qwen/Qwen3-VL-4B-Instruct",
        quantization="q4_K_M",
        rationale=["fixture"],
    )


def _labels(report: Dict[str, object]) -> Dict[str, bool]:
    return {check["label"]: check["ok"] for check in report["checks"]}  # type: ignore[index,union-attr]


# ── ④ VERIFY ───────────────────────────────────────────────────────────────
def test_verify_flags_undersized_machine_for_mlx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auto_setup, "_has_module", lambda name: False)

    report = auto_setup.verify(
        _profile(ram_mb=0, disk_free_mb=1024), _rec(runtime="mlx", backend="metal+mlx")
    )
    checks = _labels(report)

    assert report["all_pass"] is False
    assert checks["Python 3.11+"] is True
    assert checks["RAM ≥ 4 GB"] is False
    assert checks["디스크 여유 ≥ 8 GB"] is False
    assert checks["mlx_vlm import"] is False
    assert "CPU latency sample" in checks


def test_verify_checks_ollama_binary_and_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auto_setup, "_which", _which_stub({"ollama": "/usr/bin/ollama"}))

    report = auto_setup.verify(
        _profile(cuda_available=True, cuda_version="12.4"), _rec(runtime="ollama", backend="cuda")
    )
    checks = _labels(report)

    assert checks["ollama binary"] is True
    assert checks["CUDA/nvidia-smi"] is True
    assert checks["RAM ≥ 4 GB"] is True
    detail = {check["label"]: check["detail"] for check in report["checks"]}  # type: ignore[index,union-attr]
    assert detail["ollama binary"] == "/usr/bin/ollama"
    assert detail["CUDA/nvidia-smi"] == "12.4"


def test_verify_reports_missing_lm_studio_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auto_setup, "_which", _which_stub({}))

    report = auto_setup.verify(_profile(), _rec(runtime="lmstudio", backend="directml/vulkan"))
    checks = _labels(report)
    detail = {check["label"]: check["detail"] for check in report["checks"]}  # type: ignore[index,union-attr]

    assert checks["LM Studio CLI"] is False
    assert detail["LM Studio CLI"] == "not found"
    assert "CUDA/nvidia-smi" not in checks


def test_verify_skips_runtime_specific_checks_for_vllm() -> None:
    report = auto_setup.verify(_profile(), _rec(runtime="vllm", backend="rocm"))
    checks = _labels(report)

    assert set(checks) == {
        "Python 3.11+",
        "RAM ≥ 4 GB",
        "디스크 여유 ≥ 8 GB",
        "CPU latency sample",
    }


# ── ⑤ PRESET ───────────────────────────────────────────────────────────────
def test_preset_advanced_mode_on_apple_silicon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANG", "ko_KR.UTF-8")
    prof = _profile(os="darwin", gpu=auto_setup.GPUInfo(vendor="apple"), ram_mb=8 * 1024)

    out = auto_setup.preset(prof, _rec(runtime="mlx", backend="metal+mlx"))

    assert out["mode"] == "advanced"
    assert out["shortcuts"]["newChat"] == "Cmd+N"
    assert out["shortcuts"]["submit"] == "Enter"
    assert out["theme"]["density"] == "compact"
    assert out["language"] == "ko"
    assert out["model"]["runtime"] == "mlx"
    assert [tool["id"] for tool in out["mcp"]] == [
        "filesystem",
        "web-search",
        "code-execute",
        "browser-automation",
        "database",
    ]
    assert "고급 모드" in out["tips"][0]


def test_preset_basic_mode_uses_ctrl_and_locale_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANG", raising=False)
    monkeypatch.setenv("LC_ALL", "ja_JP.UTF-8")
    prof = _profile(cpu_model="Generic Xeon", ram_mb=8 * 1024)

    out = auto_setup.preset(prof, _rec())

    assert out["mode"] == "basic"
    assert out["shortcuts"]["toggleMode"] == "Ctrl+Shift+M"
    assert out["theme"] == {"mode": "auto", "accent": "#6E4AE6", "density": "comfortable"}
    assert out["language"] == "ja"
    assert "기본 모드" in out["tips"][0]


def test_preset_advanced_on_large_ram_and_developer_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANG", "en_US.UTF-8")

    big_ram = auto_setup.preset(_profile(ram_mb=32 * 1024), _rec())
    assert big_ram["mode"] == "advanced"
    assert big_ram["language"] == "en"

    dev_box = auto_setup.preset(
        _profile(ram_mb=8 * 1024, cpu_model="Codeium Coprocessor"), _rec()
    )
    assert dev_box["mode"] == "advanced"

    monkeypatch.delenv("LANG", raising=False)
    monkeypatch.delenv("LC_ALL", raising=False)
    default_locale = auto_setup.preset(_profile(ram_mb=8 * 1024, cpu_model=""), _rec())
    assert default_locale["mode"] == "basic"
    assert default_locale["language"] == "ko"


# ── orchestrator ───────────────────────────────────────────────────────────
def _stub_setup_seams(monkeypatch: pytest.MonkeyPatch) -> auto_setup.SystemProfile:
    prof = _profile()
    monkeypatch.setattr(auto_setup, "probe", lambda: prof)
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
    return prof


def test_run_all_returns_every_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    prof = _stub_setup_seams(monkeypatch)

    out = auto_setup.run_all()

    assert out["install"] is None
    assert out["probe"]["score"] == prof.score()
    assert out["recommend"]["runtime"] == "ollama"
    assert out["plan"]["steps"][0]["name"] == "weights:mlx-community/gemma-4-12b-it-4bit"
    assert out["plan"]["confirmation_token"]
    assert out["verify"]["checks"][0]["label"] == "Python 3.11+"
    assert out["preset"]["model"]["id"] == "mlx-community/gemma-4-12b-it-4bit"


def test_run_all_applies_install_with_matching_token(monkeypatch: pytest.MonkeyPatch) -> None:
    prof = _stub_setup_seams(monkeypatch)
    monkeypatch.setattr(auto_setup, "append_process_audit_event", lambda *_a, **_k: None)
    launched: List[List[str]] = []

    def fake_run(cmd, **_kwargs):
        launched.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="pulled", stderr="")

    monkeypatch.setattr(auto_setup.subprocess, "run", fake_run)
    expected_plan = auto_setup.plan(prof, auto_setup.recommend(prof))
    token = expected_plan.to_json()["confirmation_token"]

    out = auto_setup.run_all(apply_install=True, confirmation_token=token)

    assert out["install"][0]["returncode"] == 0
    assert launched == [["ollama", "pull", "hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_K_M"]]


# ── CLI ────────────────────────────────────────────────────────────────────
def _run_main(monkeypatch: pytest.MonkeyPatch, argv: List[str]) -> int:
    monkeypatch.setattr(auto_setup.sys, "argv", ["auto_setup", *argv])
    return auto_setup._main()


def test_main_probe_prints_profile(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    _stub_setup_seams(monkeypatch)

    assert _run_main(monkeypatch, ["probe"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["os"] == "linux"
    assert payload["score"] > 0


def test_main_recommend_prints_probe_and_recommendation(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _stub_setup_seams(monkeypatch)

    assert _run_main(monkeypatch, ["recommend"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"probe", "recommend"}
    assert payload["recommend"]["backend"] == "cpu"


def test_main_plan_dry_run_and_apply(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    _stub_setup_seams(monkeypatch)

    assert _run_main(monkeypatch, ["plan"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert "install" not in dry
    assert dry["plan"]["confirmation_token"]

    seen: Dict[str, object] = {}

    def fake_apply(plan_obj, *, confirm=False, confirmation_token=None):
        seen["steps"] = [step.name for step in plan_obj.steps]
        seen["confirm"] = confirm
        seen["token"] = confirmation_token
        return [{"name": "weights", "returncode": 0}]

    monkeypatch.setattr(auto_setup, "apply_plan", fake_apply)

    assert _run_main(monkeypatch, ["plan", "--apply", "--confirm-token", "tok-123"]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["install"] == [{"name": "weights", "returncode": 0}]
    assert seen == {
        "steps": ["weights:mlx-community/gemma-4-12b-it-4bit"],
        "confirm": True,
        "token": "tok-123",
    }


def test_main_verify_and_preset_and_all(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    _stub_setup_seams(monkeypatch)

    assert _run_main(monkeypatch, ["verify"]) == 0
    assert "checks" in json.loads(capsys.readouterr().out)

    assert _run_main(monkeypatch, ["preset"]) == 0
    assert json.loads(capsys.readouterr().out)["mode"] in {"basic", "advanced"}

    assert _run_main(monkeypatch, ["all"]) == 0
    everything = json.loads(capsys.readouterr().out)
    assert set(everything) == {"probe", "recommend", "plan", "install", "verify", "preset"}


def test_main_returns_two_for_an_unhandled_subcommand(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_setup_seams(monkeypatch)
    monkeypatch.setattr(
        auto_setup.argparse.ArgumentParser,
        "parse_args",
        lambda self, *_a, **_k: SimpleNamespace(cmd="mystery", apply=False, confirm_token=None),
    )

    assert _run_main(monkeypatch, []) == 2


def test_module_entrypoint_exits_with_argparse_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["auto_setup"])

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        with pytest.raises(SystemExit) as excinfo:
            runpy.run_module("latticeai.setup.auto_setup", run_name="__main__")

    assert excinfo.value.code == 2
