"""wp04 — environment-detection coverage for latticeai/setup/wizard/.

The wizard probes hardware through `subprocess`, `/proc`, `platform` and
`shutil`, so almost every branch is gated on the host it runs on. These tests
drive each branch through the module's own seams (`setup.platform`,
`setup._cmd`, `setup.Path`, ...) so a macOS laptop and an ubuntu CI runner
execute the same lines. The wizard's helpers are the twin suite,
``test_cov_wp04_wizard_helpers.py``; both share the split shim and probe
doubles in ``_wizard_common``.
"""

import os
import platform
import shutil
import sys
from types import SimpleNamespace

from latticeai.setup import wizard as setup

from ._wizard_common import (
    _fake_cmd,
    _ModuleShim,
    _patch,
    _patch_paths,
    _patch_proc_meminfo,
)

# ── chip / cpu ────────────────────────────────────────────────────────────────

def test_detect_chip_reads_the_apple_profiler(monkeypatch):
    _patch(monkeypatch, "platform", _ModuleShim(platform, system=lambda: "Darwin", machine=lambda: "arm64"))
    _patch(monkeypatch, "_cmd", _fake_cmd({"system_profiler": "      Chip: Apple M3\n"}))

    assert setup._detect_chip() == {
        "name": "Apple M3",
        "arch": "arm64",
        "is_apple_silicon": True,
        "gen": 3,
    }


def test_detect_chip_defaults_to_generation_one_without_a_profiler_match(monkeypatch):
    _patch(monkeypatch, "platform", _ModuleShim(platform, system=lambda: "Darwin", machine=lambda: "arm64"))
    _patch(monkeypatch, "_cmd", _fake_cmd({}))

    chip = setup._detect_chip()

    assert chip["name"] == "Apple Silicon"
    assert chip["gen"] == 1


def test_detect_chip_uses_sysctl_on_intel_macs(monkeypatch):
    _patch(monkeypatch, "platform", _ModuleShim(platform, system=lambda: "Darwin", machine=lambda: "x86_64"))
    _patch(monkeypatch, "_cmd", _fake_cmd({"machdep.cpu.brand_string": "Intel(R) Core(TM) i9-9880H"}))

    chip = setup._detect_chip()

    assert chip["name"] == "Intel(R) Core(TM) i9-9880H"
    assert chip["is_apple_silicon"] is False
    assert chip["gen"] is None


def test_detect_chip_parses_wmic_on_windows(monkeypatch):
    _patch(monkeypatch, "platform", _ModuleShim(platform, system=lambda: "Windows", machine=lambda: "AMD64"))
    _patch(monkeypatch, "_cmd", _fake_cmd({"get Name /value": "\r\nName=AMD Ryzen 9 7950X\r\n"}))

    assert setup._detect_chip()["name"] == "AMD Ryzen 9 7950X"


def test_detect_chip_falls_back_to_processor_when_wmic_is_silent(monkeypatch):
    _patch(
        monkeypatch,
        "platform",
        _ModuleShim(platform, system=lambda: "Windows", machine=lambda: "AMD64", processor=lambda: ""),
    )
    _patch(monkeypatch, "_cmd", _fake_cmd({}))

    assert setup._detect_chip()["name"] == "Unknown CPU"


def test_detect_chip_reads_proc_cpuinfo_on_linux(monkeypatch):
    _patch(monkeypatch, "platform", _ModuleShim(platform, system=lambda: "Linux", machine=lambda: "x86_64"))
    _patch(monkeypatch, "_cmd", _fake_cmd({}))
    _patch_paths(monkeypatch, {"/proc/cpuinfo": "processor\t: 0\nmodel name\t: Intel Xeon Gold\n"})

    assert setup._detect_chip()["name"] == "Intel Xeon Gold"


def test_detect_chip_survives_an_unreadable_cpuinfo(monkeypatch):
    _patch(
        monkeypatch,
        "platform",
        _ModuleShim(platform, system=lambda: "Linux", machine=lambda: "x86_64", processor=lambda: "generic"),
    )
    _patch(monkeypatch, "_cmd", _fake_cmd({}))
    _patch_paths(monkeypatch, {"/proc/cpuinfo": OSError("permission denied")})

    assert setup._detect_chip()["name"] == "generic"


