from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .relations import _EDGE_VERB_COMPILED

PATTERN_EDGE_WEIGHT = 0.9
STRONG_EDGE_WEIGHT = 1.0

_EN_DEFINITION = re.compile(
    r"^\s*(?:is|are|was|were)\s+(?:a|an|the)?\s*$"
    r"|^\s*(?:refers?\s+to|means?|stands\s+for|is\s+defined\s+as|is\s+known\s+as"
    r"|is\s+short\s+for)\s*$",
    re.IGNORECASE,
)
_KO_DEFINITION_TAIL = re.compile(
    r"(?:이다|입니다|이란다|이에요|예요|을 뜻한다|를 뜻한다|을 의미한다|를 의미한다"
    r"|을 말한다|를 말한다|이라고 한다|라고 한다|이라 한다)\s*[.!?]?\s*$"
)
_KO_DEFINITION_HEAD = re.compile(
    r"(?<=[가-힣])(?:이란|란|라는 것은|라 함은)\s|(?:의 정의는|정의는)\s"
)

_EN_PART_FORWARD = re.compile(
    r"^\s*(?:is|are|was|were)?\s*(?:a|an|the)?\s*"
    r"(?:part\s+of|component\s+of|subset\s+of|member\s+of|belongs?\s+to"
    r"|lives?\s+(?:in|under)|sits?\s+(?:in|under))\s*$",
    re.IGNORECASE,
)
_EN_PART_REVERSE = re.compile(
    r"^\s*(?:consists?\s+of|comprises?|is\s+made\s+(?:up\s+)?of)\s*$",
    re.IGNORECASE,
)
_KO_PART_FORWARD = re.compile(
    r"(?:의 일부|의 구성요소|의 하위|에 속한|에 포함된|의 부분)"
)
_KO_PART_REVERSE = re.compile(r"(?:로 구성|으로 구성|의 하위 항목)")

_EN_CONTRAST = re.compile(
    r"(?:\bunlike\b|\binstead\s+of\b|\brather\s+than\b|\bcontrary\s+to\b"
    r"|\bas\s+opposed\s+to\b|\bnot\b[^.]{0,20}\bbut\b)",
    re.IGNORECASE,
)
_KO_CONTRAST = re.compile(
    r"(?:아니라|아닌|대신|와 달리|과 달리|반면|이 아니고|가 아니고)"
)

_KO_SUBJECT_MARKS: Tuple[str, ...] = ("은", "는", "이", "가", "께서")
_KO_OBJECT_MARKS: Tuple[str, ...] = ("을", "를")


def _marker_after(sentence: str, end: int, markers: Sequence[str]) -> bool:
    tail = sentence[end : end + 2]
    return any(tail.startswith(mark) for mark in markers)


def _verb_label(span: str) -> Optional[str]:
    lowered = span.lower()
    for label, pattern in _EDGE_VERB_COMPILED:
        if pattern.search(lowered):
            return label
    return None


def _triple(
    subject: str,
    obj: str,
    relation: str,
    evidence: str,
    weight: float,
    context: str,
) -> Dict[str, Any]:
    return {
        "subject": subject,
        "relation": relation,
        "object": obj,
        "context": context[:240],
        "evidence": evidence,
        "weight": weight,
    }


def concept_positions(sentence: str, concepts: Sequence[str]) -> List[Tuple[int, str]]:
    lowered = sentence.lower()
    found: List[Tuple[int, str]] = []
    for concept in concepts:
        index = lowered.find(concept.lower())
        if index >= 0:
            found.append((index, concept))
    found.sort(key=lambda pair: (pair[0], pair[1]))
    return found


def typed_relation(
    sentence: str,
    left: Tuple[int, str],
    right: Tuple[int, str],
    adjacent: bool = True,
) -> Optional[Dict[str, Any]]:
    left_at, subject = left
    right_at, obj = right
    between = sentence[left_at + len(subject) : right_at]
    after = sentence[right_at + len(obj) :]

    if not adjacent:
        return _korean_subject_object(sentence, subject, obj, after, left_at, right_at)

    definition = _definition(sentence, between, subject, obj)
    if definition is not None:
        return definition
    part_of = _part_of(between, after, subject, obj, sentence)
    if part_of is not None:
        return part_of
    contrast = _contrast(between, subject, obj, sentence)
    if contrast is not None:
        return contrast
    return _verb_anchored(sentence, subject, obj, between, after, left_at, right_at)


def _definition(
    sentence: str, between: str, subject: str, obj: str
) -> Optional[Dict[str, Any]]:
    english = _EN_DEFINITION.match(between)
    korean = bool(_KO_DEFINITION_TAIL.search(sentence)) and bool(
        _KO_DEFINITION_HEAD.search(sentence)
    )
    if not english and not korean:
        return None
    return _triple(subject, obj, "설명함", "definition", STRONG_EDGE_WEIGHT, sentence)


def _part_of(
    between: str, after: str, subject: str, obj: str, sentence: str
) -> Optional[Dict[str, Any]]:
    if _EN_PART_FORWARD.match(between) or _KO_PART_FORWARD.match(after):
        return _triple(
            subject, obj, "구성요소", "structure", PATTERN_EDGE_WEIGHT, sentence
        )
    if _EN_PART_REVERSE.match(between) or _KO_PART_REVERSE.match(after):
        return _triple(
            obj, subject, "구성요소", "structure", PATTERN_EDGE_WEIGHT, sentence
        )
    return None


def _contrast(
    between: str, subject: str, obj: str, sentence: str
) -> Optional[Dict[str, Any]]:
    if _EN_CONTRAST.search(between) or _KO_CONTRAST.search(between):
        return _triple(
            subject, obj, "상충함", "contrast", PATTERN_EDGE_WEIGHT, sentence
        )
    return None


def _verb_anchored(
    sentence: str,
    subject: str,
    obj: str,
    between: str,
    after: str,
    left_at: int,
    right_at: int,
) -> Optional[Dict[str, Any]]:
    label = _verb_label(between)
    if label is not None:
        return _triple(subject, obj, label, "verb", STRONG_EDGE_WEIGHT, sentence)
    return _korean_subject_object(sentence, subject, obj, after, left_at, right_at)


def _korean_subject_object(
    sentence: str,
    subject: str,
    obj: str,
    after: str,
    left_at: int,
    right_at: int,
) -> Optional[Dict[str, Any]]:
    subject_marked = _marker_after(sentence, left_at + len(subject), _KO_SUBJECT_MARKS)
    object_marked = _marker_after(sentence, right_at + len(obj), _KO_OBJECT_MARKS)
    if not (subject_marked and object_marked):
        return None
    tail_label = _verb_label(after)
    if tail_label is None:
        return None
    return _triple(subject, obj, tail_label, "verb", STRONG_EDGE_WEIGHT, sentence)


__all__ = [
    "PATTERN_EDGE_WEIGHT",
    "STRONG_EDGE_WEIGHT",
    "concept_positions",
    "typed_relation",
]
