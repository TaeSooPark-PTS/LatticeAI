#!/usr/bin/env python3
"""Discover-and-syntax-check every first-party Python module.

Replaces the hand-maintained ``py_compile`` enumeration in CI and
``package.json``: walks the repository, skips vendored / virtualenv / build /
cache / generated directories, and checks everything that remains. New
modules are picked up automatically — there is nothing to update when a file is
added, so the syntax gate can never silently fall behind the codebase.

Unlike ``py_compile``, this script does not write ``.pyc`` files or create
``__pycache__`` directories. Validation should not dirty a developer's working
tree or leave generated files for later lint passes to trip over.

Usage::

    python scripts/check_python.py            # syntax-check all discovered modules
    python scripts/check_python.py --list     # just print what would be checked

Exit code is non-zero if any module fails to parse.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Directory names excluded anywhere in the tree: virtualenvs, build/cache
# artifacts, generated agent output, and vendored snapshots of older releases.
EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    ".build-venv",
    ".npm-cache",
    "build",
    "dist",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "agent_workspace",
    "outputs",
    "playwright-report",
    "test-results",
    "ltcai.egg-info",
    "output",
    ".ltcai",
    ".ltcai-brain",
    ".ltcai-test",
    # Vendored snapshot of an older packaged release — not part of the build.
    "ltcai-0.3.1",
}


def iter_modules():
    for path in ROOT.rglob("*.py"):
        parts = path.relative_to(ROOT).parts
        if any(part in EXCLUDE_DIRS for part in parts):
            continue
        yield path


def check_syntax(path: Path) -> str | None:
    """Return a human-readable failure string, or ``None`` when syntax is valid."""

    try:
        compile(path.read_bytes(), str(path), "exec", dont_inherit=True)
    except (OSError, SyntaxError, ValueError) as exc:
        return f"{path.relative_to(ROOT)}: {exc}"
    return None


def main(argv: list[str]) -> int:
    modules = sorted(iter_modules())
    if "--list" in argv:
        for path in modules:
            print(path.relative_to(ROOT))
        return 0

    failures: list[str] = []
    for path in modules:
        failure = check_syntax(path)
        if failure:
            failures.append(failure)

    if failures:
        print("\n".join(failures))
        print(f"check:python FAILED — {len(failures)} of {len(modules)} module(s) did not parse")
        return 1

    print(f"check:python OK — syntax-checked {len(modules)} modules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
