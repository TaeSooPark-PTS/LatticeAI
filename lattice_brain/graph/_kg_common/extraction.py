from __future__ import annotations

import asyncio
import functools
import http.client
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple


def _llm_extraction_errors() -> Tuple[type[BaseException], ...]:
    errors: List[type[BaseException]] = [
        RuntimeError,
        OSError,
        ValueError,
        TypeError,
        TimeoutError,
        KeyError,
        AttributeError,
        http.client.HTTPException,
    ]
    for mod_name, attr in (("httpx", "HTTPError"), ("jinja2", "TemplateError")):
        try:
            mod = __import__(mod_name, fromlist=[attr])
            err_cls = getattr(mod, attr, None)
            if isinstance(err_cls, type) and issubclass(err_cls, Exception):
                errors.append(err_cls)
        except ImportError:
            pass
    return tuple(errors)


_LLM_EXTRACTION_ERRORS = _llm_extraction_errors()

from ..runtime import get_llm_router
from .extraction_triples import (
    _RE_DECISION as _RE_DECISION,
)
from .extraction_triples import (
    _RE_TASK as _RE_TASK,
)
from .extraction_triples import (
    _SENTENCE_SPLIT as _SENTENCE_SPLIT,
)
from .extraction_triples import (
    _candidate_pairs as _candidate_pairs,
)
from .extraction_triples import (
    _drop_nested as _drop_nested,
)
from .extraction_triples import (
    _emit as _emit,
)
from .extraction_triples import (
    _extract_triples_rules,
)
from .extraction_triples import (
    _semantic_items as _semantic_items,
)
from .normalize import merge_entity_aliases
from .patterns import (
    concept_positions as concept_positions,
)
from .patterns import (
    typed_relation as typed_relation,
)
from .relations import (
    COOCCURRENCE_CONCEPT_LIMIT as COOCCURRENCE_CONCEPT_LIMIT,
)
from .relations import (
    COOCCURRENCE_EDGE_WEIGHT,
    VERB_EDGE_WEIGHT,
)
from .relations import (
    infer_edge_relation as infer_edge_relation,
)
from .sections import (
    heading_at as heading_at,
)
from .sections import (
    heading_spans as heading_spans,
)
from .sections import (
    sentence_offsets as sentence_offsets,
)
from .sections import (
    with_section as with_section,
)
from .text import (
    _clean_text as _clean_text,
)

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
            raw = asyncio.run(router.generate(prompt, max_tokens=1024, temperature=0.1))
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
    except _LLM_EXTRACTION_ERRORS as e:
        logging.debug("LLM concept extraction failed (falling back to rules): %s", e)
    return None


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
            raw = asyncio.run(router.generate(prompt, max_tokens=2048, temperature=0.1))
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
                                (
                                    VERB_EDGE_WEIGHT
                                    if is_semantic
                                    else COOCCURRENCE_EDGE_WEIGHT
                                )
                                * max(0.1, min(confidence, 1.0)),
                                4,
                            ),
                        }
                    )
            return triples if triples else None
    except _LLM_EXTRACTION_ERRORS as e:
        logging.debug("LLM triple extraction failed (falling back to rules): %s", e)
    return None


