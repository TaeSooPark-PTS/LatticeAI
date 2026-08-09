"""wp05 — ① PROBE side of ``latticeai/setup/auto_setup.py``.

Every hardware/OS seam is faked: ``_run`` never starts a subprocess, ``_which``
never touches PATH, ``_read_text`` never leaves ``tmp_path``, and the
darwin/windows-only branches are driven by injected values rather than by the
host actually being that OS.
"""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from typing import Callable, Dict, List, Optional

import pytest

from latticeai.setup import auto_setup


def _run_stub(responses: Dict[str, str], default: str = "") -> Callable[..., str]:
    """Fake ``auto_setup._run`` dispatching on a substring of the joined command."""

    def _run(cmd: List[str], timeout: float = 4.0) -> str:
        joined = " ".join(str(part) for part in cmd)
        for needle, value in responses.items():
            if needle in joined:
                return value
        return default

    return _run


def _which_stub(found: Dict[str, str]) -> Callable[[str], Optional[str]]:
    def _which(binary: str) -> Optional[str]:
        return found.get(binary)

    return _which


# ── dataclasses ────────────────────────────────────────────────────────────
def test_system_profile_score_is_capped_and_json_serialisable() -> None:
    prof = auto_setup.SystemProfile(
        os="linux",
        cpu_cores=8,
        ram_mb=16 * 1024,
        gpu=auto_setup.GPUInfo(vendor="nvidia", model="RTX 4070", vram_mb=8 * 1024),
    )

    # 8*2=16 cores + 16*2=32 ram + 8*4=32 vram
    assert prof.score() == 80

    maxed = auto_setup.SystemProfile(
        cpu_cores=64,
        ram_mb=512 * 1024,
        gpu=auto_setup.GPUInfo(vram_mb=80 * 1024),
    )
    assert maxed.score() == 100

    payload = prof.to_json()
    assert payload["score"] == 80
    assert payload["gpu"]["vram_mb"] == 8 * 1024
    assert json.loads(json.dumps(payload))["os"] == "linux"


# ── _read_text / _run ──────────────────────────────────────────────────────
def test_read_text_returns_content_and_empty_string_on_failure(tmp_path) -> None:
    sample = tmp_path / "version"
    sample.write_text("Linux version 6.8.0-microsoft-standard-WSL2", encoding="utf-8")

    assert auto_setup._read_text(str(sample)).startswith("Linux version")
    assert auto_setup._read_text(str(tmp_path / "does-not-exist")) == ""


def test_run_joins_streams_and_swallows_process_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: List[Dict[str, object]] = []

    def fake_run(cmd, **kwargs):
        seen.append({"cmd": cmd, **kwargs})
        return SimpleNamespace(stdout="out-", stderr="err")

    monkeypatch.setattr(auto_setup.subprocess, "run", fake_run)
    assert auto_setup._run(["nvidia-smi", "-L"], timeout=1.5) == "out-err"
    assert seen[0]["cmd"] == ["nvidia-smi", "-L"]
    assert seen[0]["timeout"] == 1.5
    assert seen[0]["check"] is False

    def blow_up(*_args, **_kwargs):
        raise OSError("binary vanished")

    monkeypatch.setattr(auto_setup.subprocess, "run", blow_up)
    assert auto_setup._run(["nvidia-smi"]) == ""


# ── _windows_candidate_paths / _which ──────────────────────────────────────
def test_windows_candidate_paths_follow_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", "C:/Users/demo/AppData/Local")
    monkeypatch.setenv("ProgramFiles", "C:/Program Files")
    monkeypatch.setenv("ProgramFiles(x86)", "C:/Program Files (x86)")

    ollama = auto_setup._windows_candidate_paths("ollama")
    assert len(ollama) == 2
    assert all(path.endswith("ollama.exe") for path in ollama)

    assert len(auto_setup._windows_candidate_paths("lms")) == 2
    smi = auto_setup._windows_candidate_paths("nvidia-smi")
    assert len(smi) == 2
    assert "x86" in smi[1]

    assert auto_setup._windows_candidate_paths("unheard-of-binary") == []

    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert len(auto_setup._windows_candidate_paths("ollama")) == 1


