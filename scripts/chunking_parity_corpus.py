"""The chunking-parity corpus: every input the goldens are built from.

Split out of :mod:`generate_chunking_parity_fixtures` so the generator stays a
runner and this stays a specification. Nothing here imports the product — these
are inputs, and the only thing that decides what they chunk into is the real
``lattice_brain`` code the generator calls.

The corpus is the coverage, so every case is here for a reason a reader can
check. Broadly:

* **shape** — empty, whitespace-only, exactly ``size``, ``size - 1``,
  ``size + 1``, a clamped overlap, a zero size;
* **markdown** — nested headings, an empty heading title, seven hashes and
  ``#NoSpace`` (neither is a heading), sections at exactly 199 / 200 / 201
  characters so the merge floor is observable, and a section too big for one
  window;
* **code** — every one of the seven declaration prefixes at a line start that
  is not already a boundary, blank-line runs filled with C0 separators (which
  Python's ``\\s`` accepts and Unicode's ``White_Space`` does not), a segment
  one character past ``int(size * 1.5)``, and greedy packing at a small window;
* **prose** — every sentence terminator and every closing mark, each alone in
  its window so each is individually load-bearing, plus paragraph breaks,
  line-break-only text and text with no boundary at all;
* **multibyte** — Korean, emoji with zero-width joiners, a regional-indicator
  flag and a combining mark, straddling boundaries at small windows. Python
  slices by *characters*; a byte-sliced port disagrees here and panics there.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


# ── corpus builders ──────────────────────────────────────────────────────────
def mixed_paragraphs(count: int) -> str:
    """``count`` numbered ko/en lines, ~100 chars each, newline separated."""
    return "\n".join(
        f"{index:02d}. 회의에서 결정한 사항을 정리합니다. "
        f"The retrieval pipeline chunks text before it is embedded. "
        f"결정 근거는 문서 {index}번에 있습니다."
        for index in range(count)
    )


def markdown_document() -> str:
    """Preamble, nested headings, tiny sections, an empty title, a big section."""
    filler = " ".join(f"근거 문단 {i}: 검색 품질은 청크 경계에 좌우됩니다." for i in range(18))
    return "\n".join(
        [
            "안내서 서문입니다. 이 문단은 첫 제목 앞에 옵니다.",
            "",
            "# 안내서",
            "짧다.",
            "## 설치",
            "설치 방법은 다음과 같습니다. " * 12,
            "### 사전 준비",
            "준비물.",
            "### 실행",
            "실행 방법.",
            "## 사용",
            filler,
            "# ",
            "빈 제목 아래 본문입니다. " * 18,
            "###### 여섯 단계",
            "가장 깊은 제목입니다. " * 18,
            "## 마무리",
            "끝.",
        ]
    )


def markdown_all_tiny() -> str:
    """Every section under the 200-char floor — the merge never completes."""
    return "\n".join(
        ["# 하나", "짧다.", "## 둘", "더 짧다.", "### 셋", "끝."]
    )


def markdown_threshold() -> str:
    """Sections spanning 250 / 199 / 250 / 200 / 250 / 201 / 300 characters.

    The 200-char merge floor is only observable when a section lands exactly on
    it: a corpus of tiny-and-huge sections passes with any floor between them.
    Here 199 merges forward, 200 stands alone and 201 stands alone, so both a
    floor of 199 and a floor of 201 produce a different chunk list.
    """
    parts = []
    for index, span in enumerate((250, 199, 250, 200, 250, 201, 300)):
        heading = f"# S{index}\n"
        parts.append(heading + "x" * (span - len(heading) - 1) + "\n")
    return "".join(parts)


def markdown_seven_hashes() -> str:
    """Things that look like headings and are not (7 hashes, no space)."""
    return "\n".join(
        [
            "####### 일곱 개는 제목이 아니다",
            "#공백없음",
            "## 진짜 제목",
            "본문입니다. " * 40,
            "  ## 들여쓴 제목은 제목이 아니다",
            "마지막 본문. " * 40,
        ]
    )


def python_source() -> str:
    """Declaration lines and blank-line runs, the two code-segment boundaries."""
    return "\n".join(
        [
            "import os",
            "",
            "",
            "def alpha(value):",
            "    return value + 1",
            "",
            "",
            "class Beta:",
            '    """도움말 문자열입니다."""',
            "",
            "    def gamma(self):",
            "        return 2",
            "",
            "",
            "const_value = 3",
            "const = 4",
            "",
            "public = 5",
            "private = 6",
            "",
            "",
            "def omega(value):",
            "    # 마지막 함수입니다.",
            "    return value * 2",
        ]
    )


def javascript_source() -> str:
    """``export`` / ``const`` / ``function`` declaration starts."""
    return "\n".join(
        [
            "export const alpha = 1;",
            "",
            "function beta(value) {",
            "  return value + 1;",
            "}",
            "",
            "",
            "export default function gamma() {",
            "  return '결과';",
            "}",
            "",
            "const delta = () => 4;",
        ]
    )


def code_monster_segment() -> str:
    """One segment past ``size * 1.5`` (1.8x at size=100), flanked by small ones."""
    monster = "x = [" + ", ".join(str(n) for n in range(60)) + "]"
    return "\n".join(["def head():", "    return 1", "", "", monster, "", "", "def tail():", "    return 2"])


def code_hard_limit_boundary() -> str:
    """A segment of exactly 38 characters — one past ``int(25 * 1.5)``.

    ``int(size * 1.5)`` truncates. A port that rounds instead computes 38 and
    packs this segment where Python windows it, which is a whole different
    chunk list from a single character of arithmetic.
    """
    return "def a():\n\n\n" + "a" * 35 + "\n\n\ndef b():\n    2"


def code_c0_blank_run() -> str:
    """A blank-line run whose filler is a C0 separator, not a space.

    Python's ``\\s`` accepts ``\\x1c``-``\\x1f``; the Unicode ``White_Space``
    property does not. A port that reached for a stock Unicode ``\\s`` would
    miss these runs and pack the statements around them into one segment.
    """
    return "x = 1\n\ny = 2\n\x1c\nz = 3\n\x1f \x1e\nw = 4\n\nv = 5"


def code_declaration_matrix() -> str:
    """All seven declaration prefixes, each the only boundary on its line.

    No blank lines anywhere, so every segment boundary comes from a declaration
    start. At a small window each of the seven is individually load-bearing:
    drop one and the two segments around it fuse into a different chunk.
    """
    return "\n".join(
        [
            "def a(): 1",
            "class b: 2",
            "function c() {}",
            "export d = 4",
            "const e = 5",
            "public f = 6",
            "private g = 7",
            "def h(): 8",
            "tail = 9",
        ]
    )


def prose_terminator_matrix() -> str:
    """One sentence per sentence-final mark, each alone in its window."""
    body = "가나다라마바사아자차카타파하"
    return "".join(f"{body}{mark} " for mark in (".", "!", "?", "。", "！", "？", "…"))


def prose_closer_matrix() -> str:
    """One sentence per closing quote/bracket, each alone in its window.

    Drop any single closer from the port's set and that sentence's boundary
    becomes a hard window cut instead, which moves every chunk after it.
    """
    body = "가나다라마바사아자차카타파하"
    closers = ('"', "'", "\u201d", "\u2019", "\u300d", "\u300f", ")", "]")
    # A plain trailing sentence, so the *last* closer is load-bearing too:
    # a boundary at the very end of the text never changes a chunk list.
    return "".join(f"{body}.{closer} " for closer in closers) + f"{body}. "


def prose_closers() -> str:
    """Every closing quote and bracket the strong boundary allows."""
    return (
        '그는 "끝났다." 라고 했다. '
        "그녀는 '정말?' 이라고 물었다. "
        "안내문은 “확인했다.” 였다. "
        "메모는 ‘완료’ 였다. "
        "인용은 「검토함.」 이었다. "
        "출처는 『보고서.』 이다. "
        "각주는 (참고함.) 이다. "
        "표는 [완료됨.] 이다. "
        "마지막 문장이다."
    )


def prose_english() -> str:
    return (
        "The retrieval pipeline chunks text before it is embedded. "
        "Each chunk carries a start offset so a citation can point at it! "
        "Does the boundary land on a sentence? It does, when one is in range. "
        'She said "the boundary matters" (twice), and nobody disagreed. '
        "A final sentence closes the paragraph without any surprises."
    )


def prose_korean() -> str:
    return (
        "회의에서 결정한 사항을 정리합니다。 근거는 문서에 남겨 두었습니다！ "
        "다음 주에 다시 검토할까요？ 검토 결과는 여기에 덧붙입니다… "
        "한국어는 문장 끝에 서술어가 오기 때문에 경계가 특히 중요합니다. "
        "그래서 청크 경계를 문장 끝에 맞춥니다."
    )


def prose_no_boundary() -> str:
    """No punctuation, no line break — the hard window cut is the only answer."""
    return "가나다라마바사아자차카타파하" * 12


def prose_lines_only() -> str:
    """Bullet lines with no sentence punctuation — the weak boundary path."""
    return "\n".join(f"- 항목 {index} 준비 완료" for index in range(30))


def prose_paragraphs() -> str:
    return "\n\n".join(
        f"{index}번 문단입니다 이 문단에는 마침표가 없습니다 그래서 문단 경계만 남습니다"
        for index in range(12)
    )


#: Emoji with zero-width joiners, a regional-indicator flag and a combining
#: mark: four graphemes, eleven code points, thirty-eight UTF-8 bytes. Slicing
#: this by bytes is not "slightly different", it is a panic.
GRAPHEME_SOUP = "a👨‍👩‍👧‍👦b🇰🇷cée"


def unicode_boundary_text() -> str:
    return (GRAPHEME_SOUP + "한글") * 8


def ascii_of_length(length: int) -> str:
    """``length`` printable ASCII chars — one char is one byte, on purpose."""
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(alphabet[index % len(alphabet)] for index in range(length))


def korean_of_length(length: int) -> str:
    """``length`` Hangul syllables — one char is three bytes, on purpose."""
    syllables = "가나다라마바사아자차카타파하"
    return "".join(syllables[index % len(syllables)] for index in range(length))


# ── the case set ─────────────────────────────────────────────────────────────
# ``strategy`` None means "route it from ``filename`` via chunk_strategy_for",
# which is how every real call site picks one.
CASES: List[Dict[str, Any]] = [
    {"key": "empty", "text": "", "filename": "empty.txt"},
    {"key": "whitespace_only", "text": "   \n\t\r\n  ", "filename": "blank.txt"},
    {"key": "plain_short", "text": "짧은 메모 한 줄.", "filename": "note"},
    {"key": "plain_strip_not_collapse", "text": "  \n 두   칸  사이  간격은   유지된다.  \n ", "filename": "note"},
    {"key": "plain_exact_minus_one", "text": ascii_of_length(63), "filename": "n", "size": 64, "overlap": 8},
    {"key": "plain_exact", "text": ascii_of_length(64), "filename": "n", "size": 64, "overlap": 8},
    {"key": "plain_exact_plus_one", "text": ascii_of_length(65), "filename": "n", "size": 64, "overlap": 8},
    {"key": "plain_default_long", "text": mixed_paragraphs(30), "filename": "log"},
    {"key": "plain_korean_small_window", "text": korean_of_length(37), "filename": "n", "size": 10, "overlap": 3},
    {"key": "plain_grapheme_soup", "text": unicode_boundary_text(), "filename": "n", "size": 7, "overlap": 2},
    {"key": "plain_overlap_clamped", "text": ascii_of_length(30), "filename": "n", "size": 5, "overlap": 100},
    {"key": "plain_overlap_zero", "text": ascii_of_length(30), "filename": "n", "size": 7, "overlap": 0},
    {"key": "plain_overlap_negative", "text": ascii_of_length(30), "filename": "n", "size": 7, "overlap": -4},
    {"key": "plain_size_one", "text": korean_of_length(6), "filename": "n", "size": 1, "overlap": 160},
    {"key": "plain_size_zero_coerced", "text": ascii_of_length(9), "filename": "n", "size": 0, "overlap": 3},
    {"key": "unknown_strategy_falls_back", "text": mixed_paragraphs(4), "filename": "x.md", "strategy": "sideways"},
    {"key": "markdown_full", "text": markdown_document(), "filename": "guide.md"},
    {"key": "markdown_full_small_window", "text": markdown_document(), "filename": "guide.md", "size": 120, "overlap": 30},
    {"key": "markdown_all_tiny", "text": markdown_all_tiny(), "filename": "tiny.markdown"},
    {"key": "markdown_threshold", "text": markdown_threshold(), "filename": "floor.md"},
    {"key": "markdown_not_headings", "text": markdown_seven_hashes(), "filename": "edge.md"},
    {"key": "markdown_no_heading", "text": mixed_paragraphs(6), "filename": "plainish.md"},
    {"key": "markdown_grapheme_soup", "text": "# 제목\n" + unicode_boundary_text(), "filename": "u.md", "size": 9, "overlap": 3},
    {"key": "code_python", "text": python_source(), "filename": "module.py"},
    {"key": "code_python_packed", "text": python_source(), "filename": "module.py", "size": 60, "overlap": 12},
    {"key": "code_javascript", "text": javascript_source(), "filename": "app.tsx", "size": 80, "overlap": 16},
    {"key": "code_monster_segment", "text": code_monster_segment(), "filename": "big.py", "size": 100, "overlap": 20},
    {"key": "code_korean_small", "text": python_source(), "filename": "module.py", "size": 24, "overlap": 5},
    {"key": "code_hard_limit_boundary", "text": code_hard_limit_boundary(), "filename": "edge.py", "size": 25, "overlap": 5},
    {"key": "code_c0_blank_run", "text": code_c0_blank_run(), "filename": "c0.py", "size": 12, "overlap": 3},
    {"key": "prose_closers", "text": prose_closers(), "filename": "quotes.txt", "size": 45, "overlap": 10},
    {"key": "code_declaration_matrix", "text": code_declaration_matrix(), "filename": "matrix.py", "size": 12, "overlap": 3},
    {"key": "prose_terminator_matrix", "text": prose_terminator_matrix(), "filename": "terms.txt", "size": 20, "overlap": 2},
    {"key": "prose_closer_matrix", "text": prose_closer_matrix(), "filename": "closers.txt", "size": 20, "overlap": 2},
    {"key": "prose_english", "text": prose_english(), "filename": "essay.txt", "size": 90, "overlap": 20},
    {"key": "prose_korean", "text": prose_korean(), "filename": "essay.txt", "size": 60, "overlap": 15},
    {"key": "prose_no_boundary", "text": prose_no_boundary(), "filename": "essay.txt", "size": 40, "overlap": 9},
    {"key": "prose_lines_only", "text": prose_lines_only(), "filename": "list.txt", "size": 70, "overlap": 14},
    {"key": "prose_paragraphs", "text": prose_paragraphs(), "filename": "para.txt", "size": 110, "overlap": 25},
    {"key": "prose_default_window", "text": mixed_paragraphs(40), "filename": "report.pdf"},
    {"key": "prose_grapheme_soup", "text": unicode_boundary_text(), "filename": "u.html", "size": 11, "overlap": 4},
    # overlap == size - 1: whenever a sentence boundary lands closer to the
    # start than the overlap, ``end - overlap`` goes backwards and the
    # ``max(start + 1, …)`` guard is the only thing that ends the loop.
    {"key": "prose_tight_overlap", "text": prose_english()[:110], "filename": "essay.txt", "size": 40, "overlap": 39},
]

#: Filename/MIME pairs pinning every branch of ``chunk_strategy_for``.
STRATEGY_CASES: List[Dict[str, str]] = [
    {"filename": "guide.md", "content_type": ""},
    {"filename": "GUIDE.MARKDOWN", "content_type": ""},
    {"filename": "module.py", "content_type": ""},
    {"filename": "app.TSX", "content_type": ""},
    {"filename": "data.json", "content_type": ""},
    {"filename": "conf.toml", "content_type": ""},
    {"filename": "notes.txt", "content_type": ""},
    {"filename": "report.pdf", "content_type": ""},
    {"filename": "page.HTM", "content_type": ""},
    {"filename": "book.epub", "content_type": ""},
    {"filename": "archive.tar.gz", "content_type": ""},
    {"filename": "noextension", "content_type": ""},
    {"filename": ".hidden", "content_type": ""},
    {"filename": "trailing.", "content_type": ""},
    {"filename": "", "content_type": ""},
    {"filename": "   ", "content_type": ""},
    {"filename": "https://example.com/docs/guide.md?v=2#top", "content_type": ""},
    {"filename": "https://example.com/docs/guide?v=2#top", "content_type": ""},
    {"filename": "C:\\projects\\lattice\\module.py", "content_type": ""},
    {"filename": "/var/data/notes/", "content_type": ""},
    {"filename": "/var/data/notes//", "content_type": ""},
    {"filename": "noextension", "content_type": "text/markdown"},
    {"filename": "noextension", "content_type": "TEXT/HTML; charset=utf-8"},
    {"filename": "noextension", "content_type": "text/plain"},
    {"filename": "noextension", "content_type": "application/octet-stream"},
    {"filename": "noextension", "content_type": "  application/x-markdown  "},
    {"filename": "report.pdf", "content_type": "text/markdown"},
    {"filename": "한글 문서.md", "content_type": ""},
    {"filename": "한글 문서", "content_type": "text/plain"},
]

#: ``metadata["structure"]`` shapes for ``pdf_page_offsets``, well formed and not.
PDF_STRUCTURES: List[Dict[str, Any]] = [
    {"key": "three_pages", "structure": {"pages": [{"chars": 100}, {"chars": 250}, {"chars": 40}]}},
    {"key": "single_page", "structure": {"pages": [{"chars": 1200}]}},
    {"key": "zero_length_page", "structure": {"pages": [{"chars": 0}, {"chars": 10}, {"chars": 0}]}},
    {"key": "float_chars", "structure": {"pages": [{"chars": 10.9}, {"chars": 5.0}]}},
    {"key": "no_pages_key", "structure": {"meta": 1}},
    {"key": "pages_not_list", "structure": {"pages": {"chars": 5}}},
    {"key": "pages_empty", "structure": {"pages": []}},
    {"key": "page_not_dict", "structure": {"pages": [{"chars": 5}, 7]}},
    {"key": "chars_missing", "structure": {"pages": [{"chars": 5}, {}]}},
    {"key": "chars_negative", "structure": {"pages": [{"chars": 5}, {"chars": -1}]}},
    {"key": "chars_bool", "structure": {"pages": [{"chars": True}]}},
    {"key": "chars_string", "structure": {"pages": [{"chars": "5"}]}},
    {"key": "structure_not_dict", "structure": [1, 2, 3]},
    {"key": "structure_null", "structure": None},
]

#: Offsets probed against the ``three_pages`` / ``zero_length_page`` offsets.
PAGE_PROBES: List[int] = [-5, -1, 0, 1, 99, 100, 101, 102, 351, 352, 353, 10_000]

#: Chunk-metadata blobs for ``citation_locator``.
LOCATOR_CASES: List[Dict[str, Any]] = [
    {},
    {"heading_path": "안내서 > 온보딩"},
    {"heading_path": "  spaced  "},
    {"heading_path": ""},
    {"page": 3},
    {"page": 3, "page_end": 5},
    {"page": 3, "page_end": 3},
    {"page": 3, "page_end": 2},
    {"page": 0},
    {"page": -1},
    {"page": "4"},
    {"page": "nope"},
    {"page": None},
    {"heading_path": "Retrieval > Fusion", "page": 2, "page_end": 4},
]

#: ``(source_type, source_uri, text, workspace_id)`` for the text/web hash rule.
TEXT_HASH_CASES: List[Dict[str, Optional[str]]] = [
    {"source_type": "note", "source_uri": None, "text": "회의 결정 사항", "workspace_id": None},
    {"source_type": "note", "source_uri": "", "text": "회의 결정 사항", "workspace_id": "ws-alpha"},
    {"source_type": "web_url", "source_uri": "https://example.com/a", "text": "hello", "workspace_id": "ws-beta"},
    {"source_type": "text", "source_uri": "file:///tmp/x.txt", "text": "", "workspace_id": None},
    {"source_type": "markdown", "source_uri": "s3://b/k", "text": GRAPHEME_SOUP, "workspace_id": "ws-∅"},
]

#: Byte payloads for the file content-hash rule (files hash their **bytes**).
FILE_HASH_CASES: List[bytes] = [
    b"",
    b"hello world\n",
    "회의록\n".encode(),
    bytes(range(256)),
]

#: Texts whose ``_clean_text`` → sha256 pair is the vector-index ``text_hash``.
VECTOR_TEXT_CASES: List[str] = [
    "",
    "   ",
    "a",
    "  회의   결정\t사항  ",
    "line one\nline two\r\nline three",
    "회의\x1c록",
    GRAPHEME_SOUP,
]
