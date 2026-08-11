"""The Python half of the Python↔Rust retrieval parity contract.

``rust/lattice-retrieval`` is pinned to the golden files under
``rust/fixtures/golden/``. Those goldens were produced by the Python engines, so
a change to Python retrieval semantics can silently invalidate them: the Rust
tests keep passing (they compare against the same stale file), and nobody learns
that the port stopped being a port until a user notices a different answer.

This module closes that loop from the other side. It re-runs the **real** Python
entry points against the **committed** fixture database and asserts the goldens
still describe what Python does — the Phase-1 search engines (``hybrid_search`` /
``search`` / ``vector_search``) and the v11.5.0 ports beside them: the knowledge
graph's ``relationship_search`` / ``traverse``, the service layer's
``graph_search`` and three-channel ``hybrid_search``, the durable history reads
and grouping, and the ``ContextAssembler``. Two consequences worth stating:

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


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    """Every v11.5.0 entry point, over its own copy of the committed database.

    Its own copy because ``ConversationStore.__init__`` runs schema migrations:
    pointing it at the checked-in artefact would edit the fixture from a test.
    """
    source = fixtures.STORE_PATH
    assert source.is_file(), f"{source} is missing — run the fixture generator"
    workspace = tmp_path_factory.mktemp("rust-parity-suites")
    target = workspace / source.name
    shutil.copyfile(source, target)
    with fixtures.pinned_environment():
        yield fixtures.Harness(target)


#: Every (suite, spec) pair the generator wrote a golden for.
SUITE_CASES = [
    (suite, spec) for suite in sorted(fixtures.SUITES) for spec in fixtures.SUITES[suite]
]


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
    } | {
        f"{suite}__{spec['key']}.json" for suite, spec in SUITE_CASES
    } | {"manifest.json", "embeddings_golden.json", "rounding_golden.json"}
    assert {path.name for path in golden_dir.glob("*.json")} == expected


def test_manifest_describes_the_current_suite_set(manifest):
    """The nine v11.5.0 ports and their specs, as the Rust suite enumerates them."""
    recorded = manifest["suites"]
    assert set(recorded) == set(fixtures.SUITES) == set(fixtures.SUITE_RUNNERS)
    assert len(recorded) == 9, "nine ported entry points; a missing suite is a missing proof"
    for suite, specs in recorded.items():
        assert specs == fixtures.SUITES[suite], suite
        assert specs, f"{suite} has no specs"
    assert len(SUITE_CASES) >= 100, "the suite set is the coverage — keep it wide"


@pytest.mark.parametrize(
    "suite,spec", SUITE_CASES, ids=lambda value: value if isinstance(value, str) else value["key"]
)
def test_python_suite_still_matches_its_golden(harness, golden_dir: Path, suite, spec):
    """Every ported entry point still answers exactly what its golden records."""
    golden = json.loads(
        (golden_dir / f"{suite}__{spec['key']}.json").read_text(encoding="utf-8")
    )
    assert golden["suite"] == suite
    assert golden["spec"] == spec, "the golden was generated from a different spec"
    with fixtures.pinned_environment():
        result = fixtures.run_suite(harness, suite, spec)
    assert _canonical(result) == _canonical(golden["result"])


def test_the_conversation_corpus_actually_landed_in_the_store(harness):
    """A history golden over an empty table would pass and prove nothing."""
    rows = harness.conversations.history()
    assert len(rows) == len(fixtures.MESSAGES)
    assert any(not item.get("conversation_id") for item in rows), "legacy rows are the point"
    assert any(item.get("workspace_id") for item in rows)
    assert {item.get("user_email") for item in rows} >= {None, "jiwon@lattice.ai"}


def test_traverse_records_its_refusals_rather_than_skipping_them(golden_dir: Path):
    """The two documented ``ValueError``s are part of the ported contract."""
    refusals = {
        key: json.loads(
            (golden_dir / f"traverse__{key}.json").read_text(encoding="utf-8")
        )["result"]
        for key in ("empty_id", "scoped_empty", "scoped_seed_hidden")
    }
    assert refusals["empty_id"] == {"error": "node_id required"}
    for key in ("scoped_empty", "scoped_seed_hidden"):
        assert refusals[key]["error"].startswith("graph node not found")
    # A successful traversal never carries the key the refusals are detected by.
    ok = json.loads((golden_dir / "traverse__hub_d2.json").read_text(encoding="utf-8"))
    assert "error" not in ok["result"]
    assert ok["result"]["nodes"] and ok["result"]["edges"]


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
