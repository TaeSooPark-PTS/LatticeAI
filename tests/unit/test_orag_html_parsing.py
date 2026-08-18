"""`.html` reaches the graph as what the page says (v12.0.0).

Before this, an HTML file was classified as *code* and read verbatim, so the
chunks were markup and the concepts were attribute names. These tests pin the
three things that made it useless — script bodies, tags, and lost structure —
and the one thing that makes it better than plain stripping: the page's own
headings survive, so an extracted fact can name the section it came from.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lattice_brain.graph._kg_common.sections import heading_spans
from latticeai.tools import _SUPPORTED_READ_EXTENSIONS
from latticeai.tools.documents import read_document
from latticeai.tools.markup import html_to_text

PAGE = """<!doctype html>
<html><head><title>Lattice AI 안내</title>
<style>.a{color:red}</style>
<script>var x = 1; if (x < 2) { console.log("leak"); }</script>
</head>
<body>
<h1>지식그래프</h1>
<p>지식그래프는 노드와 엣지로 문서를 저장합니다. &amp; 검색이 쉬워집니다.</p>
<h2>구성 요소</h2>
<ul><li>GraphWriter</li><li>Vector Index</li></ul>
<p>Graph RAG uses <b>SQLite</b> for storage.</p>
</body></html>"""


def test_scripts_styles_and_tags_never_reach_the_text() -> None:
    text = html_to_text(PAGE)
    assert "console.log" not in text
    assert "color:red" not in text
    assert "<p>" not in text and "<div" not in text and "href" not in text


def test_entities_are_decoded_once() -> None:
    assert "& 검색이" in html_to_text(PAGE)
    assert "&amp;" not in html_to_text(PAGE)
    assert html_to_text("<p>a &lt; b &#38; c</p>") == "a < b & c"


def test_headings_survive_as_markdown_so_sections_still_work() -> None:
    """`<title>` and `<h1>` are both level 1, so they are siblings — as in
    markdown, where a document cannot have a section above its top section."""
    text = html_to_text(PAGE)
    paths = [path for _, _, path in heading_spans(text)]
    assert paths == ["Lattice AI 안내", "지식그래프", "지식그래프 > 구성 요소"]


def test_a_page_with_no_h1_still_gets_its_title_as_the_top_section() -> None:
    text = html_to_text("<html><head><title>제목</title></head><body><p>본문</p></body></html>")
    assert text.startswith("# 제목")
    assert "본문" in text


def test_list_items_stay_one_line_each() -> None:
    text = html_to_text("<ul><li>하나</li><li>둘</li><li>셋</li></ul>")
    assert text.splitlines() == ["- 하나", "- 둘", "- 셋"]


def test_table_cells_are_tab_separated_rows() -> None:
    text = html_to_text("<table><tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr></table>")
    rows = [line.strip() for line in text.splitlines() if line.strip()]
    assert rows == ["a\tb", "c\td"]


def test_inline_markup_does_not_break_a_sentence_apart() -> None:
    assert html_to_text("<p>Graph <b>RAG</b> uses <i>SQLite</i>.</p>") == (
        "Graph RAG uses SQLite."
    )


def test_markup_nested_inside_a_dropped_element_is_dropped_whole() -> None:
    """`<template>` is parsed as real markup, so its tags must be skipped too.

    (A `<script>` body is CDATA — the parser never reports tags inside it —
    which is why this uses the element that *does* nest.)
    """
    text = html_to_text(
        "<p>kept</p><template><p>hidden</p><b>also hidden</b></template><p>after</p>"
    )
    assert "hidden" not in text
    assert [line for line in text.splitlines() if line] == ["kept", "after"]


def test_malformed_markup_yields_what_was_recoverable() -> None:
    assert "hello" in html_to_text("<p>hello<div><span>unclosed")
    assert html_to_text("") == ""
    assert html_to_text(None) == ""  # type: ignore[arg-type]


@pytest.mark.parametrize("suffix", [".html", ".htm"])
def test_read_document_accepts_a_page_and_returns_its_text(
    tmp_path: Path, suffix: str
) -> None:
    assert suffix in _SUPPORTED_READ_EXTENSIONS
    page = tmp_path / f"guide{suffix}"
    page.write_text(PAGE, encoding="utf-8")

    parsed = read_document(str(page))

    assert parsed["ext"] == suffix
    assert "console.log" not in parsed["content"]
    assert "# 지식그래프" in parsed["content"]
    assert parsed["chars"] == len(parsed["content"])
    assert parsed["preview"] == parsed["content"][:500]
