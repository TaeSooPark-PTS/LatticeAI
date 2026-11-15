"""Modular version synchronization CLI package for Lattice AI."""

from __future__ import annotations

from scripts.bump_version.cargo import (
    CARGO_TARGETS,
    RUST_CRATES,
    RUST_LOCKFILES,
    bump_cargo,
    parse_workspace_members,
)
from scripts.bump_version.cli import SUBCOMMANDS, build_parser, bump, main
from scripts.bump_version.markers import (
    CURRENT_RELEASE_BANNER_FILES,
    MARKER_FILES,
    bump_markers,
    inspect_or_update_marker_file,
)
from scripts.bump_version.openapi import (
    OPENAPI_FRAGMENTS,
    OPENAPI_TARGETS,
    _canonical_openapi_json,
    bump_openapi,
    canonical_openapi_json,
)
from scripts.bump_version.targets import DEFAULT_REPO, REPO, TARGETS

__all__ = [
    "CARGO_TARGETS",
    "CURRENT_RELEASE_BANNER_FILES",
    "DEFAULT_REPO",
    "MARKER_FILES",
    "OPENAPI_FRAGMENTS",
    "OPENAPI_TARGETS",
    "REPO",
    "RUST_CRATES",
    "RUST_LOCKFILES",
    "SUBCOMMANDS",
    "TARGETS",
    "_canonical_openapi_json",
    "build_parser",
    "bump",
    "bump_cargo",
    "bump_markers",
    "bump_openapi",
    "canonical_openapi_json",
    "inspect_or_update_marker_file",
    "main",
    "parse_workspace_members",
]
