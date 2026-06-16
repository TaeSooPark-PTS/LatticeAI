#!/usr/bin/env python3
"""Release smoke checks for exact-version release artifacts.

This complements ``validate_release_artifacts.py`` by opening the generated
artifacts and proving the installable surfaces are coherent:

- wheel installs/imports in a fresh environment via ``wheel_smoke.py``
- npm tgz contains the package metadata, CLI bin, and static assets
- static asset manifest points at files that exist on disk
- Tauri DMG/app bundle exists, and can optionally be launched briefly
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tarfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str], **kwargs) -> None:
    print("+", " ".join(str(item) for item in cmd), flush=True)
    subprocess.run(cmd, check=True, **kwargs)


def smoke_wheel(version: str, skip_health: bool) -> None:
    wheel = REPO_ROOT / "dist" / f"ltcai-{version}-py3-none-any.whl"
    if not wheel.is_file():
        raise SystemExit(f"missing wheel: {wheel}")
    cmd = [sys.executable, str(REPO_ROOT / "scripts" / "wheel_smoke.py"), "--wheel", str(wheel)]
    if skip_health:
        cmd.append("--skip-health")
    run(cmd, cwd=REPO_ROOT)


def smoke_npm_tgz(version: str) -> None:
    tgz = REPO_ROOT / f"ltcai-{version}.tgz"
    if not tgz.is_file():
        raise SystemExit(f"missing npm tgz: {tgz}")
    required = {
        "package/package.json",
        "package/bin/ltcai.js",
        "package/static/app/index.html",
        "package/static/app/asset-manifest.json",
    }
    with tarfile.open(tgz, "r:gz") as archive:
        names = set(archive.getnames())
        missing = sorted(required - names)
        if missing:
            raise SystemExit(f"{tgz.name} missing entries: {missing}")
        package_data = json.loads(archive.extractfile("package/package.json").read().decode("utf-8"))  # type: ignore[union-attr]
        if package_data.get("version") != version:
            raise SystemExit(f"{tgz.name} package.json version {package_data.get('version')!r} != {version!r}")
    print(f"npm tgz smoke ok: {tgz.name}")


def smoke_static_assets(version: str) -> None:
    manifest_path = REPO_ROOT / "static" / "app" / "asset-manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"missing static manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != version:
        raise SystemExit(f"static manifest version {manifest.get('version')!r} != {version!r}")
    required_paths = {"/static/app/index.html"}
    required_paths.update(str(path) for path in manifest.get("assets", {}).values())
    missing = []
    for static_path in required_paths:
        rel = static_path.removeprefix("/static/")
        path = REPO_ROOT / "static" / rel
        if not path.is_file():
            missing.append(static_path)
    if missing:
        raise SystemExit(f"static manifest references missing files: {missing}")
    print(f"static asset smoke ok: {len(required_paths)} files")


def smoke_tauri(version: str, launch: bool) -> None:
    dmg = REPO_ROOT / "src-tauri" / "target" / "release" / "bundle" / "dmg" / f"Lattice AI_{version}_aarch64.dmg"
    app = REPO_ROOT / "src-tauri" / "target" / "release" / "bundle" / "macos" / "Lattice AI.app"
    macos_dir = app / "Contents" / "MacOS"
    executable = app / "Contents" / "MacOS" / "Lattice AI"
    if not executable.exists() and macos_dir.is_dir():
        candidates = [path for path in macos_dir.iterdir() if path.is_file() and os.access(path, os.X_OK)]
        if candidates:
            executable = candidates[0]
    missing = [path for path in (dmg, app, executable) if not path.exists()]
    if missing:
        raise SystemExit("missing Tauri artifact(s): " + ", ".join(str(path) for path in missing))
    if launch:
        env = {
            **os.environ,
            "LATTICEAI_ENABLE_TELEGRAM": "false",
            "LATTICEAI_AUTOLOAD_MODELS": "false",
        }
        proc = subprocess.Popen([str(executable)], cwd=REPO_ROOT, env=env)
        time.sleep(4)
        if proc.poll() not in (None, 0):
            raise SystemExit(f"Tauri executable exited early with {proc.returncode}")
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        print("Tauri launch smoke ok")
    else:
        print(f"Tauri artifact smoke ok: {dmg.name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="exact version to smoke, e.g. 6.2.0")
    parser.add_argument("--skip-wheel-health", action="store_true", help="skip FastAPI /health check inside wheel smoke")
    parser.add_argument("--launch-tauri", action="store_true", help="briefly launch the built Tauri executable")
    args = parser.parse_args()

    smoke_wheel(args.version, args.skip_wheel_health)
    smoke_npm_tgz(args.version)
    smoke_static_assets(args.version)
    smoke_tauri(args.version, args.launch_tauri)
    print(f"release smoke passed for v{args.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
