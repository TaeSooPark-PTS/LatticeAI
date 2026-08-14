"""The gateway's proxy allowlist may not drift from the worker's route set.

``rust/fixtures/worker_allowlist.json`` is what ``lattice-host`` compiles into
the binary and forwards by. It is a *projection* of
:func:`latticeai.runtime.build_phases.worker_profile.worker_route_keys`, and a
projection nobody checks is a copy: add a route to the worker and forget the
fixture, and the front door answers 404 for a live endpoint; delete one and the
front door proxies a path that no longer exists.

So this asserts the two are the same set, byte-for-byte on the paths, and that
the file the generator would write today is the file that is committed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "rust" / "fixtures" / "worker_allowlist.json"
GENERATOR = REPO / "scripts" / "gen_worker_allowlist_fixture.py"

sys.path.insert(0, str(REPO / "scripts"))

from gen_worker_allowlist_fixture import axum_path, build  # noqa: E402

from latticeai.runtime.build_phases.worker_profile import (  # noqa: E402
    worker_route_keys,
)


@pytest.fixture(scope="module")
def committed() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_committed_allowlist_is_exactly_the_worker_route_set(committed):
    fixture_keys = {(row["method"], row["path"]) for row in committed["routes"]}
    assert fixture_keys == worker_route_keys(), (
        "rust/fixtures/worker_allowlist.json and worker_route_keys() disagree. "
        "Run: .venv/bin/python scripts/gen_worker_allowlist_fixture.py"
    )
    assert committed["count"] == len(committed["routes"]) == len(worker_route_keys())


def test_regenerating_reproduces_the_committed_bytes():
    expected = json.dumps(build(), indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    assert FIXTURE.read_text(encoding="utf-8") == expected, (
        "the committed fixture is stale. Run: "
        ".venv/bin/python scripts/gen_worker_allowlist_fixture.py"
    )


def test_the_generator_is_deterministic(tmp_path):
    """Two runs, same bytes — the drift gate is worthless otherwise."""
    runs = []
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, str(GENERATOR)],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            check=True,
        )
        assert "routes" in result.stdout
        runs.append(FIXTURE.read_bytes())
    assert runs[0] == runs[1]


def test_greedy_converters_become_axum_wildcards(committed):
    """``{id:path}`` matches slashes; ``:id`` does not. The front door must not
    404 an id that the worker would have accepted."""
    for row in committed["routes"]:
        if ":path}" in row["path"]:
            name = row["path"].split("{", 1)[1].split(":path}", 1)[0]
            assert f"*{name}" in row["axum"], (
                f"{row['method']} {row['path']} is greedy in FastAPI; axum must "
                f"mount it as /*{name}, not :{name}"
            )
        else:
            assert "*" not in row["axum"]
    assert axum_path("/models/switch/{model_id:path}") == "/models/switch/*model_id"
    assert axum_path("/api/hooks/{hook_id}") == "/api/hooks/:hook_id"
    assert axum_path("/health") == "/health"


def test_every_route_names_the_tuple_it_came_from(committed):
    groups = {row["group"] for row in committed["routes"]}
    assert groups <= {"product", "state_seam", "compute_seam"}
    assert "product" in groups, "the worker still serves product routes"
    assert "compute_seam" in groups, "the compute seams are what the worker is for"


def test_nothing_native_is_on_the_allowlist(committed):
    """§W3b moved these writes into Rust. Proxying them would mean two writers
    of one SQLite file — the invariant `lattice_core::db::tables` states."""
    paths = {row["path"] for row in committed["routes"]}
    for gone in (
        "/knowledge-graph/ingest",
        "/upload/document",
        "/api/index/drain",
        "/api/index/rebuild",
        "/api/capture/voice",
        "/tools/create_docx",
        "/tools/create_xlsx",
        "/tools/create_pptx",
        "/tools/create_pdf",
        "/worker/graph/mutate",
    ):
        assert gone not in paths, f"{gone} is native; the gateway must not proxy it"
