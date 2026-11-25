from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

RUST_CRATES: Tuple[str, ...] = (
    "lattice-agent",
    "lattice-auth",
    "lattice-chat",
    "lattice-core",
    "lattice-host",
    "lattice-ingest",
    "lattice-jobs",
    "lattice-platform",
    "lattice-retrieval",
)

RUST_LOCKFILES: Tuple[str, ...] = ("rust/Cargo.lock", "src-tauri/Cargo.lock")

CARGO_TARGETS: List[Tuple[str, str, Optional[str]]] = [
    ("src-tauri/Cargo.toml", "regex", r'(^version = ")([^"]+)(")'),
    (
        "src-tauri/Cargo.lock",
        "regex",
        r'(name = "lattice-ai-desktop"\nversion = ")([^"]+)(")',
    ),
    ("src-tauri/tauri.conf.json", "json", "version"),
    ("rust/Cargo.toml", "regex", r'(^version = ")([^"]+)(")'),
    *[
        (lock, "regex", rf'(name = "{crate}"\nversion = ")([^"]+)(")')
        for lock in RUST_LOCKFILES
        for crate in RUST_CRATES
    ],
]

DEFAULT_REPO = Path(__file__).resolve().parents[2]
REPO = DEFAULT_REPO


def parse_workspace_members(cargo_toml_text: str) -> Tuple[str, ...]:
    match = re.search(r"(?ms)^members\s*=\s*\[(.*?)\]", cargo_toml_text)
    if not match:
        return ()
    return tuple(sorted(re.findall(r'"([^"]+)"', match.group(1))))


def bump_cargo(
    version: str,
    *,
    check: bool = False,
    repo: Optional[Path] = None,
) -> int:
    from scripts.bump_version.targets import is_valid_semver, resolve_repo, run_targets

    if not is_valid_semver(version):
        print(f"error: '{version}' is not a sane semantic version", file=sys.stderr)
        return 2
    target_repo = resolve_repo(repo)
    return run_targets(CARGO_TARGETS, version, check=check, repo=target_repo)
