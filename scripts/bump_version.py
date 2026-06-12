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

# (path, kind, pattern) — pattern groups: (prefix, version)
TARGETS = [
    ("latticeai/__init__.py", "regex", r'(__version__ = ")([^"]+)(")'),
    ("latticeai/core/workspace_os.py", "regex", r'(WORKSPACE_OS_VERSION = ")([^"]+)(")'),
    ("latticeai/core/marketplace.py", "regex", r'(MARKETPLACE_VERSION = ")([^"]+)(")'),
    ("latticeai/core/multi_agent.py", "regex", r'(MULTI_AGENT_VERSION = ")([^"]+)(")'),
    ("pyproject.toml", "regex", r'(^version = ")([^"]+)(")'),
    ("package.json", "json", "version"),
    ("package-lock.json", "package-lock", None),
    ("vscode-extension/package.json", "json", "version"),
    ("vscode-extension/package-lock.json", "package-lock", None),
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
