#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import tarfile
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+([.-][0-9A-Za-z.]+)?$")
FORBIDDEN_PERSONAL_PATHS = (
    re.compile(r"(?:^|/)DISCORD_AGENTS\.md$", re.IGNORECASE),
    re.compile(r"(?:^|/)scripts/[^/]*discord-bridge[^/]*\.mjs$", re.IGNORECASE),
    re.compile(r"(?:^|/)scripts/start-[^/]*-discord\.sh$", re.IGNORECASE),
    re.compile(r"(?:^|/)scripts/com\.[^/]*\.discord\.plist$", re.IGNORECASE),
    re.compile(r"(?:^|/)bin/pts-grok$", re.IGNORECASE),
    re.compile(r"(?:^|/)scripts/launch-pts-grok\.sh$", re.IGNORECASE),
    re.compile(r"(?:^|/)(?:HEARTBEAT|IDENTITY|SOUL|TOOLS|USER)\.md$", re.IGNORECASE),
)


def _expected_names(version: str) -> Dict[str, str]:
    return {
        "wheel": f"ltcai-{version}-py3-none-any.whl",
        "sdist": f"ltcai-{version}.tar.gz",
        "vsix": f"ltcai-{version}.vsix",
    }


def _vsix_has_entrypoint(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
    except (zipfile.BadZipFile, OSError):
        return False
    return any(name.endswith("extension/out/extension.js") for name in names)


def _vsix_version(path: Path) -> Optional[str]:
    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if name.endswith("extension/package.json"):
                    data = json.loads(zf.read(name).decode("utf-8"))
                    return str(data.get("version") or "")
    except (
        zipfile.BadZipFile,
        OSError,
        KeyError,
        ValueError,
        TypeError,
        UnicodeDecodeError,
    ):
        return None
    return None


def _forbidden_archive_entries(path: Path) -> List[str]:
    names: List[str] = []
    try:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
        elif tarfile.is_tarfile(path):
            with tarfile.open(path, "r:*") as archive:
                names = archive.getnames()
    except (OSError, tarfile.TarError, zipfile.BadZipFile):
        return []

    return sorted(
        name
        for name in names
        if any(
            pattern.search(name.replace("\\", "/"))
            for pattern in FORBIDDEN_PERSONAL_PATHS
        )
    )


def _reject_personal_files(path: Path, artifact_type: str, errors: List[str]) -> None:
    forbidden = _forbidden_archive_entries(path)
    if forbidden:
        errors.append(
            f"{artifact_type} contains machine-local bot/agent files: {', '.join(forbidden)}"
        )


def validate(
    version: str,
    dist_dir: Path,
    *,
    require_vsix: bool,
    require_tgz: bool,
    require_dmg: bool = False,
) -> Dict[str, object]:
    errors: List[str] = []
    warnings: List[str] = []
    found: Dict[str, object] = {}

    if not SEMVER_RE.match(version):
        errors.append(f"version '{version}' is not a valid semantic version")

    if not dist_dir.is_dir():
        errors.append(f"dist directory not found: {dist_dir}")
        return {
            "version": version,
            "ok": False,
            "errors": errors,
            "warnings": warnings,
            "found": found,
        }

    expected = _expected_names(version)

    for key in ("wheel", "sdist"):
        artifact = dist_dir / expected[key]
        if artifact.is_file():
            found[key] = str(artifact)
            _reject_personal_files(artifact, key, errors)
        else:
            errors.append(f"missing {key}: {artifact.name}")

    vsix = dist_dir / expected["vsix"]
    if vsix.is_file():
        found["vsix"] = str(vsix)
        _reject_personal_files(vsix, "vsix", errors)
        if not _vsix_has_entrypoint(vsix):
            errors.append(
                f"{vsix.name} is missing extension/out/extension.js (compile step skipped?)"
            )
        vsix_ver = _vsix_version(vsix)
        if vsix_ver and vsix_ver != version:
            errors.append(
                f"{vsix.name} internal version '{vsix_ver}' != expected '{version}'"
            )
    elif require_vsix:
        errors.append(f"missing vsix: {vsix.name}")

    tgz = dist_dir.parent / f"ltcai-{version}.tgz"
    if tgz.is_file():
        found["tgz"] = str(tgz)
        _reject_personal_files(tgz, "npm tarball", errors)
    elif require_tgz:
        errors.append(f"missing npm tarball: {tgz.name} (run `npm pack`)")

    dmg = (
        dist_dir.parent
        / "src-tauri"
        / "target"
        / "release"
        / "bundle"
        / "dmg"
        / f"Lattice AI_{version}_aarch64.dmg"
    )
    if dmg.is_file():
        found["dmg"] = str(dmg)
    elif require_dmg:
        errors.append(f"missing dmg: {dmg}")

    other_versions = set()
    for item in dist_dir.glob("ltcai-*"):
        m = re.match(r"ltcai-(\d+\.\d+\.\d+)(?:[-.]|$)", item.name)
        if m and m.group(1) != version:
            other_versions.add(m.group(1))
    if other_versions:
        warnings.append(
            "dist/ also contains other versions "
            f"{sorted(other_versions)} — NEVER upload with a `dist/*` glob; "
            f"upload only the explicit {version} filenames."
        )

    return {
        "version": version,
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "found": found,
        "expected": expected,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("version", help="exact version to validate, e.g. 1.1.0")
    parser.add_argument("--dist", default="dist", help="dist directory (default: dist)")
    parser.add_argument(
        "--require-vsix", action="store_true", help="fail if the VSIX is absent"
    )
    parser.add_argument(
        "--require-tgz",
        action="store_true",
        help="fail if the npm pack tarball is absent",
    )
    parser.add_argument(
        "--require-dmg", action="store_true", help="fail if the Tauri DMG is absent"
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    args = parser.parse_args(argv)

    result = validate(
        args.version,
        Path(args.dist),
        require_vsix=args.require_vsix,
        require_tgz=args.require_tgz,
        require_dmg=args.require_dmg,
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        status = "OK" if result["ok"] else "FAILED"
        print(f"Release artifact validation for v{result['version']}: {status}")
        for key, path in result["found"].items():
            print(f"  found {key}: {Path(path).name}")
        for warning in result["warnings"]:
            print(f"  WARN: {warning}")
        for error in result["errors"]:
            print(f"  ERROR: {error}")

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
