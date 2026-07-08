"""Shared setup/environment detection helpers."""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple


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
        pass
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


__all__ = ["detect_cuda", "detect_tools", "detect_wsl_from_text", "parse_windows_video_controllers"]
