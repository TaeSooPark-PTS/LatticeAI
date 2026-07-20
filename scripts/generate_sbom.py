#!/usr/bin/env python3
"""Generate CycloneDX SBOMs for the Python and npm dependency trees.

Thin, dependency-light wrapper so the same artifacts can be produced locally
and in CI (.github/workflows/dependency-audit.yml). Python SBOM uses pip-audit
(``pip install pip-audit``); npm SBOM uses ``npm sbom`` (npm >= 9).

Usage:
    .venv/bin/python scripts/generate_sbom.py --out-dir dist/sbom
Each generator is best-effort: a missing tool is reported, not fatal, so one
ecosystem's absence never blocks the other.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _run(cmd: list[str], *, stdout_path: Path | None = None) -> tuple[bool, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        return False, f"tool not found: {exc}"
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "").strip()[:400]
    if stdout_path is not None:
        stdout_path.write_text(proc.stdout, encoding="utf-8")
    return True, ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate CycloneDX SBOMs")
    parser.add_argument("--out-dir", default="dist/sbom", help="output directory")
    parser.add_argument("--requirements", default="requirements.txt")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    results: list[tuple[str, bool, str]] = []

    py_sbom = out / "sbom-python.json"
    ok, err = _run(
        [
            sys.executable, "-m", "pip_audit",
            "-r", str(root / args.requirements),
            "-f", "cyclonedx-json",
            "-o", str(py_sbom),
        ]
    )
    results.append(("python (pip-audit)", ok, err or str(py_sbom)))

    npm_sbom = out / "sbom-npm.json"
    ok, err = _run(["npm", "sbom", "--sbom-format", "cyclonedx"], stdout_path=npm_sbom)
    results.append(("npm (npm sbom)", ok, err or str(npm_sbom)))

    print("SBOM generation:")
    any_ok = False
    for name, ok, detail in results:
        print(f"  [{'OK' if ok else 'SKIP'}] {name}: {detail}")
        any_ok = any_ok or ok
    return 0 if any_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
