from __future__ import annotations

import re
from typing import List, Optional, Sequence, Tuple

_HEADING = re.compile(r"^(#{1,6}) (.*)$", re.MULTILINE)

Span = Tuple[int, int, str]


def heading_spans(text: str) -> List[Span]:
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
    for start, end, path in spans:
        if start <= offset < end:
            return path
    return ""


def with_section(context: str, section: str) -> str:
    section = (section or "").strip()
    if not section:
        return context
    return f"[{section}] {context}"


def sentence_offsets(text: str, pattern: "re.Pattern[str]") -> List[Tuple[int, str]]:
    out: List[Tuple[int, str]] = []
    cursor = 0
    for match in pattern.finditer(text or ""):
        out.append((cursor, text[cursor : match.start()]))
        cursor = match.end()
    out.append((cursor, (text or "")[cursor:]))
    return out


def leading_offset(raw: str, stripped: str) -> Optional[int]:
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
