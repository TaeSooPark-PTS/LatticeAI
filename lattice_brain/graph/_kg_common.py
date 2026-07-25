"""
SQLite knowledge graph for Lattice AI workspace memory.

The graph keeps raw event JSON, normalized node metadata, and edges in one
portable database so it can later migrate to Neo4j/Postgres without changing
the ingestion contract.
"""

# ruff: noqa: F401,F841

import asyncio
import hashlib
import json
import logging
import math
import os
import platform
import re
import shutil
import sqlite3
import time
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from .schema import KGStoreV2, NodeType, EdgeType, _exec_script
except Exception:  # pragma: no cover - v2 schema is optional at import time
    KGStoreV2 = None  # type: ignore[assignment]
    NodeType = None  # type: ignore[assignment]
    EdgeType = None  # type: ignore[assignment]
    _exec_script = None  # type: ignore[assignment]

from ..embeddings import LocalEmbeddingModel
from .json_utils import _json, _safe_loads
from .runtime import get_llm_router, set_llm_router

# Default read source for the graph queries: v2 reconstruction views.
# Override with LATTICEAI_KG_READ_V2=0 to fall back to the legacy tables.
_READ_FROM_V2_DEFAULT = os.getenv("LATTICEAI_KG_READ_V2", "1") != "0"

# Static constants (projection/format versions, local-ingestion classification
# tables, OS exclusion lists) live in ._kg_constants; re-exported here so every
# existing ``from ._kg_common import <CONST>`` site is unaffected.
from ._kg_constants import (  # noqa: E402
    _KG_DB_FORMAT_KEY,
    _KG_DB_FORMAT_VERSION,
    _PROJECTION_VERSION,
    _V2_WRITE_MASTER_KEY,
    COMMON_EXCLUDED_DIRS,
    COMMON_EXCLUDED_FILE_NAMES,
    COMMON_EXCLUDED_FILE_SUFFIXES,
    GRAPH_SCHEMA_VERSION,
    LINUX_EXCLUDED_PREFIXES,
    LOCAL_CODE_EXTENSIONS,
    LOCAL_DOCUMENT_EXTENSIONS,
    LOCAL_IMAGE_EXTENSIONS,
    LOCAL_SIZE_LIMITS,
    LOCAL_SLIDE_EXTENSIONS,
    LOCAL_SPREADSHEET_EXTENSIONS,
    LOCAL_SUPPORTED_EXTENSIONS,
    LOCAL_TEXT_EXTENSIONS,
    MACOS_EXCLUDED_PREFIXES,
    SENSITIVE_PATH_KEYWORDS,
    WINDOWS_EXCLUDED_NAMES,
)


# Pure fs/path/hash/classification helpers → ._kg_fsutil (re-exported so the
# computed __all__ below still forwards them to the graph mixins).
from ._kg_fsutil import *  # noqa: E402,F401,F403


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
_CHUNK_STRATEGIES = {"plain", "markdown", "code"}
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
    extensions, ``"plain"`` otherwise. Case-insensitive, tolerant of URLs
    (query/fragment stripped) and ``Path`` objects; never raises — any
    malformed input falls back to ``"plain"``.
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
        if "markdown" in str(content_type or "").strip().lower():
            return "markdown"
    except Exception:
        pass
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


_LLM_EXTRACT_CONCEPT_PROMPT = """Extract the key concepts from the following text.
Return ONLY a JSON array of objects, each with "concept" (string) and "importance" (float 0-1).
Extract up to {limit} concepts. Focus on named entities, technical terms, and domain-specific nouns.
Do NOT include common words, stop words, or generic terms.

Text:
{text}

JSON:"""

_LLM_EXTRACT_TRIPLE_PROMPT = """Extract relationship triples from the following text.
Return ONLY a JSON array of objects, each with:
- "subject": source concept (string)
- "relation": relationship verb (string, Korean or English)
- "object": target concept (string)
- "evidence": the sentence supporting this triple (string, max 240 chars)
- "confidence": how confident you are (float 0-1)

Extract up to {limit} triples. Focus on meaningful semantic relationships.

Text:
{text}

Concepts already identified: {concepts}

JSON:"""

