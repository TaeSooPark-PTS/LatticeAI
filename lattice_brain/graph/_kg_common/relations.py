"""Edge-relation vocabulary and node-type classification.

The verb table (``EDGE_VERB``), the evidence weights that tell a semantic
relation from bare co-occurrence, and the concept → node-type classifier.
Moved verbatim out of ``_kg_common`` (v11.3.0 decomposition); depends on
nothing else in the package.
"""

from __future__ import annotations

import re
from typing import Any, Dict

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
    # `쓴다`/`쓰는`/`씁니다` are the everyday Korean for "uses"; without them a
    # sentence that never says 사용 fell through to bare co-occurrence.
    "사용함": r"사용|use|활용|이용|apply|쓴다|쓰는|씁니다|썼다",
    "연결함": r"연결|connect|통합|integrate|연동|link",
    "확장함": r"확장|extend|플러그인|plugin|addon",
    "생성함": r"생성|만들|create|generate|build|produced",
    "대체함": r"대체|replace|instead|alternative",
    "지원함": r"지원|support|제공|provide|offer",
    "발생함": r"발생|occur|throw|raise|triggered",
    "관련됨": r"관련|related|associated|연관",
}

#: Same table, compiled once. ``infer_edge_relation`` and the typed-relation
#: patterns both walk this on every pair; compiling per call was the cheap
#: half of the extraction regression.
_EDGE_VERB_COMPILED = tuple(
    (label, re.compile(pattern)) for label, pattern in EDGE_VERB.items()
)


# Concepts in a list-like sentence ("A, B, C, D를 사용한다") sit together by
# enumeration, not by relation. Beyond this many concepts in one sentence, a
# verb-less pairing is enumeration noise and is dropped outright.
COOCCURRENCE_CONCEPT_LIMIT = 4
# Verb-backed relations carry the sentence's own evidence; co-occurrence
# relations carry only adjacency, so they enter the graph at a lower weight
# and are labelled as such.
VERB_EDGE_WEIGHT = 1.0
COOCCURRENCE_EDGE_WEIGHT = 0.35


def infer_edge_relation(sentence: str) -> Dict[str, Any]:
    """Classify the relation between two concepts in one sentence.

    Review 2026-07-27 P1 #6: the graph drifted toward co-occurrence because a
    verb-less sentence still produced a "관련됨" edge indistinguishable from a
    real semantic relation. The label alone cannot carry that difference, so
    the evidence class rides with it::

        {"relation": "사용함", "evidence": "verb",         "weight": 1.0}
        {"relation": "관련됨", "evidence": "cooccurrence", "weight": 0.35}

    ``evidence`` is what the graph, the curator, and the UI use to tell a
    meaning edge from an adjacency edge — the honest distinction the previous
    label-only output erased.
    """
    s = str(sentence or "").lower()
    for label, pattern in _EDGE_VERB_COMPILED:
        if pattern.search(s):
            # "관련됨" is itself a weak, generic label: matching it by keyword
            # ("관련", "related") is still verb evidence, but nothing stronger.
            return {
                "relation": label,
                "evidence": "verb",
                "weight": VERB_EDGE_WEIGHT,
            }
    return {
        "relation": "관련됨",
        "evidence": "cooccurrence",
        "weight": COOCCURRENCE_EDGE_WEIGHT,
    }


def _infer_edge(sentence: str) -> str:
    """Back-compat wrapper: the verb label only (see :func:`infer_edge_relation`)."""
    return infer_edge_relation(sentence)["relation"]


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
