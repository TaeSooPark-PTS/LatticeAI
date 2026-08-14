"""Project the worker profile's route set into the fixture Rust proxies from.

The v11.6.0 gateway inverts the reverse proxy: instead of forwarding everything
it does not serve itself, it forwards **only** what the Python worker actually
mounts, and answers 404 for the rest. That allowlist has exactly one source of
truth — :func:`latticeai.runtime.build_phases.worker_profile.worker_route_keys`
— and this script writes it down where a Rust crate can read it at compile
time.

Two spellings per route, because the two runtimes disagree about path syntax
and only one of them can be the file's:

* ``path`` is FastAPI's, converters included (``/models/switch/{model_id:path}``).
  It is what ``APIRoute.path`` returns, so the Python side compares without
  normalisation.
* ``axum`` is the same route as axum 0.7 spells it (``/models/switch/*model_id``).
  A ``{name:path}`` converter matches slashes; a plain ``:name`` capture does
  not, so an id containing a ``/`` would 404 at the front door while working
  against the worker directly. WP-I5's ``greedy_path_params`` records the same
  fact for the public contract.

Usage::

    .venv/bin/python scripts/gen_worker_allowlist_fixture.py

Running it twice must produce the same bytes; ``tests/unit/test_worker_allowlist.py``
asserts the committed file still equals ``worker_route_keys()``.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from latticeai.runtime.build_phases.worker_profile import (  # noqa: E402
    WORKER_COMPUTE_ROUTES,
    WORKER_ROUTES,
    WORKER_SEAM_ROUTES,
    worker_route_keys,
)

FIXTURE_PATH = REPO_ROOT / "rust" / "fixtures" / "worker_allowlist.json"
SCHEMA = "worker-proxy-allowlist/v1"

#: ``{name:path}`` — FastAPI's greedy converter. Anything else is one segment.
_GREEDY = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*):path\}")
#: ``{name}`` — an ordinary single-segment parameter.
_PLAIN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

#: Which tuple a route came from, so a reader can see *why* it is allowed.
GROUPS = (
    ("product", WORKER_ROUTES),
    ("state_seam", WORKER_SEAM_ROUTES),
    ("compute_seam", WORKER_COMPUTE_ROUTES),
)


def axum_path(path: str) -> str:
    """FastAPI's path as axum 0.7 spells it."""
    path = _GREEDY.sub(r"*\1", path)
    return _PLAIN.sub(r":\1", path)


def build() -> Dict[str, Any]:
    """The fixture document."""
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


if __name__ == "__main__":  # pragma: no cover - script entry point
    raise SystemExit(main())