def test_which_falls_back_to_windows_install_locations(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(
        auto_setup.shutil, "which", lambda binary: "/usr/bin/git" if binary == "git" else None
    )

    assert auto_setup._which("git") == "/usr/bin/git"

    monkeypatch.setattr(auto_setup.platform, "system", lambda: "Linux")
    assert auto_setup._which("ollama") is None

    installed = tmp_path / "ollama.exe"
    installed.write_text("", encoding="utf-8")
    monkeypatch.setattr(auto_setup.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        auto_setup,
        "_windows_candidate_paths",
        lambda binary: [str(tmp_path / "absent.exe"), str(installed)],
    )
    assert auto_setup._which("ollama") == str(installed)

    monkeypatch.setattr(auto_setup, "_windows_candidate_paths", lambda binary: [])
    assert auto_setup._which("ollama") is None


# ── _detect_gpu ────────────────────────────────────────────────────────────
def test_detect_gpu_parses_nvidia_smi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auto_setup, "_which", _which_stub({"nvidia-smi": "/usr/bin/nvidia-smi"}))
    monkeypatch.setattr(
        auto_setup, "_run", _run_stub({"query-gpu": "NVIDIA GeForce RTX 4090, 24564\n"})
    )

    gpu = auto_setup._detect_gpu("linux", "x86_64")

    assert gpu.vendor == "nvidia"
    assert gpu.model == "NVIDIA GeForce RTX 4090"
    assert gpu.vram_mb == 24564
    assert gpu.sdk == ["cuda"]


def test_detect_gpu_survives_unparseable_nvidia_smi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auto_setup, "_which", _which_stub({"nvidia-smi": "/usr/bin/nvidia-smi"}))

    # No comma at all: the unpack raises ValueError before any field is set.
    monkeypatch.setattr(auto_setup, "_run", _run_stub({"query-gpu": "GeForce-without-memory\n"}))
    gpu = auto_setup._detect_gpu("linux", "x86_64")
    assert gpu.vendor == "unknown"
    assert gpu.vram_mb == 0

    # Comma present but the memory column is not a number.
    monkeypatch.setattr(auto_setup, "_run", _run_stub({"query-gpu": "RTX 4090, unknown\n"}))
    gpu = auto_setup._detect_gpu("windows", "amd64")
    assert gpu.vendor == "nvidia"
    assert gpu.model == "RTX 4090"
    assert gpu.vram_mb == 0
    assert gpu.sdk == []

    # Blank output leaves the profile untouched.
    monkeypatch.setattr(auto_setup, "_run", _run_stub({"query-gpu": "   \n"}))
    assert auto_setup._detect_gpu("ios", "arm64").vendor == "unknown"


def test_detect_gpu_apple_silicon_reports_metal_and_mlx(monkeypatch: pytest.MonkeyPatch) -> None:
    display = (
        "Graphics/Displays:\n"
        "    Apple M3 Max:\n"
        "      Chipset Model: Apple M3 Max\n"
        "      Type: GPU\n"
    )
    monkeypatch.setattr(auto_setup, "_which", _which_stub({}))
    monkeypatch.setattr(auto_setup, "_run", _run_stub({"SPDisplaysDataType": display}))

    monkeypatch.setattr(auto_setup, "_has_module", lambda name: name == "mlx")
    gpu = auto_setup._detect_gpu("darwin", "arm64")
    assert gpu.vendor == "apple"
    assert gpu.model == "Apple M3 Max"
    assert gpu.sdk == ["metal", "mlx"]

    monkeypatch.setattr(auto_setup, "_has_module", lambda name: False)
    assert auto_setup._detect_gpu("darwin", "arm64").sdk == ["metal"]


def test_detect_gpu_intel_mac_is_not_apple_silicon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auto_setup, "_which", _which_stub({}))
    monkeypatch.setattr(
        auto_setup, "_run", _run_stub({"SPDisplaysDataType": "Chipset Model: Apple Something\n"})
    )

    gpu = auto_setup._detect_gpu("darwin", "x86_64")

    assert gpu.vendor == "unknown"
    assert gpu.sdk == []


