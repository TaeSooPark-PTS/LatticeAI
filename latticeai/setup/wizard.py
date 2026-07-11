"""
Smart Setup Wizard — Environment Scanner, Recommender & Auto-Installer
Detects hardware, tools, and API keys; returns tailored recommendations;
streams SSE installation progress.

Formerly the root ``setup.py``; renamed in v4 so it no longer collides with
the setuptools build entrypoint and actually ships in the wheel
(``pyproject.toml`` py-modules). Packaging is owned entirely by
``pyproject.toml`` — there is deliberately no root ``setup.py``.
"""

import asyncio
import json as _json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Tuple

from latticeai.services.process_audit import (
    CommandConfirmationError,
    append_process_audit_event,
    command_plan,
    command_plan_for_commands,
    require_command_confirmation,
)
from latticeai.services.setup_detection import (
    detect_cuda,
    detect_tools,
    detect_wsl_from_text,
    parse_windows_video_controllers as _parse_windows_video_controllers,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _cmd(args: List[str], timeout: int = 10) -> str:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        return (r.stdout or r.stderr or "").strip()
    except Exception:
        return ""

def _sse(data: Dict) -> str:
    return f"data: {_json.dumps(data, ensure_ascii=False)}\n\n"


def _action_commands(action: Dict[str, Any]) -> List[List[str]]:
    atype = action.get("type")
    if atype == "pip":
        return [[sys.executable, "-m", "pip", "install", "--upgrade", str(pkg)] for pkg in action.get("packages", [])]
    if atype == "brew":
        package = str(action.get("package") or "")
        return [["brew", "install", package]] if package else []
    return []


def _action_command_plan(action: Dict[str, Any], *, name: str) -> Dict[str, Any] | None:
    commands = _action_commands(action)
    if not commands:
        return None
    return command_plan_for_commands(
        commands,
        name=name,
        purpose="setup_wizard_install",
        metadata={"action_type": action.get("type")},
    )


def _attach_action_plan(action: Dict[str, Any] | None, *, name: str) -> Dict[str, Any] | None:
    if not isinstance(action, dict):
        return action
    plan = _action_command_plan(action, name=name)
    if not plan:
        return action
    hydrated = dict(action)
    hydrated["command_plan"] = plan
    hydrated["confirmation_token"] = plan["confirmation_token"]
    return hydrated


def _hydrate_install_actions(groups: Dict[str, Any]) -> Dict[str, Any]:
    for group_name in ("components", "engines", "models", "mcps"):
        items = groups.get(group_name)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                item["action"] = _attach_action_plan(
                    item.get("action"),
                    name=str(item.get("id") or item.get("name") or group_name),
                )
    return groups


def _verify_action_confirmation(action: Dict[str, Any], token: str | None, *, name: str) -> bool:
    plan = _action_command_plan(action, name=name)
    if not plan:
        return True
    return str(token or "").strip() == plan["confirmation_token"]

OFFICIAL_DOWNLOADS: Dict[str, str] = {
    "homebrew": "https://brew.sh",
    "python": "https://www.python.org/downloads/",
    "node": "https://nodejs.org/en/download",
    "git": "https://git-scm.com/downloads",
    "ollama": "https://ollama.com/download",
    "lmstudio": "https://lmstudio.ai/download",
    "cuda": "https://developer.nvidia.com/cuda-downloads",
    "mlx": "https://ml-explore.github.io/mlx/build/html/install.html",
    "cloudflared": "https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/",
    "tesseract": "https://tesseract-ocr.github.io/tessdoc/Installation.html",
}

COMMON_PATH_DIRS = [
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    str(Path.home() / ".local" / "bin"),
    str(Path.home() / ".cargo" / "bin"),
    str(Path.home() / ".latticeai" / "bin"),
]

if platform.system() == "Windows":
    _local_appdata = os.environ.get("LOCALAPPDATA", "")
    _program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    _program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    COMMON_PATH_DIRS.extend([
        str(Path(_local_appdata) / "Programs" / "Ollama") if _local_appdata else "",
        str(Path(_program_files) / "Ollama"),
        str(Path(_program_files) / "LM Studio"),
        str(Path(_program_files) / "NVIDIA Corporation" / "NVSMI"),
        str(Path(_program_files_x86) / "NVIDIA Corporation" / "NVSMI"),
    ])
    COMMON_PATH_DIRS = [p for p in COMMON_PATH_DIRS if p]

WINDOWS_BINARY_CANDIDATES: Dict[str, List[str]] = {
    "ollama": [
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"),
        str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Ollama" / "ollama.exe"),
    ],
    "lms": [
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "LM Studio" / "resources" / "app" / ".webpack" / "lms.exe"),
        str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "LM Studio" / "resources" / "app" / ".webpack" / "lms.exe"),
    ],
    "nvidia-smi": [
        str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe"),
        str(Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe"),
    ],
}

PACKAGE_MODULES: Dict[str, str] = {
    "mlx-vlm": "mlx_vlm",
    "huggingface_hub[cli]": "huggingface_hub",
    "openai-whisper": "whisper",
}


def _project_env_file() -> Path:
    return Path(__file__).resolve().parents[2] / ".env"


def _update_env_file(env_file: Path, key: str, value: str) -> None:
    lines: List[str] = []
    found = False
    if env_file.exists():
        lines = env_file.read_text(encoding="utf-8").splitlines()
    updated: List[str] = []
    for line in lines:
        if line.startswith(f"{key}="):
            updated.append(f"{key}={value}")
            found = True
        else:
            updated.append(line)
    if not found:
        updated.append(f"{key}={value}")
    env_file.write_text("\n".join(updated) + "\n", encoding="utf-8")


