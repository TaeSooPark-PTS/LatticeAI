"""Target definitions and regex/JSON/package-lock update helpers for version bumping."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from scripts.bump_version.cargo import CARGO_TARGETS, RUST_CRATES, RUST_LOCKFILES
from scripts.bump_version.openapi import (
    OPENAPI_FRAGMENTS,
    OPENAPI_TARGETS,
    apply_openapi_envelope,
    apply_openapi_fragment,
    apply_openapi_index,
    apply_openapi_spec,
)

DEFAULT_REPO = Path(__file__).resolve().parents[2]
REPO = DEFAULT_REPO

SEMVER_PATTERN = re.compile(r"\d+\.\d+\.\d+([-.][0-9A-Za-z.]+)?")

# (path, kind, pattern) — pattern groups: (prefix, version)
TARGETS: List[Tuple[str, str, Optional[str]]] = [
    ("latticeai/__init__.py", "regex", r'(__version__ = ")([^"]+)(")'),
    ("lattice_brain/__init__.py", "regex", r'(__version__ = ")([^"]+)(")'),
    # WP-P1 deleted workspace_os / marketplace / multi_agent / the legacy
    # shim registry. The package version is the runtime canonical now.
    (
        "latticeai/services/architecture_readiness.py",
        "regex",
        r'(ARCHITECTURE_VERSION_TARGET = ")([^"]+)(")',
    ),
    (
        "latticeai/services/product_readiness.py",
        "regex",
        r'(PRODUCT_VERSION_TARGET = ")([^"]+)(")',
    ),
    ("pyproject.toml", "regex", r'(^version = ")([^"]+)(")'),
    ("package.json", "json", "version"),
    ("package-lock.json", "package-lock", None),
    ("vscode-extension/package.json", "json", "version"),
    ("vscode-extension/package-lock.json", "package-lock", None),
    ("browser-extension/manifest.json", "json", "version"),
    *CARGO_TARGETS,
    ("static/app/asset-manifest.json", "json", "version"),
    *OPENAPI_TARGETS,
]


def is_valid_semver(version: str) -> bool:
    """Return True if ``version`` is a valid semantic version string."""
    return bool(SEMVER_PATTERN.fullmatch(version))


def resolve_repo(explicit_repo: Optional[Path] = None) -> Path:
    """Resolve repository root, honoring pytest ``monkeypatch.setattr(..., 'REPO', ...)``."""
    if explicit_repo is not None:
        return explicit_repo
    for mod_name in (
        "bump_version",
        "scripts.bump_version",
        "scripts.bump_version.cli",
        "scripts.bump_version.targets",
        "scripts.bump_version.openapi",
        "scripts.bump_version.cargo",
        "scripts.bump_version.markers",
    ):
        mod = sys.modules.get(mod_name)
        if mod is not None:
            candidate = getattr(mod, "REPO", None)
            if isinstance(candidate, Path) and candidate != DEFAULT_REPO:
                return candidate
    return REPO


def apply_regex_target(
    text: str, pattern: str, version: str
) -> Tuple[bool, str, str, bool]:
    """Update a regex target with 3 capture groups ``(prefix)(version)(suffix)``."""
    m = re.search(pattern, text, flags=re.MULTILINE)
    if m is None:
        return False, "", text, False
    current = str(m.group(2))

    def _repl(match: re.Match[str]) -> str:
        return f"{match.group(1)}{version}{match.group(3)}"

    new, n = re.subn(
        pattern,
        _repl,
        text,
        count=1,
        flags=re.MULTILINE,
    )
    changed = n == 1 and new != text
    return True, current, new, changed


def apply_json_target(
    text: str, field: str, version: str
) -> Tuple[bool, str, str, bool]:
    """Update a top-level field in a JSON file."""
    data = json.loads(text)
    if field not in data:
        return False, "", text, False
    current = str(data.get(field))
    changed = current != version
    data[field] = version
    new = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    return True, current, new, changed


def apply_package_lock_target(text: str, version: str) -> Tuple[bool, str, str, bool]:
    """Update top-level ``version`` and ``packages[''].version`` in ``package-lock.json``."""
    data = json.loads(text)
    if "version" not in data:
        return False, "", text, False
    current = str(data.get("version"))
    changed = current != version
    data["version"] = version
    if "packages" in data and "" in data["packages"]:
        data["packages"][""]["version"] = version
    new = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    return True, current, new, changed


def apply_target(
    path: Path,
    kind: str,
    spec: Optional[str],
    version: str,
    *,
    check: bool = False,
) -> Tuple[bool, str, str, bool]:
    """Dispatch a single target update or check."""
    text = path.read_text(encoding="utf-8")
    if kind == "regex":
        assert spec is not None
        return apply_regex_target(text, spec, version)
    if kind == "json":
        assert spec is not None
        return apply_json_target(text, spec, version)
    if kind == "package-lock":
        return apply_package_lock_target(text, version)
    if kind == "openapi-spec":
        return apply_openapi_spec(text, version)
    if kind == "openapi-envelope":
        return apply_openapi_envelope(text, version)
    if kind == "openapi-fragment":
        return apply_openapi_fragment(text, version)
    if kind == "openapi-index":
        return apply_openapi_index(text, version, path, check=check)
    raise AssertionError(f"Unknown target kind: {kind}")  # pragma: no cover


def run_targets(
    targets: Iterable[Tuple[str, str, Optional[str]]],
    version: str,
    *,
    check: bool = False,
    repo: Optional[Path] = None,
) -> int:
    """Execute version bump or verification over ``targets``."""
    target_repo = resolve_repo(repo)
    failures = 0
    for rel, kind, spec in targets:
        path = target_repo / rel
        if not path.exists():
            print(f"  skip  {rel} (missing)")
            continue
        ok, current, new, changed = apply_target(path, kind, spec, version, check=check)
        if not ok:
            print(f"  FAIL  {rel}: version field not found")
            failures += 1
            continue
        if check:
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


__all__ = [
    "DEFAULT_REPO",
    "OPENAPI_FRAGMENTS",
    "REPO",
    "RUST_CRATES",
    "RUST_LOCKFILES",
    "TARGETS",
    "apply_json_target",
    "apply_package_lock_target",
    "apply_regex_target",
    "apply_target",
    "is_valid_semver",
    "resolve_repo",
    "run_targets",
]
