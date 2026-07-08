"""
Lattice AI — Zero-Config Auto Setup
===================================

명세: ``lattice_ai_full_spec.pptx`` 슬라이드 16·17 (자동 환경 매트릭스 + 5단계 흐름)

5단계
-----
①  PROBE       OS · CPU · GPU · RAM · 디스크 · 가속 SDK 감지
②  RECOMMEND   사양 점수 → 최적 모델 / 런타임 / 양자화 자동 선택
③  INSTALL     OS별 패키지 매니저 어댑터 호출 (winget · brew · apt · 스토어)
④  VERIFY      추론 토큰/초 측정, 첫 응답 지연, 메모리 누수 점검
⑤  PRESET      기본/고급 모드 분기 + 단축키·MCP·테마 적용

원칙
----
- **표준 라이브러리 only.**  외부 패키지 import 는 모두 try/except 로 감싼다.
- **변경하지 않는다, 추천만 한다.**  INSTALL 단계는 *실행 명령어* 를 생성하고
  돌려보낼 뿐, ``--apply`` 플래그 없이는 시스템을 건드리지 않는다.
- **모든 출력은 JSON-직렬화 가능**해야 UI(설치 마법사 화면) 에서 그대로 표시 가능.

사용
----
```bash
python3 auto_setup.py probe                  # 1단계만
python3 auto_setup.py recommend              # 1+2단계
python3 auto_setup.py plan                   # 1+2+3 (설치 계획 출력)
python3 auto_setup.py plan --apply --confirm-token <token>
python3 auto_setup.py verify                 # 4단계 단독
python3 auto_setup.py preset                 # 5단계
python3 auto_setup.py all                    # 전체 흐름
```
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

__all__ = [
    "SystemProfile", "Recommendation", "InstallPlan",
    "probe", "recommend", "plan", "verify", "preset", "run_all",
]


# ── 1. PROBE ────────────────────────────────────────────────────────────────
@dataclass
class GPUInfo:
    vendor: str = "unknown"          # nvidia | amd | intel | apple | none
    model: str = ""
    vram_mb: int = 0
    sdk: List[str] = field(default_factory=list)   # ['cuda', 'metal', 'mlx', ...]


@dataclass
class SystemProfile:
    os: str = ""                     # windows | darwin | linux | ios | android
    os_version: str = ""
    arch: str = ""                   # x86_64 | arm64 | …
    cpu_model: str = ""
    cpu_cores: int = 0
    cpu_logical_cores: int = 0
    cpu_instructions: List[str] = field(default_factory=list)
    ram_mb: int = 0
    disk_free_mb: int = 0
    gpu: GPUInfo = field(default_factory=GPUInfo)
    package_manager: Optional[str] = None   # winget | brew | apt | dnf | pacman
    has_internet: bool = True
    python_version: str = ""
    is_wsl: bool = False
    wsl_version: str = ""
    cuda_available: bool = False
    cuda_version: str = ""
    tools: Dict[str, str] = field(default_factory=dict)

    def score(self) -> int:
        """LLM 적합도 점수 (0..100). RECOMMEND 의 입력."""
        s = 0
        s += min(self.cpu_cores * 2, 24)
        s += min(self.ram_mb // 1024 * 2, 40)
        s += min(self.gpu.vram_mb // 1024 * 4, 36)
        return min(s, 100)

    def to_json(self) -> Dict[str, Any]:
        d = asdict(self)
        d["score"] = self.score()
        return d


def _read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _run(cmd: List[str], timeout: float = 4.0) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout, check=False)
        return (out.stdout or "") + (out.stderr or "")
    except Exception:
        return ""


def _windows_candidate_paths(binary: str) -> List[str]:
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    candidates = {
        "ollama": [
            str(Path(local_appdata) / "Programs" / "Ollama" / "ollama.exe") if local_appdata else "",
            str(Path(program_files) / "Ollama" / "ollama.exe"),
        ],
        "lms": [
            str(Path(local_appdata) / "Programs" / "LM Studio" / "resources" / "app" / ".webpack" / "lms.exe") if local_appdata else "",
            str(Path(program_files) / "LM Studio" / "resources" / "app" / ".webpack" / "lms.exe"),
        ],
        "nvidia-smi": [
            str(Path(program_files) / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe"),
            str(Path(program_files_x86) / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe"),
        ],
    }
    return [item for item in candidates.get(binary, []) if item]


def _which(binary: str) -> Optional[str]:
    found = shutil.which(binary)
    if found:
        return found
    if platform.system() == "Windows":
        for candidate in _windows_candidate_paths(binary):
            if Path(candidate).exists():
                return candidate
    return None


def _detect_gpu(prof_os: str, arch: str) -> GPUInfo:
    """OS별 휴리스틱으로 GPU 감지. 외부 라이브러리 없이 가능한 만큼만."""
    gpu = GPUInfo()

    # NVIDIA
    nvidia_smi = _which("nvidia-smi")
    if nvidia_smi:
        info = _run([nvidia_smi, "--query-gpu=name,memory.total",
                     "--format=csv,noheader,nounits"])
        if info.strip():
            first = info.strip().splitlines()[0]
            try:
                name, mem = [x.strip() for x in first.split(",", 1)]
                gpu.vendor = "nvidia"
                gpu.model = name
                gpu.vram_mb = int(float(mem))
                gpu.sdk.append("cuda")
            except ValueError:
                pass

    # Apple Silicon / Metal
    if prof_os == "darwin":
        sp = _run(["system_profiler", "SPDisplaysDataType"], timeout=6.0)
        if "Apple" in sp and arch == "arm64":
            gpu.vendor = "apple"
            for line in sp.splitlines():
                if "Chipset Model" in line:
                    gpu.model = line.split(":", 1)[-1].strip()
                    break
            # Apple Silicon 의 GPU 메모리는 통합 메모리 = RAM. 별도 표기 안 함.
            gpu.sdk.extend(["metal", "mlx" if _has_module("mlx") else ""])
            gpu.sdk = [s for s in gpu.sdk if s]

    # Windows
    if prof_os == "windows" and gpu.vendor == "unknown":
        shell = _which("powershell") or _which("pwsh")
        info = ""
        if shell:
            info = _run([
                shell, "-NoProfile", "-Command",
                "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM | ConvertTo-Json -Compress",
            ], timeout=8.0)
        if not info:
            info = _run(["wmic", "path", "win32_VideoController", "get",
                         "Name,AdapterRAM", "/format:list"])
        controllers = _parse_windows_video_controllers(info)
        if controllers:
            primary = max(controllers, key=lambda item: int(item.get("vram_mb") or 0))
            name = str(primary.get("name") or "")
            gpu.model = name
            gpu.vram_mb = int(primary.get("vram_mb") or 0)
            low = name.lower()
            if "nvidia" in low or "rtx" in low or "geforce" in low:
                gpu.vendor = "nvidia"; gpu.sdk.append("cuda")
            elif "amd" in low or "radeon" in low:
                gpu.vendor = "amd"; gpu.sdk.extend(["directml", "vulkan"])
            elif "intel" in low or "arc" in low or "iris" in low:
                gpu.vendor = "intel"; gpu.sdk.extend(["directml", "vulkan"])

    # Linux (lspci)
    if prof_os == "linux" and gpu.vendor == "unknown":
        info = _run(["lspci"], timeout=3.0).lower()
        if "nvidia" in info:
            gpu.vendor = "nvidia"; gpu.sdk.append("cuda")
        elif "amd/ati" in info or "advanced micro devices" in info:
            gpu.vendor = "amd"; gpu.sdk.extend(["rocm", "vulkan"])
        elif "intel corporation" in info and "vga" in info:
            gpu.vendor = "intel"; gpu.sdk.append("vulkan")

    return gpu


def _detect_package_manager(prof_os: str) -> Optional[str]:
    if prof_os == "windows":
        return "winget" if _which("winget") else None
    if prof_os == "darwin":
        return "brew" if _which("brew") else None
    if prof_os == "linux":
        for pm in ("apt", "dnf", "pacman", "zypper", "apk"):
            if _which(pm):
                return pm
    return None


def _detect_tools() -> Dict[str, str]:
    detected = detect_tools(_which, ("ollama", "lms", "nvidia-smi", "nvcc", "winget", "brew", "apt", "git", "node", "python", "python3"))
    return {binary: path for binary, path in detected.items() if path}


def _detect_wsl(prof_os: str) -> Tuple[bool, str]:
    return detect_wsl_from_text(prof_os, _read_text("/proc/version"))


def _detect_cuda() -> Tuple[bool, str]:
    available, version, _nvidia_smi, _nvcc = detect_cuda(_which, lambda args: _run(args, timeout=4.0))
    return available, version


def _detect_cpu_details(prof_os: str) -> Tuple[str, int, int, List[str]]:
    model = platform.processor() or ""
    physical = os.cpu_count() or 0
    logical = os.cpu_count() or 0
    flags: List[str] = []
    if prof_os == "darwin":
        model = _run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip() or model
        try:
            physical = int((_run(["sysctl", "-n", "hw.physicalcpu"]).strip() or physical))
            logical = int((_run(["sysctl", "-n", "hw.logicalcpu"]).strip() or logical))
        except ValueError:
            pass
        flags = [item.lower() for item in _run(["sysctl", "-n", "machdep.cpu.features"]).split()]
    elif prof_os == "linux":
        text = _read_text("/proc/cpuinfo")
        for line in text.splitlines():
            if line.lower().startswith("model name") and not model:
                model = line.split(":", 1)[-1].strip()
            if line.lower().startswith(("flags", "features")) and not flags:
                flags = line.split(":", 1)[-1].strip().lower().split()
    elif prof_os == "windows":
        raw = _run(["wmic", "cpu", "get", "Name,NumberOfCores,NumberOfLogicalProcessors", "/format:list"])
        for line in raw.splitlines():
            key, _, value = line.partition("=")
            if key == "Name" and value.strip():
                model = value.strip()
            elif key == "NumberOfCores" and value.strip():
                try:
                    physical = int(value.strip())
                except ValueError:
                    pass
            elif key == "NumberOfLogicalProcessors" and value.strip():
                try:
                    logical = int(value.strip())
                except ValueError:
                    pass
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            feature_map = {6: "sse", 10: "sse2", 13: "sse3", 19: "neon", 28: "rdrand"}
            flags.extend(name for code, name in feature_map.items() if kernel32.IsProcessorFeaturePresent(code))
        except Exception:
            pass
    interesting = {"avx", "avx2", "avx512f", "fma", "neon", "sse4_2", "sse", "sse2", "sse3", "rdrand"}
    return model, physical, logical, sorted({flag for flag in flags if flag in interesting})


def _has_module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


def probe() -> SystemProfile:
    """① PROBE — 외부 의존성 없이 가능한 만큼 환경을 감지한다."""
    prof = SystemProfile()
    prof.os = {"Darwin": "darwin", "Windows": "windows",
               "Linux": "linux"}.get(platform.system(), platform.system().lower())
    prof.os_version = platform.release()
    prof.arch = platform.machine().lower()
    cpu_model, cpu_cores, cpu_logical_cores, cpu_instructions = _detect_cpu_details(prof.os)
    prof.cpu_model = cpu_model
    prof.cpu_cores = cpu_cores
    prof.cpu_logical_cores = cpu_logical_cores
    prof.cpu_instructions = cpu_instructions
    prof.python_version = platform.python_version()
    prof.is_wsl, prof.wsl_version = _detect_wsl(prof.os)
    prof.cuda_available, prof.cuda_version = _detect_cuda()
    prof.tools = _detect_tools()

    # RAM
    try:
        if prof.os == "linux":
            for line in _read_text("/proc/meminfo").splitlines():
                if line.startswith("MemTotal:"):
                    prof.ram_mb = int(line.split()[1]) // 1024
                    break
        elif prof.os == "darwin":
            out = _run(["sysctl", "-n", "hw.memsize"])
            if out.strip():
                try:
                    prof.ram_mb = int(out.strip()) // (1024 * 1024)
                except ValueError:
                    prof.ram_mb = 0
            if not prof.ram_mb:
                profiler = _run(["system_profiler", "SPHardwareDataType"], timeout=8.0)
                m = re.search(r"Memory:\s+([\d.]+)\s*(TB|GB|MB)", profiler, re.IGNORECASE)
                if m:
                    value = float(m.group(1))
                    unit = m.group(2).lower()
                    if unit == "tb":
                        prof.ram_mb = int(value * 1024 * 1024)
                    elif unit == "gb":
                        prof.ram_mb = int(value * 1024)
                    else:
                        prof.ram_mb = int(value)
                if not prof.ram_mb:
                    hostinfo = _run(["hostinfo"])
                    m = re.search(r"Primary memory available:\s+([\d.]+)\s+gigabytes", hostinfo, re.IGNORECASE)
                    if m:
                        prof.ram_mb = int(float(m.group(1)) * 1024)
        elif prof.os == "windows":
            out = _run(["wmic", "ComputerSystem", "get", "TotalPhysicalMemory",
                        "/format:list"])
            for line in out.splitlines():
                if line.startswith("TotalPhysicalMemory="):
                    prof.ram_mb = int(line.split("=", 1)[-1].strip()) // (1024 * 1024)
                    break
    except Exception:
        pass

    # Disk
    try:
        usage = shutil.disk_usage(Path.home())
        prof.disk_free_mb = usage.free // (1024 * 1024)
    except Exception:
        pass

    prof.gpu = _detect_gpu(prof.os, prof.arch)
    prof.package_manager = _detect_package_manager(prof.os)
    return prof


# ── 2. RECOMMEND ────────────────────────────────────────────────────────────
@dataclass
class Recommendation:
    runtime: str              # llama.cpp | mlx | vllm | mlc-llm | tflite
    backend: str              # cuda | metal+mlx | directml | vulkan | rocm | cpu
    model_id: str             # 추천 모델 (huggingface-like id)
    quantization: str         # q4_K_M | q5_K_M | mxfp4 | f16
    rationale: List[str]      # 왜 이걸 골랐는지 (UI에 표시)
    estimated_tokens_per_sec: Optional[float] = None

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


# 모델 카탈로그. PPT 슬라이드 16 의 "추천 모델" 열과 동기화.
_MODEL_CATALOG: List[Dict[str, Any]] = [
    # (min_ram_mb, min_vram_mb, model_id, quant, runtime_preference)
    # OS 오버헤드(~4-6 GB) + KV 캐시 여유를 감안한 보수적 RAM 임계값
    {"ram": 64 * 1024, "vram": 32 * 1024,
     "id": "mlx-community/gemma-4-31b-it-4bit", "q": "4bit", "multimodal": True},
    {"ram": 64 * 1024, "vram": 32 * 1024,
     "id": "Qwen/Qwen3-VL-30B-A3B-Instruct", "q": "q4_K_M", "multimodal": True},
    {"ram": 48 * 1024, "vram": 24 * 1024,
     "id": "mlx-community/gemma-4-31b-it-4bit", "q": "4bit", "multimodal": True},
    {"ram": 32 * 1024, "vram": 16 * 1024,
     "id": "mlx-community/gemma-4-26b-a4b-it-4bit", "q": "4bit", "multimodal": True},
    {"ram": 48 * 1024, "vram": 24 * 1024,
     "id": "Qwen/Qwen3-VL-30B-A3B-Instruct", "q": "q4_K_M", "multimodal": True},
    {"ram": 24 * 1024, "vram": 12 * 1024,
     "id": "mlx-community/Llama-4-Scout-17B-16E-Instruct-4bit", "q": "4bit", "multimodal": True},
    {"ram": 16 * 1024, "vram": 8 * 1024,
     "id": "mlx-community/gemma-4-12b-it-4bit", "q": "4bit", "multimodal": True},
    {"ram": 32 * 1024, "vram": 16 * 1024,
     "id": "Qwen/Qwen3-VL-8B-Instruct", "q": "q5_K_M", "multimodal": True},
    {"ram": 24 * 1024, "vram": 12 * 1024,
     "id": "Qwen/Qwen3-VL-8B-Instruct", "q": "q4_K_M", "multimodal": True},
    {"ram": 16 * 1024, "vram": 8 * 1024,
     "id": "Qwen/Qwen3-VL-8B-Instruct", "q": "q4_K_M", "multimodal": True},
    {"ram": 12 * 1024, "vram": 6 * 1024,
     "id": "Qwen/Qwen3-VL-4B-Instruct", "q": "q4_K_M", "multimodal": True},
    {"ram":  8 * 1024, "vram": 4 * 1024,
     "id": "Qwen/Qwen3-VL-4B-Instruct", "q": "q4_K_M", "multimodal": True},
    {"ram":  4 * 1024, "vram": 0,
     "id": "Qwen/Qwen3-VL-4B-Instruct", "q": "q4_K_M", "multimodal": True},
]


def recommend(profile: SystemProfile) -> Recommendation:
    """② RECOMMEND — 프로파일을 보고 런타임/모델/양자화를 결정한다."""
    rationale: List[str] = []

    # backend / runtime
    if profile.os == "darwin" and profile.gpu.vendor == "apple":
        backend = "metal+mlx"
        runtime = "mlx" if _has_module("mlx_vlm") else "llama.cpp"
        rationale.append("Apple Silicon → Metal + MLX-VLM")
    elif profile.gpu.vendor == "nvidia" and profile.cuda_available and (profile.os == "linux" or profile.is_wsl):
        backend = "cuda"
        runtime = "vllm" if profile.gpu.vram_mb >= 12 * 1024 else "llama.cpp"
        rationale.append(f"NVIDIA GPU {profile.gpu.vram_mb} MB VRAM + CUDA → {runtime}")
    elif profile.gpu.vendor == "nvidia":
        backend = "cuda" if profile.cuda_available else "vulkan"
        runtime = "lmstudio" if profile.tools.get("lms") else ("ollama" if profile.tools.get("ollama") else "llama.cpp")
        rationale.append("Windows NVIDIA는 LM Studio/Ollama 우선, vLLM은 WSL/Linux 권장")
    elif profile.os == "windows" and profile.gpu.vendor in ("amd", "intel"):
        backend = "directml/vulkan"
        runtime = "lmstudio" if profile.tools.get("lms") else ("ollama" if profile.tools.get("ollama") else "llama.cpp")
        rationale.append("Windows + AMD/Intel GPU → DirectML/Vulkan")
    elif profile.os == "linux" and profile.gpu.vendor == "amd":
        backend = "rocm" if "rocm" in profile.gpu.sdk else "vulkan"
        runtime = "ollama" if profile.tools.get("ollama") else "llama.cpp"
        rationale.append("Linux + AMD GPU → ROCm/Vulkan")
    else:
        backend = "cpu"
        runtime = "ollama" if profile.tools.get("ollama") else "llama.cpp"
        instruction_hint = ", ".join(profile.cpu_instructions) or "명령어 미감지"
        rationale.append(f"GPU 가속이 없거나 미감지 → CPU 추론 ({profile.cpu_logical_cores or profile.cpu_cores} threads, {instruction_hint})")

    # model size by RAM/VRAM
    pick = _MODEL_CATALOG[-1]   # 가장 작은 모델 기본값
    for entry in _MODEL_CATALOG:
        if profile.ram_mb >= entry["ram"] and (
            backend in {"cpu", "metal+mlx"} or profile.gpu.vram_mb >= entry["vram"]
        ):
            pick = entry
            break
    rationale.append(
        f"RAM {profile.ram_mb} MB · VRAM {profile.gpu.vram_mb} MB → {pick['id']}"
    )
    if pick.get("multimodal"):
        rationale.append("최신 멀티모달 모델을 우선 선택")

    # 양자화: VRAM 충분 → 더 정밀한 양자화로 업그레이드
    quant = pick["q"]
    if profile.gpu.vram_mb >= 24 * 1024:
        quant = "f16"
        rationale.append("VRAM ≥ 24 GB → f16 풀 정밀도")

    # 거친 tokens/sec 예측 (very rough)
    est_tps = None
    if backend == "cuda":
        est_tps = max(8.0, profile.gpu.vram_mb / 800)
    elif backend == "metal+mlx":
        est_tps = max(6.0, (profile.ram_mb // 1024) * 0.7)
    elif backend == "cpu":
        est_tps = max(1.5, profile.cpu_cores * 0.6)

    return Recommendation(
        runtime=runtime, backend=backend,
        model_id=pick["id"], quantization=quant,
        rationale=rationale, estimated_tokens_per_sec=est_tps,
    )


# ── 3. INSTALL plan ─────────────────────────────────────────────────────────
@dataclass
class InstallStep:
    name: str
    why: str
    command: List[str]
    requires_admin: bool = False

    def to_json(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "why": self.why,
            "command": self.command,
            "requires_admin": self.requires_admin,
            "command_plan": command_plan(
                self.command,
                name=self.name,
                purpose="auto_setup_install",
                requires_admin=self.requires_admin,
                metadata={"why": self.why},
            ),
        }


@dataclass
class InstallPlan:
    package_manager: Optional[str]
    steps: List[InstallStep]
    notes: List[str] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        plan_summary = command_plan_for_commands(
            [step.command for step in self.steps],
            name="auto_setup_plan",
            purpose="auto_setup_install",
            metadata={"package_manager": self.package_manager},
        )
        return {
            "package_manager": self.package_manager,
            "steps": [s.to_json() for s in self.steps],
            "notes": self.notes,
            "command_plan": plan_summary,
            "confirmation_token": plan_summary["confirmation_token"],
        }


# 패키지 카탈로그: 핵심 의존성을 OS별 명령으로 매핑
_PKG_MAP: Dict[str, Dict[str, Tuple[str, ...]]] = {
    # name : { pm : (cmd parts) }
    "python3.11+": {
        "winget": ("winget", "install", "-e", "--id", "Python.Python.3.11"),
        "brew":   ("brew", "install", "python@3.11"),
        "apt":    ("apt-get", "install", "-y", "python3.11"),
        "dnf":    ("dnf", "install", "-y", "python3.11"),
    },
    "node20":      {
        "winget": ("winget", "install", "-e", "--id", "OpenJS.NodeJS.LTS"),
        "brew":   ("brew", "install", "node@20"),
        "apt":    ("apt-get", "install", "-y", "nodejs"),
        "dnf":    ("dnf", "install", "-y", "nodejs"),
    },
    "ollama":      {
        "brew":   ("brew", "install", "ollama"),
        "winget": ("winget", "install", "-e", "--id", "Ollama.Ollama"),
        "apt":    ("sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"),
    },
    "huggingface-cli": {
        "brew":   ("pip3", "install", "--upgrade", "huggingface_hub"),
        "winget": ("pip", "install", "--upgrade", "huggingface_hub"),
        "apt":    ("pip3", "install", "--upgrade", "huggingface_hub"),
        "dnf":    ("pip3", "install", "--upgrade", "huggingface_hub"),
    },
}


def plan(profile: SystemProfile, rec: Recommendation) -> InstallPlan:
    """③ INSTALL — 추천을 만족시키는 *명령 계획* 을 만든다.  실행하지 않는다."""
    pm = profile.package_manager
    steps: List[InstallStep] = []
    notes: List[str] = []

    def need(name: str, why: str) -> None:
        cmd_tuple = _PKG_MAP.get(name, {}).get(pm or "")
        if cmd_tuple:
            steps.append(InstallStep(
                name=name, why=why,
                command=list(cmd_tuple),
                requires_admin=(cmd_tuple[0] in ("apt-get", "dnf", "pacman")),
            ))
        else:
            notes.append(f"패키지 매니저 어댑터 없음: {name} ({pm}) — 수동 설치 필요")

    if sys.version_info < (3, 11):
        need("python3.11+", "Lattice AI 서버는 Python 3.11 이상이 필요합니다.")
    if not _which("node"):
        need("node20", "VSCode 확장 / npm CLI 부트스트랩에 필요")

    # 런타임별 추가
    if rec.runtime == "mlx" and not _has_module("mlx_vlm"):
        steps.append(InstallStep(
            name="mlx-vlm", why="Apple Silicon 멀티모달 추론",
            command=["pip3", "install", "--upgrade", "mlx-vlm"],
        ))
    if rec.runtime in {"llama.cpp", "ollama"} and not _which("ollama"):
        need("ollama", "llama.cpp 가중치를 가장 쉽게 받는 경로")
    if rec.runtime == "lmstudio" and not _which("lms"):
        notes.append("LM Studio CLI(lms)를 찾지 못했습니다. https://lmstudio.ai/download 에서 설치하면 Windows/macOS/Linux 모델 다운로드와 GPU 백엔드를 자동 감지합니다.")
    if rec.runtime == "vllm" and not _has_module("vllm"):
        steps.append(InstallStep(
            name="vllm", why="NVIDIA CUDA/WSL/Linux 서버형 추론",
            command=["pip3", "install", "--upgrade", "vllm", "huggingface_hub"],
        ))
    if profile.gpu.vendor == "nvidia" and not profile.cuda_available:
        notes.append("NVIDIA GPU는 감지됐지만 CUDA/nvidia-smi를 찾지 못했습니다. Windows에서는 NVIDIA 드라이버와 CUDA Toolkit 설치 후 재검사를 권장합니다.")
    if profile.os == "windows" and profile.gpu.vendor == "nvidia" and not profile.is_wsl:
        notes.append("vLLM은 Windows native보다 WSL2/Linux에서 안정적입니다. Windows 데스크톱은 LM Studio 또는 Ollama GPU 경로를 먼저 권장합니다.")

    if not _which("huggingface-cli"):
        need("huggingface-cli", "추천 모델 가중치 다운로드용")

    # 모델 가중치 풀
    model_command = ["huggingface-cli", "download", rec.model_id, "--quiet"]
    if rec.runtime == "ollama":
        lower = rec.model_id.lower()
        if "gemma-4-31b" in lower:
            model_command = ["ollama", "pull", "hf.co/ggml-org/gemma-4-31B-it-GGUF:Q4_K_M"]
        elif "gemma-4-12b" in lower:
            model_command = ["ollama", "pull", "hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_K_M"]
        elif "llama-4-scout" in lower:
            model_command = ["ollama", "pull", "hf.co/ggml-org/Llama-4-Scout-17B-16E-Instruct-GGUF:Q4_K_M"]
        elif "qwen3-vl-8b" in lower:
            model_command = ["ollama", "pull", "qwen3-vl:8b"]
        elif "qwen3-vl-4b" in lower:
            model_command = ["ollama", "pull", "qwen3-vl:4b"]
    elif rec.runtime == "lmstudio":
        model_command = ["lms", "get", rec.model_id]
    steps.append(InstallStep(
        name=f"weights:{rec.model_id}",
        why="추론에 사용할 모델 가중치",
        command=model_command,
    ))

    return InstallPlan(package_manager=pm, steps=steps, notes=notes)


def apply_plan(
    plan_obj: InstallPlan,
    *,
    confirm: bool = False,
    confirmation_token: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """위험: 실제로 설치 명령을 실행한다. ``confirm`` and token are required."""
    if not confirm:
        raise RuntimeError("refuse to apply: pass confirm=True")
    expected = plan_obj.to_json().get("confirmation_token")
    if str(confirmation_token or "").strip() != expected:
        raise CommandConfirmationError("refuse to apply: confirmation token mismatch")
    results: List[Dict[str, Any]] = []
    for step in plan_obj.steps:
        step_plan = command_plan(
            step.command,
            name=step.name,
            purpose="auto_setup_install",
            requires_admin=step.requires_admin,
            metadata={"why": step.why},
        )
        try:
            require_command_confirmation(
                step.command,
                step_plan["confirmation_token"],
                purpose="auto_setup_install",
            )
            append_process_audit_event("installer_command", plan=step_plan, status="started")
            r = subprocess.run(step.command, capture_output=True, text=True,
                               timeout=300, check=False)
            append_process_audit_event(
                "installer_command",
                plan=step_plan,
                status="finished",
                returncode=r.returncode,
                stdout=r.stdout,
                stderr=r.stderr,
            )
            results.append({
                "name": step.name,
                "command_hash": step_plan["command_hash"],
                "command_preview": step_plan["command_preview"],
                "returncode": r.returncode,
                "stdout_tail": (r.stdout or "")[-2000:],
                "stderr_tail": (r.stderr or "")[-2000:],
            })
        except Exception as exc:
            append_process_audit_event("installer_command", plan=step_plan, status="error", error=str(exc))
            results.append({"name": step.name, "command_hash": step_plan["command_hash"], "error": str(exc)})
    return results


# ── 4. VERIFY ───────────────────────────────────────────────────────────────
def verify(profile: SystemProfile, rec: Recommendation) -> Dict[str, Any]:
    """④ VERIFY — 가벼운 sanity check.  실제 LLM 추론 벤치는 별도 도구로.
    여기서는 ‘설치된 것들이 import 되는가’ + ‘디스크/RAM 여유’ 정도만 본다."""
    checks: List[Dict[str, Any]] = []

    def add(label: str, ok: bool, detail: str = "") -> None:
        checks.append({"label": label, "ok": ok, "detail": detail})

    add("Python 3.11+", sys.version_info >= (3, 11), platform.python_version())
    add("RAM ≥ 4 GB", profile.ram_mb >= 4 * 1024, f"{profile.ram_mb} MB")
    add("디스크 여유 ≥ 8 GB", profile.disk_free_mb >= 8 * 1024,
        f"{profile.disk_free_mb} MB free")

    if rec.runtime == "mlx":
        add("mlx_vlm import", _has_module("mlx_vlm"), "Apple Silicon 멀티모달 런타임")
    if rec.runtime in {"llama.cpp", "ollama"}:
        add("ollama binary", _which("ollama") is not None,
            _which("ollama") or "not found")
    if rec.runtime == "lmstudio":
        add("LM Studio CLI", _which("lms") is not None, _which("lms") or "not found")
    if rec.backend == "cuda":
        add("CUDA/nvidia-smi", profile.cuda_available, profile.cuda_version or "not found")

    # CPU/메모리 잠깐 측정
    t0 = time.perf_counter()
    _ = sum(i * i for i in range(200_000))
    cpu_ms = (time.perf_counter() - t0) * 1000
    add("CPU latency sample", cpu_ms < 200, f"{cpu_ms:.1f} ms / 200k ops")

    return {
        "checks": checks,
        "all_pass": all(c["ok"] for c in checks),
    }


# ── 5. PRESET ───────────────────────────────────────────────────────────────
def preset(profile: SystemProfile, rec: Recommendation) -> Dict[str, Any]:
    """⑤ PRESET — UX 분기 + 단축키 + 테마 + MCP 도구 기본값.

    PPT 슬라이드 3 (모드 선택) · 17 (PRESET) 명세를 따른다.
    """
    # 기본 모드 vs 고급 모드 자동 선택 휴리스틱
    advanced = (
        profile.gpu.vendor in ("nvidia", "apple") or
        profile.ram_mb >= 24 * 1024 or
        "code" in (profile.cpu_model or "").lower()   # 개발자 머신 추정
    )
    mode = "advanced" if advanced else "basic"

    # 단축키는 OS별 자연 컨벤션 따름
    mod = "Cmd" if profile.os == "darwin" else "Ctrl"
    shortcuts = {
        "newChat":        f"{mod}+N",
        "toggleSidebar":  f"{mod}+B",
        "openGraph":      f"{mod}+G",
        "search":         f"{mod}+K",
        "toggleMode":     f"{mod}+Shift+M",
        "submit":         "Enter",
        "newline":        "Shift+Enter",
    }

    # 기본 MCP 도구 (PPT 슬라이드 11 의 기본 5종)
    mcp_defaults = [
        {"id": "filesystem",      "scope": "local",  "enabled": True},
        {"id": "web-search",      "scope": "remote", "enabled": True},
        {"id": "code-execute",    "scope": "local",  "enabled": True},
        {"id": "browser-automation","scope": "remote","enabled": False},
        {"id": "database",        "scope": "remote", "enabled": False},
    ]

    # 테마: OS 다크 모드 추종이 기본
    theme = {"mode": "auto",                    # auto | light | dark
             "accent": "#6E4AE6",               # PPT 슬라이드 19 토큰
             "density": "comfortable" if mode == "basic" else "compact"}

    # 다국어: OS locale 기반 추정
    locale = os.environ.get("LANG", os.environ.get("LC_ALL", "ko_KR"))
    lang = "ko" if locale.lower().startswith("ko") else (
        "ja" if locale.lower().startswith("ja") else "en"
    )

    return {
        "mode": mode,
        "model": {"id": rec.model_id, "runtime": rec.runtime,
                  "backend": rec.backend, "quantization": rec.quantization},
        "shortcuts": shortcuts,
        "mcp": mcp_defaults,
        "theme": theme,
        "language": lang,
        "tips": (
            ["기본 모드는 카드형 액션과 큰 입력창 위주. 언제든 '고급 모드' 로 전환할 수 있어요."]
            if mode == "basic"
            else ["고급 모드: 사이드바·디테일 패널·파이프라인 도구가 모두 활성화됩니다."]
        ),
    }


# ── orchestrator ────────────────────────────────────────────────────────────
def run_all(*, apply_install: bool = False, confirmation_token: Optional[str] = None) -> Dict[str, Any]:
    p = probe()
    r = recommend(p)
    pl = plan(p, r)
    install_results = None
    if apply_install:
        install_results = apply_plan(pl, confirm=True, confirmation_token=confirmation_token)
    v = verify(p, r)
    ps = preset(p, r)
    return {
        "probe":   p.to_json(),
        "recommend": r.to_json(),
        "plan":    pl.to_json(),
        "install": install_results,
        "verify":  v,
        "preset":  ps,
    }


# ── CLI ────────────────────────────────────────────────────────────────────
def _main() -> int:
    parser = argparse.ArgumentParser(prog="auto_setup",
                                     description="Lattice AI zero-config setup")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("probe")
    sub.add_parser("recommend")
    sp_plan = sub.add_parser("plan")
    sp_plan.add_argument("--apply", action="store_true",
                         help="actually run the install commands (DANGER)")
    sp_plan.add_argument("--confirm-token", default=None,
                         help="confirmation token from the dry-run plan output")
    sub.add_parser("verify")
    sub.add_parser("preset")
    sub.add_parser("all")
    args = parser.parse_args()

    if args.cmd == "probe":
        print(json.dumps(probe().to_json(), indent=2, ensure_ascii=False)); return 0
    if args.cmd == "recommend":
        p = probe(); r = recommend(p)
        print(json.dumps({"probe": p.to_json(), "recommend": r.to_json()},
                         indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "plan":
        p = probe(); r = recommend(p); pl = plan(p, r)
        out: Dict[str, Any] = {"plan": pl.to_json()}
        if args.apply:
            out["install"] = apply_plan(pl, confirm=True, confirmation_token=args.confirm_token)
        print(json.dumps(out, indent=2, ensure_ascii=False)); return 0
    if args.cmd == "verify":
        p = probe(); r = recommend(p)
        print(json.dumps(verify(p, r), indent=2, ensure_ascii=False)); return 0
    if args.cmd == "preset":
        p = probe(); r = recommend(p)
        print(json.dumps(preset(p, r), indent=2, ensure_ascii=False)); return 0
    if args.cmd == "all":
        print(json.dumps(run_all(apply_install=False), indent=2, ensure_ascii=False))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(_main())
