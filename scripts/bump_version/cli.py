"""Command-line interface for the modular Lattice AI version synchronization tool."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from scripts.bump_version.cargo import RUST_CRATES, RUST_LOCKFILES, bump_cargo
from scripts.bump_version.markers import bump_markers
from scripts.bump_version.openapi import OPENAPI_FRAGMENTS, bump_openapi
from scripts.bump_version.targets import (
    DEFAULT_REPO,
    REPO,
    TARGETS,
    is_valid_semver,
    resolve_repo,
    run_targets,
)

SUBCOMMANDS: Tuple[str, ...] = ("all", "openapi", "cargo", "markers")


def bump(
    version: str,
    *,
    check: bool = False,
    repo: Optional[Path] = None,
    targets: Optional[Iterable[Tuple[str, str, Optional[str]]]] = None,
) -> int:
    """Synchronize or verify all version copies across the repository."""
    if not is_valid_semver(version):
        print(f"error: '{version}' is not a sane semantic version", file=sys.stderr)
        return 2
    target_repo = resolve_repo(repo)
    active_targets = targets if targets is not None else TARGETS
    return run_targets(active_targets, version, check=check, repo=target_repo)


def _normalize_argv(argv: Sequence[str]) -> List[str]:
    """Normalize CLI args so bare ``<version> [--check]`` maps to ``all <version> [--check]``."""
    args = list(argv)
    if not args:
        return args
    for token in args:
        if token in ("-h", "--help"):
            return args
        if not token.startswith("-"):
            if token not in SUBCOMMANDS:
                return ["all", *args]
            return args
    return args


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser supporting ``all``, ``openapi``, ``cargo``, and ``markers``."""
    parser = argparse.ArgumentParser(
        prog="bump_version",
        description=(
            "Single-source version bump and verification CLI for Lattice AI. "
            "Supports full synchronization (default or `all`) as well as "
            "`openapi`, `cargo`, and `markers` subcommands."
        ),
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    for cmd_name, help_text in (
        ("all", "Synchronize or verify all 49 package, Cargo, and OpenAPI version targets"),
        ("openapi", "Synchronize or verify frontend/openapi.json, _envelope.json, 13 fragments, and _index.json"),
        ("cargo", "Synchronize or verify Cargo.toml, Cargo.lock, and Tauri configuration versions"),
        ("markers", "Inspect or synchronize multilingual release version markers across documentation"),
    ):
        sub = subparsers.add_parser(cmd_name, help=help_text)
        sub.add_argument("version", help="Semantic version (e.g. 12.5.0)")
        sub.add_argument(
            "--check",
            action="store_true",
            help="Verify only; write nothing",
        )

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entrypoint supporting both subcommand and positional ``<version> [--check]`` forms."""
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    normalized = _normalize_argv(raw_argv)
    parser = build_parser()
    args = parser.parse_args(normalized)

    if args.subcommand == "openapi":
        return bump_openapi(args.version, check=args.check)
    if args.subcommand == "cargo":
        return bump_cargo(args.version, check=args.check)
    if args.subcommand == "markers":
        return bump_markers(args.version, check=args.check)
    return bump(args.version, check=args.check)


__all__ = [
    "DEFAULT_REPO",
    "OPENAPI_FRAGMENTS",
    "REPO",
    "RUST_CRATES",
    "RUST_LOCKFILES",
    "SUBCOMMANDS",
    "TARGETS",
    "build_parser",
    "bump",
    "main",
]
