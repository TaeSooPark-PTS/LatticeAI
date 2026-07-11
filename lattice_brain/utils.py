"""Shared Brain Core utility helpers."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def local_now() -> datetime:
    """Return local wall-clock time for legacy local persistence formats."""

    return datetime.now()


def now_iso(*, timespec: str = "seconds") -> str:
    """Return one consistently formatted local timestamp."""

    return local_now().isoformat(timespec=timespec)


def utc_now_iso(*, timespec: str = "auto") -> str:
    """Return an offset-aware UTC timestamp."""

    return datetime.now(timezone.utc).isoformat(timespec=timespec)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


__all__ = ["local_now", "now_iso", "parse_iso", "sha256_file", "utc_now_iso"]