def test_detect_cpu_reads_sysctl_on_darwin(monkeypatch):
    _patch(monkeypatch, "platform", _ModuleShim(platform, system=lambda: "Darwin", machine=lambda: "arm64"))
    _patch(
        monkeypatch,
        "_cmd",
        _fake_cmd(
            {
                "machdep.cpu.features": "FPU AVX2 FMA NEON UNRELATED",
                "hw.physicalcpu": "8",
                "hw.logicalcpu": "16",
                "system_profiler": "Chip: Apple M3\n",
            }
        ),
    )

    cpu = setup._detect_cpu()

    assert cpu["model"] == "Apple M3"
    assert cpu["physical_cores"] == 8
    assert cpu["logical_cores"] == 16
    assert cpu["instructions"] == ["avx2", "fma", "neon"]


def test_detect_cpu_keeps_defaults_when_sysctl_counts_are_unparsable(monkeypatch):
    _patch(monkeypatch, "platform", _ModuleShim(platform, system=lambda: "Darwin", machine=lambda: "arm64"))
    _patch(
        monkeypatch,
        "_cmd",
        _fake_cmd({"machdep.cpu.features": "", "hw.physicalcpu": "many", "system_profiler": ""}),
    )

    cpu = setup._detect_cpu()

    assert cpu["physical_cores"] == (os.cpu_count() or 0)
    assert cpu["logical_cores"] == (os.cpu_count() or 0)


def test_detect_cpu_reads_flags_from_proc_cpuinfo(monkeypatch):
    _patch(monkeypatch, "platform", _ModuleShim(platform, system=lambda: "Linux", machine=lambda: "x86_64"))
    _patch(monkeypatch, "_cmd", _fake_cmd({}))
    _patch_paths(
        monkeypatch,
        {"/proc/cpuinfo": "model name\t: Intel Xeon Gold\nflags\t\t: fpu avx avx2 fma sse4_2\n"},
    )

    cpu = setup._detect_cpu()

    assert cpu["model"] == "Intel Xeon Gold"
    assert cpu["instructions"] == ["avx", "avx2", "fma", "sse4_2"]


def test_detect_cpu_survives_an_unreadable_cpuinfo(monkeypatch):
    _patch(
        monkeypatch,
        "platform",
        _ModuleShim(platform, system=lambda: "Linux", machine=lambda: "x86_64", processor=lambda: "generic"),
    )
    _patch(monkeypatch, "_cmd", _fake_cmd({}))
    _patch_paths(monkeypatch, {"/proc/cpuinfo": OSError("permission denied")})

    assert setup._detect_cpu()["instructions"] == []


