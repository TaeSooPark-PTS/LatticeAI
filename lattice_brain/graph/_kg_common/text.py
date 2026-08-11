"""Text cleaning, chunking, and citation-locator maths.

Moved verbatim out of the ``_kg_common`` grab-bag (v11.3.0 decomposition).
Nothing here reaches back into the rest of the package — the import graph is
``text ← relations ← extraction ← __init__`` — so this is the layer every
other one may build on.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from ...quiet import quiet


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _chunks(text: str, size: int = 1200, overlap: int = 160) -> List[str]:
    cleaned = str(text or "").strip()
    if not cleaned:
        return []
    chunks: List[str] = []
    start = 0
    while start < len(cleaned):
        end = min(len(cleaned), start + size)
        chunks.append(cleaned[start:end])
        if end >= len(cleaned):
            break
        start = max(0, end - overlap)
    return chunks


# ── Typed chunking (review 2026-07-25 §5.2 S2 — Wave 2.1 + 2.4) ──────────────
# ``_chunks`` above is a compatibility contract (chunk ids hash over the chunk
# text) and stays byte-for-byte untouched. ``typed_chunks`` layers strategy-
# aware boundaries plus per-chunk provenance (start_char / heading_path) on
# top; ``strategy="plain"`` reproduces the exact ``_chunks`` boundaries so
# unchanged plain content keeps identical chunk ids.

_MARKDOWN_CHUNK_EXTENSIONS = {".md", ".markdown"}
_CODE_CHUNK_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".rb",
    ".c", ".h", ".cpp", ".css", ".sh", ".sql", ".vue", ".svelte",
    ".json", ".yaml", ".yml", ".toml",
}
_PROSE_CHUNK_EXTENSIONS = {
    ".txt", ".pdf", ".docx", ".doc", ".rtf", ".odt", ".epub", ".html", ".htm",
}
_CHUNK_STRATEGIES = {"plain", "markdown", "code", "prose"}
# Markdown sections smaller than this merge forward into the next section so
# heading-dense documents don't shatter into confetti chunks.
_MARKDOWN_MIN_SECTION_CHARS = 200
_MARKDOWN_HEADING_RE = re.compile(r"^(#{1,6}) (.*)$", re.MULTILINE)
_CODE_BOUNDARY_LINE_RE = re.compile(
    r"^(?:def |class |function |export |const |public |private )", re.MULTILINE
)
_CODE_BLANK_RUN_RE = re.compile(r"\n\s*\n")


def chunk_strategy_for(filename: Any, *, content_type: str = "") -> str:
    """Route a filename / path / URI (plus optional MIME hint) to a strategy.

    Returns ``"markdown"`` for .md/.markdown, ``"code"`` for known source-code
    extensions, ``"prose"`` for document formats whose text is running prose
    (.txt/.pdf/.docx/.html/…), ``"plain"`` otherwise. Case-insensitive,
    tolerant of URLs (query/fragment stripped) and ``Path`` objects; never
    raises — any malformed input falls back to ``"plain"``.

    Unknown/extension-less input stays ``"plain"`` on purpose: the plain
    strategy is the byte-compatible legacy walk, and guessing prose for
    something that might be a data dump would move chunk boundaries for no
    retrieval gain.
    """
    try:
        name = str(filename or "").strip().lower()
        for sep in ("?", "#"):
            name = name.split(sep, 1)[0]
        name = name.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        dot = name.rfind(".")
        ext = name[dot:] if dot > 0 else ""
        if ext in _MARKDOWN_CHUNK_EXTENSIONS:
            return "markdown"
        if ext in _CODE_CHUNK_EXTENSIONS:
            return "code"
        if ext in _PROSE_CHUNK_EXTENSIONS:
            return "prose"
        mime = str(content_type or "").strip().lower()
        if "markdown" in mime:
            return "markdown"
        if mime.startswith("text/html") or mime.startswith("text/plain"):
            return "prose"
    except Exception:
        quiet()
    return "plain"


def _plain_windows(
    cleaned: str,
    size: int,
    overlap: int,
    *,
    base_offset: int = 0,
    strategy: str = "plain",
    heading_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """The exact ``_chunks`` walk with ``start_char`` tracked.

    Boundaries and chunk texts are byte-identical to ``_chunks`` over the same
    string — this is the plain-strategy compatibility guarantee.
    """
    out: List[Dict[str, Any]] = []
    start = 0
    total = len(cleaned)
    while start < total:
        end = min(total, start + size)
        out.append(
            {
                "text": cleaned[start:end],
                "meta": {
                    "strategy": strategy,
                    "start_char": base_offset + start,
                    "heading_path": heading_path,
                },
            }
        )
        if end >= total:
            break
        start = max(0, end - overlap)
    return out


def _markdown_section_spans(cleaned: str) -> List[Tuple[int, int, Optional[str]]]:
    """``(start, end, heading_path)`` spans split at ``^#{1,6} `` heading lines.

    ``heading_path`` is the " > "-joined path of the enclosing headings
    including the section's own heading (e.g. ``"Guide > Setup"``); the
    preamble before the first heading carries ``None``. Spans are contiguous
    raw slices of ``cleaned`` so every chunk text round-trips via start_char.
    """
    spans: List[Tuple[int, int, Optional[str]]] = []
    stack: List[Tuple[int, str]] = []
    prev_start = 0
    prev_path: Optional[str] = None
    for match in _MARKDOWN_HEADING_RE.finditer(cleaned):
        offset = match.start()
        if offset > prev_start:
            spans.append((prev_start, offset, prev_path))
        level = len(match.group(1))
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, match.group(2).strip()))
        prev_start = offset
        prev_path = " > ".join(title for _, title in stack) or None
    if len(cleaned) > prev_start:
        spans.append((prev_start, len(cleaned), prev_path))
    return spans


def _merge_small_sections(
    spans: List[Tuple[int, int, Optional[str]]], min_chars: int
) -> List[Tuple[int, int, Optional[str]]]:
    """Merge sections under ``min_chars`` forward into the next section.

    A merged section keeps the heading_path of its first constituent (the
    path in effect at the chunk start). A trailing undersized section merges
    backward into the previous emitted section when one exists.
    """
    merged: List[Tuple[int, int, Optional[str]]] = []
    pending: Optional[Tuple[int, int, Optional[str]]] = None
    for start, end, path in spans:
        if pending is None:
            pending = (start, end, path)
        else:
            pending = (pending[0], end, pending[2])
        if pending[1] - pending[0] >= min_chars:
            merged.append(pending)
            pending = None
    if pending is not None:
        if merged and pending[1] - pending[0] < min_chars:
            last = merged.pop()
            merged.append((last[0], pending[1], last[2]))
        else:
            merged.append(pending)
    return merged


def _markdown_chunks(cleaned: str, size: int, overlap: int) -> List[Dict[str, Any]]:
    sections = _merge_small_sections(
        _markdown_section_spans(cleaned), _MARKDOWN_MIN_SECTION_CHARS
    )
    out: List[Dict[str, Any]] = []
    for start, end, path in sections:
        body = cleaned[start:end]
        if len(body) <= size:
            out.append(
                {
                    "text": body,
                    "meta": {
                        "strategy": "markdown",
                        "start_char": start,
                        "heading_path": path,
                    },
                }
            )
        else:
            out.extend(
                _plain_windows(
                    body,
                    size,
                    overlap,
                    base_offset=start,
                    strategy="markdown",
                    heading_path=path,
                )
            )
    return out


def _code_segment_spans(cleaned: str) -> List[Tuple[int, int]]:
    """Contiguous top-level segments split at blank-line runs and decl lines."""
    boundaries = {0, len(cleaned)}
    for match in _CODE_BLANK_RUN_RE.finditer(cleaned):
        boundaries.add(match.end())
    for match in _CODE_BOUNDARY_LINE_RE.finditer(cleaned):
        boundaries.add(match.start())
    ordered = sorted(boundaries)
    return [
        (ordered[i], ordered[i + 1])
        for i in range(len(ordered) - 1)
        if ordered[i + 1] > ordered[i]
    ]


def _code_chunks(cleaned: str, size: int, overlap: int) -> List[Dict[str, Any]]:
    hard_limit = int(size * 1.5)
    out: List[Dict[str, Any]] = []
    pack: Optional[Tuple[int, int]] = None

    def _emit(span: Tuple[int, int]) -> None:
        out.append(
            {
                "text": cleaned[span[0] : span[1]],
                "meta": {
                    "strategy": "code",
                    "start_char": span[0],
                    "heading_path": None,
                },
            }
        )

    for start, end in _code_segment_spans(cleaned):
        if end - start > hard_limit:
            # Monster segment: flush the pack, then window it like plain text.
            if pack is not None:
                _emit(pack)
                pack = None
            out.extend(
                _plain_windows(
                    cleaned[start:end],
                    size,
                    overlap,
                    base_offset=start,
                    strategy="code",
                )
            )
            continue
        if pack is None:
            pack = (start, end)
        elif end - pack[0] <= size:
            pack = (pack[0], end)
        else:
            _emit(pack)
            pack = (start, end)
    if pack is not None:
        _emit(pack)
    return out


# ── Prose chunking (review 2026-07-27 P1 #4) ────────────────────────────────
# The plain walk cuts every ``size`` characters, which lands mid-sentence and
# — for Korean, where the verb carrying the meaning sits at the end — routinely
# splits a claim from its predicate. Retrieval then matches half a statement
# and the citation shows a fragment. The prose strategy keeps the same window
# budget but ends each chunk at the last sentence/paragraph boundary inside it.

# Strong: sentence-final punctuation (ASCII + CJK) with optional closing
# quotes/brackets, followed by whitespace; or a blank-line paragraph break.
_PROSE_STRONG_BOUNDARY_RE = re.compile(
    r"(?:[.!?。！？…]+[\"'”’」』\)\]]*\s+|\n[ \t]*\n)"
)
# Weak: a single line break. Korean notes and bullet lists often carry no
# sentence punctuation at all; a line end is still a real boundary there.
_PROSE_WEAK_BOUNDARY_RE = re.compile(r"\n")
# Never emit a chunk shorter than this fraction of ``size`` just to hit a
# boundary — tiny chunks hurt recall more than a mid-sentence cut.
_PROSE_MIN_SPAN_RATIO = 0.5


def _last_boundary(cleaned: str, lo: int, hi: int) -> Optional[int]:
    """End offset of the last sentence/paragraph boundary in ``cleaned[lo:hi]``.

    Strong boundaries win; a single line break is the fallback. Returns None
    when the span holds neither, so the caller keeps the hard window cut.
    """
    window = cleaned[lo:hi]
    for pattern in (_PROSE_STRONG_BOUNDARY_RE, _PROSE_WEAK_BOUNDARY_RE):
        last = None
        for match in pattern.finditer(window):
            last = match.end()
        if last:
            return lo + last
    return None


def _prose_chunks(cleaned: str, size: int, overlap: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    total = len(cleaned)
    min_span = max(1, int(size * _PROSE_MIN_SPAN_RATIO))
    start = 0
    while start < total:
        hard_end = min(total, start + size)
        end = hard_end
        if hard_end < total:
            boundary = _last_boundary(cleaned, start + min_span, hard_end)
            if boundary is not None and boundary > start:
                end = boundary
        out.append(
            {
                "text": cleaned[start:end],
                "meta": {
                    "strategy": "prose",
                    "start_char": start,
                    "heading_path": None,
                },
            }
        )
        if end >= total:
            break
        # Overlap carries the tail of the previous chunk into the next one so
        # a claim split across a boundary is still retrievable from both.
        start = max(start + 1, end - overlap)
    return out


def typed_chunks(
    text: str,
    *,
    strategy: str = "plain",
    size: int = 1200,
    overlap: int = 160,
) -> List[Dict[str, Any]]:
    """Strategy-aware chunking with per-chunk provenance metadata.

    Returns ``[{"text": str, "meta": {"strategy", "start_char", "heading_path"}}]``
    where ``start_char`` is the offset in ``str(text or "").strip()`` (every
    chunk text is an exact substring at that offset).

    Contract: ``[c["text"] for c in typed_chunks(t)] == _chunks(t)`` for the
    default plain strategy — unknown strategies also fall back to plain.
    """
    cleaned = str(text or "").strip()
    if not cleaned:
        return []
    try:
        size = max(1, int(size))
    except Exception:
        size = 1200
    try:
        overlap = min(max(0, int(overlap)), size - 1)
    except Exception:
        overlap = min(160, size - 1)
    label = strategy if strategy in _CHUNK_STRATEGIES else "plain"
    if label == "markdown":
        return _markdown_chunks(cleaned, size, overlap)
    if label == "code":
        return _code_chunks(cleaned, size, overlap)
    if label == "prose":
        return _prose_chunks(cleaned, size, overlap)
    return _plain_windows(cleaned, size, overlap)


def typed_chunk_meta_fields(piece: Dict[str, Any]) -> Dict[str, Any]:
    """Additive chunk-metadata fields for one ``typed_chunks`` piece.

    Ingest call sites merge this into the existing ``{"index", "source_node"}``
    chunk metadata; ``heading_path`` is only present when known — honest
    absence over empty labels.
    """
    meta = piece.get("meta") or {}
    fields: Dict[str, Any] = {
        "strategy": str(meta.get("strategy") or "plain"),
        "start_char": int(meta.get("start_char") or 0),
    }
    heading_path = meta.get("heading_path")
    if heading_path:
        fields["heading_path"] = str(heading_path)
    return fields


def citation_locator(chunk_metadata: Any) -> str:
    """Human "where in the document" label for one chunk, or "".

    Built only from provenance the chunk actually carries — a section heading
    path and/or a page number. When neither is known the answer is the empty
    string, so a citation never claims a location it cannot prove.
    """
    if not isinstance(chunk_metadata, dict):
        return ""
    parts: List[str] = []
    heading = str(chunk_metadata.get("heading_path") or "").strip()
    if heading:
        parts.append(heading)
    def _page(key: str) -> int:
        value = chunk_metadata.get(key)
        try:
            return int(value) if value is not None else 0
        except (TypeError, ValueError):
            return 0

    page_number = _page("page")
    if page_number > 0:
        page_end = _page("page_end")
        parts.append(
            f"p.{page_number}–{page_end}" if page_end > page_number else f"p.{page_number}"
        )
    return " · ".join(parts)


def pdf_page_offsets(structure: Any) -> List[int]:
    """Start offset of each PDF page in the "\\n\\n"-joined page text.

    ``structure`` is the ``metadata["structure"]`` dict produced by
    ``_pdf_structure`` (``pages`` = ``[{"chars": int, ...}, ...]``); pages were
    joined with ``"\\n\\n"`` (see ``read_document``), so page k starts at
    ``sum(chars[j] + 2 for j < k)``. Empty or malformed input returns ``[]``.
    """
    if not isinstance(structure, dict):
        return []
    pages = structure.get("pages")
    if not isinstance(pages, list) or not pages:
        return []
    offsets: List[int] = []
    cursor = 0
    for page in pages:
        if not isinstance(page, dict):
            return []
        chars = page.get("chars")
        if isinstance(chars, bool) or not isinstance(chars, (int, float)) or chars < 0:
            return []
        offsets.append(cursor)
        cursor += int(chars) + 2  # +2 for the "\n\n" page joiner
    return offsets


def page_for_offset(page_offsets: List[int], offset: int) -> Optional[int]:
    """1-based page number containing ``offset`` given page start offsets.

    Returns ``None`` when ``page_offsets`` is empty or the offset precedes the
    first page start (honest absence over a wrong label).
    """
    if not page_offsets:
        return None
    try:
        target = int(offset)
    except Exception:
        return None
    page = 0
    for index, start in enumerate(page_offsets):
        try:
            if target >= int(start):
                page = index + 1
            else:
                break
        except Exception:
            return None
    return page if page >= 1 else None
