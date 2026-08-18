"""HTML → readable text, with the document's own structure kept.

Until v12.0.0 an ``.html`` file was classified as *code* and read verbatim, so
what reached the graph was `<div class="wrapper">` and the contents of every
`<script>` tag. Chunks were markup, concepts were attribute names, and a search
for a sentence on the page could not find the page.

The conversion is deliberately small and dependency-free (``html.parser`` and
``html.unescape`` are stdlib; adding BeautifulSoup for this would be a new
runtime dependency for a hundred lines of tag handling):

* ``<script>``, ``<style>``, ``<noscript>``, ``<template>`` and everything in
  ``<head>`` except ``<title>`` are dropped — they are not what the page says;
* ``<h1>``–``<h6>`` become markdown headings, so the section machinery in
  :mod:`lattice_brain.graph._kg_common.sections` gives every extracted fact the
  heading it lived under, exactly as it does for a ``.md`` file;
* ``<li>`` becomes ``- ``, table cells are tab-separated, and block elements
  end a line, so paragraph and list boundaries survive into chunking;
* entities are unescaped once, at the end of each text run.

The output is plain text that happens to carry markdown headings. It is *not*
a markdown converter and does not try to be: bold, links and images are read
for their text, because a knowledge graph wants the sentence, not the styling.
"""

from __future__ import annotations

import logging
import re
from html import unescape
from html.parser import HTMLParser
from typing import List, Set

#: Elements whose *content* is never page text.
SKIPPED_ELEMENTS: Set[str] = {"script", "style", "noscript", "template", "svg"}
#: Elements that end the current line.
BLOCK_ELEMENTS: Set[str] = {
    "p", "div", "section", "article", "header", "footer", "nav", "aside",
    "main", "ul", "ol", "li", "table", "tr", "blockquote", "pre", "form",
    "figure", "figcaption", "dl", "dt", "dd", "hr", "br", "h1", "h2", "h3",
    "h4", "h5", "h6", "title",
}
#: Elements that separate cells with a tab rather than a newline.
CELL_ELEMENTS: Set[str] = {"td", "th"}

_HEADINGS = {f"h{level}": level for level in range(1, 7)}
_BLANK_RUN = re.compile(r"\n{3,}")
_TRAILING_SPACE = re.compile(r"[ \t]+\n")


class _TextExtractor(HTMLParser):
    """Collect a page's readable text, marking its headings."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: List[str] = []
        self._skip_depth = 0
        self._heading: int = 0
        self._in_head = False

    # ── structure ────────────────────────────────────────────────────────
    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in SKIPPED_ELEMENTS:
            self._skip_depth += 1
            return
        if tag == "head":
            self._in_head = True
            return
        if self._skip_depth:
            return
        level = _HEADINGS.get(tag)
        # `<title>` is the page's name; treat it as the top heading so the
        # section path of a page with no `<h1>` is still its own title.
        if tag == "title":
            level = 1
        if level:
            self._heading = level
            self.parts.append("\n\n" + "#" * level + " ")
            return
        if tag == "li":
            self.parts.append("\n- ")
        elif tag in CELL_ELEMENTS:
            self.parts.append("\t")
        elif tag in BLOCK_ELEMENTS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIPPED_ELEMENTS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "head":
            self._in_head = False
            return
        if self._skip_depth:
            return
        if tag in _HEADINGS or tag == "title":
            self._heading = 0
            self.parts.append("\n\n")
        elif tag == "li":
            # `<li>` already opens its own line; closing one too would put a
            # blank line between every bullet and turn a list into a page.
            return
        elif tag in BLOCK_ELEMENTS:
            self.parts.append("\n")

    # ── text ─────────────────────────────────────────────────────────────
    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        # Inside `<head>`, only `<title>` text is page content; `<meta>` and
        # friends carry no data, but stray whitespace and CDATA do.
        if self._in_head and not self._heading:
            return
        text = unescape(data)
        if self._heading:
            # A heading must stay on one line or it stops being a heading.
            text = " ".join(text.split())
            if text:
                self.parts.append(text)
            return
        cleaned = re.sub(r"[ \t\r\f\v]+", " ", text.replace("\n", " "))
        if cleaned.strip():
            self.parts.append(cleaned)
        elif cleaned:
            self.parts.append(" ")

    def handle_entityref(self, name: str) -> None:
        self.handle_data(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.handle_data(f"&#{name};")


def html_to_text(markup: str) -> str:
    """Readable text for ``markup``, with headings kept as ``#`` lines.

    Never raises: a truncated or malformed page yields whatever text was
    recoverable, because half a document in the graph beats a failed ingest.
    """
    extractor = _TextExtractor()
    try:
        extractor.feed(str(markup or ""))
        extractor.close()
    except Exception as exc:  # pragma: no cover - html.parser is very tolerant
        logging.debug("HTML parse stopped early; keeping what was read: %s", exc)
    text = "".join(extractor.parts)
    text = _TRAILING_SPACE.sub("\n", text)
    text = _BLANK_RUN.sub("\n\n", text)
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


__all__ = ["BLOCK_ELEMENTS", "CELL_ELEMENTS", "SKIPPED_ELEMENTS", "html_to_text"]
