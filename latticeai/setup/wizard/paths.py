"""PATH discovery, binary lookup, and the ``.env`` handoff for the wizard.

The base layer of the setup wizard: it knows where installers put things on
each platform, how to get those directories onto ``PATH`` for the running
process, and how to persist that repair into the project ``.env``. Nothing
here imports another wizard submodule, which is also what lets
``tests/unit/test_cov_wp04_wizard_detect.py`` re-execute this file under a
throwaway name to reach the Windows-only import-time branch.

``COMMON_PATH_DIRS`` is module-level state that the import-time Windows block
extends and tests replace, so it lives here and only here — the modules that
repair PATH call :func:`repair_path_for` rather than copying the list.
"""

from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path
from typing import Any, Dict, List

from latticeai.core.module_probe import module_available as _module_available

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
