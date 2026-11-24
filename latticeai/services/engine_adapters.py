"""Cross-platform engine adapter registry, hardware RAM/pressure probes, and one-way installers.

Centralizes platform-specific engine installation (macOS Homebrew + zero-Homebrew oMLX wheel
fallback, Windows Winget + pip fallbacks, Linux official install script + pip fallbacks),
real-time available memory detection, and hardware-tuned CLI arguments for ``omlx`` and
``llama.cpp`` so macOS, Windows, and Linux users get one-click setup and download.
"""

from __future__ import annotations

import ctypes
import importlib.util
import os
import platform as _platform
import re
import shutil as _shutil
import subprocess
import sys as _sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from latticeai.core.quiet import quiet
from latticeai.services.model_errors import ModelRuntimeError


@dataclass(frozen=True, slots=True)
class EngineAdapter:
    """Declarative metadata for a local inference engine."""

    id: str
    display_name: str
    apple_silicon_only: bool = False
    uses_double_dash_repo_id: bool = False
    default_port: int = 8000

    def to_served_model_id(self, model_name: str) -> str:
        raw = str(model_name or "").strip()
        if self.uses_double_dash_repo_id and "/" in raw:
            return raw.replace("/", "--")
        return raw

    def to_hf_repo_id(self, model_name: str) -> str:
        raw = str(model_name or "").strip()
        if self.uses_double_dash_repo_id and "/" not in raw and "--" in raw:
            return raw.replace("--", "/", 1)
        return raw


ENGINE_ADAPTERS: Dict[str, EngineAdapter] = {
    "local_mlx": EngineAdapter("local_mlx", "MLX", apple_silicon_only=True, default_port=0),
    "omlx": EngineAdapter(
        "omlx",
        "oMLX",
        apple_silicon_only=True,
        uses_double_dash_repo_id=True,
        default_port=8000,
    ),
    "ollama": EngineAdapter("ollama", "Ollama", default_port=11434),
    "llamacpp": EngineAdapter("llamacpp", "llama.cpp", default_port=8080),
    "lmstudio": EngineAdapter("lmstudio", "LM Studio", default_port=1234),
    "vllm": EngineAdapter("vllm", "vLLM", default_port=8000),
}


def is_apple_silicon(*, sys_mod: Any = _sys, platform_mod: Any = _platform) -> bool:
    return bool(
        str(getattr(sys_mod, "platform", "")) == "darwin"
        and str(platform_mod.machine() or "").lower() in {"arm64", "aarch64"}
    )


def windows_binary_candidates(binary: str, *, env: Optional[Mapping[str, str]] = None) -> List[Path]:
    source = env if env is not None else os.environ
    local_appdata = source.get("LOCALAPPDATA", "")
    program_files = source.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = source.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    candidates: Dict[str, List[Optional[Path]]] = {
        "ollama": [
            Path(local_appdata) / "Programs" / "Ollama" / "ollama.exe" if local_appdata else None,
            Path(program_files) / "Ollama" / "ollama.exe",
        ],
        "lms": [
            Path(local_appdata) / "Programs" / "LM Studio" / "resources" / "app" / ".webpack" / "lms.exe"
            if local_appdata
            else None,
            Path(program_files) / "LM Studio" / "resources" / "app" / ".webpack" / "lms.exe",
        ],
        "llama-server": [
            Path(local_appdata) / "Microsoft" / "WinGet" / "Links" / "llama-server.exe"
            if local_appdata
            else None,
            Path(local_appdata) / "Programs" / "llama.cpp" / "llama-server.exe"
            if local_appdata
            else None,
            Path(program_files) / "llama.cpp" / "llama-server.exe",
        ],
        "nvidia-smi": [
            Path(program_files) / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe",
            Path(program_files_x86) / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe",
        ],
    }
    return [item for item in candidates.get(binary, []) if item is not None]


