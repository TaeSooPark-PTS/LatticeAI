#!/usr/bin/env python3
"""Single-source version bump — thin wrapper delegating to ``scripts.bump_version``.

Usage:
    python scripts/bump_version.py 12.5.0
    python scripts/bump_version.py 12.5.0 --check
    python scripts/bump_version.py openapi 12.5.0 --check
    python scripts/bump_version.py cargo 12.5.0 --check
    python scripts/bump_version.py markers 12.5.0 --check
    python -m scripts.bump_version openapi 12.5.0 --check
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.bump_version.cargo import (  # noqa: E402
    CARGO_TARGETS,
    RUST_CRATES,
    RUST_LOCKFILES,
    bump_cargo,
)
from scripts.bump_version.cli import (  # noqa: E402
    bump as _cli_bump,
)
from scripts.bump_version.cli import (
    main as _cli_main,
)
from scripts.bump_version.markers import (  # noqa: E402
    MARKER_FILES,
    bump_markers,
)
from scripts.bump_version.openapi import (  # noqa: E402
    OPENAPI_FRAGMENTS,
    OPENAPI_TARGETS,
    _canonical_openapi_json,
    bump_openapi,
    canonical_openapi_json,
)
from scripts.bump_version.targets import DEFAULT_REPO, TARGETS  # noqa: E402


def bump(
    version: str,
    *,
    check: bool = False,
    repo: Optional[Path] = None,
    targets: Optional[Iterable[Tuple[str, str, Optional[str]]]] = None,
) -> int:
    """Delegate version synchronization to ``scripts.bump_version.cli.bump``."""
    effective_repo = repo if repo is not None else (REPO if REPO != DEFAULT_REPO else None)
    return _cli_bump(version, check=check, repo=effective_repo, targets=targets)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Delegate CLI execution to ``scripts.bump_version.cli.main``."""
    return _cli_main(argv)


__all__ = [
    "CARGO_TARGETS",
    "MARKER_FILES",
    "OPENAPI_FRAGMENTS",
    "OPENAPI_TARGETS",
    "REPO",
    "RUST_CRATES",
    "RUST_LOCKFILES",
    "TARGETS",
    "_canonical_openapi_json",
    "bump",
    "bump_cargo",
    "bump_markers",
    "bump_openapi",
    "canonical_openapi_json",
    "main",
]

if __name__ == "__main__":
    raise SystemExit(main())
