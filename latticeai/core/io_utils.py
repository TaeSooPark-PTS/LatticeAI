"""Shared small IO helpers for JSON, timestamps, and file hashes."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict

from lattice_brain.utils import parse_iso as parse_iso
from latticeai.core.quiet import quiet


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2))
    os.replace(tmp_path, path)
    try:
        path.chmod(0o600)
    except OSError:
        # Windows and unusual filesystems may not expose POSIX mode bits; the
        # atomic write is still the safest supported fallback there.
        quiet()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


__all__ = ["atomic_write_json", "parse_iso", "sha256_file"]