ENABLE_LLM_EXTRACTION = os.getenv("LATTICEAI_LLM_EXTRACTION", "true").lower() in (
    "1",
    "true",
    "yes",
)


def _llm_extract_concepts(text: str, limit: int = 12) -> Optional[List[str]]:
    router = get_llm_router()
    if not ENABLE_LLM_EXTRACTION or not router:
        return None
    if not router.current_model_id:
        return None
    prompt = _LLM_EXTRACT_CONCEPT_PROMPT.format(text=text[:3000], limit=limit)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    asyncio.run,
                    router.generate(prompt, max_tokens=1024, temperature=0.1),
                )
                raw = future.result(timeout=30)
        else:
            raw = asyncio.run(
                router.generate(prompt, max_tokens=1024, temperature=0.1)
            )
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            concepts = []
            for item in parsed[:limit]:
                if isinstance(item, dict) and "concept" in item:
                    concepts.append(item["concept"])
                elif isinstance(item, str):
                    concepts.append(item)
            return concepts if concepts else None
    except Exception as e:
        logging.debug("LLM concept extraction failed (falling back to rules): %s", e)
    return None


def _llm_extract_triples(
    text: str, concepts: List[str], limit: int = 20
) -> Optional[List[Dict[str, str]]]:
    router = get_llm_router()
    if not ENABLE_LLM_EXTRACTION or not router:
        return None
    if not router.current_model_id:
        return None
    prompt = _LLM_EXTRACT_TRIPLE_PROMPT.format(
        text=text[:3000],
        limit=limit,
        concepts=", ".join(concepts[:15]),
    )
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    asyncio.run,
                    router.generate(prompt, max_tokens=2048, temperature=0.1),
                )
                raw = future.result(timeout=30)
        else:
            raw = asyncio.run(
                router.generate(prompt, max_tokens=2048, temperature=0.1)
            )
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            triples = []
            for item in parsed[:limit]:
                if isinstance(item, dict) and "subject" in item and "object" in item:
                    triples.append(
                        {
                            "subject": str(item["subject"]),
                            "relation": str(item.get("relation", "관련됨")),
                            "object": str(item["object"]),
                            "context": str(item.get("evidence", ""))[:240],
                            "confidence": float(item.get("confidence", 0.8)),
                        }
                    )
            return triples if triples else None
    except Exception as e:
        logging.debug("LLM triple extraction failed (falling back to rules): %s", e)
    return None


_CONCEPT_STOP: set = {
    # English stop words
    "the",
    "and",
    "for",
    "with",
    "this",
    "that",
    "from",
    "into",
    "which",
    "are",
    "was",
    "were",
    "has",
    "have",
    "had",
    "can",
    "will",
    "would",
    "could",
    "should",
    "may",
    "might",
    "must",
    "shall",
    "being",
    "been",
    "also",
    "just",
    "then",
    "than",
    "when",
    "where",
    "what",
    "how",
    "why",
    "its",
    "their",
    "your",
    "our",
    "you",
    "they",
    "them",
    "these",
    "those",
    "use",
    "used",
    "using",
    "based",
    "like",
    "such",
    "via",
    "per",
    "let",
    "yes",
    "not",
    "but",
    "are",
    "all",
    "any",
    "out",
    "new",
    "get",
    "set",
    # Korean stop words
    "사용자",
    "내용",
    "파일",
    "채팅",
    "답변",
    "입니다",
    "그리고",
    "처럼",
    "있어",
    "없어",
    "이야",
    "이다",
    "한다",
    "하다",
    "되다",
    "됩니다",
    "경우",
    "방법",
    "부분",
    "상태",
    "정도",
    "결과",
    "이후",
    "이전",
    "그것",
    "이것",
    "저것",
    "여기",
    "거기",
    "저기",
    "우리",
    "저희",
    "기능",
    "서버",
    "모델",
    "설정",
    "설명",
    "버전",
    "지원",
    "사용",
    "실행",
    "todo",
    "fixme",
    "note",
    "참고",
    "주의",
    "warning",
}