def test_detect_cpu_parses_wmic_and_processor_features_on_windows(monkeypatch):
    _patch(monkeypatch, "platform", _ModuleShim(platform, system=lambda: "Windows", machine=lambda: "AMD64"))
    _patch(
        monkeypatch,
        "_cmd",
        _fake_cmd(
            {
                "NumberOfCores": (
                    "Name=AMD Ryzen 9 7950X\n"
                    "NumberOfCores=16\n"
                    "NumberOfLogicalProcessors=32\n"
                    "UnrelatedLine\n"
                ),
                "get Name /value": "Name=AMD Ryzen 9 7950X\n",
            }
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "ctypes",
        SimpleNamespace(
            windll=SimpleNamespace(
                kernel32=SimpleNamespace(IsProcessorFeaturePresent=lambda code: code in {6, 10})
            )
        ),
    )

    cpu = setup._detect_cpu()

    assert cpu["model"] == "AMD Ryzen 9 7950X"
    assert cpu["physical_cores"] == 16
    assert cpu["logical_cores"] == 32
    assert cpu["instructions"] == ["sse", "sse2"]


def test_detect_cpu_survives_unparsable_windows_counts_and_missing_ctypes(monkeypatch):
    _patch(monkeypatch, "platform", _ModuleShim(platform, system=lambda: "Windows", machine=lambda: "AMD64"))
    _patch(
        monkeypatch,
        "_cmd",
        _fake_cmd(
            {
                "NumberOfCores": "NumberOfCores=lots\nNumberOfLogicalProcessors=plenty\n",
                "get Name /value": "",
            }
        ),
    )
    monkeypatch.setitem(sys.modules, "ctypes", None)

    cpu = setup._detect_cpu()

    assert cpu["physical_cores"] == (os.cpu_count() or 0)
    assert cpu["logical_cores"] == (os.cpu_count() or 0)
    assert cpu["instructions"] == []


# ── ram / disk ────────────────────────────────────────────────────────────────

def test_detect_ram_gb_reads_windows_total_physical_memory(monkeypatch):
    _patch(monkeypatch, "platform", _ModuleShim(platform, system=lambda: "Windows"))
    _patch(monkeypatch, "_cmd", _fake_cmd({"TotalPhysicalMemory": "TotalPhysicalMemory=34359738368\n"}))

    assert setup._detect_ram_gb() == 32.0


def test_detect_ram_gb_falls_through_when_windows_reports_garbage(monkeypatch):
    _patch(monkeypatch, "platform", _ModuleShim(platform, system=lambda: "Windows"))
    _patch(monkeypatch, "_cmd", _fake_cmd({"TotalPhysicalMemory": "TotalPhysicalMemory=NaN\n"}))
    _patch_proc_meminfo(monkeypatch, "MemFree: 1 kB\nMemTotal:       16384000 kB\n")

    assert setup._detect_ram_gb() == 15.6


def test_detect_ram_gb_reads_sysctl_memsize(monkeypatch):
    _patch(monkeypatch, "platform", _ModuleShim(platform, system=lambda: "Darwin"))
    _patch(monkeypatch, "_cmd", _fake_cmd({"hw.memsize": "17179869184"}))

    assert setup._detect_ram_gb() == 16.0


def test_detect_ram_gb_reads_the_darwin_profiler_in_every_unit(monkeypatch):
    _patch(monkeypatch, "platform", _ModuleShim(platform, system=lambda: "Darwin"))

    for profiler_text, expected in (
        ("Memory: 1 TB", 1024.0),
        ("Memory: 64 GB", 64.0),
        ("Memory: 512 MB", 0.5),
    ):
        _patch(
            monkeypatch,
            "_cmd",
            _fake_cmd({"hw.memsize": "not-a-number", "system_profiler": profiler_text}),
        )
        assert setup._detect_ram_gb() == expected


def test_detect_ram_gb_falls_back_to_hostinfo(monkeypatch):
    _patch(monkeypatch, "platform", _ModuleShim(platform, system=lambda: "Darwin"))
    _patch(
        monkeypatch,
        "_cmd",
        _fake_cmd({"hostinfo": "Primary memory available: 96.00 gigabytes"}),
    )

    assert setup._detect_ram_gb() == 96.0


def test_detect_ram_gb_reads_proc_meminfo_on_linux(monkeypatch):
    _patch(monkeypatch, "platform", _ModuleShim(platform, system=lambda: "Linux"))
    _patch(monkeypatch, "_cmd", _fake_cmd({}))
    _patch_proc_meminfo(monkeypatch, "MemTotal:       16384000 kB\n")

    assert setup._detect_ram_gb() == 15.6


def test_detect_ram_gb_returns_zero_when_nothing_answers(monkeypatch):
    _patch(monkeypatch, "platform", _ModuleShim(platform, system=lambda: "Linux"))
    _patch(monkeypatch, "_cmd", _fake_cmd({}))
    _patch_proc_meminfo(monkeypatch, OSError("no /proc"))

    assert setup._detect_ram_gb() == 0.0


def test_detect_disk_free_gb_uses_the_windows_system_drive(monkeypatch):
    seen = []

    def _usage(path):
        seen.append(path)
        return SimpleNamespace(free=100 * 1_073_741_824)

    _patch(monkeypatch, "platform", _ModuleShim(platform, system=lambda: "Windows"))
    _patch(monkeypatch, "shutil", _ModuleShim(shutil, disk_usage=_usage))

    assert setup._detect_disk_free_gb() == 100.0
    assert seen == ["C:\\"]


def test_detect_disk_free_gb_returns_zero_when_the_probe_fails(monkeypatch):
    def _boom(path):
        raise OSError("no such volume")

    _patch(monkeypatch, "shutil", _ModuleShim(shutil, disk_usage=_boom))

    assert setup._detect_disk_free_gb() == 0.0


# ── gpu / cuda / wsl / tools ──────────────────────────────────────────────────

def test_detect_gpu_reads_nvidia_smi_and_skips_malformed_rows(monkeypatch):
    _patch(monkeypatch, "platform", _ModuleShim(platform, system=lambda: "Linux"))
    _patch(monkeypatch, "_which_any", lambda binary: "/usr/bin/nvidia-smi" if binary == "nvidia-smi" else None)
    _patch(
        monkeypatch,
        "_cmd",
        _fake_cmd({"query-gpu": "NVIDIA GeForce RTX 4090, 24576\nbroken-row\n\n"}),
    )

    gpu = setup._detect_gpu()

    assert gpu["vendor"] == "nvidia"
    assert gpu["name"] == "NVIDIA GeForce RTX 4090"
    assert gpu["vram_mb"] == 24576
    assert gpu["vram_gb"] == 24.0
    assert gpu["backend"] == "cuda"
    assert len(gpu["devices"]) == 1


def test_detect_gpu_uses_powershell_on_windows_and_dedupes(monkeypatch):
    _patch(monkeypatch, "platform", _ModuleShim(platform, system=lambda: "Windows"))
    _patch(
        monkeypatch,
        "_which_any",
        lambda binary: {"nvidia-smi": "/nvidia-smi", "powershell": "/powershell"}.get(binary),
    )
    _patch(
        monkeypatch,
        "_cmd",
        _fake_cmd(
            {
                "query-gpu": "NVIDIA GeForce RTX 4090, 24576",
                "Win32_VideoController": (
                    '[{"Name":"NVIDIA GeForce RTX 4090","AdapterRAM":25769803776},'
                    '{"Name":"Intel UHD Graphics 770","AdapterRAM":1073741824}]'
                ),
            }
        ),
    )

    gpu = setup._detect_gpu()

    names = [device["name"] for device in gpu["devices"]]
    assert names == ["NVIDIA GeForce RTX 4090", "Intel UHD Graphics 770"]
    assert gpu["devices"][1]["vendor"] == "intel"
    assert gpu["devices"][1]["backend"] == "directml/vulkan"
    assert gpu["vendor"] == "nvidia"


def test_detect_gpu_falls_back_to_wmic_when_no_shell_is_available(monkeypatch):
    _patch(monkeypatch, "platform", _ModuleShim(platform, system=lambda: "Windows"))
    _patch(monkeypatch, "_which_any", lambda binary: None)
    _patch(
        monkeypatch,
        "_cmd",
        _fake_cmd(
            {
                "win32_VideoController": (
                    "Name=AMD Radeon RX 7900\n"
                    "AdapterRAM=21474836480\n"
                    "Name=NVIDIA GeForce RTX 4090\n"
                    "AdapterRAM=25769803776\n"
                    "Name=Mystery Display Device\n"
                    "AdapterRAM=0\n"
                )
            }
        ),
    )

    gpu = setup._detect_gpu()

    assert [device["vendor"] for device in gpu["devices"]] == ["amd", "nvidia", "unknown"]
    assert gpu["devices"][0]["backend"] == "directml/vulkan"
    assert gpu["devices"][1]["backend"] == "cuda"
    assert gpu["devices"][2]["backend"] == "cpu"
    assert gpu["vendor"] == "nvidia"


def test_detect_gpu_reads_the_darwin_display_profiler(monkeypatch):
    _patch(monkeypatch, "platform", _ModuleShim(platform, system=lambda: "Darwin"))
    _patch(monkeypatch, "_which_any", lambda binary: None)
    _patch(
        monkeypatch,
        "_cmd",
        _fake_cmd({"SPDisplaysDataType": "Graphics/Displays:\n      Chipset Model: Apple M3\n      Type: GPU\n"}),
    )

    gpu = setup._detect_gpu()

    assert gpu["vendor"] == "apple"
    assert gpu["name"] == "Apple M3"
    assert gpu["backend"] == "metal/mlx"


def test_detect_gpu_reads_lspci_on_linux(monkeypatch):
    _patch(monkeypatch, "platform", _ModuleShim(platform, system=lambda: "Linux"))
    _patch(monkeypatch, "_which_any", lambda binary: None)
    _patch(
        monkeypatch,
        "_cmd",
        _fake_cmd(
            {
                "lspci": (
                    "00:1f.3 Audio device: Intel Corporation HDA\n"
                    "01:00.0 VGA compatible controller: NVIDIA Corporation GA102\n"
                    "02:00.0 Display controller: Advanced Micro Devices Radeon\n"
                    "03:00.0 3D controller: Intel Corporation Arc A770\n"
                    "04:00.0 VGA compatible controller: Matrox G200\n"
                )
            }
        ),
    )

    gpu = setup._detect_gpu()

    vendors = [device["vendor"] for device in gpu["devices"]]
    assert vendors == ["nvidia", "amd", "intel"]
    assert gpu["vram_gb"] == 0.0
    assert gpu["backend"] == "cuda"


def test_detect_gpu_reports_nothing_when_no_probe_answers(monkeypatch):
    _patch(monkeypatch, "platform", _ModuleShim(platform, system=lambda: "Linux"))
    _patch(monkeypatch, "_which_any", lambda binary: None)
    _patch(monkeypatch, "_cmd", _fake_cmd({}))

    assert setup._detect_gpu() == {
        "devices": [],
        "vendor": "none",
        "name": "",
        "vram_mb": 0,
        "vram_gb": 0.0,
        "backend": "cpu",
    }


def test_detect_cuda_reports_driver_and_release_versions(monkeypatch):
    _patch(
        monkeypatch,
        "_which_any",
        lambda binary: {"nvidia-smi": "/nvidia-smi", "nvcc": "/nvcc"}.get(binary),
    )
    _patch(
        monkeypatch,
        "_cmd",
        _fake_cmd({"driver_version": "550.54.15\n", "--version": "Cuda compilation tools, release 12.4, V12.4.99"}),
    )

    cuda = setup._detect_cuda()

    assert cuda["available"] is True
    assert cuda["version"] == "12.4"
    assert cuda["nvidia_smi"] == "/nvidia-smi"


def test_detect_wsl_recognises_a_microsoft_kernel(monkeypatch):
    _patch(monkeypatch, "platform", _ModuleShim(platform, system=lambda: "Linux"))
    _patch_paths(monkeypatch, {"/proc/version": "Linux version 5.15.0-microsoft-standard-WSL2"})

    assert setup._detect_wsl() == {"is_wsl": True, "version": "2"}


def test_detect_wsl_survives_a_missing_proc_version(monkeypatch):
    _patch(monkeypatch, "platform", _ModuleShim(platform, system=lambda: "Darwin"))
    _patch_paths(monkeypatch, {"/proc/version": OSError("no /proc")})

    assert setup._detect_wsl() == {"is_wsl": False, "version": ""}


def test_detect_tools_and_mlx_and_api_keys(monkeypatch):
    _patch(monkeypatch, "repair_path_for", lambda binary=None: [])
    _patch(monkeypatch, "_which_any", lambda binary: "/usr/bin/git" if binary == "git" else None)
    _patch(monkeypatch, "_module_available", lambda module: module == "mlx")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("TOGETHER_API_KEY", raising=False)

    tools = setup._detect_tools()

    assert tools["git"] is True
    assert tools["ollama"] is False
    assert setup._detect_mlx() == {"available": True, "mlx_vlm": False}
    assert setup._detect_api_keys() == {
        "openai": True,
        "openrouter": False,
        "groq": False,
        "together": False,
    }


def test_scan_environment_composes_every_detector(monkeypatch):
    _patch(
        monkeypatch,
        "platform",
        _ModuleShim(platform, system=lambda: "Darwin", mac_ver=lambda: ("15.3", ("", "", ""), "arm64")),
    )
    _patch(monkeypatch, "_detect_chip", lambda: {"name": "Apple M3", "is_apple_silicon": True})
    _patch(monkeypatch, "_detect_cpu", lambda: {"model": "Apple M3", "logical_cores": 12})
    _patch(monkeypatch, "_detect_gpu", lambda: {"vendor": "apple", "vram_gb": 0.0})
    _patch(monkeypatch, "_detect_cuda", lambda: {"available": False, "version": ""})
    _patch(monkeypatch, "_detect_wsl", lambda: {"is_wsl": False, "version": ""})
    _patch(monkeypatch, "_detect_tools", lambda: {"python3": True, "brew": True})
    _patch(monkeypatch, "_detect_ram_gb", lambda: 36.0)
    _patch(monkeypatch, "_detect_disk_free_gb", lambda: 210.0)
    _patch(monkeypatch, "_which_any", lambda binary: "/opt/homebrew/bin/" + binary)
    _patch(monkeypatch, "_module_available", lambda module: module == "mlx")
    monkeypatch.setenv("PATH", "/opt/homebrew/bin")
    monkeypatch.setenv("LATTICEAI_EXTRA_PATH", "/opt/extra")

    env = setup.scan_environment()

    assert env["os"] == "Darwin"
    assert env["os_version"] == "15.3"
    assert env["ram_gb"] == 36.0
    assert env["disk_free_gb"] == 210.0
    assert env["components"]["python"]["path"] == "/opt/homebrew/bin/python3"
    assert env["components"]["mlx"]["installed"] is True
    assert env["components"]["mlx_vlm"]["module_available"] is False
    assert env["components"]["cuda"]["available"] is False
    assert env["path"] == {"active": "/opt/homebrew/bin", "extra": "/opt/extra"}
    assert env["mlx"] == {"available": True, "mlx_vlm": False}
