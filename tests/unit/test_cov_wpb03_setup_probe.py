"""wpb03: hardware probing on the machines nobody in CI is running.

``setup_detection`` and ``auto_setup.probe`` read whatever the OS happens to
say.  The existing suite drives the *happy* shapes — a WMIC dump with real
adapters, an nvcc banner with a release number, a Linux box whose
``/proc/meminfo`` has a ``MemTotal`` line.  The branches below are the shapes a
real machine also produces: a JSON payload that is neither object nor array, a
video-controller dump with no adapters at all, an nvcc build with no release
string, an Apple GPU whose ``system_profiler`` output never names a chipset,
and a Windows/BSD host whose memory query comes back without the field the
parser wants.  Every probe is driven through the module's own ``_run`` /
``_which`` / ``_read_text`` seams, so nothing here reads the developer's
hardware.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

from latticeai.services.setup_detection import (
    detect_cuda,
    parse_windows_video_controllers,
)
from latticeai.setup import auto_setup

# ── setup_detection.parse_windows_video_controllers ─────────────────────────


def test_a_scalar_json_payload_falls_through_to_the_line_parser():
    """PowerShell returns bare JSON when the query matched nothing useful."""
    controllers = parse_windows_video_controllers("5")

    assert controllers == []


def test_a_wmic_dump_with_no_adapter_lines_yields_nothing():
    raw = "\n".join([
        "",
        "Status=OK",
        "AdapterRAM=1073741824",
        "",
    ])

    assert parse_windows_video_controllers(raw) == []


def test_unrelated_wmic_lines_between_adapters_are_ignored():
    raw = "\n".join([
        "Name=NVIDIA GeForce RTX 4090",
        "DeviceID=VideoController1",
        "AdapterRAM=25769803776",
        "",
        "Name=Intel(R) UHD Graphics",
        "DeviceID=VideoController2",
        "AdapterRAM=1073741824",
    ])

    assert parse_windows_video_controllers(raw) == [
        {"name": "NVIDIA GeForce RTX 4090", "vram_mb": 24576},
        {"name": "Intel(R) UHD Graphics", "vram_mb": 1024},
    ]


# ── setup_detection.detect_cuda ─────────────────────────────────────────────


def test_an_nvcc_banner_without_a_release_string_leaves_the_version_empty():
    outputs = {
        "nvidia-smi": "",
        "nvcc": "nvcc: NVIDIA (R) Cuda compiler driver\nBuilt on a custom toolchain\n",
    }
    seen: List[List[str]] = []

    def _run(args: List[str]) -> str:
        seen.append(list(args))
        return outputs["nvcc" if args[0].endswith("nvcc") else "nvidia-smi"]

    available, version, nvidia_smi, nvcc = detect_cuda(
        lambda binary: "/usr/local/cuda/bin/nvcc" if binary == "nvcc" else None,
        _run,
    )

    assert available is True
    assert version == "", "a build banner with no release number invents nothing"
    assert nvidia_smi is None
    assert nvcc == "/usr/local/cuda/bin/nvcc"
    assert seen == [["/usr/local/cuda/bin/nvcc", "--version"]]


# ── auto_setup._detect_gpu ──────────────────────────────────────────────────


def test_an_apple_gpu_without_a_chipset_line_still_reports_the_metal_stack(monkeypatch):
    monkeypatch.setattr(auto_setup, "_which", lambda _binary: None)
    monkeypatch.setattr(
        auto_setup,
        "_run",
        lambda _cmd, timeout=4.0: "Graphics/Displays:\n\n    Apple M-series GPU:\n      Vendor: Apple\n",
    )
    monkeypatch.setattr(auto_setup, "_has_module", lambda name: name == "mlx")

    gpu = auto_setup._detect_gpu("darwin", "arm64")

    assert gpu.vendor == "apple"
    assert gpu.model == "", "no Chipset Model line means no model is claimed"
    assert gpu.sdk == ["metal", "mlx"]
    assert gpu.vram_mb == 0


# ── auto_setup.probe ────────────────────────────────────────────────────────


def _stub_probe(monkeypatch, *, system: str, machine: str = "x86_64",
                read_text: str = "", run_output: str = "") -> None:
    """Replace every OS probe so ``probe()`` reads only scripted values."""
    monkeypatch.setattr(
        auto_setup,
        "platform",
        SimpleNamespace(
            system=lambda: system,
            release=lambda: "test-release",
            machine=lambda: machine,
            python_version=lambda: "3.11.0",
        ),
    )
    monkeypatch.setattr(
        auto_setup, "_detect_cpu_details", lambda _os: ("Test CPU", 4, 8, ["avx2"])
    )
    monkeypatch.setattr(auto_setup, "_detect_wsl", lambda _os: (False, ""))
    monkeypatch.setattr(auto_setup, "_detect_cuda", lambda: (False, ""))
    monkeypatch.setattr(auto_setup, "_detect_tools", lambda: {})
    monkeypatch.setattr(auto_setup, "_detect_gpu", lambda _os, _arch: auto_setup.GPUInfo())
    monkeypatch.setattr(auto_setup, "_detect_package_manager", lambda _os: None)
    monkeypatch.setattr(auto_setup, "_read_text", lambda _path: read_text)
    monkeypatch.setattr(auto_setup, "_run", lambda _cmd, timeout=4.0: run_output)


def test_a_linux_meminfo_without_memtotal_leaves_ram_unknown(monkeypatch):
    _stub_probe(
        monkeypatch,
        system="Linux",
        read_text="MemFree:         1024 kB\nBuffers:          512 kB\n",
    )

    profile = auto_setup.probe()

    assert profile.os == "linux"
    assert profile.ram_mb == 0, "an absent MemTotal is reported as unknown, not guessed"
    assert profile.cpu_model == "Test CPU"


def test_a_windows_wmic_dump_without_the_memory_field_leaves_ram_unknown(monkeypatch):
    _stub_probe(
        monkeypatch,
        system="Windows",
        run_output="Caption=WIN-HOST\nModel=Test\n",
    )

    profile = auto_setup.probe()

    assert profile.os == "windows"
    assert profile.ram_mb == 0


def test_an_unsupported_os_skips_every_memory_probe(monkeypatch):
    calls: List[Any] = []

    _stub_probe(monkeypatch, system="FreeBSD")
    monkeypatch.setattr(
        auto_setup, "_run", lambda cmd, timeout=4.0: calls.append(cmd) or ""
    )

    profile = auto_setup.probe()

    assert profile.os == "freebsd"
    assert profile.ram_mb == 0
    assert calls == [], "no linux/darwin/windows memory command is attempted"
    assert profile.python_version == "3.11.0"


# ── auto_setup.recommend ────────────────────────────────────────────────────


def test_a_text_only_catalog_pick_gets_no_multimodal_note(monkeypatch):
    """Older/text-only catalog entries must not claim a multimodal upgrade."""
    catalog: List[Dict[str, Any]] = [
        {"ram": 8 * 1024, "vram": 0, "id": "org/text-only-7b", "q": "q4_K_M"},
    ]
    monkeypatch.setattr(auto_setup, "_MODEL_CATALOG", catalog)

    profile = auto_setup.SystemProfile(
        os="linux", arch="x86_64", ram_mb=16 * 1024, cpu_cores=8, cpu_logical_cores=16
    )
    rec = auto_setup.recommend(profile)

    assert rec.model_id == "org/text-only-7b"
    assert rec.backend == "cpu"
    assert not any("멀티모달" in line for line in rec.rationale)
    assert any("RAM 16384 MB" in line for line in rec.rationale)
