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
python3 auto_setup.py plan --apply           # 실제 설치 실행 (위험)
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
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
    ram_mb: int = 0
    disk_free_mb: int = 0
    gpu: GPUInfo = field(default_factory=GPUInfo)
    package_manager: Optional[str] = None   # winget | brew | apt | dnf | pacman
    has_internet: bool = True
    python_version: str = ""

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


def _detect_gpu(prof_os: str, arch: str) -> GPUInfo:
    """OS별 휴리스틱으로 GPU 감지. 외부 라이브러리 없이 가능한 만큼만."""
    gpu = GPUInfo()

    # NVIDIA
    if shutil.which("nvidia-smi"):
        info = _run(["nvidia-smi", "--query-gpu=name,memory.total",
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
        info = _run(["wmic", "path", "win32_VideoController", "get",
                     "Name,AdapterRAM", "/format:list"])
        if info:
            name = ""
            ram = 0
            for line in info.splitlines():
                if line.startswith("Name="):
                    name = line.split("=", 1)[-1].strip()
                elif line.startswith("AdapterRAM="):
                    try:
                        ram = int(line.split("=", 1)[-1].strip()) // (1024 * 1024)
                    except ValueError:
                        ram = 0
            if name:
                gpu.model = name
                low = name.lower()
                if "nvidia" in low or "rtx" in low or "geforce" in low:
                    gpu.vendor = "nvidia"; gpu.sdk.append("cuda")
                elif "amd" in low or "radeon" in low:
                    gpu.vendor = "amd"; gpu.sdk.extend(["directml", "vulkan"])
                elif "intel" in low:
                    gpu.vendor = "intel"; gpu.sdk.extend(["directml", "vulkan"])
                if ram > 0:
                    gpu.vram_mb = ram

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
        return "winget" if shutil.which("winget") else None
    if prof_os == "darwin":
        return "brew" if shutil.which("brew") else None
    if prof_os == "linux":
        for pm in ("apt", "dnf", "pacman", "zypper", "apk"):
            if shutil.which(pm):
                return pm
    return None


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
    prof.cpu_model = platform.processor() or ""
    prof.cpu_cores = os.cpu_count() or 0
    prof.python_version = platform.python_version()

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
                prof.ram_mb = int(out.strip()) // (1024 * 1024)
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
    {"ram": 24 * 1024, "vram": 16 * 1024,
     "id": "google/gemma-3-12b-it", "q": "q5_K_M"},
    {"ram": 16 * 1024, "vram": 8 * 1024,
     "id": "Qwen/Qwen2.5-7B-Instruct", "q": "q4_K_M"},
    {"ram": 12 * 1024, "vram": 6 * 1024,
     "id": "google/gemma-3-4b-it", "q": "q4_K_M"},
    {"ram":  8 * 1024, "vram": 4 * 1024,
     "id": "microsoft/Phi-3.5-mini-instruct", "q": "q4_K_M"},
    {"ram":  4 * 1024, "vram": 0,
     "id": "google/gemma-3-2b-it", "q": "q4_K_M"},
]


def recommend(profile: SystemProfile) -> Recommendation:
    """② RECOMMEND — 프로파일을 보고 런타임/모델/양자화를 결정한다."""
    rationale: List[str] = []

    # backend / runtime
    if profile.os == "darwin" and profile.gpu.vendor == "apple":
        backend = "metal+mlx"
        runtime = "mlx" if _has_module("mlx") else "llama.cpp"
        rationale.append("Apple Silicon → Metal + MLX")
    elif profile.gpu.vendor == "nvidia" and profile.gpu.vram_mb >= 6000:
        backend = "cuda"
        runtime = "llama.cpp"
        rationale.append(f"NVIDIA GPU {profile.gpu.vram_mb} MB VRAM → CUDA + llama.cpp")
    elif profile.os == "windows" and profile.gpu.vendor in ("amd", "intel"):
        backend = "directml"
        runtime = "llama.cpp"
        rationale.append("Windows + AMD/Intel GPU → DirectML")
    elif profile.os == "linux" and profile.gpu.vendor == "amd":
        backend = "rocm" if "rocm" in profile.gpu.sdk else "vulkan"
        runtime = "llama.cpp"
        rationale.append("Linux + AMD GPU → ROCm/Vulkan")
    else:
        backend = "cpu"
        runtime = "llama.cpp"
        rationale.append("GPU 가속이 없거나 미감지 → CPU 추론")

    # model size by RAM/VRAM
    pick = _MODEL_CATALOG[-1]   # 가장 작은 모델 기본값
    for entry in _MODEL_CATALOG:
        if profile.ram_mb >= entry["ram"] and (
            backend == "cpu" or profile.gpu.vram_mb >= entry["vram"]
        ):
            pick = entry
            break
    rationale.append(
        f"RAM {profile.ram_mb} MB · VRAM {profile.gpu.vram_mb} MB → {pick['id']}"
    )

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


@dataclass
class InstallPlan:
    package_manager: Optional[str]
    steps: List[InstallStep]
    notes: List[str] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        return {
            "package_manager": self.package_manager,
            "steps": [asdict(s) for s in self.steps],
            "notes": self.notes,
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
    if not shutil.which("node"):
        need("node20", "VSCode 확장 / npm CLI 부트스트랩에 필요")

    # 런타임별 추가
    if rec.runtime == "mlx" and not _has_module("mlx_lm"):
        steps.append(InstallStep(
            name="mlx-lm", why="Apple Silicon LLM 추론",
            command=["pip3", "install", "--upgrade", "mlx-lm"],
        ))
    if rec.runtime == "llama.cpp" and not shutil.which("ollama"):
        need("ollama", "llama.cpp 가중치를 가장 쉽게 받는 경로")

    if not shutil.which("huggingface-cli"):
        need("huggingface-cli", "추천 모델 가중치 다운로드용")

    # 모델 가중치 풀
    steps.append(InstallStep(
        name=f"weights:{rec.model_id}",
        why="추론에 사용할 모델 가중치",
        command=["huggingface-cli", "download", rec.model_id, "--quiet"],
    ))

    return InstallPlan(package_manager=pm, steps=steps, notes=notes)


def apply_plan(plan_obj: InstallPlan, *, confirm: bool = False) -> List[Dict[str, Any]]:
    """위험: 실제로 설치 명령을 실행한다. ``confirm=True`` 필수."""
    if not confirm:
        raise RuntimeError("refuse to apply: pass confirm=True")
    results: List[Dict[str, Any]] = []
    for step in plan_obj.steps:
        try:
            r = subprocess.run(step.command, capture_output=True, text=True,
                               timeout=300, check=False)
            results.append({
                "name": step.name,
                "returncode": r.returncode,
                "stdout_tail": (r.stdout or "")[-2000:],
                "stderr_tail": (r.stderr or "")[-2000:],
            })
        except Exception as exc:
            results.append({"name": step.name, "error": str(exc)})
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
        add("mlx_lm import", _has_module("mlx_lm"), "Apple Silicon 런타임")
    if rec.runtime == "llama.cpp":
        add("ollama binary", shutil.which("ollama") is not None,
            shutil.which("ollama") or "not found")

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
def run_all(*, apply_install: bool = False) -> Dict[str, Any]:
    p = probe()
    r = recommend(p)
    pl = plan(p, r)
    install_results = None
    if apply_install:
        install_results = apply_plan(pl, confirm=True)
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
            out["install"] = apply_plan(pl, confirm=True)
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
