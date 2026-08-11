"""``.latticeignore`` parsing and matching for the folder walk.

A gitignore-like subset: blank lines and ``#`` comments are dropped, patterns
are ``fnmatch`` globs, and a trailing ``/`` restricts a pattern to directories.
Patterns match against both the root-relative posix path and the basename, so
``*.log`` and ``docs/draft.md`` both behave the way a reader expects.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Iterable, List

from .constants import LATTICEIGNORE_FILENAME


def _load_latticeignore(root: Path) -> List[str]:
    """Parse ``root/.latticeignore`` → glob patterns (gitignore-like subset)."""
    ignore_file = root / LATTICEIGNORE_FILENAME
    patterns: List[str] = []
    if not ignore_file.is_file():
        return patterns
    try:
        lines = ignore_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return patterns
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def _matches_ignore(
    rel_posix: str, name: str, *, is_dir: bool, patterns: Iterable[str]
) -> bool:
    """fnmatch-based .latticeignore matching.

    - ``pattern/`` matches directories only (files under it never appear
      because ignored directories are pruned during the walk).
    - Patterns match against both the root-relative posix path and the
      basename, so ``*.log`` and ``docs/draft.md`` both behave as expected.
    """
    for raw in patterns:
        pattern = raw
        if pattern.endswith("/"):
            if not is_dir:
                continue
            pattern = pattern.rstrip("/")
        pattern = pattern.lstrip("/")
        if not pattern:
            continue
        if fnmatch.fnmatch(rel_posix, pattern) or fnmatch.fnmatch(name, pattern):
            return True
    return False
