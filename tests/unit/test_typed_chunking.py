"""Typed chunking strategies + PDF page metadata.

Review 2026-07-25 (docs/reviews/CODE_REVIEW_2026-07-25_UX_HARNESS_LOOP_KG_RAG.md
§5.2 S2, Wave 2.1 + 2.4): "타입별 청킹 전략 (markdown/code/plain)" and "PDF
페이지 메타". House rules verified here: the plain strategy is byte-compatible
with the legacy ``_chunks`` walk (identical texts → identical chunk ids), the
new chunk metadata (strategy / start_char / heading_path / page) is purely
additive, and PDF page labels are attached only when the claimed page extents
plausibly match the chunked text — honest absence over wrong labels.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import lattice_brain.graph._kg_common as kg_common
from lattice_brain.graph._kg_common import (
    _chunks,
    _sha256_text,
    chunk_strategy_for,
    page_for_offset,
    pdf_page_offsets,
    typed_chunks,
)
from lattice_brain.graph.store import KnowledgeGraphStore
from lattice_brain.ingestion import IngestionItem, IngestionPipeline


def _store(tmp_path: Path) -> KnowledgeGraphStore:
    return KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")


def _chunk_rows(store: KnowledgeGraphStore, source_node: str):
    """Chunk (text, metadata) rows for a source node, ordered by chunk index."""
    with store._connect() as conn:
        rows = conn.execute(
            "SELECT text, metadata_json FROM chunks WHERE source_node = ?",
            (source_node,),
        ).fetchall()
    parsed = [(row["text"], json.loads(row["metadata_json"] or "{}")) for row in rows]
    return sorted(parsed, key=lambda item: item[1].get("index", 0))


@pytest.fixture(autouse=True)
def _rule_based_extraction_only(monkeypatch):
    """LLM extraction is nondeterministic — force the deterministic rule path."""
    monkeypatch.setenv("LATTICEAI_LLM_EXTRACTION", "0")
    monkeypatch.setattr(kg_common, "ENABLE_LLM_EXTRACTION", False)


# ── plain: byte-compat contract with _chunks ─────────────────────────────────


def test_plain_typed_chunks_are_byte_identical_to_legacy_chunks():
    """The byte-compat contract: plain typed_chunks == _chunks, text for text."""
    text = "  Lattice AI keeps the knowledge graph as the durable asset.\n" * 60
    assert len(text) > 3000
    pieces = typed_chunks(text)
    assert [piece["text"] for piece in pieces] == _chunks(text)
    assert len(pieces) >= 3


def test_plain_start_char_round_trips_on_cleaned_text():
    text = "\n  windowed plain text with offsets. " * 120 + "  "
    cleaned = str(text).strip()
    for piece in typed_chunks(text):
        meta = piece["meta"]
        assert meta["strategy"] == "plain"
        assert meta["heading_path"] is None
        start = meta["start_char"]
        assert cleaned[start : start + len(piece["text"])] == piece["text"]


def test_unknown_strategy_falls_back_to_plain():
    pieces = typed_chunks("hello world", strategy="mystery")
    assert [p["text"] for p in pieces] == _chunks("hello world")
    assert pieces[0]["meta"]["strategy"] == "plain"


# ── markdown ─────────────────────────────────────────────────────────────────


def _markdown_doc() -> str:
    intro = "intro paragraph for the guide section. " * 6
    setup = "setup instructions with concrete steps. " * 6
    usage = "usage notes and examples for daily work. " * 6
    return "# Guide\n" + intro + "\n## Setup\n" + setup + "\n## Usage\n" + usage


def test_markdown_sections_split_on_headings_with_heading_paths():
    pieces = typed_chunks(_markdown_doc(), strategy="markdown")
    assert len(pieces) == 3
    assert pieces[0]["text"].startswith("# Guide")
    assert pieces[1]["text"].startswith("## Setup")
    assert pieces[2]["text"].startswith("## Usage")
    assert pieces[0]["meta"]["heading_path"] == "Guide"
    assert pieces[1]["meta"]["heading_path"] == "Guide > Setup"
    assert pieces[2]["meta"]["heading_path"] == "Guide > Usage"
    cleaned = _markdown_doc().strip()
    for piece in pieces:
        assert piece["meta"]["strategy"] == "markdown"
        start = piece["meta"]["start_char"]
        assert cleaned[start : start + len(piece["text"])] == piece["text"]


def test_markdown_tiny_sections_merge_forward():
    """Sections under 200 chars merge into the next section (no confetti)."""
    body = "long body sentence with enough repeated content here. " * 6
    md = "# A\ntiny\n# B\n" + body
    pieces = typed_chunks(md, strategy="markdown")
    assert len(pieces) == 1
    assert "# A" in pieces[0]["text"] and "# B" in pieces[0]["text"]
    # Merged section keeps the heading path in effect at the chunk start.
    assert pieces[0]["meta"]["heading_path"] == "A"
    assert pieces[0]["meta"]["start_char"] == 0


def test_markdown_oversized_section_windows_but_keeps_heading_path():
    md = "# Big\n" + "wide section body that exceeds one window. " * 60
    pieces = typed_chunks(md, strategy="markdown")
    assert len(pieces) >= 2
    cleaned = md.strip()
    for piece in pieces:
        assert piece["meta"]["heading_path"] == "Big"
        start = piece["meta"]["start_char"]
        assert cleaned[start : start + len(piece["text"])] == piece["text"]


def test_markdown_without_headings_matches_plain_boundaries():
    text = "no headings at all, just prose that flows onward. " * 80
    assert [p["text"] for p in typed_chunks(text, strategy="markdown")] == _chunks(text)


# ── code ─────────────────────────────────────────────────────────────────────


def _code_functions(count: int = 8, body_lines: int = 12):
    funcs = []
    for i in range(count):
        lines = [f"def fn_{i}(value):"]
        for j in range(body_lines):
            lines.append(f"    step_{j} = value + {i} * {j}")
        lines.append(f"    return step_{body_lines - 1}")
        funcs.append("\n".join(lines))
    return "\n\n".join(funcs), funcs


def test_code_chunks_never_split_a_small_function_body():
    source, funcs = _code_functions()
    assert len(source) > 2400  # forces multiple chunks
    pieces = typed_chunks(source, strategy="code")
    assert len(pieces) >= 2
    for piece in pieces:
        assert piece["text"].startswith("def fn_")  # boundaries only at decls
        assert piece["meta"]["strategy"] == "code"
        assert piece["meta"]["heading_path"] is None
    for func in funcs:
        containing = [piece for piece in pieces if func in piece["text"]]
        assert len(containing) == 1, "small function must live in exactly one chunk"


def test_code_monster_segment_falls_back_to_windowing():
    monster = "data = [\n" + "".join(f"    {i},\n" for i in range(600)) + "]"
    assert len(monster) > 1800  # beyond size * 1.5 → plain walker fallback
    pieces = typed_chunks(monster, strategy="code")
    assert len(pieces) >= 3
    cleaned = monster.strip()
    for piece in pieces:
        assert len(piece["text"]) <= 1200
        assert piece["meta"]["strategy"] == "code"
        start = piece["meta"]["start_char"]
        assert cleaned[start : start + len(piece["text"])] == piece["text"]


# ── strategy routing ─────────────────────────────────────────────────────────


def test_chunk_strategy_for_routes_by_extension():
    assert chunk_strategy_for("notes.md") == "markdown"
    assert chunk_strategy_for("README.MARKDOWN") == "markdown"
    assert chunk_strategy_for("main.py") == "code"
    assert chunk_strategy_for("App.TSX") == "code"
    assert chunk_strategy_for(Path("nested/dir/lib.rs")) == "code"
    assert chunk_strategy_for("https://example.com/docs/guide.md?ref=1#top") == "markdown"
    assert chunk_strategy_for("report.pdf") == "plain"
    assert chunk_strategy_for("no_extension") == "plain"
    assert chunk_strategy_for("") == "plain"
    assert chunk_strategy_for(None) == "plain"
    assert chunk_strategy_for("page", content_type="text/markdown") == "markdown"


# ── PDF page offset math ─────────────────────────────────────────────────────


def test_pdf_page_offsets_and_page_for_offset_math():
    structure = {"pages": [{"chars": 10}, {"chars": 20}, {"chars": 30}]}
    offsets = pdf_page_offsets(structure)
    assert offsets == [0, 12, 34]
    assert page_for_offset(offsets, 0) == 1
    assert page_for_offset(offsets, 11) == 1
    assert page_for_offset(offsets, 12) == 2
    assert page_for_offset(offsets, 33) == 2
    assert page_for_offset(offsets, 34) == 3
    assert page_for_offset(offsets, 9_999) == 3
    assert page_for_offset([], 5) is None


def test_pdf_page_offsets_reject_malformed_structures():
    assert pdf_page_offsets(None) == []
    assert pdf_page_offsets({}) == []
    assert pdf_page_offsets({"pages": []}) == []
    assert pdf_page_offsets({"pages": "nope"}) == []
    assert pdf_page_offsets({"pages": [{"chars": -1}]}) == []
    assert pdf_page_offsets({"pages": [{"width": 100.0}]}) == []


# ── end-to-end through the ingestion pipeline / store ────────────────────────


def test_markdown_document_chunks_carry_typed_metadata_end_to_end(tmp_path):
    """Pipeline file ingest of a .md writes strategy/start_char/heading_path."""
    md_text = _markdown_doc()
    src = tmp_path / "guide.md"
    src.write_text(md_text, encoding="utf-8")
    pipe = IngestionPipeline(_store(tmp_path))
    res = pipe.ingest(
        IngestionItem(
            source_type="file",
            path=str(src),
            owner="u@x.com",
            metadata={"extracted": {"content": md_text}},
        ),
        user_email="u@x.com",
    )
    assert res.status == "ok"
    rows = _chunk_rows(pipe._kg, res.node_id)
    assert len(rows) == 3
    cleaned = md_text.strip()
    for text, meta in rows:
        assert meta["strategy"] == "markdown"
        start = meta["start_char"]
        assert cleaned[start : start + len(text)] == text
    assert rows[0][1]["heading_path"] == "Guide"
    assert rows[1][1]["heading_path"] == "Guide > Setup"
    assert rows[2][1]["heading_path"] == "Guide > Usage"


def test_plain_document_chunk_ids_match_legacy_recipe_end_to_end(tmp_path):
    """Unchanged plain content produces the exact pre-feature chunk ids."""
    body = ("plain text sentence kept for chunk id identity. " * 60).strip()
    src = tmp_path / "notes.txt"
    src.write_text(body, encoding="utf-8")
    store = _store(tmp_path)
    res = store.ingest_document(src, extracted={"content": body})
    expected = []
    for index, chunk in enumerate(_chunks(body)):
        identity = f"{res['node_id']}:{index}:{chunk}"
        expected.append("chunk:" + _sha256_text(identity)[:24])
    assert len(expected) >= 2
    assert res["chunk_ids"] == expected
    for _text, meta in _chunk_rows(store, res["node_id"]):
        assert meta["strategy"] == "plain"
        assert "heading_path" not in meta
        assert "page" not in meta  # not a PDF — no page labels


def test_pdf_chunks_carry_page_numbers_end_to_end(tmp_path, monkeypatch):
    """PDF chunks resolve a 1-based page from the \\n\\n-joined page offsets."""
    page1 = "a" * 900
    page2 = "b" * 950
    joined = page1 + "\n\n" + page2
    pdf_path = tmp_path / "report.pdf"
    pdf_path.write_text(joined, encoding="utf-8")
    store = _store(tmp_path)
    monkeypatch.setattr(
        store,
        "_document_structure",
        lambda path, ext: {"pages": [{"chars": len(page1)}, {"chars": len(page2)}]},
    )
    res = store.ingest_document(
        pdf_path, original_filename="report.pdf", extracted={"content": joined}
    )
    rows = _chunk_rows(store, res["node_id"])
    assert len(rows) == 2  # 1852 chars → windows at 0 and 1040
    assert rows[0][1]["strategy"] == "plain"
    assert rows[0][1]["start_char"] == 0
    assert rows[0][1]["page"] == 1
    assert rows[1][1]["start_char"] == 1040
    assert rows[1][1]["page"] == 2  # 1040 >= page-2 start offset (902)


def test_pdf_page_meta_omitted_when_structure_disagrees_with_text(tmp_path, monkeypatch):
    """Implausible page extents → chunks carry no page label (honest absence)."""
    joined = "c" * 300
    pdf_path = tmp_path / "claims.pdf"
    pdf_path.write_text(joined, encoding="utf-8")
    store = _store(tmp_path)
    monkeypatch.setattr(
        store,
        "_document_structure",
        lambda path, ext: {"pages": [{"chars": 5000}, {"chars": 5000}]},
    )
    res = store.ingest_document(
        pdf_path, original_filename="claims.pdf", extracted={"content": joined}
    )
    rows = _chunk_rows(store, res["node_id"])
    assert rows
    for _text, meta in rows:
        assert "page" not in meta
        assert meta["strategy"] == "plain"