def _merge_path_dirs(dirs: List[str]) -> List[str]:
    current = os.environ.get("PATH", "")
    parts = [p for p in current.split(os.pathsep) if p]
    for item in dirs:
        expanded = str(Path(item).expanduser())
        if Path(expanded).exists() and expanded not in parts:
            parts.insert(0, expanded)
    os.environ["PATH"] = os.pathsep.join(parts)
    return parts


def _persist_extra_path(dirs: List[str]) -> None:
    existing = [
        p for p in os.environ.get("LATTICEAI_EXTRA_PATH", "").split(os.pathsep)
        if p
    ]
    merged = existing[:]
    for item in dirs:
        expanded = str(Path(item).expanduser())
        if Path(expanded).exists() and expanded not in merged:
            merged.append(expanded)
    if merged:
        os.environ["LATTICEAI_EXTRA_PATH"] = os.pathsep.join(merged)
        _update_env_file(_project_env_file(), "LATTICEAI_EXTRA_PATH", os.environ["LATTICEAI_EXTRA_PATH"])


def repair_path_for(binary: str | None = None) -> List[str]:
    before = _which_any(binary) if binary else None
    paths = _merge_path_dirs(COMMON_PATH_DIRS)
    if binary and not before and _which_any(binary):
        _persist_extra_path(COMMON_PATH_DIRS)
    return paths


def _which_any(binary: str) -> str | None:
    path = shutil.which(binary)
    if path:
        return path
    if platform.system() == "Windows":
        for candidate in WINDOWS_BINARY_CANDIDATES.get(binary, []):
            if candidate and Path(candidate).exists():
                return candidate
    return None


def _which_detail(binary: str) -> Dict[str, Any]:
    path = _which_any(binary)
    return {"installed": path is not None, "path": path}


