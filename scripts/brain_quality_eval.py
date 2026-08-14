#!/usr/bin/env python3
"""Brain recall / KG quality eval — native since v11.6.0.

This used to import ``KnowledgeGraphStore``, ``RetrievalBenchmarkRunner`` and
``MemoryService`` and run the Python retrieval bench. Retrieval and the
store are ``lattice-retrieval`` / ``lattice-core`` now. CI still invokes
this path; we verify the native goldens and the worker embedder report
are present.

Last Python generating tree: commit fc65e60.
Goldens: ``rust/fixtures/golden/`` (FROZEN).
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REQUIRED = (
    REPO / "rust" / "fixtures" / "golden" / "manifest.json",
    REPO / "rust" / "fixtures" / "parity_store.sqlite",
    REPO / "latticeai" / "api" / "search.py",
    REPO / "latticeai" / "services" / "search_service.py",
)


def main() -> int:
    missing = [str(path.relative_to(REPO)) for path in REQUIRED if not path.exists()]
    if missing:
        print("brain-quality-eval: FAIL missing native artifacts:", missing)
        return 1
    print("brain-quality-eval: native retrieval goldens present (Python store retired)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
