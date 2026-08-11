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
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from ..runtime import get_llm_router
from .relations import (
    COOCCURRENCE_CONCEPT_LIMIT,
    COOCCURRENCE_EDGE_WEIGHT,
    VERB_EDGE_WEIGHT,
    infer_edge_relation,
)
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

        relation = infer_edge_relation(sent)
        edge = relation["relation"]
        # Enumeration guard (review 2026-07-27 P1 #6): a verb-less sentence
        # listing many concepts is a list, not a set of relations. Verb-backed
        # sentences keep every pair — the verb is the evidence.
        if (
            relation["evidence"] == "cooccurrence"
            and len(present) > COOCCURRENCE_CONCEPT_LIMIT
        ):
            continue

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
                    "evidence": relation["evidence"],
                    "weight": relation["weight"],
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