@pytest.mark.parametrize(
    ("name", "vendor", "sdk"),
    [
        ("NVIDIA RTX A2000", "nvidia", ["cuda"]),
        ("AMD Radeon RX 7900 XTX", "amd", ["directml", "vulkan"]),
        ("Intel(R) Arc(TM) A770 Graphics", "intel", ["directml", "vulkan"]),
        ("Matrox G200eW3", "unknown", []),
    ],
)
def test_detect_gpu_windows_powershell_json(
    monkeypatch: pytest.MonkeyPatch, name: str, vendor: str, sdk: List[str]
) -> None:
    payload = json.dumps({"Name": name, "AdapterRAM": 12 * 1024 * 1024 * 1024})
    monkeypatch.setattr(auto_setup, "_which", _which_stub({"powershell": "powershell.exe"}))
    monkeypatch.setattr(auto_setup, "_run", _run_stub({"Win32_VideoController": payload}))

    gpu = auto_setup._detect_gpu("windows", "amd64")

    assert gpu.vendor == vendor
    assert gpu.model == name
    assert gpu.vram_mb == 12288
    assert gpu.sdk == sdk


def test_detect_gpu_windows_falls_back_to_wmic(monkeypatch: pytest.MonkeyPatch) -> None:
    wmic = "Name=Intel(R) UHD Graphics 770\nAdapterRAM=1073741824\n"
    monkeypatch.setattr(auto_setup, "_which", _which_stub({}))
    monkeypatch.setattr(auto_setup, "_run", _run_stub({"win32_VideoController": wmic}))

    gpu = auto_setup._detect_gpu("windows", "amd64")

    assert gpu.vendor == "intel"
    assert gpu.vram_mb == 1024


def test_detect_gpu_windows_without_any_controller(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auto_setup, "_which", _which_stub({"pwsh": "pwsh.exe"}))
    monkeypatch.setattr(auto_setup, "_run", _run_stub({}))

    gpu = auto_setup._detect_gpu("windows", "amd64")

    assert gpu.vendor == "unknown"
    assert gpu.model == ""


@pytest.mark.parametrize(
    ("lspci", "vendor", "sdk"),
    [
        ("01:00.0 VGA compatible controller: NVIDIA Corporation AD102", "nvidia", ["cuda"]),
        ("03:00.0 VGA: Advanced Micro Devices, Inc. [AMD/ATI] Navi 31", "amd", ["rocm", "vulkan"]),
        ("00:02.0 VGA compatible controller: Intel Corporation UHD", "intel", ["vulkan"]),
        ("00:02.0 VGA compatible controller: Matrox Electronics", "unknown", []),
    ],
)
def test_detect_gpu_linux_lspci(
    monkeypatch: pytest.MonkeyPatch, lspci: str, vendor: str, sdk: List[str]
) -> None:
    monkeypatch.setattr(auto_setup, "_which", _which_stub({}))
    monkeypatch.setattr(auto_setup, "_run", _run_stub({"lspci": lspci}))

    gpu = auto_setup._detect_gpu("linux", "x86_64")

    assert gpu.vendor == vendor
    assert gpu.sdk == sdk


# ── package manager / tools / wsl / cuda ───────────────────────────────────
@pytest.mark.parametrize(
    ("prof_os", "found", "expected"),
    [
        ("windows", {"winget": "winget.exe"}, "winget"),
        ("windows", {}, None),
        ("darwin", {"brew": "/opt/homebrew/bin/brew"}, "brew"),
        ("darwin", {}, None),
        ("linux", {"dnf": "/usr/bin/dnf"}, "dnf"),
        ("linux", {"apt": "/usr/bin/apt", "dnf": "/usr/bin/dnf"}, "apt"),
        ("linux", {}, None),
        ("android", {"apt": "/usr/bin/apt"}, None),
    ],
)
def test_detect_package_manager(
    monkeypatch: pytest.MonkeyPatch, prof_os: str, found: Dict[str, str], expected: Optional[str]
) -> None:
    monkeypatch.setattr(auto_setup, "_which", _which_stub(found))

    assert auto_setup._detect_package_manager(prof_os) == expected


