from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from latticeai.runtime.build_phases.worker_profile import (
    WORKER_COMPUTE_ROUTES,
    WORKER_ROUTES,
    WORKER_SEAM_ROUTES,
    worker_route_keys,
)

FIXTURE_PATH = REPO_ROOT / "rust" / "fixtures" / "worker_allowlist.json"
SCHEMA = "worker-proxy-allowlist/v1"

_GREEDY = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*):path\}")
_PLAIN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

GROUPS = (
    ("product", WORKER_ROUTES),
    ("state_seam", WORKER_SEAM_ROUTES),
    ("compute_seam", WORKER_COMPUTE_ROUTES),
)


def axum_path(path: str) -> str:
    path = _GREEDY.sub(r"*\1", path)
    return _PLAIN.sub(r":\1", path)


def build() -> Dict[str, Any]:
    group_of = {
        (method, path): name for name, routes in GROUPS for method, path in routes
    }
    routes: List[Dict[str, str]] = []
    for method, path in sorted(worker_route_keys()):
        routes.append(
            {
                "method": method,
                "path": path,
                "axum": axum_path(path),
                "group": group_of[(method, path)],
            }
        )
    return {
        "schema": SCHEMA,
        "note": (
            "The gateway's reverse proxy allowlist. Generated from "
            "latticeai.runtime.build_phases.worker_profile.worker_route_keys() "
            "by scripts/gen_worker_allowlist_fixture.py; a path absent here is "
            "answered natively or 404, never forwarded."
        ),
        "source": "latticeai/runtime/build_phases/worker_profile.py",
        "generated_by": "scripts/gen_worker_allowlist_fixture.py",
        "count": len(routes),
        "routes": routes,
    }


def main() -> int:
    document = build()
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"wrote {FIXTURE_PATH.relative_to(REPO_ROOT)} ({document['count']} routes)",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