def _windows_memory_status_gb() -> Optional[tuple[float, float]]:
    if not _sys.platform.startswith("win"):
        return None
    try:
        class _MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = _MemoryStatusEx()
        stat.dwLength = ctypes.sizeof(_MemoryStatusEx)
        windll = getattr(ctypes, "windll", None)
        if windll is not None and windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            gib = float(1024 ** 3)
            return stat.ullTotalPhys / gib, stat.ullAvailPhys / gib
    except (OSError, AttributeError, TypeError, ValueError, RuntimeError):
        quiet()
    return None


def host_ram_gb() -> float:
    """Return installed physical RAM in GiB across macOS, Linux, and Windows."""
    win_mem = _windows_memory_status_gb()
    if win_mem is not None and win_mem[0] > 0:
        return win_mem[0]
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        if pages > 0 and page_size > 0:
            return (pages * page_size) / (1024 ** 3)
    except (OSError, ValueError, TypeError, AttributeError, RuntimeError):
        quiet()
    return 16.0


def host_available_ram_gb(total_ram_gb: Optional[float] = None) -> float:
    """Return reclaimable/available RAM in GiB to guard against concurrent-app memory pressure."""
    win_mem = _windows_memory_status_gb()
    if win_mem is not None and win_mem[1] > 0:
        return win_mem[1]
    if _sys.platform.startswith("linux"):
        try:
            meminfo = Path("/proc/meminfo").read_text(encoding="utf-8")
            for line in meminfo.splitlines():
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    return (kb * 1024) / (1024 ** 3)
        except (OSError, ValueError, IndexError, TypeError, RuntimeError):
            quiet()
    if _sys.platform == "darwin":
        try:
            proc = subprocess.run(
                ["vm_stat"],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout:
                page_match = re.search(r"page size of (\d+) bytes", proc.stdout)
                page_size = int(page_match.group(1)) if page_match else 16384
                reclaimable_pages = 0
                for key in ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable"):
                    m = re.search(rf"{key}:\s+(\d+)\.", proc.stdout)
                    if m:
                        reclaimable_pages += int(m.group(1))
                if reclaimable_pages > 0:
                    return (reclaimable_pages * page_size) / (1024 ** 3)
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, RuntimeError):
            quiet()
    fallback_total = float(total_ram_gb) if total_ram_gb is not None else host_ram_gb()
    return max(4.0, fallback_total * 0.75)


def omlx_serve_args(
    ram_gb: Optional[float] = None,
    port: int = 8000,
    *,
    available_ram_gb: Optional[float] = None,
    model_size_gb: float = 16.1,
) -> List[str]:
    """Return hardware- and memory-pressure-tuned CLI flags for ``omlx serve``."""
    effective_ram = float(ram_gb) if ram_gb is not None else host_ram_gb()
    if available_ram_gb is not None:
        effective_avail = float(available_ram_gb)
    elif ram_gb is not None:
        effective_avail = effective_ram * 0.75
    else:
        effective_avail = host_available_ram_gb(effective_ram)

    tight_memory = effective_ram < 32.0 or effective_avail < (model_size_gb + 2.0)

    memory_guard = (os.getenv("LATTICEAI_OMLX_MEMORY_GUARD") or "").strip().lower()
    if memory_guard not in {"off", "safe", "balanced", "aggressive"}:
        memory_guard = "safe" if tight_memory else "balanced"

    hot_cache = (os.getenv("LATTICEAI_OMLX_HOT_CACHE_MAX_SIZE") or "").strip()
    if not hot_cache:
        if effective_ram < 32.0 or effective_avail < model_size_gb + 0.5:
            hot_cache = "0"
        elif tight_memory:
            hot_cache = "1GB"
        elif effective_ram >= 48.0:
            hot_cache = "4GB"
        else:
            hot_cache = "2GB"

    max_concurrent = (os.getenv("LATTICEAI_OMLX_MAX_CONCURRENT_REQUESTS") or "").strip()
    if not max_concurrent:
        if tight_memory and effective_ram >= 24.0 and available_ram_gb is not None:
            max_concurrent = "1"
        elif effective_ram >= 48.0:
            max_concurrent = "4"
        else:
            max_concurrent = "2"

    ssd_cache_dir = (os.getenv("LATTICEAI_OMLX_PAGED_SSD_CACHE_DIR") or "").strip()
    if not ssd_cache_dir:
        ssd_cache_dir = str(Path.home() / ".omlx" / "kv-cache")

    ssd_max_size = (os.getenv("LATTICEAI_OMLX_PAGED_SSD_CACHE_MAX_SIZE") or "32GB").strip()
    initial_blocks = (os.getenv("LATTICEAI_OMLX_INITIAL_CACHE_BLOCKS") or "128").strip()

    return [
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--headless",
        "--hf-cache",
        "--memory-guard",
        memory_guard,
        "--hot-cache-max-size",
        hot_cache,
        "--paged-ssd-cache-dir",
        ssd_cache_dir,
        "--paged-ssd-cache-max-size",
        ssd_max_size,
        "--max-concurrent-requests",
        max_concurrent,
        "--initial-cache-blocks",
        initial_blocks,
    ]