_CONCEPT_STOP: set = {
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
    llm_result = _llm_extract_concepts(text, limit)
    if llm_result:
        return merge_entity_aliases(llm_result, text)[:limit]
    return _extract_concepts_rules(text, limit)


_MAX_CONCEPT_WORDS = 5

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
_RE_TOPIC_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{2,}|[가-힣]{2,12}")

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

_ASCII_EDGE = "A-Za-z0-9_"
_HANGUL_EDGE = "가-힣"


def _edge_class(char: str) -> str:
    return _HANGUL_EDGE if "가" <= char <= "힣" else _ASCII_EDGE


def _in_edge_class(char: str, edge: str) -> bool:
    if edge is _HANGUL_EDGE:
        return "가" <= char <= "힣"
    return (
        ("A" <= char <= "Z")
        or ("a" <= char <= "z")
        or ("0" <= char <= "9")
        or char == "_"
    )


def _fold_len_stable(term: str) -> bool:
    return len(term) == len(term.lower()) == len(term.upper())


@functools.lru_cache(maxsize=4096)
def _whole_word_re(term: str) -> re.Pattern[str]:
    return re.compile(
        f"(?<![{_edge_class(term[0])}])"
        + re.escape(term)
        + f"(?![{_edge_class(term[-1])}])",
        re.IGNORECASE,
    )


def _drop_leading_stopwords(phrase: str) -> str:
    words = phrase.split()
    while len(words) > 1 and words[0].lower() in _LEADING_STOPWORDS:
        words = words[1:]
    return " ".join(words)


def _whole_word_spans(
    term: str,
    haystack: str,
    haystack_lower: Optional[str] = None,
) -> List[Any]:
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
    if not short:
        return False
    if _fold_len_stable(short):
        return bool(_spans_by_find(short, longer))
    return _whole_word_re(short).search(longer) is not None


def _always_inside(short: str, longer: List[str], spans: Dict[str, List[Any]]) -> bool:
    hits = spans.get(short) or []
    if not hits:
        return False
    covers = [span for term in longer for span in spans.get(term) or []]
    return all(
        any(start >= c_start and end <= c_end for c_start, c_end in covers)
        for start, end in hits
    )


def _add_rule_concept(seen: Dict[str, str], term: str) -> None:
    if "\n" in term or len(term.split()) > _MAX_CONCEPT_WORDS:
        return
    key = term.strip().lower()
    if key and key not in _CONCEPT_STOP and not key.isdigit() and len(key) >= 2:
        seen.setdefault(key, term.strip())


def _collect_rule_concept_candidates(text: str) -> List[str]:
    seen: Dict[str, str] = {}

    for m in _RE_BACKTICK.findall(text):
        if not _RE_CODE_PUNCT.search(m):
            _add_rule_concept(seen, m)

    for m in _RE_DQUOTE.findall(text):
        _add_rule_concept(seen, m)

    for m in _RE_PROPER_MIXED.findall(text):
        _add_rule_concept(seen, _drop_leading_stopwords(m))
    for m in _RE_PROPER_CAPS.findall(text):
        _add_rule_concept(seen, _drop_leading_stopwords(m))

    freq: Dict[str, int] = {}
    for w in _RE_PROPER_SINGLE.findall(text):
        freq[w] = freq.get(w, 0) + 1
    sentence_starts = set(_RE_SENTENCE_START.findall(text))
    for m, cnt in freq.items():
        if m.lower() not in _CONCEPT_STOP and (cnt >= 2 or m not in sentence_starts):
            _add_rule_concept(seen, m)

    _collect_korean_rule_concepts(text, seen)

    for m in _RE_HYPHEN_ID.findall(text):
        if len(m) >= 4:
            _add_rule_concept(seen, m)

    return list(seen.values())


def _collect_korean_rule_concepts(text: str, seen: Dict[str, str]) -> None:
    for m in _RE_KO_COMPOUND.findall(text):
        _add_rule_concept(seen, m)
    ko_counts: Dict[str, int] = {}
    for m in _RE_KO_PARTICLE.findall(text):
        if m.lower() not in _CONCEPT_STOP and len(m) >= 2:
            if m not in ko_counts:
                ko_counts[m] = text.count(m)
            if len(m) >= 3 or ko_counts[m] >= 2:
                _add_rule_concept(seen, m)

    for m in _RE_KO_DEFINITION.findall(text):
        _add_rule_concept(seen, m)


def _deduplicate_contained_concepts(values: List[str], text: str) -> List[str]:
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
    wanted = {values[index] for i, group in swallowers.items() for index in [i, *group]}
    text_lower = text.lower() if wanted else ""
    spans = {term: _whole_word_spans(term, text, text_lower) for term in wanted}

    keep = set(range(len(values)))
    for i, longer in swallowers.items():
        alive = [values[j] for j in longer if j in keep]
        if alive and _always_inside(values[i], alive, spans):
            keep.discard(i)

    return [values[i] for i in range(len(values)) if i in keep]


def _extract_concepts_rules(text: str, limit: int = 12) -> List[str]:
    text = str(text or "")
    values = _collect_rule_concept_candidates(text)
    final = _deduplicate_contained_concepts(values, text)
    return merge_entity_aliases(final, text)[:limit]


def _extract_triples(
    text: str,
    concepts: List[str],
    limit: int = 20,
) -> List[Dict[str, Any]]:
    llm_result = _llm_extract_triples(text, concepts, limit)
    if llm_result:
        return llm_result
    return _extract_triples_rules(text, concepts, limit)


def _topic_candidates(text: str, limit: int = 8) -> List[str]:
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
