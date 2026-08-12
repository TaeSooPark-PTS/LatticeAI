"""Hardware, OS, toolchain and API-key detection for the setup wizard.

Everything the recommender needs to know about *this* machine: chip, CPU
features, RAM, free disk, GPU/CUDA/WSL, which CLIs are on PATH, whether MLX
imports, and which provider keys are in the environment. Every probe is
best-effort — a failed probe answers "unknown", never raises.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from latticeai.core.quiet import quiet
from latticeai.services.setup_detection import (
    detect_cuda,
    detect_tools,
    detect_wsl_from_text,
    parse_windows_cpu_info,
    windows_processor_features,
)
from latticeai.services.setup_detection import (
    parse_windows_video_controllers as _parse_windows_video_controllers,
)
from latticeai.setup.wizard.paths import (
    _component_detail,
    _module_available,
    _which_any,
    repair_path_for,
)


def _cmd(args: List[str], timeout: int = 10) -> str:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        return (r.stdout or r.stderr or "").strip()
    except Exception:
        return ""

# ── Environment Detection ─────────────────────────────────────────────────────

def _detect_chip() -> Dict[str, Any]:
    arch = platform.machine()
    is_apple = arch == "arm64" and platform.system() == "Darwin"
    name = "Unknown CPU"
    gen: Any = None

    if is_apple:
        profiler = _cmd(["system_profiler", "SPHardwareDataType"], timeout=8)
        m = re.search(r"Chip:\s+(Apple M\S+)", profiler)
        name = m.group(1) if m else "Apple Silicon"
        gm = re.search(r"M(\d+)", name)
        gen = int(gm.group(1)) if gm else 1
    else:
        brand = ""
        if platform.system() == "Darwin":
            brand = _cmd(["sysctl", "-n", "machdep.cpu.brand_string"])
        elif platform.system() == "Windows":
            raw = _cmd(["wmic", "cpu", "get", "Name", "/value"], timeout=5)
            if "Name=" in raw:
                brand = raw.split("Name=", 1)[-1].splitlines()[0].strip()
        elif platform.system() == "Linux":
            try:
                for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.lower().startswith("model name"):
                        brand = line.split(":", 1)[-1].strip()
                        break
            except Exception:
                quiet()
        name = brand or platform.processor() or "Unknown CPU"

    return {"name": name, "arch": arch, "is_apple_silicon": is_apple, "gen": gen}


def _detect_cpu() -> Dict[str, Any]:
    flags: List[str] = []
    physical_cores = os.cpu_count() or 0
    logical_cores = os.cpu_count() or 0
    model = _detect_chip()["name"]
    if platform.system() == "Darwin":
        flags = [item.lower() for item in _cmd(["sysctl", "-n", "machdep.cpu.features"], timeout=5).split()]
        try:
            physical_cores = int(_cmd(["sysctl", "-n", "hw.physicalcpu"], timeout=5) or physical_cores)
            logical_cores = int(_cmd(["sysctl", "-n", "hw.logicalcpu"], timeout=5) or logical_cores)
        except ValueError:
            quiet()
    elif platform.system() == "Linux":
        try:
            text = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                if line.lower().startswith(("flags", "features")):
                    flags = line.split(":", 1)[-1].strip().lower().split()
                    break
        except Exception:
            quiet()
    elif platform.system() == "Windows":
        raw = _cmd(["wmic", "cpu", "get", "Name,NumberOfCores,NumberOfLogicalProcessors", "/format:list"], timeout=5)
        model, physical_cores, logical_cores = parse_windows_cpu_info(
            raw, model=model, physical_cores=physical_cores, logical_cores=logical_cores,
        )
        flags.extend(windows_processor_features())
    interesting = {"avx", "avx2", "avx512f", "fma", "neon", "sse4_2"}
    if platform.system() == "Windows":
        interesting.update({"sse", "sse2", "sse3", "rdrand"})
    return {
        "model": model,
        "physical_cores": physical_cores,
        "logical_cores": logical_cores,
        "instructions": sorted({flag for flag in flags if flag in interesting}),
    }

def _detect_ram_gb() -> float:
    if platform.system() == "Windows":
        raw = _cmd(["wmic", "ComputerSystem", "get", "TotalPhysicalMemory", "/format:list"], timeout=5)
        for line in raw.splitlines():
            if line.startswith("TotalPhysicalMemory="):
                try:
                    return round(int(line.split("=", 1)[-1].strip()) / 1_073_741_824, 1)
                except ValueError:
                    break
    raw = _cmd(["sysctl", "-n", "hw.memsize"])
    if raw:
        try:
            return round(int(raw) / 1_073_741_824, 1)
        except ValueError:
            quiet()
    if platform.system() == "Darwin":
        profiler = _cmd(["system_profiler", "SPHardwareDataType"], timeout=8)
        m = re.search(r"Memory:\s+([\d.]+)\s*(TB|GB|MB)", profiler, re.IGNORECASE)
        if m:
            value = float(m.group(1))
            unit = m.group(2).lower()
            if unit == "tb":
                return round(value * 1024, 1)
            if unit == "gb":
                return round(value, 1)
            return round(value / 1024, 1)
        hostinfo = _cmd(["hostinfo"], timeout=5)
        m = re.search(r"Primary memory available:\s+([\d.]+)\s+gigabytes", hostinfo, re.IGNORECASE)
        if m:
            return round(float(m.group(1)), 1)
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 1_048_576, 1)
    except Exception:
        quiet()
    return 0.0

def _detect_disk_free_gb() -> float:
    try:
        path = "C:\\" if platform.system() == "Windows" else "/"
        return round(shutil.disk_usage(path).free / 1_073_741_824, 1)
    except Exception:
        return 0.0


def _detect_gpu() -> Dict[str, Any]:
    devices: List[Dict[str, Any]] = []
    nvidia_smi = _which_any("nvidia-smi")
    if nvidia_smi:
        info = _cmd([nvidia_smi, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"], timeout=8)
        for line in [item.strip() for item in info.splitlines() if item.strip()]:
            try:
                name, mem = [part.strip() for part in line.split(",", 1)]
                devices.append({"vendor": "nvidia", "name": name, "vram_mb": int(float(mem)), "backend": "cuda"})
            except Exception:
                quiet()
                continue

    if platform.system() == "Windows":
        shell = _which_any("powershell") or _which_any("pwsh")
        raw = ""
        if shell:
            raw = _cmd([
                shell, "-NoProfile", "-Command",
                "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM | ConvertTo-Json -Compress",
            ], timeout=8)
        if not raw:
            raw = _cmd(["wmic", "path", "win32_VideoController", "get", "Name,AdapterRAM", "/format:list"], timeout=8)
        for item in _parse_windows_video_controllers(raw):
            if any(existing.get("name") == item["name"] for existing in devices):
                continue
            low = item["name"].lower()
            vendor, backend = "unknown", "cpu"
            if "nvidia" in low or "geforce" in low or "rtx" in low:
                vendor, backend = "nvidia", "cuda"
            elif "amd" in low or "radeon" in low:
                vendor, backend = "amd", "directml/vulkan"
            elif "intel" in low or "arc" in low or "iris" in low:
                vendor, backend = "intel", "directml/vulkan"
            devices.append({"vendor": vendor, "name": item["name"], "vram_mb": item["vram_mb"], "backend": backend})
    elif platform.system() == "Darwin":
        sp = _cmd(["system_profiler", "SPDisplaysDataType"], timeout=8)
        for line in sp.splitlines():
            if "Chipset Model" in line:
                devices.append({"vendor": "apple", "name": line.split(":", 1)[-1].strip(), "vram_mb": 0, "backend": "metal/mlx"})
                break
    elif platform.system() == "Linux" and not devices:
        info = _cmd(["lspci"], timeout=5)
        for line in info.splitlines():
            low = line.lower()
            if not any(token in low for token in ("vga", "3d controller", "display")):
                continue
            if "nvidia" in low:
                devices.append({"vendor": "nvidia", "name": line.strip(), "vram_mb": 0, "backend": "cuda"})
            elif "amd" in low or "advanced micro devices" in low or "radeon" in low:
                devices.append({"vendor": "amd", "name": line.strip(), "vram_mb": 0, "backend": "rocm/vulkan"})
            elif "intel" in low:
                devices.append({"vendor": "intel", "name": line.strip(), "vram_mb": 0, "backend": "vulkan"})

    primary = max(devices, key=lambda item: int(item.get("vram_mb") or 0), default={})
    vram_mb = int(primary.get("vram_mb") or 0)
    return {
        "devices": devices,
        "vendor": primary.get("vendor", "none"),
        "name": primary.get("name", ""),
        "vram_mb": vram_mb,
        "vram_gb": round(vram_mb / 1024, 1),
        "backend": primary.get("backend", "cpu"),
    }


def _detect_cuda() -> Dict[str, Any]:
    available, version, nvidia_smi, nvcc = detect_cuda(_which_any, lambda args: _cmd(args, timeout=5))
    return {"available": available, "nvidia_smi": nvidia_smi, "nvcc": nvcc, "version": version}


def _detect_wsl() -> Dict[str, Any]:
    raw = ""
    try:
        raw = Path("/proc/version").read_text(encoding="utf-8", errors="replace")
    except Exception:
        quiet()
    is_wsl, version = detect_wsl_from_text(platform.system().lower(), raw)
    return {"is_wsl": is_wsl, "version": version}


def _detect_tools() -> Dict[str, bool]:
    repair_path_for()
    detected = detect_tools(_which_any, ["brew", "ollama", "python3", "python", "node", "npm", "git", "tesseract", "lms", "nvidia-smi", "nvcc"])
    return {tool: path is not None for tool, path in detected.items()}

def _detect_mlx() -> Dict[str, Any]:
    return {
        "available": _module_available("mlx"),
        "mlx_vlm": _module_available("mlx_vlm"),
    }

def _detect_api_keys() -> Dict[str, bool]:
    return {
        "openai":     bool(os.getenv("OPENAI_API_KEY")),
        "openrouter": bool(os.getenv("OPENROUTER_API_KEY")),
        "groq":       bool(os.getenv("GROQ_API_KEY")),
        "together":   bool(os.getenv("TOGETHER_API_KEY")),
    }

def scan_environment() -> Dict[str, Any]:
    chip = _detect_chip()
    cpu = _detect_cpu()
    gpu = _detect_gpu()
    cuda = _detect_cuda()
    wsl = _detect_wsl()
    tools = _detect_tools()
    python_binary = "python3" if tools.get("python3") else "python"
    return {
        "os":           platform.system(),
        "os_version":   platform.mac_ver()[0] if platform.system() == "Darwin" else platform.version(),
        "chip":         chip,
        "cpu":          cpu,
        "gpu":          gpu,
        "cuda":         cuda,
        "wsl":          wsl,
        "ram_gb":       _detect_ram_gb(),
        "disk_free_gb": _detect_disk_free_gb(),
        "tools":        tools,
        "components": {
            "homebrew": _component_detail("homebrew", "brew"),
            "python": {**_component_detail("python", python_binary), "version": platform.python_version()},
            "node": _component_detail("node", "node"),
            "npm": _component_detail("node", "npm"),
            "git": _component_detail("git", "git"),
            "ollama": _component_detail("ollama", "ollama"),
            "lmstudio": _component_detail("lmstudio", "lms"),
            "cuda": {**_component_detail("cuda", "nvcc"), **cuda},
            "tesseract": _component_detail("tesseract", "tesseract"),
            "mlx": _component_detail("mlx", module="mlx"),
            "mlx_vlm": _component_detail("mlx", module="mlx_vlm"),
        },
        "path": {
            "active": os.environ.get("PATH", ""),
            "extra": os.environ.get("LATTICEAI_EXTRA_PATH", ""),
        },
        "mlx":          _detect_mlx(),
        "api_keys":     _detect_api_keys(),
    }
