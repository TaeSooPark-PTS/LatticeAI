"""Pure runtime helpers extracted from ltcai_cli entrypoint."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _apply_extra_path() -> None:
    extra = os.getenv("LATTICEAI_EXTRA_PATH", "")
    if not extra:
        return
    current = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    for item in reversed([p for p in extra.split(os.pathsep) if p]):
        expanded = str(Path(item).expanduser())
        if Path(expanded).exists() and expanded not in current:
            current.insert(0, expanded)
    os.environ["PATH"] = os.pathsep.join(current)


def _has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None
