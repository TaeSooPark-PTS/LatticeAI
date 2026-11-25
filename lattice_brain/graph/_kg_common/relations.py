from __future__ import annotations

import re
from typing import Any, Dict

EDGE_VERB = {
    "언급함": r"언급|mention|refer|cited",
    "포함함": r"포함|include|consist|구성|탑재|contains",
    "해결함": r"해결|resolv|fix|수정|고쳤|closed",
    "의존함": r"의존|depend|require|필요|based on",
    "설명함": r"설명|explain|describe|정의|란|이란|means",
    "비교함": r"비교|versus|vs\.?|차이|다르|compare",
    "사용함": r"사용|use|활용|이용|apply|쓴다|쓰는|씁니다|썼다",
    "연결함": r"연결|connect|통합|integrate|연동|link",
    "확장함": r"확장|extend|플러그인|plugin|addon",
    "생성함": r"생성|만들|create|generate|build|produced",
    "대체함": r"대체|replace|instead|alternative",
    "지원함": r"지원|support|제공|provide|offer",
    "발생함": r"발생|occur|throw|raise|triggered",
    "관련됨": r"관련|related|associated|연관",
}

_EDGE_VERB_COMPILED = tuple(
    (label, re.compile(pattern)) for label, pattern in EDGE_VERB.items()
)


COOCCURRENCE_CONCEPT_LIMIT = 4
VERB_EDGE_WEIGHT = 1.0
COOCCURRENCE_EDGE_WEIGHT = 0.35


def infer_edge_relation(sentence: str) -> Dict[str, Any]:
    s = str(sentence or "").lower()
    for label, pattern in _EDGE_VERB_COMPILED:
        if pattern.search(s):
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
    return infer_edge_relation(sentence)["relation"]


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
    term = concept.lower()

    if re.search(r"(?:error|exception|traceback|오류|에러|버그)$", term, re.I):
        return "Error"
    if re.search(r"error|exception|err\b", term, re.I) and len(concept) < 30:
        return "Error"
    if re.search(r"\(\)|\.py$|\.js$|\.ts$|\.go$|::\w", term):
        return "Code"

    if re.match(r"^[A-Z][a-z]{1,15} [A-Z][a-z]{1,15}$", concept):
        words = term.split()
        if not any(w in _NOT_PERSON_WORDS for w in words):
            return "Person"

    idx = text.lower().find(term)
    if idx >= 0:
        win = text[max(0, idx - 60) : idx + len(concept) + 60].lower()
        if re.search(r"def |class |function|함수|클래스|메서드|import", win):
            return "Code"
        if len(concept) <= 12 and re.search(
            rf"{re.escape(term)}.{{0,8}}(?:기능|feature)|(?:기능|feature).{{0,8}}{re.escape(term)}",
            win,
        ):
            return "Feature"

    return "Concept"
