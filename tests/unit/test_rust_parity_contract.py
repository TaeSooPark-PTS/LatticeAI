"""The Python half of the Python↔Rust retrieval parity contract.

``rust/lattice-retrieval`` is pinned to the golden files under
``rust/fixtures/golden/``. Those goldens were produced by the Python engines, so
a change to Python retrieval semantics can silently invalidate them: the Rust
tests keep passing (they compare against the same stale file), and nobody learns
that the port stopped being a port until a user notices a different answer.

This module closes that loop from the other side. It re-runs the **real**
``hybrid_search`` / ``search`` / ``vector_search`` against the **committed**
fixture database and asserts the goldens still describe what Python does. Two
consequences worth stating:

* it is a contract test, not a regression test — a deliberate ranking change is
  supposed to fail it, and the fix is to regenerate the fixtures with
  ``.venv/bin/python scripts/generate_rust_parity_fixtures.py`` and re-run the
  cargo suite so both halves move together;
* it imports nothing from the Rust side. The shared artefacts are the database
  and the JSON; no toolchain is required to run this file.

Comparison is over the canonical JSON encoding rather than ``==`` so that an
``int`` quietly becoming a ``float`` (``1 == 1.0`` in Python) still fails.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = REPO_ROOT / "scripts" / "generate_rust_parity_fixtures.py"


def _load_generator():
    """Import the fixture generator by path (``scripts`` is not a package)."""
    spec = importlib.util.spec_from_file_location("rust_parity_fixtures", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixtures = _load_generator()


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


@pytest.fixture(scope="module")
def golden_dir() -> Path:
    directory = fixtures.GOLDEN_DIR
    assert directory.is_dir(), (
        f"{directory} is missing — run scripts/generate_rust_parity_fixtures.py"
    )
    return directory


@pytest.fixture(scope="module")
def manifest(golden_dir: Path) -> dict:
    return json.loads((golden_dir / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    """The committed database, opened from a copy so the artefact stays pristine.

    The store opens SQLite read/write and switches the journal to WAL, which
    would leave ``-wal``/``-shm`` siblings next to a checked-in file. Copying
    first is the difference between a fixture and a fixture that drifts.
    """
    source = fixtures.STORE_PATH
    assert source.is_file(), f"{source} is missing — run the fixture generator"
    workspace = tmp_path_factory.mktemp("rust-parity")
    target = workspace / source.name
    shutil.copyfile(source, target)
    with fixtures.pinned_environment():
        yield fixtures.open_store(target)


def test_fixture_database_is_committed_and_small():
    """A parity fixture nobody wants to clone is a parity fixture nobody runs."""
    size = fixtures.STORE_PATH.stat().st_size
    assert 0 < size < 1_000_000, f"fixture database is {size} bytes"


def test_manifest_describes_the_current_query_set(manifest, golden_dir: Path):
    """The manifest, the query set and the files on disk must agree.

    Adding a query without regenerating leaves a manifest that promises a golden
    which does not exist — this is where that shows up, instead of in a confusing
    "file not found" from the Rust suite.
    """
    assert manifest["frozen_now"] == fixtures.FROZEN_NOW
    assert manifest["store"] == fixtures.STORE_PATH.name
    assert manifest["engines"] == sorted(fixtures.ENGINES)
    assert manifest["pinned_env"] == fixtures.PINNED_ENV
    assert [spec["key"] for spec in manifest["queries"]] == [
        spec["key"] for spec in fixtures.QUERIES
    ]
    assert len(fixtures.QUERIES) >= 14, "the query set is the coverage — keep it wide"
    keys = {spec["key"] for spec in fixtures.QUERIES}
    expected = {
        f"{engine}__{key}.json" for engine in fixtures.ENGINES for key in keys
    } | {"manifest.json", "embeddings_golden.json", "rounding_golden.json"}
    assert {path.name for path in golden_dir.glob("*.json")} == expected


@pytest.mark.parametrize("spec", fixtures.QUERIES, ids=lambda spec: spec["key"])
@pytest.mark.parametrize("engine", sorted(fixtures.ENGINES))
def test_python_engine_still_matches_its_golden(store, golden_dir: Path, engine, spec):
    """Every query × engine still answers exactly what the golden records."""
    golden = json.loads(
        (golden_dir / f"{engine}__{spec['key']}.json").read_text(encoding="utf-8")
    )
    assert golden["engine"] == engine
    assert golden["query"] == spec["query"]
    with fixtures.pinned_environment():
        result = fixtures.run_engine(store, engine, spec)
    assert _canonical(result) == _canonical(golden["result"])


def test_embedding_golden_still_describes_the_python_embedder(golden_dir: Path):
    """Tokenizer output, hash pairs and full vectors, re-derived and compared."""
    with fixtures.pinned_environment():
        rebuilt = fixtures.embeddings_golden()
    recorded = json.loads(
        (golden_dir / "embeddings_golden.json").read_text(encoding="utf-8")
    )
    assert _canonical(rebuilt) == _canonical(recorded)
    assert len(recorded["cases"]) >= 8
    assert all(len(case["vector"]) == recorded["dim"] for case in recorded["cases"])


def test_rounding_golden_still_describes_cpython_round(golden_dir: Path):
    """``round(x, 6)`` on the values where the half-even tie rule is visible."""
    recorded = json.loads(
        (golden_dir / "rounding_golden.json").read_text(encoding="utf-8")
    )
    assert _canonical(fixtures.rounding_golden()) == _canonical(recorded)
    assert len(recorded) >= 12


def test_recency_decay_is_pinned_to_the_frozen_clock(store, golden_dir: Path):
    """The one place the engines read a clock, and the reason it is frozen.

    Without the freeze this whole file would pass on the day it was written and
    fail every day after, which is the failure mode that makes teams delete
    parity harnesses.
    """
    golden = json.loads((golden_dir / "hybrid__en_recency.json").read_text(encoding="utf-8"))
    decays = {
        match["node_id"]: match["scores"].get("age_decay")
        for match in golden["result"]["matches"]
    }
    assert decays, "the recency query must produce matches"
    assert all(value is not None and 0.5 <= value <= 1.0 for value in decays.values())
    assert golden["result"]["query_class"] == "recency"
    # A non-recency class records no decay at all — the honest signal that the
    # half-life applies exactly where the policy wires it.
    other = json.loads((golden_dir / "hybrid__en_fact.json").read_text(encoding="utf-8"))
    assert all(
        "age_decay" not in match["scores"] for match in other["result"]["matches"]
    )