def _extract_concepts(text: str, limit: int = 12) -> List[str]:
    """LLM-first concept extraction with rule-based fallback."""
    llm_result = _llm_extract_concepts(text, limit)
    if llm_result:
        return llm_result
    return _extract_concepts_rules(text, limit)


def _extract_concepts_rules(text: str, limit: int = 12) -> List[str]:
    """Extract meaningful named concepts from text (rule-based).

    Priority order:
    1. Backtick / quoted terms (explicitly technical)
    2. Multi-word proper nouns (Lattice AI, GPT-4o, Claude Sonnet)
    3. Single capitalized proper nouns not at sentence start (Claude, Python, FastAPI)
    4. Korean compound technical terms (멀티모달, 에이전트, 그래프RAG)
    5. Hyphenated / versioned identifiers (gpt-4o, mlx-vlm, gemma-4)
    """
    text = str(text or "")
    seen: dict = {}  # concept_lower → original form

    def _add(term: str) -> None:
        key = term.strip().lower()
        if key and key not in _CONCEPT_STOP and not key.isdigit() and len(key) >= 2:
            seen.setdefault(key, term.strip())

    # 1. Backtick-quoted code/term (highest confidence)
    for m in re.findall(r"`([^`]{2,40})`", text):
        if not re.search(r"[\(\)\[\]{}]", m):  # skip code expressions
            _add(m)

    # 2. Double/single quoted terms
    for m in re.findall(r'"([^"]{2,40})"', text):
        _add(m)

    # 3. Multi-word English proper nouns (Title Case or ALL-CAPS first word, 2–4 words).
    #    Pattern A: Mixed-case first word — "Lattice AI", "Tool Use", "Graph RAG"
    for m in re.findall(
        r"([A-Z][a-z]{1,20}(?:\s+(?:[A-Z]{2,10}|[A-Z][a-z0-9]{1,20}|\d[\w.]{0,6})){1,3})",
        text,
    ):
        _add(m)
    #    Pattern B: ALL-CAPS first word — "VS Code", "MCP Server", "GPT-4o Mini"
    for m in re.findall(
        r"([A-Z]{2,6}(?:\s+(?:[A-Z]{2,10}|[A-Z][a-z0-9]{1,20})){1,2})",
        text,
    ):
        _add(m)

    # 4. Single capitalized proper noun.
    #    Use ASCII-boundary lookaround instead of \b so Korean particles
    #    (와, 의, 는 …) after an English word don't block the match.
    all_caps_words = re.findall(
        r"(?<![A-Za-z0-9])([A-Z][A-Za-z0-9]{2,24})(?![A-Za-z0-9])", text
    )
    freq: Dict[str, int] = {}
    for w in all_caps_words:
        freq[w] = freq.get(w, 0) + 1
    sentence_starts = set(re.findall(r"(?:^|(?<=[.!?])\s+)([A-Z][a-z]+)", text))
    for m, cnt in freq.items():
        if m.lower() in _CONCEPT_STOP:
            continue
        if cnt >= 2 or m not in sentence_starts:
            _add(m)

    # 5. Korean technical compound nouns (3–12 chars, no common particles)
    for m in re.findall(
        r"[가-힣]{2,12}(?:AI|LLM|API|UI|RAG|bot|Bot|기능|모델|서버|에이전트|파이프라인|워크플로)",
        text,
    ):
        _add(m)
    # Korean standalone terms that appear after topic markers (은/는/이/가 앞)
    for m in re.findall(
        r"([가-힣]{2,12})(?:은|는|이|가|을|를|의|에서|으로|와|과)", text
    ):
        if m.lower() not in _CONCEPT_STOP and len(m) >= 2:
            # Only add if it's non-trivial (has 3+ chars or appears multiple times)
            cnt = text.count(m)
            if len(m) >= 3 or cnt >= 2:
                _add(m)

    # 6. Hyphenated / versioned identifiers (gpt-4o, gemma-4, mlx-vlm)
    for m in re.findall(r"\b([a-zA-Z][a-zA-Z0-9]*(?:-[a-zA-Z0-9.]+)+)\b", text):
        if len(m) >= 4:
            _add(m)

    # De-duplicate: remove shorter if ALL its occurrences in the source text
    # are followed immediately by the suffix that forms the longer concept.
    # "Lattice" → dropped when every occurrence is "Lattice AI"
    # "Claude"  → kept  because it appears as just "Claude" too.
    values = list(seen.values())
    values_lower = [v.lower() for v in values]
    keep = set(range(len(values)))
    for i, v in enumerate(values):
        vl = v.lower()
        for j, wl in enumerate(values_lower):
            if i == j or j not in keep:
                continue
            # Check if vl is a word-prefix of wl
            suffix = wl[len(vl) :]
            if not (wl.startswith(vl) and re.match(r"^[\s\-]", suffix)):
                continue
            # Count occurrences of v NOT followed by the suffix
            suffix_stripped = suffix.lstrip(" -")
            # Escape for regex
            pattern_with_suffix = re.escape(v) + r"[\s\-]+" + re.escape(suffix_stripped)
            pattern_alone = (
                re.escape(v) + r"(?![\s\-]*" + re.escape(suffix_stripped) + r")"
            )
            alone_count = len(re.findall(pattern_alone, text, re.IGNORECASE))
            if alone_count == 0:
                # Shorter term never appears alone → safe to remove
                keep.discard(i)
                break

    final = [values[i] for i in range(len(values)) if i in keep]
    return final[:limit]


