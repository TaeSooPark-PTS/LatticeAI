#!/usr/bin/env python3
"""Build the committed Python↔Rust *chunking* parity fixture (v11.5.0 §2c).

``rust/lattice-ingest`` ports ``lattice_brain/graph/_kg_common/text.py`` — the
four chunking strategies, the strategy router, the chunk-id and content-hash
conventions, and the PDF page arithmetic. A port is only worth having if
something keeps proving it is still one, so this script is the Python half of
that proof: it runs the **real** ``typed_chunks`` / ``chunk_strategy_for`` /
``typed_chunk_meta_fields`` / ``pdf_page_offsets`` / ``page_for_offset`` /
``citation_locator`` over a deliberately awkward corpus and writes what they
answer to ``rust/fixtures/chunking/golden/``.

Two consumers read what it writes:

* ``tests/unit/test_chunking_parity_contract.py`` re-runs the Python functions
  against the committed goldens, so a change to Python chunking semantics fails
  loudly instead of silently invalidating the contract the Rust side is pinned
  to;
* ``rust/lattice-ingest/tests/chunking_parity.rs`` runs the Rust port against
  the same goldens, comparing exactly.

Determinism is free here — every function in the port is pure — so the only
design constraint is *coverage*. The inputs live next door in
``scripts/chunking_parity_corpus.py`` (this file is the runner; that one is the
specification), and they are shaped to reach every branch:

* all four strategies plus an unknown label (which must fall back to plain);
* empty, whitespace-only, exactly ``size``, ``size-1`` and ``size+1`` inputs;
* markdown with nested headings, an empty heading title, sub-200-char sections
  that merge forward, a trailing undersized section that merges backward, and a
  section too big for one window;
* code with declaration lines, blank-line runs, greedy packing, and a segment
  past the ``size * 1.5`` hard limit;
* prose with strong (sentence) boundaries, weak (line-break) boundaries and no
  boundary at all;
* **multibyte text straddling every boundary**, because Python slices strings by
  *characters* and a Rust port that slices by bytes would silently disagree (or
  panic). Every chunk records ``len_chars`` and ``len_bytes`` so the difference
  is visible in the artefact rather than asserted from memory.

Usage::

    .venv/bin/python scripts/generate_chunking_parity_fixtures.py
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURE_DIR = REPO_ROOT / "rust" / "fixtures" / "chunking"
GOLDEN_DIR = FIXTURE_DIR / "golden"

# The corpus lives beside this file. ``scripts`` is not a package, so it is
# loaded by path — the same trick tests/unit/test_chunking_parity_contract.py
# uses on this module, for the same reason.
_CORPUS_SPEC = importlib.util.spec_from_file_location(
    "chunking_parity_corpus",
    Path(__file__).resolve().parent / "chunking_parity_corpus.py",
)
corpus = importlib.util.module_from_spec(_CORPUS_SPEC)
_CORPUS_SPEC.loader.exec_module(corpus)

#: Re-exported so a consumer of this module (the contract test) sees one
#: surface rather than having to know the corpus moved.
CASES = corpus.CASES
STRATEGY_CASES = corpus.STRATEGY_CASES
PDF_STRUCTURES = corpus.PDF_STRUCTURES
PAGE_PROBES = corpus.PAGE_PROBES
LOCATOR_CASES = corpus.LOCATOR_CASES
TEXT_HASH_CASES = corpus.TEXT_HASH_CASES
FILE_HASH_CASES = corpus.FILE_HASH_CASES
VECTOR_TEXT_CASES = corpus.VECTOR_TEXT_CASES
GRAPHEME_SOUP = corpus.GRAPHEME_SOUP


def _dump(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def case_defaults(case: Dict[str, Any]) -> Dict[str, Any]:
    """One case with every optional field resolved — the shared shape."""
    from lattice_brain.graph._kg_common.text import chunk_strategy_for

    filename = case.get("filename", "")
    content_type = case.get("content_type", "")
    strategy = case.get("strategy") or chunk_strategy_for(filename, content_type=content_type)
    return {
        "key": case["key"],
        "filename": filename,
        "content_type": content_type,
        "requested_strategy": case.get("strategy"),
        "strategy": strategy,
        "size": case.get("size", 1200),
        "overlap": case.get("overlap", 160),
        "source_node_id": case.get("source_node_id", f"file:{case['key']}"),
        "text": case["text"],
    }


def chunk_golden(case: Dict[str, Any]) -> Dict[str, Any]:
    """Run the real chunker over one case and record everything it produced."""
    from lattice_brain.graph._kg_common.text import (
        typed_chunk_meta_fields,
        typed_chunks,
    )
    from lattice_brain.graph._kg_fsutil import _sha256_text

    resolved = case_defaults(case)
    pieces = typed_chunks(
        resolved["text"],
        strategy=resolved["strategy"],
        size=resolved["size"],
        overlap=resolved["overlap"],
    )
    node = resolved["source_node_id"]
    chunks = []
    for index, piece in enumerate(pieces):
        text = piece["text"]
        chunks.append(
            {
                "index": index,
                "text": text,
                "meta": piece["meta"],
                "meta_fields": typed_chunk_meta_fields(piece),
                "chunk_id": f"chunk:{_sha256_text(f'{node}:{index}:{text}')[:24]}",
                "len_chars": len(text),
                "len_bytes": len(text.encode("utf-8")),
            }
        )
    cleaned = str(resolved["text"] or "").strip()
    return {
        **resolved,
        "cleaned_len_chars": len(cleaned),
        "cleaned_len_bytes": len(cleaned.encode("utf-8")),
        "chunk_count": len(chunks),
        "chunks": chunks,
    }


def strategy_golden() -> List[Dict[str, str]]:
    from lattice_brain.graph._kg_common.text import chunk_strategy_for

    return [
        {**case, "expected": chunk_strategy_for(case["filename"], content_type=case["content_type"])}
        for case in STRATEGY_CASES
    ]


def pdf_golden() -> Dict[str, Any]:
    from lattice_brain.graph._kg_common.text import (
        citation_locator,
        page_for_offset,
        pdf_page_offsets,
    )

    structures = [
        {"key": case["key"], "structure": case["structure"], "offsets": pdf_page_offsets(case["structure"])}
        for case in PDF_STRUCTURES
    ]
    by_key = {entry["key"]: entry["offsets"] for entry in structures}
    probes = [
        {
            "offsets_key": key,
            "offsets": by_key[key],
            "probes": [{"offset": offset, "page": page_for_offset(by_key[key], offset)} for offset in PAGE_PROBES],
        }
        for key in ("three_pages", "single_page", "zero_length_page", "pages_empty")
    ]
    return {
        "structures": structures,
        "page_for_offset": probes,
        "citation_locator": [
            {"metadata": metadata, "expected": citation_locator(metadata)} for metadata in LOCATOR_CASES
        ],
    }


def hash_golden() -> Dict[str, Any]:
    from lattice_brain.graph._kg_common.text import _clean_text
    from lattice_brain.graph._kg_fsutil import _sha256_bytes, _sha256_text

    text_cases = []
    for case in TEXT_HASH_CASES:
        content_hash = _sha256_text(f"{case['source_type']}|{case['source_uri'] or ''}|{case['text']}")
        identity = _sha256_text(f"{case['workspace_id'] or 'legacy-global'}|{content_hash}")
        text_cases.append(
            {
                **case,
                "content_hash": content_hash,
                "identity_hash": identity,
                "content_id": f"webdoc:{identity[:24]}",
            }
        )
    return {
        "sha256_text": [
            {"text": text, "sha256": _sha256_text(text)}
            for text in ["", "a", "회의 결정 사항", GRAPHEME_SOUP, "x" * 1000]
        ],
        "file_content_hash": [
            {"bytes_hex": payload.hex(), "sha256": _sha256_bytes(payload)} for payload in FILE_HASH_CASES
        ],
        "text_content_hash": text_cases,
        "vector_text_hash": [
            {"text": text, "cleaned": _clean_text(text), "text_hash": _sha256_text(_clean_text(text))}
            for text in VECTOR_TEXT_CASES
        ],
    }


def manifest() -> Dict[str, Any]:
    cases = [case_defaults(case) for case in CASES]
    strategies = sorted({case["strategy"] for case in cases})
    return {
        "source": "lattice_brain/graph/_kg_common/text.py",
        "defaults": {"size": 1200, "overlap": 160, "markdown_min_section_chars": 200},
        "strategies": strategies,
        "cases": [{key: case[key] for key in ("key", "filename", "content_type", "strategy", "size", "overlap", "source_node_id")} for case in cases],
    }


def main() -> int:
    if GOLDEN_DIR.exists():
        shutil.rmtree(GOLDEN_DIR)
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    keys = [case["key"] for case in CASES]
    if len(set(keys)) != len(keys):
        raise SystemExit("duplicate case keys")
    total_chunks = 0
    multibyte_cases = 0
    for case in CASES:
        golden = chunk_golden(case)
        total_chunks += golden["chunk_count"]
        if any(chunk["len_bytes"] != chunk["len_chars"] for chunk in golden["chunks"]):
            multibyte_cases += 1
        _dump(GOLDEN_DIR / f"chunks__{case['key']}.json", golden)
    _dump(GOLDEN_DIR / "strategy_golden.json", strategy_golden())
    _dump(GOLDEN_DIR / "pdf_golden.json", pdf_golden())
    _dump(GOLDEN_DIR / "hash_golden.json", hash_golden())
    _dump(GOLDEN_DIR / "manifest.json", manifest())
    print(f"golden: {len(CASES)} chunking cases ({total_chunks} chunks), {multibyte_cases} with multibyte chunks")
    print(f"        {len(STRATEGY_CASES)} strategy cases, {len(PDF_STRUCTURES)} pdf structures")
    print(f"        written to {GOLDEN_DIR.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