def test_detect_tools_drops_missing_binaries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        auto_setup, "_which", _which_stub({"git": "/usr/bin/git", "node": "/usr/bin/node"})
    )

    assert auto_setup._detect_tools() == {"git": "/usr/bin/git", "node": "/usr/bin/node"}


def test_detect_wsl_reads_proc_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        auto_setup, "_read_text", lambda path: "Linux version 5.15.0-microsoft-standard-WSL2"
    )

    assert auto_setup._detect_wsl("linux") == (True, "2")
    assert auto_setup._detect_wsl("darwin") == (False, "")


def test_detect_cuda_prefers_nvcc_release(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        auto_setup,
        "_which",
        _which_stub({"nvidia-smi": "/usr/bin/nvidia-smi", "nvcc": "/usr/bin/nvcc"}),
    )
    monkeypatch.setattr(
        auto_setup,
        "_run",
        _run_stub(
            {
                "driver_version": "550.54.14\n",
                "--version": "Cuda compilation tools, release 12.4, V12.4.99\n",
            }
        ),
    )

    assert auto_setup._detect_cuda() == (True, "12.4")

    monkeypatch.setattr(auto_setup, "_which", _which_stub({}))
    assert auto_setup._detect_cuda() == (False, "")


# ── _detect_cpu_details ────────────────────────────────────────────────────
def test_detect_cpu_details_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auto_setup.platform, "processor", lambda: "arm")
    monkeypatch.setattr(auto_setup.os, "cpu_count", lambda: 12)
    monkeypatch.setattr(
        auto_setup,
        "_run",
        _run_stub(
            {
                "machdep.cpu.brand_string": "Apple M3 Max\n",
                "hw.physicalcpu": "14\n",
                "hw.logicalcpu": "14\n",
                "machdep.cpu.features": "FPU VME AVX2 SSE4_2 RDRAND MMX\n",
            }
        ),
    )

    model, physical, logical, flags = auto_setup._detect_cpu_details("darwin")

    assert model == "Apple M3 Max"
    assert (physical, logical) == (14, 14)
    assert flags == ["avx2", "rdrand", "sse4_2"]


def test_detect_cpu_details_darwin_non_numeric_sysctl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auto_setup.platform, "processor", lambda: "i386")
    monkeypatch.setattr(auto_setup.os, "cpu_count", lambda: 10)
    monkeypatch.setattr(auto_setup, "_run", _run_stub({"hw.physicalcpu": "many\n"}))

    model, physical, logical, flags = auto_setup._detect_cpu_details("darwin")

    assert model == "i386"
    assert (physical, logical) == (10, 10)
    assert flags == []


def test_detect_cpu_details_linux_reads_cpuinfo(monkeypatch: pytest.MonkeyPatch) -> None:
    cpuinfo = (
        "processor\t: 0\n"
        "model name\t: AMD Ryzen 9 7950X 16-Core Processor\n"
        "flags\t\t: fpu vme avx avx2 fma sse4_2 rdrand\n"
        "processor\t: 1\n"
        "model name\t: AMD Ryzen 9 7950X 16-Core Processor\n"
        "flags\t\t: fpu vme avx avx2\n"
    )
    monkeypatch.setattr(auto_setup.platform, "processor", lambda: "")
    monkeypatch.setattr(auto_setup.os, "cpu_count", lambda: 32)
    monkeypatch.setattr(auto_setup, "_read_text", lambda path: cpuinfo)

    model, physical, logical, flags = auto_setup._detect_cpu_details("linux")

    assert model == "AMD Ryzen 9 7950X 16-Core Processor"
    assert (physical, logical) == (32, 32)
    assert flags == ["avx", "avx2", "fma", "rdrand", "sse4_2"]