# ──────────────────────────────────────────────────────────────────────────────
# Node type taxonomy  (점 = 명사)
# ──────────────────────────────────────────────────────────────────────────────
# Chat      — 대화 세션
# Document  — 파일 (PDF·PPT·Word·Excel·이미지 등)
# Concept   — 개념·아이디어·기술 용어
# Person    — 사람 (사용자, 언급된 인물)
# Error     — 오류·버그·예외
# Code      — 코드 스니펫·함수·클래스
# Feature   — 소프트웨어 기능
# Task      — 할 일·액션 아이템
# Decision  — 결정 사항

# Edge type vocabulary  (선 = 동사 — 과거형 서술어)
EDGE_VERB = {
    "언급함": r"언급|mention|refer|cited",
    "포함함": r"포함|include|consist|구성|탑재|contains",
    "해결함": r"해결|resolv|fix|수정|고쳤|closed",
    "의존함": r"의존|depend|require|필요|based on",
    "설명함": r"설명|explain|describe|정의|란|이란|means",
    "비교함": r"비교|versus|vs\.?|차이|다르|compare",
    "사용함": r"사용|use|활용|이용|apply",
    "연결함": r"연결|connect|통합|integrate|연동|link",
    "확장함": r"확장|extend|플러그인|plugin|addon",
    "생성함": r"생성|만들|create|generate|build|produced",
    "대체함": r"대체|replace|instead|alternative",
    "지원함": r"지원|support|제공|provide|offer",
    "발생함": r"발생|occur|throw|raise|triggered",
    "관련됨": r"관련|related|associated|연관",
}


def _infer_edge(sentence: str) -> str:
    """Return the best-matching verb-form edge label for a sentence."""
    s = sentence.lower()
    for label, pattern in EDGE_VERB.items():
        if re.search(pattern, s):
            return label
    return "관련됨"


# Technical words that cannot be person names
_NOT_PERSON_WORDS: set = {
    "use",
    "api",
    "rag",
    "sdk",
    "ide",
    "cli",
    "llm",
    "mcp",
    "ui",
    "ux",
    "new",
    "old",
    "get",
    "set",
    "run",
    "add",
    "fix",
    "tool",
    "code",
    "base",
    "core",
    "data",
    "file",
    "test",
    "type",
    "mode",
    "view",
}