def llamacpp_serve_args(
    model_file: Path,
    model_name: str,
    *,
    mmproj_file: Optional[Path] = None,
    ram_gb: Optional[float] = None,
    port: int = 8080,
) -> List[str]:
    """Return cross-platform hardware-tuned CLI flags for ``llama-server`` (Windows/Linux/macOS)."""
    effective_ram = float(ram_gb) if ram_gb is not None else host_ram_gb()
    ctx_size = (os.getenv("LATTICEAI_LLAMACPP_CTX_SIZE") or "").strip()
    if not ctx_size:
        ctx_size = "32768" if effective_ram >= 48.0 else ("16384" if effective_ram >= 24.0 else "8192")
    gpu_layers = (os.getenv("LATTICEAI_LLAMACPP_GPU_LAYERS") or "auto").strip()
    args = [
        "-m",
        str(model_file),
        "--alias",
        model_name,
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "-ngl",
        gpu_layers,
        "-c",
        ctx_size,
        "-fa",
        "on",
        "--cache-type-k",
        "q8_0",
        "--cache-type-v",
        "q8_0",
    ]
    if mmproj_file is not None:
        args.extend(["--mmproj", str(mmproj_file)])
    return args


def default_local_engine_for_host(
    *,
    engine_installed_fn: Optional[Callable[[str], bool]] = None,
    apple_silicon: Optional[bool] = None,
    sys_mod: Any = _sys,
    platform_mod: Any = _platform,
) -> str:
    """Pick the best local runtime for this OS/architecture for zero-config one-way setup."""
    is_mac_arm = apple_silicon if apple_silicon is not None else is_apple_silicon(sys_mod=sys_mod, platform_mod=platform_mod)
    if is_mac_arm:
        if engine_installed_fn is not None:
            if engine_installed_fn("omlx") and not engine_installed_fn("local_mlx"):
                return "omlx"
        return "local_mlx"
    if engine_installed_fn is not None:
        for candidate in ("ollama", "llamacpp", "vllm", "lmstudio"):
            if engine_installed_fn(candidate):
                return candidate
    return "ollama"


def _omlx_wheel_fallback_script(python_exe: str) -> str:
    """Install official jundot/omlx macOS universal2 wheel into ~/.omlx/venv when Homebrew is absent."""
    return (
        "set -euo pipefail; "
        "mkdir -p \"$HOME/.omlx/bin\"; "
        f"PY_BIN={python_exe!r}; "
        "if command -v uv >/dev/null 2>&1; then "
        "  uv venv --python 3.12 \"$HOME/.omlx/venv\" --allow-existing; "
        "else "
        "  \"$PY_BIN\" -m venv \"$HOME/.omlx/venv\"; "
        "fi; "
        "\"$HOME/.omlx/venv/bin/python\" -c '"
        "import json, subprocess, sys, urllib.request; "
        "tag = f\"cp{sys.version_info.major}{sys.version_info.minor}\"; "
        "req = urllib.request.Request(\"https://api.github.com/repos/jundot/omlx/releases/latest\", headers={\"User-Agent\": \"latticeai\"}); "
        "data = json.loads(urllib.request.urlopen(req, timeout=20).read().decode()); "
        "wheels = [a[\"browser_download_url\"] for a in data.get(\"assets\", []) if a.get(\"name\", \"\").endswith(\".whl\")]; "
        "url = next((u for u in wheels if tag in u), wheels[0] if wheels else \"\"); "
        "assert url, \"No oMLX wheel found in latest release\"; "
        "subprocess.check_call([sys.executable, \"-m\", \"pip\", \"install\", \"--upgrade\", url])"
        "'; "
        "ln -sf \"$HOME/.omlx/venv/bin/omlx\" \"$HOME/.omlx/bin/omlx\""
    )


