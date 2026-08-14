#!/usr/bin/env python3
"""Single-source version bump — rewrites every synchronized version copy.

The canonical version has always been nine hand-edited copies guarded by
tests/unit/test_version_consistency.py. This script makes the bump one
command; the consistency test keeps guarding the result.

Usage:
    python scripts/bump_version.py 4.0.0
    python scripts/bump_version.py 4.0.0 --check   # verify only, no writes
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# The rust/ workspace crates carry `version.workspace = true`, so the workspace
# root is the only hand-edited copy of the number. Both lockfiles still record
# it per crate — src-tauri's too, since 11.4.0, because the desktop shell now
# depends on lattice-host by path — and a lockfile left behind is a file the
# next `cargo build` silently rewrites underneath a tagged release.
#
# 11.5.2: this tuple listed only the three Phase-1 crates, so every bump since
# 11.5.0 left `lattice-agent`, `lattice-ingest` and `lattice-jobs` at the
# previous version in both lockfiles — and the first `cargo` invocation of the
# test suite rewrote them, dirtying the tree mid-release. Every workspace
# member belongs here.
RUST_CRATES = (
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
RUST_LOCKFILES = ("rust/Cargo.lock", "src-tauri/Cargo.lock")

# (path, kind, pattern) — pattern groups: (prefix, version)
TARGETS = [
    ("latticeai/__init__.py", "regex", r'(__version__ = ")([^"]+)(")'),
    ("lattice_brain/__init__.py", "regex", r'(__version__ = ")([^"]+)(")'),
    # WP-P1 deleted workspace_os / marketplace / multi_agent / the legacy
    # shim registry. The package version is the runtime canonical now.
    ("latticeai/services/architecture_readiness.py", "regex", r'(ARCHITECTURE_VERSION_TARGET = ")([^"]+)(")'),
    ("latticeai/services/product_readiness.py", "regex", r'(PRODUCT_VERSION_TARGET = ")([^"]+)(")'),
    ("pyproject.toml", "regex", r'(^version = ")([^"]+)(")'),
    ("package.json", "json", "version"),
    ("package-lock.json", "package-lock", None),
    ("vscode-extension/package.json", "json", "version"),
    ("vscode-extension/package-lock.json", "package-lock", None),
    ("browser-extension/manifest.json", "json", "version"),
    ("src-tauri/Cargo.toml", "regex", r'(^version = ")([^"]+)(")'),
    ("src-tauri/Cargo.lock", "regex", r'(name = "lattice-ai-desktop"\nversion = ")([^"]+)(")'),
    ("src-tauri/tauri.conf.json", "json", "version"),
    ("rust/Cargo.toml", "regex", r'(^version = ")([^"]+)(")'),
    *[
        (lock, "regex", rf'(name = "{crate}"\nversion = ")([^"]+)(")')
        for lock in RUST_LOCKFILES
        for crate in RUST_CRATES
    ],
    ("static/app/asset-manifest.json", "json", "version"),
]


def bump(version: str, *, check: bool = False) -> int:
    if not re.fullmatch(r"\d+\.\d+\.\d+([-.][0-9A-Za-z.]+)?", version):
        print(f"error: '{version}' is not a sane semantic version", file=sys.stderr)
        return 2
    failures = 0
    for rel, kind, spec in TARGETS:
        path = REPO / rel
        if not path.exists():
            print(f"  skip  {rel} (missing)")
            continue
        text = path.read_text(encoding="utf-8")
        if kind == "regex":
            new, n = re.subn(spec, lambda m: m.group(1) + version + m.group(3), text, count=1, flags=re.MULTILINE)
            changed = n == 1 and new != text
            ok = n == 1
        elif kind == "json":
            data = json.loads(text)
            ok = spec in data
            changed = ok and data.get(spec) != version
            if ok:
                data[spec] = version
                new = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        elif kind == "package-lock":
            data = json.loads(text)
            ok = "version" in data
            changed = ok and data.get("version") != version
            if ok:
                data["version"] = version
                if "packages" in data and "" in data["packages"]:
                    data["packages"][""]["version"] = version
                new = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        else:  # pragma: no cover
            raise AssertionError(kind)
        if not ok:
            print(f"  FAIL  {rel}: version field not found")
            failures += 1
            continue
        if check:
            current = re.search(spec, text, flags=re.MULTILINE).group(2) if kind == "regex" else json.loads(text)["version"]
            status = "ok" if current == version else f"MISMATCH ({current})"
            if current != version:
                failures += 1
            print(f"  {status:>9}  {rel}")
            continue
        if changed:
            path.write_text(new, encoding="utf-8")
            print(f"  bumped  {rel}")
        else:
            print(f"  ok      {rel}")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("--check", action="store_true", help="verify only; write nothing")
    args = parser.parse_args()
    return bump(args.version, check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
