"""The Python half of the Python↔Rust *chunking* parity contract (v11.5.0 §2c).

``rust/lattice-ingest`` is pinned to the golden files under
``rust/fixtures/chunking/golden/``. Those goldens were produced by the Python
chunker, so a change to Python chunking semantics can silently invalidate them:
the Rust tests keep passing (they compare against the same stale file), and
nobody learns that the port stopped being a port until a user notices their
citations moved.

This module closes that loop from the other side. It re-runs the **real**
``typed_chunks`` / ``chunk_strategy_for`` / ``typed_chunk_meta_fields`` /
``pdf_page_offsets`` / ``page_for_offset`` / ``citation_locator`` and the hash
conventions against the **committed** goldens. Two consequences worth stating:

* it is a contract test, not a regression test — a deliberate chunking change is
  supposed to fail it, and the fix is to regenerate with
  ``.venv/bin/python scripts/generate_chunking_parity_fixtures.py`` and re-run
  the cargo suite so both halves move together;
* it imports nothing from the Rust side. The shared artefact is the JSON; no
  toolchain is required to run this file.

Comparison is over the canonical JSON encoding rather than ``==`` so that an
``int`` quietly becoming a ``float`` (``1 == 1.0`` in Python) still fails.

One thing is asserted here that the Rust side cannot see: chunk boundaries are
**character** offsets. Python has no other option, which is exactly why it is
worth pinning — the port had to choose, and a byte-indexed choice would have
disagreed on every Korean sentence in the corpus.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = REPO_ROOT / "scripts" / "generate_chunking_parity_fixtures.py"


def _load_generator():
    """Import the fixture generator by path (``scripts`` is not a package)."""
    spec = importlib.util.spec_from_file_location("chunking_parity_fixtures", GENERATOR)
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
        f"{directory} is missing — run scripts/generate_chunking_parity_fixtures.py"
    )
    return directory


def _read(golden_dir: Path, name: str):
    return json.loads((golden_dir / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def manifest(golden_dir: Path) -> dict:
    return _read(golden_dir, "manifest.json")


def test_manifest_describes_the_current_corpus(manifest, golden_dir: Path):
    """The manifest, the corpus and the files on disk must agree.

    Adding a case without regenerating leaves a manifest promising a golden that
    does not exist — this is where that shows up, instead of as a confusing
    "file not found" from the cargo suite.
    """
    keys = [case["key"] for case in fixtures.CASES]
    assert [case["key"] for case in manifest["cases"]] == keys
    assert manifest["source"] == "lattice_brain/graph/_kg_common/text.py"
    assert manifest["defaults"] == {
        "size": 1200,
        "overlap": 160,
        "markdown_min_section_chars": 200,
    }
    assert len(keys) == len(set(keys)), "duplicate case keys"
    assert len(keys) >= 30, "the corpus is the coverage — keep it wide"
    expected = {f"chunks__{key}.json" for key in keys} | {
        "manifest.json",
        "strategy_golden.json",
        "pdf_golden.json",
        "hash_golden.json",
    }
    assert {path.name for path in golden_dir.glob("*.json")} == expected


def test_the_corpus_reaches_all_four_strategies_and_the_fallback(manifest):
    """A corpus that only exercised the plain walk would prove almost nothing."""
    assert set(manifest["strategies"]) >= {"plain", "markdown", "code", "prose"}
    labels = {case["strategy"] for case in manifest["cases"]}
    assert "sideways" in labels, "an unknown strategy must be in the corpus"


@pytest.mark.parametrize("case", fixtures.CASES, ids=lambda case: case["key"])
def test_python_chunking_still_matches_its_golden(golden_dir: Path, case):
    """Every case still chunks into exactly what the golden records."""
    golden = _read(golden_dir, f"chunks__{case['key']}.json")
    rebuilt = fixtures.chunk_golden(case)
    assert _canonical(rebuilt) == _canonical(golden)


def test_offsets_are_character_offsets(golden_dir: Path):
    """Every ``start_char`` re-slices its chunk out of the stripped source.

    And for the multibyte cases the same offset read as *bytes* would not, which
    is the property the Rust port had to reproduce deliberately.
    """
    multibyte_cases = 0
    divergent_offsets = 0
    for case in fixtures.CASES:
        golden = _read(golden_dir, f"chunks__{case['key']}.json")
        cleaned = str(golden["text"] or "").strip()
        assert len(cleaned) == golden["cleaned_len_chars"]
        assert len(cleaned.encode("utf-8")) == golden["cleaned_len_bytes"]
        multibyte = golden["cleaned_len_bytes"] != golden["cleaned_len_chars"]
        multibyte_cases += int(multibyte)
        for chunk in golden["chunks"]:
            start = chunk["meta"]["start_char"]
            assert cleaned[start : start + len(chunk["text"])] == chunk["text"]
            assert len(chunk["text"]) == chunk["len_chars"]
            assert len(chunk["text"].encode("utf-8")) == chunk["len_bytes"]
            if multibyte and start > 0:
                byte_offset = len(cleaned[:start].encode("utf-8"))
                divergent_offsets += int(byte_offset > start)
    assert multibyte_cases >= 15, f"only {multibyte_cases} multibyte cases"
    assert divergent_offsets >= 50, (
        f"only {divergent_offsets} chunks where the byte and character offsets differ"
    )


def test_chunk_ids_hash_the_node_the_index_and_the_text(golden_dir: Path):
    """The id convention itself, not just the recorded values."""
    from lattice_brain.graph._kg_fsutil import _sha256_text

    golden = _read(golden_dir, "chunks__plain_default_long.json")
    node = golden["source_node_id"]
    assert golden["chunks"], "this case must produce chunks"
    for chunk in golden["chunks"]:
        expected = _sha256_text(f"{node}:{chunk['index']}:{chunk['text']}")[:24]
        assert chunk["chunk_id"] == f"chunk:{expected}"
    # A moved boundary re-keys the chunk — the reason plain boundaries are a
    # compatibility contract rather than an implementation detail.
    first = golden["chunks"][0]
    shifted_text = first["text"] + " "
    shifted = _sha256_text("{0}:0:{1}".format(node, shifted_text))[:24]
    assert f"chunk:{shifted}" != first["chunk_id"]


def test_the_strategy_router_still_matches_its_golden(golden_dir: Path):
    assert _canonical(fixtures.strategy_golden()) == _canonical(
        _read(golden_dir, "strategy_golden.json")
    )


def test_the_strategy_router_never_raises_on_anything():
    """The Python-only half of the contract: non-string input, and a bad ``__str__``.

    The Rust signature takes ``&str``, so these branches have no port to compare
    against — but they are still the behaviour the call sites rely on, and
    nothing else asserts them.
    """
    from lattice_brain.graph._kg_common.text import chunk_strategy_for

    class Hostile:
        def __str__(self):
            raise RuntimeError("no name for you")

    assert chunk_strategy_for(None) == "plain"
    assert chunk_strategy_for(3) == "plain"
    assert chunk_strategy_for(Path("/tmp/notes/guide.md")) == "markdown"
    assert chunk_strategy_for(Hostile()) == "plain"
    assert chunk_strategy_for("x", content_type=None) == "plain"


def test_the_pdf_arithmetic_still_matches_its_golden(golden_dir: Path):
    assert _canonical(fixtures.pdf_golden()) == _canonical(_read(golden_dir, "pdf_golden.json"))


def test_page_offsets_really_are_the_two_char_joiner(golden_dir: Path):
    """The `+2` is the whole trick; this states it rather than trusting it."""
    golden = _read(golden_dir, "pdf_golden.json")
    by_key = {case["key"]: case for case in golden["structures"]}
    pages = by_key["three_pages"]["structure"]["pages"]
    offsets = by_key["three_pages"]["offsets"]
    cursor = 0
    for index, page in enumerate(pages):
        assert offsets[index] == cursor
        cursor += page["chars"] + 2
    assert by_key["chars_bool"]["offsets"] == [], "a bool is not a char count"


def test_the_hash_conventions_still_match_their_golden(golden_dir: Path):
    assert _canonical(fixtures.hash_golden()) == _canonical(_read(golden_dir, "hash_golden.json"))


def test_the_two_content_hash_conventions_stay_distinct(golden_dir: Path):
    """Files hash bytes; text sources hash a typed string. Not interchangeable."""
    golden = _read(golden_dir, "hash_golden.json")
    payload = "hello world\n"
    file_case = next(
        case for case in golden["file_content_hash"] if bytes.fromhex(case["bytes_hex"]) == payload.encode()
    )
    assert file_case["sha256"] == hashlib.sha256(payload.encode()).hexdigest()
    for case in golden["text_content_hash"]:
        assert case["content_hash"] != file_case["sha256"]
        assert case["content_id"] == f"webdoc:{case['identity_hash'][:24]}"
    # An absent workspace and an empty one land in the same legacy bucket.
    legacy = [case for case in golden["text_content_hash"] if not case["workspace_id"]]
    assert legacy, "the corpus must include a legacy-global case"


def test_the_vector_hash_collapses_whitespace_and_chunking_does_not(golden_dir: Path):
    """The one place the two text normalisations are visibly different."""
    from lattice_brain.graph._kg_common.text import _clean_text, typed_chunks

    golden = _read(golden_dir, "hash_golden.json")
    spaced = next(case for case in golden["vector_text_hash"] if case["text"] == "  회의   결정\t사항  ")
    assert spaced["cleaned"] == "회의 결정 사항"
    assert spaced["text_hash"] == hashlib.sha256(spaced["cleaned"].encode()).hexdigest()
    # Chunking strips only, so the internal run survives into the chunk text.
    assert typed_chunks(spaced["text"])[0]["text"] == "회의   결정\t사항"
    assert _clean_text(spaced["text"]) != typed_chunks(spaced["text"])[0]["text"]