def test_detect_cpu_details_windows_with_processor_features(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = (
        "\n"
        "Name=12th Gen Intel(R) Core(TM) i9-12900K\n"
        "NumberOfCores=16\n"
        "NumberOfLogicalProcessors=24\n"
        "Caption=Intel64 Family 6\n"
    )
    monkeypatch.setattr(auto_setup.platform, "processor", lambda: "Intel64 Family 6")
    monkeypatch.setattr(auto_setup.os, "cpu_count", lambda: 24)
    monkeypatch.setattr(auto_setup, "_run", _run_stub({"wmic": raw}))
    fake_ctypes = SimpleNamespace(
        windll=SimpleNamespace(
            kernel32=SimpleNamespace(IsProcessorFeaturePresent=lambda code: code in (6, 10))
        )
    )
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)

    model, physical, logical, flags = auto_setup._detect_cpu_details("windows")

    assert model == "12th Gen Intel(R) Core(TM) i9-12900K"
    assert (physical, logical) == (16, 24)
    assert flags == ["sse", "sse2"]


def test_detect_cpu_details_windows_bad_counts_and_no_ctypes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = "Name=\nNumberOfCores=many\nNumberOfLogicalProcessors=lots\n"
    monkeypatch.setattr(auto_setup.platform, "processor", lambda: "AMD64")
    monkeypatch.setattr(auto_setup.os, "cpu_count", lambda: 4)
    monkeypatch.setattr(auto_setup, "_run", _run_stub({"wmic": raw}))
    # No ``windll`` attribute → the feature probe raises and is swallowed.
    monkeypatch.setitem(sys.modules, "ctypes", SimpleNamespace())

    model, physical, logical, flags = auto_setup._detect_cpu_details("windows")

    assert model == "AMD64"
    assert (physical, logical) == (4, 4)
    assert flags == []


def test_detect_cpu_details_unknown_os_uses_platform_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auto_setup.platform, "processor", lambda: "riscv64")
    monkeypatch.setattr(auto_setup.os, "cpu_count", lambda: 2)

    assert auto_setup._detect_cpu_details("android") == ("riscv64", 2, 2, [])


# ── _has_module ────────────────────────────────────────────────────────────
def test_has_module_reports_importability() -> None:
    assert auto_setup._has_module("json") is True
    assert auto_setup._has_module("lattice_no_such_module_wp05") is False


# ── probe() ────────────────────────────────────────────────────────────────
def _stub_probe_environment(
    monkeypatch: pytest.MonkeyPatch,
    *,
    system: str = "Linux",
    machine: str = "x86_64",
    read_text: Optional[Callable[..., str]] = None,
    run: Optional[Callable[..., str]] = None,
    disk_usage: Optional[Callable[..., object]] = None,
) -> None:
    """Replace every seam ``probe()`` reaches for: no subprocess, no host I/O."""
    monkeypatch.setattr(auto_setup.platform, "system", lambda: system)
    monkeypatch.setattr(auto_setup.platform, "release", lambda: "6.8.0-test")
    monkeypatch.setattr(auto_setup.platform, "machine", lambda: machine)
    monkeypatch.setattr(auto_setup.platform, "python_version", lambda: "3.11.9")
    monkeypatch.setattr(
        auto_setup, "_detect_cpu_details", lambda prof_os: ("Test CPU", 8, 16, ["avx2"])
    )
    monkeypatch.setattr(auto_setup, "_detect_wsl", lambda prof_os: (True, "2"))
    monkeypatch.setattr(auto_setup, "_detect_cuda", lambda: (True, "12.4"))
    monkeypatch.setattr(auto_setup, "_detect_tools", lambda: {"git": "/usr/bin/git"})
    monkeypatch.setattr(
        auto_setup, "_detect_gpu", lambda prof_os, arch: auto_setup.GPUInfo(vendor="none")
    )
    monkeypatch.setattr(auto_setup, "_detect_package_manager", lambda prof_os: "apt")
    monkeypatch.setattr(auto_setup, "_read_text", read_text or (lambda path: ""))
    monkeypatch.setattr(auto_setup, "_run", run or _run_stub({}))
    monkeypatch.setattr(
        auto_setup.shutil,
        "disk_usage",
        disk_usage
        or (lambda path: SimpleNamespace(total=0, used=0, free=200 * 1024 * 1024 * 1024)),
    )