def _classify_node_type(concept: str, text: str) -> str:
    """Classify a concept into the node taxonomy.

    Term-level signals take priority; then a tight ±60-char window is used
    so distant keywords don't cause mis-classification.
    """
    term = concept.lower()

    # ── Term-level signals (highest confidence) ───────────────────────────
    if re.search(r"(?:error|exception|traceback|오류|에러|버그)$", term, re.I):
        return "Error"
    if re.search(r"error|exception|err\b", term, re.I) and len(concept) < 30:
        return "Error"
    if re.search(r"\(\)|\.py$|\.js$|\.ts$|\.go$|::\w", term):
        return "Code"

    # Person: "First Last" pattern, neither word is a known technical term
    if re.match(r"^[A-Z][a-z]{1,15} [A-Z][a-z]{1,15}$", concept):
        words = term.split()
        if not any(w in _NOT_PERSON_WORDS for w in words):
            return "Person"

    # ── Windowed context (±60 chars) — NOT used for Error to avoid false positives
    idx = text.lower().find(term)
    if idx >= 0:
        win = text[max(0, idx - 60) : idx + len(concept) + 60].lower()
        if re.search(r"def |class |function|함수|클래스|메서드|import", win):
            return "Code"
        # Feature: concept appears DIRECTLY adjacent to 기능/feature keyword
        if len(concept) <= 12 and re.search(
            rf"{re.escape(term)}.{{0,8}}(?:기능|feature)|(?:기능|feature).{{0,8}}{re.escape(term)}",
            win,
        ):
            return "Feature"

    return "Concept"


def _extract_triples(
    text: str,
    concepts: List[str],
    limit: int = 20,
) -> List[Dict[str, str]]:
    """LLM-first triple extraction with rule-based fallback."""
    llm_result = _llm_extract_triples(text, concepts, limit)
    if llm_result:
        return llm_result
    return _extract_triples_rules(text, concepts, limit)


def _extract_triples_rules(
    text: str,
    concepts: List[str],
    limit: int = 20,
) -> List[Dict[str, str]]:
    """Extract (subject, verb-edge, object, context) triples from text (rule-based).

    For each sentence containing ≥2 concepts, infer the verb-form edge label
    from surrounding context and create a directed triple.
    """
    if len(concepts) < 2:
        return []

    concept_lower = {c.lower(): c for c in concepts}
    triples: List[Dict[str, str]] = []
    seen_pairs: set = set()

    # Split on sentence boundaries
    sentences = re.split(r"(?<=[.!?\n])\s+|\n{2,}", text)
    for sent in sentences:
        sent = sent.strip()
        if len(sent) < 8:
            continue
        sent_lower = sent.lower()

        present = [concept_lower[k] for k in concept_lower if k in sent_lower]
        if len(present) < 2:
            continue

        edge = _infer_edge(sent)

        for i in range(len(present) - 1):
            subj, obj = present[i], present[i + 1]
            # Deduplicate by (subj, obj) regardless of direction for same edge
            pair_key = tuple(sorted([subj.lower(), obj.lower()])) + (edge,)
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            triples.append(
                {
                    "subject": subj,
                    "relation": edge,  # verb form (동사)
                    "object": obj,
                    "context": sent[:240],
                }
            )
            if len(triples) >= limit:
                return triples

    return triples


def _semantic_items(text: str) -> List[Dict[str, str]]:
    """Extract explicit decision / task items from text."""
    items: List[Dict[str, str]] = []
    for raw_line in str(text or "").splitlines():
        line = _clean_text(raw_line)
        if len(line) < 6:
            continue
        lowered = line.lower()
        if re.search(r"(결정|확정|하기로|decided|decision)", lowered):
            items.append(
                {"type": "Decision", "title": line[:120], "summary": line[:500]}
            )
        if re.search(r"(todo|해야|하자|진행|구현|수정|확인|next|task|\[ \])", lowered):
            items.append({"type": "Task", "title": line[:120], "summary": line[:500]})
    return items[:8]


def _topic_candidates(text: str, limit: int = 8) -> List[str]:
    """Return compact keyword candidates for fallback graph search."""
    candidates = _extract_concepts(text, limit=limit)
    if candidates:
        return candidates[:limit]
    seen: Dict[str, str] = {}
    for token in re.findall(
        r"[A-Za-z][A-Za-z0-9_.:-]{2,}|[가-힣]{2,12}", str(text or "")
    ):
        key = token.lower()
        if key in _CONCEPT_STOP or key.isdigit():
            continue
        seen.setdefault(key, token)
        if len(seen) >= limit:
            break
    return list(seen.values())[:limit]


__all__ = [name for name in globals() if not name.startswith("__")]