def _module_available(module_name: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(module_name) is not None


def _package_module(package: str) -> str:
    return PACKAGE_MODULES.get(package, package.replace("-", "_").split("[", 1)[0])


def _component_detail(name: str, binary: str | None = None, module: str | None = None) -> Dict[str, Any]:
    detail: Dict[str, Any] = {"official_url": OFFICIAL_DOWNLOADS.get(name)}
    if binary:
        detail.update(_which_detail(binary))
    if module:
        detail["module_available"] = _module_available(module)
        detail["installed"] = bool(detail.get("installed") or detail["module_available"])
    return detail


def _verify_binary(binary: str, version_args: List[str] | None = None, timeout: int = 20) -> Tuple[bool, str]:
    repair_path_for(binary)
    found = _which_any(binary)
    if not found:
        return False, f"{binary} 실행 파일을 PATH에서 찾지 못했습니다."
    args = [found, *(version_args or ["--version"])]
    try:
        completed = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except Exception as e:
        return False, str(e)
    output = (completed.stdout or completed.stderr or "").strip().splitlines()
    if completed.returncode == 0:
        return True, output[0] if output else found
    return False, (completed.stderr or completed.stdout or f"returncode={completed.returncode}")[-400:]


async def _wait_for_binary(binary: str, seconds: int = 300) -> Tuple[bool, str]:
    deadline = time.time() + seconds
    while time.time() < deadline:
        ok, msg = _verify_binary(binary)
        if ok:
            return True, msg
        await asyncio.sleep(2)
    return False, f"{binary} 설치 완료를 제한 시간 안에 감지하지 못했습니다."

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
                pass
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
            pass
    elif platform.system() == "Linux":
        try:
            text = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                if line.lower().startswith(("flags", "features")):
                    flags = line.split(":", 1)[-1].strip().lower().split()
                    break
        except Exception:
            pass
    elif platform.system() == "Windows":
        raw = _cmd(["wmic", "cpu", "get", "Name,NumberOfCores,NumberOfLogicalProcessors", "/format:list"], timeout=5)
        for line in raw.splitlines():
            key, _, value = line.partition("=")
            if key == "Name" and value.strip():
                model = value.strip()
            elif key == "NumberOfCores" and value.strip():
                try:
                    physical_cores = int(value.strip())
                except ValueError:
                    pass
            elif key == "NumberOfLogicalProcessors" and value.strip():
                try:
                    logical_cores = int(value.strip())
                except ValueError:
                    pass
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            feature_map = {
                6: "sse",
                10: "sse2",
                13: "sse3",
                19: "neon",
                28: "rdrand",
            }
            flags.extend(name for code, name in feature_map.items() if kernel32.IsProcessorFeaturePresent(code))
        except Exception:
            pass
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
            pass
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
        pass
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
        pass
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

# ── Model Catalog ─────────────────────────────────────────────────────────────
# (model_id, display_name, size_gb, tag, description, min_ram_gb)
_MODEL_CATALOG = [
    ("mlx-community/Qwen3-VL-4B-Instruct-4bit",      "Qwen3-VL 4B",          2.7,  "VLM",     "최신 Qwen 멀티모달 · 저사양",       4),
    ("mlx-community/gemma-4-e2b-it-4bit",            "Gemma 4 E2B",          3.6,  "VLM",     "Gemma 4 소형 멀티모달",            8),
    ("mlx-community/Qwen3-VL-8B-Instruct-4bit",      "Qwen3-VL 8B",          4.8,  "VLM",     "최신 Qwen 멀티모달 · 균형 추천",    16),
    ("mlx-community/gemma-4-12b-it-4bit",            "Gemma 4 12B",          7.6,  "VLM",     "Gemma 4 기본 추천 · 4bit",         16),
    ("mlx-community/Llama-4-Scout-17B-16E-Instruct-4bit", "Llama 4 Scout",   11.8, "VLM",     "Meta 최신 멀티모달 Scout",          24),
    ("mlx-community/gemma-4-26b-a4b-it-4bit",        "Gemma 4 26B",         15.6,  "VLM",     "이미지 지원 · 대형 추천",        32),
    ("mlx-community/gemma-4-31b-it-4bit",            "Gemma 4 31B",         18.4,  "VLM+",    "Gemma 4 최신 31B instruct",      48),
    ("mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit", "Qwen3-VL 30B A3B",    18.0,  "VLM+",    "최신 Qwen 대형 멀티모달",        48),
]

_CROSS_PLATFORM_MODEL_CATALOG: Dict[str, List[Tuple[str, str, float, str, str, int]]] = {
    "ollama": [
        ("ollama:qwen3-vl:4b", "Qwen3-VL 4B", 2.7, "VLM", "Ollama 멀티모달 · 저사양", 4),
        ("ollama:qwen3-vl:8b", "Qwen3-VL 8B", 4.8, "VLM", "Ollama 멀티모달 · 균형 추천", 16),
        ("ollama:hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_K_M", "Gemma 4 12B Q4", 7.9, "VLM", "Hugging Face GGUF 기반 Gemma 4", 16),
        ("ollama:hf.co/ggml-org/gemma-4-31B-it-GGUF:Q4_K_M", "Gemma 4 31B Q4", 18.7, "VLM+", "Hugging Face GGUF 기반 Gemma 4", 48),
        ("ollama:hf.co/ggml-org/Llama-4-Scout-17B-16E-Instruct-GGUF:Q4_K_M", "Llama 4 Scout Q4", 12.0, "VLM", "Meta 최신 멀티모달 Scout", 24),
    ],
    "lmstudio": [
        ("lmstudio:Qwen/Qwen3-VL-4B-Instruct", "Qwen3-VL 4B", 2.7, "VLM", "LM Studio 멀티모달 · 저사양", 4),
        ("lmstudio:Qwen/Qwen3-VL-8B-Instruct", "Qwen3-VL 8B", 4.8, "VLM", "LM Studio 멀티모달 · 균형 추천", 16),
        ("lmstudio:ggml-org/gemma-4-12B-it-GGUF", "Gemma 4 12B 4-bit", 7.9, "VLM", "LM Studio GGUF Gemma 4", 16),
        ("lmstudio:ggml-org/gemma-4-31B-it-GGUF", "Gemma 4 31B 4-bit", 18.7, "VLM+", "LM Studio GGUF Gemma 4", 48),
        ("lmstudio:Qwen/Qwen3-VL-30B-A3B-Instruct", "Qwen3-VL 30B A3B", 18.0, "VLM+", "대형 Qwen 멀티모달 · 24GB+ VRAM 권장", 32),
        ("lmstudio:meta-llama/Llama-4-Scout-17B-16E-Instruct", "Llama 4 Scout", 12.0, "VLM", "Meta 최신 멀티모달 Scout", 24),
    ],
    "vllm": [
        ("vllm:Qwen/Qwen3-VL-4B-Instruct", "Qwen3-VL 4B", 2.7, "VLM", "내 컴퓨터 GPU 실행 도구 권장", 4),
        ("vllm:Qwen/Qwen3-VL-8B-Instruct", "Qwen3-VL 8B", 4.8, "VLM", "내 컴퓨터 NVIDIA 실행 도구 권장", 16),
        ("vllm:google/gemma-4-12b-it", "Gemma 4 12B", 7.6, "VLM", "Gemma 4 기본 추천 · 4bit", 16),
        ("vllm:Qwen/Qwen3-VL-30B-A3B-Instruct", "Qwen3-VL 30B A3B", 18.0, "VLM+", "대형 Qwen 멀티모달 · 24GB+ VRAM 권장", 32),
        ("vllm:suitch/gemma-4-31B-it-4bit", "Gemma 4 31B", 18.7, "VLM+", "Gemma 4 최신 31B instruct", 48),
        ("vllm:meta-llama/Llama-4-Scout-17B-16E-Instruct", "Llama 4 Scout", 12.0, "VLM", "Meta 최신 멀티모달 Scout", 24),
    ],
    "llamacpp": [
        ("llamacpp:Qwen/Qwen3-VL-4B-Instruct-GGUF", "Qwen3-VL 4B GGUF", 2.7, "GGUF", "CPU/Vulkan 백업 · 멀티모달 GGUF", 4),
        ("llamacpp:Qwen/Qwen3-VL-8B-Instruct-GGUF", "Qwen3-VL 8B GGUF", 4.8, "GGUF", "CPU/Vulkan 백업 · 균형형", 16),
        ("llamacpp:ggml-org/gemma-4-12B-it-GGUF", "Gemma 4 12B GGUF", 7.9, "GGUF", "Gemma 4 12B Q4_K_M", 16),
        ("llamacpp:ggml-org/gemma-4-31B-it-GGUF", "Gemma 4 31B GGUF", 18.7, "GGUF", "Gemma 4 31B Q4_K_M", 48),
        ("llamacpp:ggml-org/Llama-4-Scout-17B-16E-Instruct-GGUF", "Llama 4 Scout GGUF", 12.0, "GGUF", "Meta 최신 멀티모달 Scout", 24),
    ],
}

_VERSIONED_MODEL_PATTERNS = (
    ("gemma", re.compile(r"\bgemma[-\s]?(\d+(?:\.\d+)?)", re.IGNORECASE)),
    ("qwen", re.compile(r"\bqwen[-\s]?(\d+(?:\.\d+)?)", re.IGNORECASE)),
    ("llama", re.compile(r"\bllama[-\s]?(\d+(?:\.\d+)?)", re.IGNORECASE)),
)

_BEST_MODEL_TIERS: Dict[str, List[Tuple[int, str]]] = {
    "local_mlx": [
        (48, "mlx-community/gemma-4-31b-it-4bit"),
        (32, "mlx-community/gemma-4-26b-a4b-it-4bit"),
        (16, "mlx-community/gemma-4-12b-it-4bit"),
        (16, "mlx-community/Qwen3-VL-8B-Instruct-4bit"),
        (4, "mlx-community/Qwen3-VL-4B-Instruct-4bit"),
    ],
    "ollama": [
        (48, "ollama:hf.co/ggml-org/gemma-4-31B-it-GGUF:Q4_K_M"),
        (16, "ollama:hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_K_M"),
        (16, "ollama:qwen3-vl:8b"),
        (4, "ollama:qwen3-vl:4b"),
    ],
    "lmstudio": [
        (48, "lmstudio:ggml-org/gemma-4-31B-it-GGUF"),
        (16, "lmstudio:ggml-org/gemma-4-12B-it-GGUF"),
        (16, "lmstudio:Qwen/Qwen3-VL-8B-Instruct"),
        (4, "lmstudio:Qwen/Qwen3-VL-4B-Instruct"),
    ],
    "vllm": [
        (48, "vllm:suitch/gemma-4-31B-it-4bit"),
        (16, "vllm:google/gemma-4-12b-it"),
        (16, "vllm:Qwen/Qwen3-VL-8B-Instruct"),
        (4, "vllm:Qwen/Qwen3-VL-4B-Instruct"),
    ],
    "llamacpp": [
        (48, "llamacpp:ggml-org/gemma-4-31B-it-GGUF"),
        (16, "llamacpp:ggml-org/gemma-4-12B-it-GGUF"),
        (16, "llamacpp:Qwen/Qwen3-VL-8B-Instruct-GGUF"),
        (4, "llamacpp:Qwen/Qwen3-VL-4B-Instruct-GGUF"),
    ],
}


def _version_tuple(raw: str) -> Tuple[int, ...]:
    return tuple(int(part) for part in raw.split(".") if part.isdigit())


def _catalog_row_family_version(row: Tuple[str, str, float, str, str, int]) -> Tuple[str, Tuple[int, ...]] | None:
    text = f"{row[0]} {row[1]}"
    for family, pattern in _VERSIONED_MODEL_PATTERNS:
        match = pattern.search(text)
        if match:
            version = _version_tuple(match.group(1))
            if version:
                return family, version
    return None


def _filter_lower_family_versions(
    rows: List[Tuple[str, str, float, str, str, int]],
) -> List[Tuple[str, str, float, str, str, int]]:
    max_versions: Dict[str, Tuple[int, ...]] = {}
    detected: List[Tuple[Tuple[str, str, float, str, str, int], Tuple[str, Tuple[int, ...]] | None]] = []
    for row in rows:
        version_info = _catalog_row_family_version(row)
        detected.append((row, version_info))
        if not version_info:
            continue
        family, version = version_info
        if version > max_versions.get(family, (0,)):
            max_versions[family] = version
    return [
        row for row, version_info in detected
        if not version_info or version_info[1] >= max_versions.get(version_info[0], version_info[1])
    ]


def _best_model_for_engine(engine: str, ram_gb: float, rows: List[Tuple[str, str, float, str, str, int]]) -> str:
    available_ids = {row[0] for row in rows}
    for min_ram, model_id in _BEST_MODEL_TIERS.get(engine, []):
        if ram_gb >= min_ram and model_id in available_ids:
            return model_id
    return rows[0][0] if rows else ""


# ── Recommendation Logic ──────────────────────────────────────────────────────

def get_recommendations(env: Dict[str, Any]) -> Dict[str, Any]:
    ram       = env["ram_gb"]
    chip      = env["chip"]
    mlx       = env["mlx"]
    tools     = env["tools"]
    api_keys  = env["api_keys"]
    disk_free = env["disk_free_gb"]
    is_apple  = chip["is_apple_silicon"]
    gpu       = env.get("gpu", {})
    cuda      = env.get("cuda", {})
    wsl       = env.get("wsl", {})
    cpu       = env.get("cpu", {})
    os_name   = env.get("os", "")

    max_model_gb = ram * 0.72  # ~28% headroom for OS + apps

    if is_apple:
        preferred_engine = "local_mlx"
    elif gpu.get("vendor") == "nvidia" and cuda.get("available") and (os_name == "Linux" or wsl.get("is_wsl")):
        preferred_engine = "vllm"
    elif tools.get("lms"):
        preferred_engine = "lmstudio"
    elif tools.get("ollama"):
        preferred_engine = "ollama"
    else:
        preferred_engine = "llamacpp"

    apple_catalog = _filter_lower_family_versions(_MODEL_CATALOG)
    engine_catalog = (
        []
        if is_apple
        else _filter_lower_family_versions(_CROSS_PLATFORM_MODEL_CATALOG[preferred_engine])
    )
    best_id = _best_model_for_engine(
        "local_mlx" if is_apple else preferred_engine,
        ram,
        apple_catalog if is_apple else engine_catalog,
    )

    # ── Engines ──────────────────────────────────────────────────────────────
    engines: List[Dict] = []

    if is_apple:
        if mlx["available"] and mlx["mlx_vlm"]:
            engines.append({
                "id": "engine_mlx", "name": "MLX",
                "subtitle": f"{chip['name']} GPU 가속 · MLX-VLM 멀티모달 실행",
                "status": "installed", "priority": "recommended",
                "checked": True, "action": None, "badge": "설치됨",
            })
        else:
            engines.append({
                "id": "engine_mlx", "name": "MLX",
                "subtitle": f"{chip['name']} 전용 MLX-VLM 멀티모달 실행",
                "status": "available", "priority": "recommended",
                "checked": True,
                "action": {"type": "pip", "packages": ["mlx-vlm"], "verify_modules": ["mlx", "mlx_vlm"]},
                "badge": "설치 필요",
            })

    if tools.get("ollama"):
        engines.append({
            "id": "engine_ollama", "name": "Ollama",
            "subtitle": "범용 로컬 LLM 서버 · 크로스 플랫폼",
            "status": "installed", "priority": "recommended" if preferred_engine == "ollama" else "optional",
            "checked": preferred_engine == "ollama", "action": None, "badge": "설치됨",
        })
    else:
        hint = "brew install 가능" if (tools.get("brew") or env["os"] == "Darwin") else "수동 설치 필요"
        engines.append({
            "id": "engine_ollama", "name": "Ollama",
            "subtitle": "범용 로컬 LLM 서버 · 크로스 플랫폼",
            "status": "available", "priority": "recommended" if preferred_engine == "ollama" else "optional",
            "checked": preferred_engine == "ollama",
            "action": (
                {"type": "brew", "package": "ollama", "binary": "ollama", "official_url": OFFICIAL_DOWNLOADS["ollama"]}
                if tools.get("brew")
                else {"type": "url", "url": OFFICIAL_DOWNLOADS["ollama"], "binary": "ollama"}
            ),
            "badge": hint,
        })

    if not is_apple:
        lmstudio_installed = bool(tools.get("lms"))
        engines.append({
            "id": "engine_lmstudio", "name": "LM Studio",
            "subtitle": "Windows/macOS/Linux 데스크톱 GPU 서버 · 모델 다운로드 UI 포함",
            "status": "installed" if lmstudio_installed else "available",
            "priority": "recommended" if preferred_engine == "lmstudio" else "optional",
            "checked": preferred_engine == "lmstudio",
            "action": None if lmstudio_installed else {"type": "url", "url": OFFICIAL_DOWNLOADS["lmstudio"], "binary": "lms"},
            "badge": "설치됨" if lmstudio_installed else "설치 필요",
        })
        if gpu.get("vendor") == "nvidia":
            engines.append({
                "id": "engine_cuda", "name": "CUDA",
                "subtitle": f"NVIDIA {gpu.get('name') or 'GPU'} · VRAM {gpu.get('vram_gb') or 0} GB",
                "status": "installed" if cuda.get("available") else "available",
                "priority": "recommended",
                "checked": False,
                "action": None if cuda.get("available") else {"type": "url", "url": OFFICIAL_DOWNLOADS["cuda"], "binary": "nvcc"},
                "badge": cuda.get("version") or ("감지됨" if cuda.get("available") else "설치 필요"),
            })
            engines.append({
                "id": "engine_vllm", "name": "vLLM",
                "subtitle": "NVIDIA 서버형 추론 · Windows는 WSL/Linux 권장",
                "status": "available",
                "priority": "recommended" if preferred_engine == "vllm" else "optional",
                "checked": preferred_engine == "vllm",
                "action": {"type": "pip", "packages": ["vllm", "huggingface_hub[cli]"], "verify_modules": ["vllm", "huggingface_hub"]},
                "badge": "WSL/Linux 권장" if os_name == "Windows" and not wsl.get("is_wsl") else "설치 가능",
            })
        elif gpu.get("vendor") in {"amd", "intel"}:
            engines.append({
                "id": "engine_vulkan_directml", "name": "Vulkan/DirectML",
                "subtitle": f"{gpu.get('vendor', '').upper()} GPU 감지 · LM Studio 또는 llama.cpp 백엔드 권장",
                "status": "available",
                "priority": "recommended" if preferred_engine in {"lmstudio", "llamacpp"} else "optional",
                "checked": False,
                "action": None,
                "badge": gpu.get("backend") or "GPU",
            })

    components: List[Dict] = []
    component_specs = [
        ("homebrew", "Homebrew", "macOS 패키지 관리자 · 자동 설치 기반", "brew", None, "recommended"),
        ("git", "Git", "저장소 · 확장 · MCP 도구 연동에 필요", "git", "git", "recommended"),
        ("node", "Node.js", "npm 패키지와 VS Code 확장 개발에 필요", "node", "node", "optional"),
        ("tesseract", "Tesseract OCR", "이미지/PDF OCR 기능에 필요", "tesseract", "tesseract", "optional"),
    ]
    for cid, name, subtitle, binary, brew_pkg, priority in component_specs:
        installed = bool(tools.get(binary))
        if cid == "homebrew" and env["os"] != "Darwin":
            continue
        if installed:
            components.append({
                "id": f"component_{cid}", "name": name,
                "subtitle": subtitle, "status": "installed",
                "priority": priority, "checked": False, "action": None,
                "badge": "설치됨",
            })
            continue
        if cid == "homebrew":
            action = {"type": "url", "url": OFFICIAL_DOWNLOADS["homebrew"], "binary": "brew"}
        elif tools.get("brew") and brew_pkg:
            action = {"type": "brew", "package": brew_pkg, "binary": binary, "official_url": OFFICIAL_DOWNLOADS.get(cid)}
        else:
            action = {"type": "url", "url": OFFICIAL_DOWNLOADS.get(cid, ""), "binary": binary}
        components.append({
            "id": f"component_{cid}", "name": name,
            "subtitle": subtitle, "status": "available",
            "priority": priority, "checked": priority == "recommended",
            "action": action, "badge": "설치 필요",
        })

    python_ok = sys.version_info >= (3, 11)
    if not python_ok:
        components.insert(0, {
            "id": "component_python", "name": "Python 3.11+",
            "subtitle": "Lattice AI 서버 실행에 필요한 Python 런타임",
            "status": "available", "priority": "recommended", "checked": True,
            "action": {"type": "url", "url": OFFICIAL_DOWNLOADS["python"], "binary": "python3"},
            "badge": "업데이트 필요",
        })

    for provider, has_key in api_keys.items():
        if has_key:
            engines.append({
                "id": f"engine_{provider}", "name": provider.title(),
                "subtitle": f"{provider.upper()}_API_KEY 감지됨 · 클라우드 API",
                "status": "ready", "priority": "optional",
                "checked": False, "action": None, "badge": "준비됨",
            })

    # ── Models ───────────────────────────────────────────────────────────────
    models: List[Dict] = []

    if is_apple:
        for mid, mname, size_gb, tag, desc, min_ram in apple_catalog:
            fits    = ram >= min_ram and size_gb <= max_model_gb and disk_free >= size_gb + 2
            is_best = mid == best_id
            models.append({
                "id":       f"model_{mid.replace('/', '__').replace('-', '_')}",
                "model_id": mid,
                "name":     mname,
                "subtitle": desc,
                "size_gb":  size_gb,
                "tag":      tag,
                "fits":     fits,
                "priority": "recommended" if is_best else "optional",
                "checked":  is_best and fits,
                "disabled": not fits,
                "badge":    f"{size_gb} GB",
                "action":   {"type": "load_model", "model_id": mid} if fits else None,
            })
    else:
        vram_gb = float(gpu.get("vram_gb") or 0)
        gpu_budget_gb = vram_gb * 1.15 if gpu.get("vendor") in {"nvidia", "amd", "intel"} and vram_gb else max_model_gb
        model_budget_gb = min(max_model_gb, gpu_budget_gb)
        for mid, mname, size_gb, tag, desc, min_ram in engine_catalog:
            fits = ram >= min_ram and size_gb <= model_budget_gb and disk_free >= size_gb + 2
            is_best = mid == best_id
            models.append({
                "id":       f"model_{mid.replace('/', '__').replace(':', '__').replace('-', '_')}",
                "model_id": mid,
                "name":     mname,
                "subtitle": desc,
                "size_gb":  size_gb,
                "tag":      tag,
                "fits":     fits,
                "priority": "recommended" if is_best else "optional",
                "checked":  is_best and fits,
                "disabled": not fits,
                "badge":    f"{size_gb} GB · {preferred_engine}",
                "action":   {"type": "load_model", "model_id": mid} if fits else None,
            })
    if models and not any(item.get("checked") for item in models):
        for item in models:
            if not item.get("disabled"):
                item["priority"] = "recommended"
                item["checked"] = True
                break

    # ── MCPs ─────────────────────────────────────────────────────────────────
    mcps: List[Dict] = [
        {
            "id": "mcp_files", "name": "Workspace Files",
            "subtitle": "파일 읽기/쓰기 · 코드 생성 · 미리보기",
            "status": "active", "priority": "recommended",
            "checked": True, "action": None,
            "badge": "기본 탑재", "needs_auth": False,
        },
        {
            "id": "mcp_presentations", "name": "Presentations",
            "subtitle": "PPTX · 슬라이드 자동 생성",
            "status": "active", "priority": "optional",
            "checked": False, "action": None,
            "badge": "기본 탑재", "needs_auth": False,
        },
        {
            "id": "mcp_github", "name": "GitHub",
            "subtitle": "저장소 · PR · 이슈 · CI 연동",
            "status": "available", "priority": "optional",
            "checked": False,
            "action": {"type": "auth", "url": "https://github.com/apps", "mcp_id": "github"},
            "badge": "인증 필요", "needs_auth": True,
        },
        {
            "id": "mcp_googledrive", "name": "Google Drive",
            "subtitle": "Docs · Sheets · Drive 파일 연동",
            "status": "available", "priority": "optional",
            "checked": False,
            "action": {"type": "auth", "url": "https://chatgpt.com/connectors", "mcp_id": "google-drive"},
            "badge": "인증 필요", "needs_auth": True,
        },
        {
            "id": "mcp_slack", "name": "Slack",
            "subtitle": "팀 채널 공유 · 알림 워크플로",
            "status": "available", "priority": "optional",
            "checked": False,
            "action": {"type": "auth", "url": "https://chatgpt.com/connectors", "mcp_id": "slack"},
            "badge": "인증 필요", "needs_auth": True,
        },
    ]

    return _hydrate_install_actions({
        "components": components,
        "engines": engines,
        "models":  models,
        "mcps":    mcps,
        "summary": {
            "chip":            chip["name"],
            "cpu_cores":       cpu.get("logical_cores"),
            "cpu_instructions": cpu.get("instructions", []),
            "gpu":             gpu.get("name") or gpu.get("vendor"),
            "gpu_vendor":      gpu.get("vendor"),
            "vram_gb":         gpu.get("vram_gb"),
            "cuda":            cuda.get("available"),
            "cuda_version":    cuda.get("version"),
            "wsl":             wsl,
            "preferred_engine": preferred_engine,
            "ram_gb":          ram,
            "disk_free_gb":    disk_free,
            "is_apple_silicon": is_apple,
            "max_model_gb":    round(max_model_gb, 1),
        },
    })

# ── Installation Stream ───────────────────────────────────────────────────────

def _verify_action(action: Dict[str, Any]) -> Tuple[bool, str]:
    atype = action.get("type")
    if atype == "pip":
        modules = action.get("verify_modules") or [_package_module(pkg) for pkg in action.get("packages", [])]
        missing = [module for module in modules if not _module_available(module)]
        if missing:
            return False, "Python 모듈 감지 실패: " + ", ".join(missing)
        return True, "Python 모듈 import 테스트 통과"
    binary = action.get("binary")
    if binary:
        return _verify_binary(binary)
    return True, "검증 항목 없음"


async def _repair_action(
    action: Dict[str, Any],
    *,
    confirmation_token: str | None = None,
    actor: str | None = None,
) -> Tuple[bool, str]:
    binary = action.get("binary")
    if binary:
        repair_path_for(binary)
        ok, msg = _verify_binary(binary)
        if ok:
            return True, f"PATH 자동 보정 완료: {msg}"
    if action.get("type") == "pip":
        packages = action.get("packages", [])
        if packages:
            if not _verify_action_confirmation(action, confirmation_token, name="repair_action"):
                return False, "설치 명령 확인 토큰이 일치하지 않습니다."
            for pkg in packages:
                success, err = await _pip_install(pkg, confirmed=True, actor=actor)
                if not success:
                    return False, err
            return _verify_action(action)
    return False, "자동 복구 방법을 찾지 못했습니다."


async def install_stream(
    items: List[Dict],
    router: Any,
    *,
    confirmation_token: str | None = None,
    user_email: str | None = None,
) -> AsyncIterator[str]:
    for item in items:
        item_id    = item.get("id", "unknown")
        name       = item.get("name", item_id)
        action     = item.get("action") or {}
        atype      = action.get("type")

        if not atype:
            yield _sse({"id": item_id, "status": "skipped", "msg": f"{name} — 이미 준비됨"})
            await asyncio.sleep(0.04)
            continue

        yield _sse({"id": item_id, "status": "starting", "msg": f"{name} 준비 중..."})

        if atype == "pip":
            packages = action.get("packages", [])
            token = confirmation_token or action.get("confirmation_token") or (action.get("command_plan") or {}).get("confirmation_token")
            if not _verify_action_confirmation(action, token, name=str(item_id)):
                yield _sse({"id": item_id, "status": "error", "msg": "설치 명령 확인 토큰이 일치하지 않습니다."})
                continue
            ok = True
            for pkg in packages:
                yield _sse({"id": item_id, "status": "running", "msg": f"pip install {pkg} ..."})
                success, err = await _pip_install(pkg, confirmed=True, actor=user_email)
                if success:
                    yield _sse({"id": item_id, "status": "progress", "msg": f"{pkg} 설치 완료"})
                else:
                    yield _sse({"id": item_id, "status": "error", "msg": f"{pkg} 실패: {err[:400]}"})
                    ok = False
                    break
            if ok:
                yield _sse({"id": item_id, "status": "running", "msg": f"{name} 동작 테스트 중..."})
                verified, detail = _verify_action(action)
                if verified:
                    yield _sse({"id": item_id, "status": "done", "msg": f"{name} 설치 · 검증 완료 ✅\n{detail}"})
                else:
                    yield _sse({"id": item_id, "status": "running", "msg": f"검증 실패 — 자동 복구 중...\n{detail}"})
                    repaired, repair_msg = await _repair_action(action, confirmation_token=token, actor=user_email)
                    yield _sse({"id": item_id, "status": "done" if repaired else "error", "msg": repair_msg[:500]})

        elif atype == "brew":
            pkg = action.get("package", "")
            token = confirmation_token or action.get("confirmation_token") or (action.get("command_plan") or {}).get("confirmation_token")
            if not _verify_action_confirmation(action, token, name=str(item_id)):
                yield _sse({"id": item_id, "status": "error", "msg": "설치 명령 확인 토큰이 일치하지 않습니다."})
                continue
            yield _sse({"id": item_id, "status": "running", "msg": f"brew install {pkg} ..."})
            success, err = await _brew_install(pkg, confirmed=True, actor=user_email)
            if success:
                yield _sse({"id": item_id, "status": "running", "msg": "설치 완료 감지 · PATH 보정 중..."})
                binary = action.get("binary")
                if binary:
                    repair_path_for(binary)
                verified, detail = _verify_action(action)
                if verified:
                    yield _sse({"id": item_id, "status": "done", "msg": f"{name} 설치 · 연결 · 검증 완료 ✅\n{detail}"})
                else:
                    yield _sse({"id": item_id, "status": "running", "msg": f"검증 실패 — 자동 복구 중...\n{detail}"})
                    repaired, repair_msg = await _repair_action(action, confirmation_token=token, actor=user_email)
                    yield _sse({"id": item_id, "status": "done" if repaired else "error", "msg": repair_msg[:500]})
            else:
                url = action.get("official_url") or action.get("url")
                if url:
                    yield _sse({"id": item_id, "status": "auth", "msg": f"자동 설치 실패 — 공식 다운로드 페이지를 엽니다.\n{err[:240]}", "auth_url": url})
                    open_url(url)
                yield _sse({"id": item_id, "status": "error", "msg": f"실패: {err[:400]}"})

        elif atype == "load_model":
            model_id = action.get("model_id", "")
            yield _sse({"id": item_id, "status": "running",
                        "msg": f"모델 다운로드 · 로딩 중...\n{model_id}\n(용량에 따라 수 분 소요)"})
            try:
                await router.load_model(model_id)
                yield _sse({"id": item_id, "status": "done", "msg": f"{name} 로드 완료 ✅"})
            except Exception as e:
                yield _sse({"id": item_id, "status": "error", "msg": f"로드 실패: {str(e)[:400]}"})

        elif atype == "auth":
            url = action.get("url", "")
            yield _sse({"id": item_id, "status": "auth",
                        "msg": "브라우저에서 인증 페이지를 엽니다...", "auth_url": url})
            open_url(url)
            yield _sse({"id": item_id, "status": "waiting",
                        "msg": "브라우저에서 인증 완료 후 계속하세요"})

        elif atype == "url":
            url = action.get("url", "")
            yield _sse({"id": item_id, "status": "auth",
                        "msg": "설치 페이지를 브라우저에서 엽니다...", "auth_url": url})
            open_url(url)
            binary = action.get("binary")
            if binary:
                yield _sse({"id": item_id, "status": "waiting",
                            "msg": f"{binary} 설치 완료를 자동 감지하는 중입니다..."})
                ok, detail = await _wait_for_binary(binary)
                if ok:
                    repair_path_for(binary)
                    yield _sse({"id": item_id, "status": "done",
                                "msg": f"{name} 설치 · PATH 연결 · 검증 완료 ✅\n{detail}"})
                else:
                    yield _sse({"id": item_id, "status": "error",
                                "msg": f"{detail}\n공식 페이지에서 설치 후 다시 시도하세요."})
            else:
                yield _sse({"id": item_id, "status": "waiting",
                            "msg": "브라우저에서 설치 또는 인증을 완료한 뒤 다시 시도하세요"})

        else:
            yield _sse({"id": item_id, "status": "error", "msg": f"알 수 없는 액션: {atype}"})

    yield _sse({"status": "complete", "msg": "모든 항목 처리 완료!"})


async def _pip_install(
    package: str,
    *,
    confirmation_token: str | None = None,
    confirmed: bool = False,
    actor: str | None = None,
) -> Tuple[bool, str]:
    command = [sys.executable, "-m", "pip", "install", "--upgrade", package]
    plan = command_plan(
        command,
        name=f"pip:{package}",
        purpose="setup_wizard_install",
        metadata={"package": package},
    )
    try:
        if not confirmed:
            require_command_confirmation(command, confirmation_token, purpose="setup_wizard_install")
        append_process_audit_event("setup_wizard_install", plan=plan, status="started", user_email=actor)
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
        stderr_text = stderr.decode(errors="replace")
        append_process_audit_event(
            "setup_wizard_install",
            plan=plan,
            status="finished",
            user_email=actor,
            returncode=proc.returncode,
            stderr=stderr_text,
        )
        if proc.returncode == 0:
            return True, ""
        return False, stderr_text
    except CommandConfirmationError as e:
        append_process_audit_event("setup_wizard_install", plan=plan, status="denied", user_email=actor, error=str(e))
        return False, str(e)
    except asyncio.TimeoutError:
        append_process_audit_event("setup_wizard_install", plan=plan, status="timeout", user_email=actor)
        return False, "설치 시간 초과 (10분)"
    except Exception as e:
        append_process_audit_event("setup_wizard_install", plan=plan, status="error", user_email=actor, error=str(e))
        return False, str(e)


async def _brew_install(
    package: str,
    *,
    confirmation_token: str | None = None,
    confirmed: bool = False,
    actor: str | None = None,
) -> Tuple[bool, str]:
    brew = shutil.which("brew")
    if not brew:
        return False, "Homebrew 미설치 — https://brew.sh 에서 설치하세요"
    command = [brew, "install", package]
    plan = command_plan(
        command,
        name=f"brew:{package}",
        purpose="setup_wizard_install",
        metadata={"package": package},
    )
    try:
        if not confirmed:
            require_command_confirmation(command, confirmation_token, purpose="setup_wizard_install")
        append_process_audit_event("setup_wizard_install", plan=plan, status="started", user_email=actor)
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        stderr_text = stderr.decode(errors="replace")
        append_process_audit_event(
            "setup_wizard_install",
            plan=plan,
            status="finished",
            user_email=actor,
            returncode=proc.returncode,
            stderr=stderr_text,
        )
        if proc.returncode == 0:
            return True, ""
        return False, stderr_text
    except CommandConfirmationError as e:
        append_process_audit_event("setup_wizard_install", plan=plan, status="denied", user_email=actor, error=str(e))
        return False, str(e)
    except asyncio.TimeoutError:
        append_process_audit_event("setup_wizard_install", plan=plan, status="timeout", user_email=actor)
        return False, "설치 시간 초과 (5분)"
    except Exception as e:
        append_process_audit_event("setup_wizard_install", plan=plan, status="error", user_email=actor, error=str(e))
        return False, str(e)


def open_url(url: str) -> None:
    command: List[str]
    try:
        system = platform.system()
        if system == "Darwin":
            command = ["open", url]
            plan = command_plan(command, name="open_url", purpose="setup_wizard_open_url")
            append_process_audit_event("setup_wizard_open_url", plan=plan, status="started")
            subprocess.Popen(command)
            append_process_audit_event("setup_wizard_open_url", plan=plan, status="spawned")
        elif system == "Windows":
            command = ["os.startfile", url]
            plan = command_plan(command, name="open_url", purpose="setup_wizard_open_url")
            append_process_audit_event("setup_wizard_open_url", plan=plan, status="started")
            os.startfile(url)  # type: ignore[attr-defined]
            append_process_audit_event("setup_wizard_open_url", plan=plan, status="spawned")
        else:
            command = ["xdg-open", url]
            plan = command_plan(command, name="open_url", purpose="setup_wizard_open_url")
            append_process_audit_event("setup_wizard_open_url", plan=plan, status="started")
            subprocess.Popen(command)
            append_process_audit_event("setup_wizard_open_url", plan=plan, status="spawned")
    except Exception as exc:
        try:
            append_process_audit_event(
                "setup_wizard_open_url",
                plan=command_plan(["open_url", url], name="open_url", purpose="setup_wizard_open_url"),
                status="error",
                error=str(exc),
            )
        except Exception:
            pass
        pass