def test_probe_linux_reads_meminfo_and_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    meminfo = "SwapTotal:   0 kB\nMemTotal:       32899724 kB\nMemFree:  1024 kB\n"
    _stub_probe_environment(monkeypatch, read_text=lambda path: meminfo)

    prof = auto_setup.probe()

    assert prof.os == "linux"
    assert prof.os_version == "6.8.0-test"
    assert prof.arch == "x86_64"
    assert prof.cpu_model == "Test CPU"
    assert (prof.cpu_cores, prof.cpu_logical_cores) == (8, 16)
    assert prof.cpu_instructions == ["avx2"]
    assert prof.python_version == "3.11.9"
    assert (prof.is_wsl, prof.wsl_version) == (True, "2")
    assert (prof.cuda_available, prof.cuda_version) == (True, "12.4")
    assert prof.tools == {"git": "/usr/bin/git"}
    assert prof.ram_mb == 32899724 // 1024
    assert prof.disk_free_mb == 200 * 1024
    assert prof.package_manager == "apt"
    assert prof.gpu.vendor == "none"


def test_probe_darwin_uses_sysctl_memsize(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_probe_environment(
        monkeypatch,
        system="Darwin",
        machine="arm64",
        run=_run_stub({"hw.memsize": "68719476736\n"}),
    )

    prof = auto_setup.probe()

    assert prof.os == "darwin"
    assert prof.arch == "arm64"
    assert prof.ram_mb == 64 * 1024


@pytest.mark.parametrize(
    ("memory", "expected"),
    [("1 TB", 1024 * 1024), ("96 GB", 96 * 1024), ("512 MB", 512)],
)
def test_probe_darwin_falls_back_to_system_profiler(
    monkeypatch: pytest.MonkeyPatch, memory: str, expected: int
) -> None:
    _stub_probe_environment(
        monkeypatch,
        system="Darwin",
        machine="arm64",
        run=_run_stub(
            {
                "hw.memsize": "not-a-number\n",
                "SPHardwareDataType": "Hardware Overview:\n      Memory: " + memory + "\n",
            }
        ),
    )

    assert auto_setup.probe().ram_mb == expected


def test_probe_darwin_falls_back_to_hostinfo(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_probe_environment(
        monkeypatch,
        system="Darwin",
        machine="arm64",
        run=_run_stub(
            {
                "SPHardwareDataType": "Hardware Overview:\n      Model Name: Mac Studio\n",
                "hostinfo": "Primary memory available: 64.00 gigabytes\n",
            }
        ),
    )

    assert auto_setup.probe().ram_mb == 64 * 1024


def test_probe_darwin_without_any_memory_source(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_probe_environment(monkeypatch, system="Darwin", machine="arm64")

    assert auto_setup.probe().ram_mb == 0


def test_probe_windows_reads_wmic_total_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_probe_environment(
        monkeypatch,
        system="Windows",
        machine="AMD64",
        run=_run_stub({"TotalPhysicalMemory": "Node=DESKTOP\nTotalPhysicalMemory=17179869184\n"}),
    )

    prof = auto_setup.probe()

    assert prof.os == "windows"
    assert prof.arch == "amd64"
    assert prof.ram_mb == 16 * 1024


def test_probe_survives_failing_probe_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    def exploding_run(cmd, timeout: float = 4.0) -> str:
        raise RuntimeError("probe command failed")

    def exploding_disk_usage(path):
        raise OSError("volume unavailable")

    _stub_probe_environment(
        monkeypatch,
        system="Darwin",
        machine="arm64",
        run=exploding_run,
        disk_usage=exploding_disk_usage,
    )

    prof = auto_setup.probe()

    assert prof.ram_mb == 0
    assert prof.disk_free_mb == 0
    assert prof.os == "darwin"
