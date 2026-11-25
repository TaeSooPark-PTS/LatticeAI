from __future__ import annotations

import logging
import re
from html import unescape
from html.parser import HTMLParser
from typing import List, Set

SKIPPED_ELEMENTS: Set[str] = {"script", "style", "noscript", "template", "svg"}
BLOCK_ELEMENTS: Set[str] = {
    "p",
    "div",
    "section",
    "article",
    "header",
    "footer",
    "nav",
    "aside",
    "main",
    "ul",
    "ol",
    "li",
    "table",
    "tr",
    "blockquote",
    "pre",
    "form",
    "figure",
    "figcaption",
    "dl",
    "dt",
    "dd",
    "hr",
    "br",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "title",
}
CELL_ELEMENTS: Set[str] = {"td", "th"}

_HEADINGS = {f"h{level}": level for level in range(1, 7)}
_BLANK_RUN = re.compile(r"\n{3,}")
_TRAILING_SPACE = re.compile(r"[ \t]+\n")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: List[str] = []
        self._skip_depth = 0
        self._heading: int = 0
        self._in_head = False

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
            return
        elif tag in BLOCK_ELEMENTS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_head and not self._heading:
            return
        text = unescape(data)
        if self._heading:
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
    extractor = _TextExtractor()
    try:
        extractor.feed(str(markup or ""))
        extractor.close()
    except (
        ValueError,
        TypeError,
        RuntimeError,
        AttributeError,
        LookupError,
    ) as exc:
        logging.debug("HTML parse stopped early; keeping what was read: %s", exc)
    text = "".join(extractor.parts)
    text = _TRAILING_SPACE.sub("\n", text)
    text = _BLANK_RUN.sub("\n\n", text)
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


__all__ = ["BLOCK_ELEMENTS", "CELL_ELEMENTS", "SKIPPED_ELEMENTS", "html_to_text"]
