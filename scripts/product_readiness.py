#!/usr/bin/env python3
"""Print the 7.7 product-readiness scorecard and fail CI when incomplete.

Run it as often as you like — it re-probes the repo every time, so it is the
single objective answer to "is this a finished product yet?". Exit code is 0
only when every product gate resolves its evidence on disk.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from latticeai.services.product_readiness import product_readiness  # noqa: E402


def main() -> int:
    report = product_readiness(REPO_ROOT)
    print(f"Lattice AI product readiness — target {report['version_target']}")
    print(f"Score: {report['score']}  (architecture: {report['architecture']})")
    print("-" * 60)
    for gate in report["gates"]:
        mark = "✓" if gate["status"] == "complete" else "✗"
        print(f"{mark} {gate['id']:<22} {gate['title']}")
        for miss in gate["missing"]:
            print(f"    missing: {miss}")
    print("-" * 60)
    print(f"STATUS: {report['status'].upper()}")
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