def _resolve_ollama_installer(
    sys_platform: str, which_fn: Callable[[str], Optional[str]]
) -> List[str]:
    if sys_platform.startswith("win"):
        if not which_fn("winget"):
            raise ModelRuntimeError(status_code=400, detail="winget가 설치되어 있지 않아 Ollama를 자동 설치할 수 없습니다.")
        return [
            "winget",
            "install",
            "--id",
            "Ollama.Ollama",
            "-e",
            "--silent",
            "--accept-source-agreements",
            "--accept-package-agreements",
        ]
    if sys_platform.startswith("linux"):
        if which_fn("curl"):
            return ["/bin/sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"]
        if which_fn("brew"):
            return ["brew", "install", "ollama"]
        raise ModelRuntimeError(status_code=400, detail="curl 또는 brew가 설치되어 있지 않아 자동 설치할 수 없습니다.")
    if not which_fn("brew"):
        raise ModelRuntimeError(status_code=400, detail="brew가 설치되어 있지 않아 자동 설치할 수 없습니다.")
    return ["brew", "install", "ollama"]


def _resolve_llamacpp_installer(
    sys_platform: str, python_exe: str, which_fn: Callable[[str], Optional[str]]
) -> List[str]:
    if sys_platform.startswith("win"):
        if which_fn("winget"):
            return [
                "winget",
                "install",
                "--id",
                "ggml.llamacpp",
                "-e",
                "--silent",
                "--accept-source-agreements",
                "--accept-package-agreements",
            ]
        return [python_exe, "-m", "pip", "install", "--upgrade", "llama-cpp-python[server]", "huggingface_hub[cli]"]
    if which_fn("brew"):
        return ["brew", "install", "llama.cpp"]
    return [python_exe, "-m", "pip", "install", "--upgrade", "llama-cpp-python[server]", "huggingface_hub[cli]"]


def _resolve_lmstudio_installer(
    sys_platform: str, which_fn: Callable[[str], Optional[str]]
) -> List[str]:
    if sys_platform.startswith("win"):
        if not which_fn("winget"):
            raise ModelRuntimeError(status_code=400, detail="winget가 설치되어 있지 않아 LM Studio를 자동 설치할 수 없습니다.")
        return [
            "winget",
            "install",
            "--id",
            "ElementLabs.LMStudio",
            "-e",
            "--silent",
            "--accept-source-agreements",
            "--accept-package-agreements",
        ]
    if not which_fn("brew"):
        raise ModelRuntimeError(status_code=400, detail="brew가 설치되어 있지 않아 자동 설치할 수 없습니다.")
    return ["brew", "install", "--cask", "lm-studio"]


