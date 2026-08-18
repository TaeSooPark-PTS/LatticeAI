"""Heading paths for a document, so a fact can say *where* it came from.

The typed chunker already computes a `" > "`-joined heading path per chunk and
files it on the Chunk node (`heading_path`). Extraction ran on the whole
document text and had no idea which section a sentence sat in, so an edge could
say "이 문장이 근거다" but never "그 문장은 「아키텍처 > 저장소」 절에 있다".

This module closes that gap with the *same* rule the chunker uses — a line
matching `^#{1,6} ` opens a section — so the heading a triple names and the
heading its chunk carries are the same string.

Character offsets throughout, because Python slices `str` by code point and the
rest of the pipeline (chunk `start_char`, the Rust port) does too.
"""

from __future__ import annotations

import re
from typing import List, Optional, Sequence, Tuple

#: `^(#{1,6}) (.*)$` under `re.MULTILINE` — the chunker's heading rule.
_HEADING = re.compile(r"^(#{1,6}) (.*)$", re.MULTILINE)

#: `(start, end, heading_path)`; `end` is exclusive.
Span = Tuple[int, int, str]


def heading_spans(text: str) -> List[Span]:
    """Every heading's span and its `" > "`-joined path, in document order.

    Text before the first heading belongs to no section and is deliberately
    absent from the result — an honest "no heading" beats inventing one from
    the filename.

    >>> heading_spans("# A\\nintro\\n## B\\nbody")
    [(0, 12, 'A'), (12, 20, 'A > B')]
    """
    matches = list(_HEADING.finditer(text or ""))
    spans: List[Span] = []
    stack: List[Tuple[int, str]] = []
    for index, match in enumerate(matches):
        level = len(match.group(1))
        title = match.group(2).strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        spans.append((match.start(), end, " > ".join(part for _, part in stack)))
    return spans


def heading_at(spans: Sequence[Span], offset: int) -> str:
    """The heading path covering ``offset``, or `""` when there is none."""
    for start, end, path in spans:
        if start <= offset < end:
            return path
    return ""


def with_section(context: str, section: str) -> str:
    """``context`` prefixed with the section it came from, when there is one.

    ``"[아키텍처 > 저장소] 쓰기는 GraphWriter가 담당한다."`` — one string,
    because `TripleSpec.context` is the only free-text channel an extracted
    edge has. Blank sections leave the context untouched rather than adding an
    empty bracket.
    """
    section = (section or "").strip()
    if not section:
        return context
    return f"[{section}] {context}"


def sentence_offsets(text: str, pattern: "re.Pattern[str]") -> List[Tuple[int, str]]:
    """``(offset, sentence)`` for a split that keeps each piece's position.

    ``re.split`` throws the offsets away, and the offset is exactly what maps a
    sentence back to its heading. Walking the separators keeps both.
    """
    out: List[Tuple[int, str]] = []
    cursor = 0
    for match in pattern.finditer(text or ""):
        out.append((cursor, text[cursor : match.start()]))
        cursor = match.end()
    out.append((cursor, (text or "")[cursor:]))
    return out


def leading_offset(raw: str, stripped: str) -> Optional[int]:
    """How many characters ``str.strip()`` removed from the front of ``raw``.

    ``None`` when ``stripped`` is empty — there is no position to report for a
    piece that stripped away entirely.
    """
    if not stripped:
        return None
    return raw.index(stripped)


__all__ = [
    "Span",
    "heading_at",
    "heading_spans",
    "leading_offset",
    "sentence_offsets",
    "with_section",
]
