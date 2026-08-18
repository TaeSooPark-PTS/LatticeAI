"""Concept and triple extraction — LLM-first, rules as the fallback.

Moved verbatim out of ``_kg_common`` (v11.3.0 decomposition). The two seams
tests reach for live **here**, next to the code that reads them:
``ENABLE_LLM_EXTRACTION`` and ``get_llm_router``. Patching them on the
``_kg_common`` package would rebind the package attribute and leave this
module's own globals untouched, so the patch target is
``lattice_brain.graph._kg_common.extraction``.
"""

from __future__ import annotations

# F841: `_extract_concepts_rules` builds `pattern_with_suffix` alongside the
# pattern it actually matches on; it documents the pair and is left as it was
# under the module-wide directive `_kg_common.py` carried before the split.
# ruff: noqa: F841
import asyncio
import functools
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from ..runtime import get_llm_router
from .normalize import merge_entity_aliases
from .patterns import concept_positions, typed_relation
from .relations import (
    COOCCURRENCE_CONCEPT_LIMIT,
    COOCCURRENCE_EDGE_WEIGHT,
    VERB_EDGE_WEIGHT,
    infer_edge_relation,
)
from .sections import heading_at, heading_spans, sentence_offsets, with_section
from .text import _clean_text

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


# Triples carry a numeric ``weight``/``confidence`` alongside string fields,
# so the value type is Any rather than str.
def _llm_extract_triples(
    text: str, concepts: List[str], limit: int = 20
) -> Optional[List[Dict[str, Any]]]:
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
            triples: List[Dict[str, Any]] = []
            for item in parsed[:limit]:
                if isinstance(item, dict) and "subject" in item and "object" in item:
                    relation = str(item.get("relation", "관련됨"))
                    evidence_text = str(item.get("evidence", ""))[:240]
                    confidence = float(item.get("confidence", 0.8))
                    # An LLM triple that names a real verb and cites the
                    # sentence it came from is semantic evidence; a bare
                    # "관련됨" with no quoted evidence is the model restating
                    # co-occurrence, and is weighted (and labelled) as such.
                    is_semantic = bool(evidence_text) and relation != "관련됨"
                    triples.append(
                        {
                            "subject": str(item["subject"]),
                            "relation": relation,
                            "object": str(item["object"]),
                            "context": evidence_text,
                            "confidence": confidence,
                            "evidence": "verb" if is_semantic else "cooccurrence",
                            "weight": round(
                                (VERB_EDGE_WEIGHT if is_semantic else COOCCURRENCE_EDGE_WEIGHT)
                                * max(0.1, min(confidence, 1.0)),
                                4,
                            ),
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
    """LLM-first concept extraction with rule-based fallback.

    Both paths are pushed through :func:`merge_entity_aliases` before they
    leave, so ``"Lattice AI"`` / ``"lattice  ai"`` / ``"지식그래프에서"`` become
    one concept and therefore one node. The merge is deterministic and needs no
    model — the LLM is free to be inconsistent about case and 조사, and the
    graph still gets one entity.
    """
    llm_result = _llm_extract_concepts(text, limit)
    if llm_result:
        return merge_entity_aliases(llm_result, text)[:limit]
    return _extract_concepts_rules(text, limit)


#: Longest a concept may be, in words. A name is a name; five words is already
#: generous for "OpenAI GPT-4o Mini Preview". Past that it is a clause.
_MAX_CONCEPT_WORDS = 5

# Precompiled once: each of these used to be a ``re.findall`` literal inside
# ``_extract_concepts_rules``, which recompiled them on every document.
_RE_BACKTICK = re.compile(r"`([^`]{2,40})`")
_RE_CODE_PUNCT = re.compile(r"[\(\)\[\]{}]")
_RE_DQUOTE = re.compile(r'"([^"]{2,40})"')
_RE_PROPER_MIXED = re.compile(
    r"([A-Z][a-z]{1,20}(?:[ \t]+(?:[A-Z]{2,10}|[A-Z][a-z0-9]{1,20}|\d[\w.]{0,6})){1,3})"
)
_RE_PROPER_CAPS = re.compile(
    r"([A-Z]{2,6}(?:[ \t]+(?:[A-Z]{2,10}|[A-Z][a-z0-9]{1,20})){1,2})"
)
_RE_PROPER_SINGLE = re.compile(
    r"(?<![A-Za-z0-9])([A-Z][A-Za-z0-9]{2,24})(?![A-Za-z0-9])"
)
_RE_SENTENCE_START = re.compile(r"(?:^|(?<=[.!?])\s+)([A-Z][a-z]+)")
_RE_KO_COMPOUND = re.compile(
    r"[가-힣]{2,12}(?:AI|LLM|API|UI|RAG|bot|Bot|기능|모델|서버|에이전트|파이프라인|워크플로)"
)
_RE_KO_PARTICLE = re.compile(
    r"([가-힣]{2,12})"
    r"(?:에서|에게|으로|부터|까지|보다|처럼|한테|은|는|이|가|을|를|의|와|과|에|도|로|만)"
)
_RE_KO_DEFINITION = re.compile(r"([가-힣]{2,12}?)(?:이란|란)(?![가-힣])")
_RE_HYPHEN_ID = re.compile(r"\b([a-zA-Z][a-zA-Z0-9]*(?:-[a-zA-Z0-9.]+)+)\b")
_RE_DECISION = re.compile(r"(결정|확정|하기로|decided|decision)")
_RE_TASK = re.compile(r"(todo|해야|하자|진행|구현|수정|확인|next|task|\[ \])")
_RE_TOPIC_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{2,}|[가-힣]{2,12}")

#: A capitalized sentence opener is not part of the name that follows it.
#: "The Vector Index" and "Unlike Keyword Search" are the sentence's grammar,
#: not the entity's; the lowercase forms already sit in :data:`_CONCEPT_STOP`,
#: and these are the ones that also appear capitalized at a sentence start.
_LEADING_STOPWORDS: frozenset = frozenset(
    {
        "a",
        "an",
        "the",
        "this",
        "that",
        "these",
        "those",
        "and",
        "but",
        "for",
        "with",
        "without",
        "in",
        "on",
        "at",
        "by",
        "from",
        "into",
        "unlike",
        "instead",
        "our",
        "their",
        "its",
        "some",
        "each",
        "every",
        "both",
        "when",
        "while",
        "where",
        "if",
        "as",
        "than",
        "then",
        "so",
        "also",
        "now",
        "here",
        "there",
        "no",
        "not",
        "only",
        "just",
    }
)

#: The character class a term's own edge must not touch. A Latin word ends at
#: the next Latin letter, **not** at the Korean particle glued to it: with a
#: blanket `\w` guard, `"Lattice AI"` never matches inside `"Lattice AI는"` and
#: the containment de-duplication silently keeps `"Lattice"` as a second node.
_ASCII_EDGE = "A-Za-z0-9_"
_HANGUL_EDGE = "가-힣"


def _edge_class(char: str) -> str:
    """Which character class would continue the word ``char`` ends (or starts)."""
    return _HANGUL_EDGE if "가" <= char <= "힣" else _ASCII_EDGE


def _in_edge_class(char: str, edge: str) -> bool:
    """True when ``char`` would continue a word whose edge class is ``edge``."""
    if edge is _HANGUL_EDGE:
        return "가" <= char <= "힣"
    return (
        ("A" <= char <= "Z")
        or ("a" <= char <= "z")
        or ("0" <= char <= "9")
        or char == "_"
    )


def _fold_len_stable(term: str) -> bool:
    """True when case-folding ``term`` cannot move character offsets.

    ``re.IGNORECASE`` and ``str.lower()`` agree on span starts for these
    terms; anything else (``İ``, ``ß``) falls back to the compiled regex so
    the match list stays byte-identical.
    """
    return len(term) == len(term.lower()) == len(term.upper())


@functools.lru_cache(maxsize=4096)
def _whole_word_re(term: str) -> re.Pattern[str]:
    """The compiled whole-word pattern for ``term`` (cached)."""
    return re.compile(
        f"(?<![{_edge_class(term[0])}])"
        + re.escape(term)
        + f"(?![{_edge_class(term[-1])}])",
        re.IGNORECASE,
    )


def _drop_leading_stopwords(phrase: str) -> str:
    """``phrase`` without the sentence-grammar words in front of the name.

    Stops before eating the whole phrase: a two-word match that is *entirely*
    stopwords is returned unchanged and refused later by ``_add``.
    """
    words = phrase.split()
    while len(words) > 1 and words[0].lower() in _LEADING_STOPWORDS:
        words = words[1:]
    return " ".join(words)


def _whole_word_spans(
    term: str,
    haystack: str,
    haystack_lower: Optional[str] = None,
) -> List[Any]:
    """``(start, end)`` for every whole-word occurrence of ``term``.

    On case-fold-stable terms this is a ``str.find`` walk with the same
    lookaround rules as the compiled regex; anything whose lower/upper
    length differs (or an empty term) uses the regex so the span list
    cannot drift.
    """
    if not term:
        return []
    if _fold_len_stable(term):
        return _spans_by_find(term, haystack, haystack_lower)
    return [m.span() for m in _whole_word_re(term).finditer(haystack)]


def _spans_by_find(
    term: str,
    haystack: str,
    haystack_lower: Optional[str] = None,
) -> List[Any]:
    """Whole-word spans via case-folded ``find`` — identical to the regex."""
    needle = term.lower()
    hay = haystack_lower if haystack_lower is not None else haystack.lower()
    left_edge = _edge_class(term[0])
    right_edge = _edge_class(term[-1])
    width = len(needle)
    spans: List[Any] = []
    start = 0
    while True:
        idx = hay.find(needle, start)
        if idx < 0:
            return spans
        end = idx + width
        if (idx == 0 or not _in_edge_class(haystack[idx - 1], left_edge)) and (
            end >= len(haystack) or not _in_edge_class(haystack[end], right_edge)
        ):
            spans.append((idx, end))
        start = idx + 1


def _term_is_whole_word_in(short: str, longer: str) -> bool:
    """True when ``short`` occurs as a whole word inside ``longer``."""
    if not short:
        return False
    if _fold_len_stable(short):
        return bool(_spans_by_find(short, longer))
    return _whole_word_re(short).search(longer) is not None


def _always_inside(short: str, longer: List[str], spans: Dict[str, List[Any]]) -> bool:
    """True when every occurrence of ``short`` sits inside a longer concept.

    Span containment rather than a per-term count, because a short term can be
    covered by *different* longer terms in different places — "Vector" is
    inside "Vector Index" here and "Vector Search" there, and counting against
    one long term at a time would keep it as a third, meaningless node.

    ``spans`` is precomputed once per document by the caller. Scanning the text
    again per (short, long) pair is quadratic in the *candidate count* times
    linear in the document, which on a 100 KB file took minutes; the same
    answer from cached spans is interval arithmetic over a few dozen tuples.
    """
    hits = spans.get(short) or []
    if not hits:
        return False
    covers = [span for term in longer for span in spans.get(term) or []]
    return all(
        any(start >= c_start and end <= c_end for c_start, c_end in covers)
        for start, end in hits
    )


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
        # A candidate that crosses a line, or runs past a handful of words, is
        # a quoted *passage* the backtick and quote rules swept up — the graph
        # was storing whole sentences as entity names.
        if "\n" in term or len(term.split()) > _MAX_CONCEPT_WORDS:
            return
        key = term.strip().lower()
        if key and key not in _CONCEPT_STOP and not key.isdigit() and len(key) >= 2:
            seen.setdefault(key, term.strip())

    # 1. Backtick-quoted code/term (highest confidence)
    for m in _RE_BACKTICK.findall(text):
        if not _RE_CODE_PUNCT.search(m):  # skip code expressions
            _add(m)

    # 2. Double/single quoted terms
    for m in _RE_DQUOTE.findall(text):
        _add(m)

    # 3. Multi-word English proper nouns (Title Case or ALL-CAPS first word, 2–4 words).
    #    The inner separator is `[ \t]+`, not `\s+`: `\s` crosses newlines, so a
    #    heading glued to the next line's first word produced phantom concepts
    #    like "Retrieval Lattice AI" — and, worse, consumed the "Lattice AI"
    #    the next pass would have found (regex scanning does not overlap).
    #    Pattern A: Mixed-case first word — "Lattice AI", "Tool Use", "Graph RAG"
    for m in _RE_PROPER_MIXED.findall(text):
        _add(_drop_leading_stopwords(m))
    #    Pattern B: ALL-CAPS first word — "VS Code", "MCP Server", "GPT-4o Mini"
    for m in _RE_PROPER_CAPS.findall(text):
        _add(_drop_leading_stopwords(m))

    # 4. Single capitalized proper noun.
    #    Use ASCII-boundary lookaround instead of \b so Korean particles
    #    (와, 의, 는 …) after an English word don't block the match.
    all_caps_words = _RE_PROPER_SINGLE.findall(text)
    freq: Dict[str, int] = {}
    for w in all_caps_words:
        freq[w] = freq.get(w, 0) + 1
    sentence_starts = set(_RE_SENTENCE_START.findall(text))
    for m, cnt in freq.items():
        if m.lower() in _CONCEPT_STOP:
            continue
        if cnt >= 2 or m not in sentence_starts:
            _add(m)

    # 5. Korean technical compound nouns (3–12 chars, no common particles)
    for m in _RE_KO_COMPOUND.findall(text):
        _add(m)
    # Korean standalone terms that appear before a particle. Multi-syllable
    # particles come first in the alternation: the group is greedy, so
    # "지식그래프에서" has to be offered 에서 before 에 or the leftover 서
    # blocks the match entirely.
    ko_stems = _RE_KO_PARTICLE.findall(text)
    ko_counts: Dict[str, int] = {}
    for m in ko_stems:
        if m.lower() not in _CONCEPT_STOP and len(m) >= 2:
            if m not in ko_counts:
                ko_counts[m] = text.count(m)
            if len(m) >= 3 or ko_counts[m] >= 2:
                _add(m)

    # 5b. The term a Korean definition sentence is *about*: `지식그래프란 …이다`.
    #     The stem is lazy and the marker must end the word, so `설명이란` keeps
    #     `설명` (the `이` belongs to `이란`) and `의견란에` is left alone
    #     entirely (`란` there is part of the noun, not a marker).
    for m in _RE_KO_DEFINITION.findall(text):
        _add(m)

    # 6. Hyphenated / versioned identifiers (gpt-4o, gemma-4, mlx-vlm)
    for m in _RE_HYPHEN_ID.findall(text):
        if len(m) >= 4:
            _add(m)

    # De-duplicate by containment: drop the shorter term when every occurrence
    # of it in the source text is *inside* a longer concept.
    #
    #   "Lattice" → dropped, every occurrence is "Lattice AI"
    #   "RAG"     → dropped, every occurrence is "Graph RAG"   (new: suffixes)
    #   "Claude"  → kept, it also appears on its own
    #   "Lat"     → kept, "Lattice AI" does not contain it as a whole word
    #
    # Until v12.0.0 this only looked at *prefixes*, so "Graph RAG" and "RAG"
    # both became nodes and the graph answered one question with two entities.
    values = list(seen.values())
    # Which candidates *could* swallow which is decided on the candidate
    # strings alone — matching "Vector" inside "Vector Index" is a few
    # characters of work. Only the terms that survive that filter are then
    # searched for in the document, so a 100 KB file is scanned a handful of
    # times instead of once per candidate pair.
    # The pair filter is a plain substring test before it is a regex one. A
    # long document yields well over a thousand raw candidates, and a regex
    # compiled per *pair* is two million compilations — seconds of work to
    # answer a question `in` answers in nanoseconds for all but a few pairs.
    lowered = [value.lower() for value in values]
    lengths = [len(value) for value in values]
    swallowers: Dict[int, List[int]] = {}
    for i, short in enumerate(values):
        short_l = lowered[i]
        short_n = lengths[i]
        longer = [
            j
            for j, other in enumerate(values)
            if lengths[j] > short_n
            and short_l in lowered[j]
            and _term_is_whole_word_in(short, other)
        ]
        if longer:
            swallowers[i] = longer
    wanted = {
        values[index]
        for i, group in swallowers.items()
        for index in [i, *group]
    }
    text_lower = text.lower() if wanted else ""
    spans = {
        term: _whole_word_spans(term, text, text_lower) for term in wanted
    }

    keep = set(range(len(values)))
    for i, longer in swallowers.items():
        alive = [values[j] for j in longer if j in keep]
        if alive and _always_inside(values[i], alive, spans):
            keep.discard(i)

    final = [values[i] for i in range(len(values)) if i in keep]
    # The surface merge is the last word: whatever the six passes above
    # produced, two spellings of one entity leave as one concept and therefore
    # as one node. Applied after the prefix de-duplication so the two rules
    # compose rather than fight.
    return merge_entity_aliases(final, text)[:limit]


#: `(?<=[.!?\n])\s+|\n{2,}` — the sentence split, compiled so
#: :func:`sentence_offsets` can keep each piece's position.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?\n])\s+|\n{2,}")


def _extract_triples(
    text: str,
    concepts: List[str],
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """LLM-first triple extraction with rule-based fallback."""
    llm_result = _llm_extract_triples(text, concepts, limit)
    if llm_result:
        return llm_result
    return _extract_triples_rules(text, concepts, limit)


def _drop_nested(present: List[Any]) -> List[Any]:
    """Drop a concept whose span sits inside another concept's span.

    ``"RAG"`` found *inside* ``"Graph RAG"`` is not a second participant; it is
    the same eight characters counted twice, and pairing them produces an edge
    from a thing to part of its own name.
    """
    kept: List[Any] = []
    for start, concept in present:
        end = start + len(concept)
        if any(
            other_start <= start and end <= other_start + len(other)
            for other_start, other in present
            if other != concept
        ):
            continue
        kept.append((start, concept))
    return kept


def _candidate_pairs(present: List[Any]) -> List[Any]:
    """``(left, right, adjacent)`` for every pair worth testing, adjacent first.

    Korean puts the verb last and separates subject from object with whatever
    clause it likes — ``하이브리드검색은 키워드검색이 아니라 벡터검색을 쓴다``
    states a relation between the *first* and the *last* concept, and adjacency
    never sees it.
    """
    adjacent = [(present[i], present[i + 1], True) for i in range(len(present) - 1)]
    distant = [
        (present[i], present[j], False)
        for i in range(len(present))
        for j in range(i + 2, len(present))
    ]
    return adjacent + distant


def _extract_triples_rules(
    text: str,
    concepts: List[str],
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Extract (subject, typed-edge, object, context) triples (rule-based).

    Per sentence: try the four syntactic patterns in :mod:`.patterns` first —
    they read *where* each concept sits and what stands between the two, so
    ``A는 B를 사용한다`` and ``B is used by A`` agree on the subject — and fall
    back to the sentence-level co-occurrence classification for pairs no
    pattern claims. The context each triple carries names the markdown section
    the sentence came from, so a reader can see where the fact lives.
    """
    if len(concepts) < 2:
        return []

    spans = heading_spans(text)
    triples: List[Dict[str, Any]] = []
    seen_pairs: set = set()

    for offset, raw_sentence in sentence_offsets(text, _SENTENCE_SPLIT):
        sent = raw_sentence.strip()
        if len(sent) < 8:
            continue
        section = heading_at(spans, offset + raw_sentence.index(sent))

        present = _drop_nested(concept_positions(sent, concepts))
        if len(present) < 2:
            continue

        relation = infer_edge_relation(sent)
        # Enumeration guard (review 2026-07-27 P1 #6): a verb-less sentence
        # listing many concepts is a list, not a set of relations. Verb-backed
        # sentences keep every pair — the verb is the evidence. A *pattern*
        # match is evidence too, so the guard only gates the fallback.
        enumeration = (
            relation["evidence"] == "cooccurrence"
            and len(present) > COOCCURRENCE_CONCEPT_LIMIT
        )

        # Two passes, patterns first. A pattern names *which* concept is the
        # subject; the fallback only knows text order, so once a pattern has
        # spoken for a concept the coarse pairing around it is noise —
        # ``A는 B가 아니라 C를 쓴다`` must not also emit "A 사용함 B".
        pending: List[Any] = []
        claimed: set = set()
        for left, right, adjacent in _candidate_pairs(present):
            triple = typed_relation(sent, left, right, adjacent)
            if triple is None:
                if adjacent:
                    pending.append((left, right))
                continue
            claimed.add(left[1])
            claimed.add(right[1])
            if not _emit(triples, seen_pairs, triple, section, limit):
                return triples[:limit]

        if not enumeration:
            for left, right in pending:
                if left[1] in claimed or right[1] in claimed:
                    continue
                coarse = {
                    "subject": left[1],
                    "relation": relation["relation"],  # verb form (동사)
                    "object": right[1],
                    "context": sent[:240],
                    "evidence": relation["evidence"],
                    "weight": relation["weight"],
                }
                if not _emit(triples, seen_pairs, coarse, section, limit):
                    return triples[:limit]

    return triples


def _emit(
    triples: List[Dict[str, Any]],
    seen_pairs: set,
    triple: Dict[str, Any],
    section: str,
    limit: int,
) -> bool:
    """Append ``triple`` unless its pair is already there. False ⇒ limit hit."""
    # Deduplicate by (subj, obj) regardless of direction for same edge
    pair_key = tuple(
        sorted([str(triple["subject"]).lower(), str(triple["object"]).lower()])
    ) + (triple["relation"],)
    if pair_key in seen_pairs:
        return True
    seen_pairs.add(pair_key)
    triple["context"] = with_section(str(triple["context"]), section)[:240]
    triples.append(triple)
    return len(triples) < limit


def _semantic_items(text: str) -> List[Dict[str, Any]]:
    """Extract explicit decision / task items from text.

    Each item carries the markdown section its line sat in (when there is one)
    so the Task or Decision node knows where in the document it was decided —
    the ingest doors store the whole item dict in ``nodes.raw_json``.
    """
    body = str(text or "")
    spans = heading_spans(body)
    items: List[Dict[str, Any]] = []
    cursor = 0
    for raw_line in body.splitlines():
        section = heading_at(spans, cursor)
        cursor += len(raw_line) + 1
        line = _clean_text(raw_line)
        if len(line) < 6:
            continue
        lowered = line.lower()
        for kind, pattern in (
            ("Decision", _RE_DECISION),
            ("Task", _RE_TASK),
        ):
            if not pattern.search(lowered):
                continue
            item: Dict[str, Any] = {
                "type": kind,
                "title": line[:120],
                "summary": line[:500],
            }
            if section:
                item["section"] = section
            items.append(item)
    return items[:8]


def _topic_candidates(text: str, limit: int = 8) -> List[str]:
    """Return compact keyword candidates for fallback graph search."""
    candidates = _extract_concepts(text, limit=limit)
    if candidates:
        return candidates[:limit]
    seen: Dict[str, str] = {}
    for token in _RE_TOPIC_TOKEN.findall(str(text or "")):
        key = token.lower()
        if key in _CONCEPT_STOP or key.isdigit():
            continue
        seen.setdefault(key, token)
        if len(seen) >= limit:
            break
    return list(seen.values())[:limit]