def resolve_engine_installer(
    engine: str,
    installers: Mapping[str, Dict[str, Any]],
    *,
    sys_mod: Any = _sys,
    platform_mod: Any = _platform,
    which_fn: Callable[[str], Optional[str]] = _shutil.which,
) -> List[str]:
    """Resolve an OS-appropriate one-way install command for ``engine``."""
    if engine not in installers:
        raise ModelRuntimeError(status_code=400, detail="지원하지 않는 엔진입니다.")

    sys_platform = str(getattr(sys_mod, "platform", ""))
    python_exe = str(getattr(sys_mod, "executable", _sys.executable))
    apple_arm = is_apple_silicon(sys_mod=sys_mod, platform_mod=platform_mod)
    installer = installers[engine]

    if engine == "omlx":
        if not apple_arm:
            raise ModelRuntimeError(status_code=400, detail="oMLX는 Apple Silicon macOS에서만 지원됩니다.")
        if which_fn("brew"):
            return [
                "/bin/bash",
                "-lc",
                "brew tap jundot/omlx https://github.com/jundot/omlx && brew install jundot/omlx/omlx",
            ]
        return ["/bin/bash", "-lc", _omlx_wheel_fallback_script(python_exe)]

    if engine == "ollama":
        return _resolve_ollama_installer(sys_platform, which_fn)

    if engine == "llamacpp":
        return _resolve_llamacpp_installer(sys_platform, python_exe, which_fn)

    if engine == "lmstudio":
        return _resolve_lmstudio_installer(sys_platform, which_fn)

    if engine == "vllm" and apple_arm:
        return [
            "/bin/bash",
            "-lc",
            "set -euo pipefail; "
            "if [ ! -x /opt/homebrew/bin/python3.12 ]; then brew install python@3.12; fi; "
            "/opt/homebrew/bin/python3.12 -m venv ~/.venv-vllm-metal; "
            "~/.venv-vllm-metal/bin/pip install -U pip setuptools wheel; "
            "~/.venv-vllm-metal/bin/pip install vllm-metal",
        ]

    required_binary = str(installer.get("requires_binary") or "")
    if required_binary and which_fn(required_binary) is None:
        raise ModelRuntimeError(status_code=400, detail=f"{required_binary}가 설치되어 있지 않아 자동 설치할 수 없습니다.")
    return list(installer["command"])


def llamacpp_python_available() -> bool:
    return importlib.util.find_spec("llama_cpp") is not None


async def smoke_test_loaded_model(
    resolution: Any,
    *,
    api_key_override: Optional[str] = None,
    model_router: Any = None,
) -> Dict[str, Any]:
    """Run a lightweight post-load chat smoke test for local engines (skipping cloud models)."""
    _ = api_key_override
    try:
        import asyncio

        from latticeai.core.model_compat import (
            classify_smoke_response as _classify_smoke_response,
        )
        from latticeai.core.model_compat import (
            ensure_profile as _ensure_compat_profile,
        )
        from latticeai.core.model_compat import (
            fast_postprocess as _compat_fast_postprocess,
        )
        from latticeai.core.model_compat import (
            record_smoke_result as _record_smoke_result,
        )
        from latticeai.services.model_runtime import (
            _LOCAL_SMOKE_ENGINES,
            _SMOKE_PROMPT,
        )
    except (ImportError, OSError, RuntimeError, AttributeError) as exc:
        return {"ok": False, "reason": f"smoke import failed: {exc}", "skipped": True}

    if model_router is None:
        return {"ok": False, "reason": "model router is not configured", "skipped": True}

    if (getattr(resolution, "engine", "") or "").lower() not in _LOCAL_SMOKE_ENGINES:
        profile = _ensure_compat_profile(getattr(resolution, "load_id", ""), getattr(resolution, "engine", ""))
        return {
            "ok": True,
            "reason": "skipped (cloud model — smoke test would incur cost)",
            "answer": None,
            "profile": profile.to_dict(),
            "skipped": True,
        }
    try:
        text = await asyncio.wait_for(
            model_router.generate(
                _SMOKE_PROMPT,
                context=None,
                max_tokens=128,
                temperature=0.1,
            ),
            timeout=30,
        )
    except (TimeoutError, RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as exc:
        reason = str(exc)[:200] or "generation_failed"
        profile = _record_smoke_result(
            getattr(resolution, "load_id", ""), getattr(resolution, "engine", ""), False, reason, status="failed"
        )
        return {
            "ok": False,
            "status": "failed",
            "reason": reason,
            "answer": None,
            "profile": profile.to_dict(),
        }

    profile = _ensure_compat_profile(getattr(resolution, "load_id", ""), getattr(resolution, "engine", ""))
    cleaned = _compat_fast_postprocess(str(text or ""), profile.to_dict())
    status, reason = _classify_smoke_response(cleaned)
    ok = status != "failed"
    profile = _record_smoke_result(
        getattr(resolution, "load_id", ""), getattr(resolution, "engine", ""), ok, reason, status=status
    )
    return {
        "ok": ok,
        "status": status,
        "reason": reason,
        "answer": cleaned,
        "profile": profile.to_dict(),
    }
