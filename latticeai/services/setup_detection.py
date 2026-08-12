"""Shared setup/environment detection helpers."""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from latticeai.core.quiet import quiet

WhichFn = Callable[[str], Optional[str]]
RunFn = Callable[[List[str]], str]


def parse_windows_video_controllers(raw: str) -> List[Dict[str, Any]]:
    controllers: List[Dict[str, Any]] = []
    if not raw:
        return controllers
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
        if isinstance(data, list):
            for item in data:
                name = str(item.get("Name") or "").strip()
                if not name:
                    continue
                try:
                    ram_mb = int(item.get("AdapterRAM") or 0) // (1024 * 1024)
                except Exception:
                    ram_mb = 0
                controllers.append({"name": name, "vram_mb": ram_mb})
        if controllers:
            return controllers
    except Exception:
        quiet()
    current: Dict[str, Any] = {}
    for line in raw.splitlines():
        if line.startswith("Name="):
            if current:
                controllers.append(current)
            current = {"name": line.split("=", 1)[-1].strip(), "vram_mb": 0}
        elif line.startswith("AdapterRAM=") and current:
            try:
                current["vram_mb"] = int(line.split("=", 1)[-1].strip()) // (1024 * 1024)
            except ValueError:
                current["vram_mb"] = 0
    if current:
        controllers.append(current)
    return controllers



def parse_windows_cpu_info(
    raw: str,
    *,
    model: str,
    physical_cores: int,
    logical_cores: int,
) -> Tuple[str, int, int]:
    """Read ``wmic cpu get Name,NumberOfCores,NumberOfLogicalProcessors``.

    Whatever the output does not say keeps the value the caller already had —
    ``wmic`` is absent on modern Windows images and prints nothing there, and
    "0 cores" would be a worse answer than ``os.cpu_count()``'s guess.
    """
    for line in raw.splitlines():
        key, _, value = line.partition("=")
        if key == "Name" and value.strip():
            model = value.strip()
        elif key == "NumberOfCores" and value.strip():
            try:
                physical_cores = int(value.strip())
            except ValueError:
                quiet()
        elif key == "NumberOfLogicalProcessors" and value.strip():
            try:
                logical_cores = int(value.strip())
            except ValueError:
                quiet()
    return model, physical_cores, logical_cores


#: ``IsProcessorFeaturePresent`` codes worth reporting, by instruction name.
WINDOWS_PROCESSOR_FEATURES: Dict[int, str] = {
    6: "sse", 10: "sse2", 13: "sse3", 19: "neon", 28: "rdrand",
}


def windows_processor_features() -> List[str]:
    """Instruction-set flags from the Win32 API, or none anywhere else.

    ``ctypes.windll`` exists only on Windows, so on every other platform this
    raises and answers "nothing known" — which is the honest reading, since the
    flags are unobtainable rather than absent.
    """
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]  # Windows-only
        return [
            name
            for code, name in WINDOWS_PROCESSOR_FEATURES.items()
            if kernel32.IsProcessorFeaturePresent(code)
        ]
    except Exception:
        quiet()
        return []


def detect_cuda(which: WhichFn, run: RunFn) -> Tuple[bool, str, Optional[str], Optional[str]]:
    nvidia_smi = which("nvidia-smi")
    nvcc = which("nvcc")
    version = ""
    if nvidia_smi:
        raw = run([nvidia_smi, "--query-gpu=driver_version", "--format=csv,noheader"])
        version = raw.splitlines()[0].strip() if raw.splitlines() else ""
    if nvcc:
        raw = run([nvcc, "--version"])
        match = re.search(r"release\s+([\d.]+)", raw)
        if match:
            version = match.group(1)
    return bool(nvidia_smi or nvcc), version, nvidia_smi, nvcc


def detect_wsl_from_text(prof_os: str, raw: str) -> Tuple[bool, str]:
    if prof_os.lower() != "linux":
        return False, ""
    lowered = (raw or "").lower()
    is_wsl = "microsoft" in lowered or "wsl" in lowered
    version = "2" if "microsoft-standard" in lowered or "wsl2" in lowered else ("1" if is_wsl else "")
    return is_wsl, version


def detect_tools(which: WhichFn, binaries: Iterable[str]) -> Dict[str, Optional[str]]:
    return {binary: which(binary) for binary in binaries}


__all__ = [
    "WINDOWS_PROCESSOR_FEATURES",
    "detect_cuda",
    "detect_tools",
    "detect_wsl_from_text",
    "parse_windows_cpu_info",
    "parse_windows_video_controllers",
    "windows_processor_features",
]
