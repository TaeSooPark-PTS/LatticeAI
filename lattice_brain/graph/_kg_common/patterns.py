"""Directed, typed relation patterns — the part of extraction that reads syntax.

``infer_edge_relation`` classifies a *sentence*: it finds a verb anywhere in it
and stamps every concept pair in that sentence with the same label, in text
order. That is cheap and it is often wrong about **direction**, and it cannot
tell a definition from a passing mention.

The four rules here look at where each concept sits inside the sentence and at
what stands *between* the two, so ``A는 B를 사용한다`` and ``B is used by A``
come out with the same subject. Each rule returns the relation label the graph
should carry, the evidence class, and a weight:

| rule | label | `edges_v2` type | evidence | weight |
|---|---|---|---|---|
| definition | `설명함` | `MENTIONS` | `definition` | 1.0 |
| SVO / SOV | the matched `EDGE_VERB` label | that label's type | `verb` | 1.0 |
| part-of | `구성요소` | `PART_OF` | `structure` | 0.9 |
| contrast | `상충함` | `CONTRADICTS` | `contrast` | 0.9 |

``PART_OF`` and ``CONTRADICTS`` were reachable in the taxonomy but nothing
extracted them; the graph only ever produced the ten labels `EDGE_VERB` names.

Everything is regex over a single sentence — deterministic, no model, and the
same input always yields the same edge.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .relations import _EDGE_VERB_COMPILED

#: Weight for a relation a syntactic pattern named outright.
PATTERN_EDGE_WEIGHT = 0.9
#: Weight for definition and verb-anchored relations — the strongest evidence.
STRONG_EDGE_WEIGHT = 1.0

#: `A is a B` / `A refers to B` — the copula and its cousins, English.
_EN_DEFINITION = re.compile(
    r"^\s*(?:is|are|was|were)\s+(?:a|an|the)?\s*$"
    r"|^\s*(?:refers?\s+to|means?|stands\s+for|is\s+defined\s+as|is\s+known\s+as"
    r"|is\s+short\s+for)\s*$",
    re.IGNORECASE,
)
#: `A란 B이다` / `A는 B를 의미한다` — the Korean definition tail.
_KO_DEFINITION_TAIL = re.compile(
    r"(?:이다|입니다|이란다|이에요|예요|을 뜻한다|를 뜻한다|을 의미한다|를 의미한다"
    r"|을 말한다|를 말한다|이라고 한다|라고 한다|이라 한다)\s*[.!?]?\s*$"
)
#: `A란`, `A이란`, `A라는 것은` — the Korean definition head marker. The
#: lookbehind and the trailing space keep the bare ``란`` from matching the
#: middle of an ordinary word (``결과란에``, ``발란스``).
_KO_DEFINITION_HEAD = re.compile(
    r"(?<=[가-힣])(?:이란|란|라는 것은|라 함은)\s|(?:의 정의는|정의는)\s"
)

#: `A is part of B` — part first, whole second.
_EN_PART_FORWARD = re.compile(
    r"^\s*(?:is|are|was|were)?\s*(?:a|an|the)?\s*"
    r"(?:part\s+of|component\s+of|subset\s+of|member\s+of|belongs?\s+to"
    r"|lives?\s+(?:in|under)|sits?\s+(?:in|under))\s*$",
    re.IGNORECASE,
)
#: `B consists of A` — whole first, part second, so the edge is reversed.
#: ``contains``/``includes`` are deliberately **not** here: they already route
#: to `포함함` → `CONTAINS`, which is the right edge pointing the right way.
_EN_PART_REVERSE = re.compile(
    r"^\s*(?:consists?\s+of|comprises?|is\s+made\s+(?:up\s+)?of)\s*$",
    re.IGNORECASE,
)
#: `A는 B의 일부` / `A는 B에 속한다` — part first, whole second.
_KO_PART_FORWARD = re.compile(r"(?:의 일부|의 구성요소|의 하위|에 속한|에 포함된|의 부분)")
#: `A는 B로 구성된다` — whole first, part second.
_KO_PART_REVERSE = re.compile(r"(?:로 구성|으로 구성|의 하위 항목)")

#: `A unlike B` / `A instead of B` — a stated opposition, not a comparison.
_EN_CONTRAST = re.compile(
    r"(?:\bunlike\b|\binstead\s+of\b|\brather\s+than\b|\bcontrary\s+to\b"
    r"|\bas\s+opposed\s+to\b|\bnot\b[^.]{0,20}\bbut\b)",
    re.IGNORECASE,
)
#: `A가 아니라 B` / `A 대신 B` / `A와 달리 B`.
_KO_CONTRAST = re.compile(r"(?:아니라|아닌|대신|와 달리|과 달리|반면|이 아니고|가 아니고)")

#: Subject markers: the syllable that says "this noun is the actor".
_KO_SUBJECT_MARKS: Tuple[str, ...] = ("은", "는", "이", "가", "께서")
#: Object markers: the syllable that says "this noun is acted on".
_KO_OBJECT_MARKS: Tuple[str, ...] = ("을", "를")


def _marker_after(sentence: str, end: int, markers: Sequence[str]) -> bool:
    """True when one of ``markers`` sits immediately after ``end``.

    Korean glues the particle to the noun with no space, so a single lookahead
    character is the whole test.
    """
    tail = sentence[end : end + 2]
    return any(tail.startswith(mark) for mark in markers)


def _verb_label(span: str) -> Optional[str]:
    """The `EDGE_VERB` label whose pattern matches ``span``, if any."""
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


def concept_positions(
    sentence: str, concepts: Sequence[str]
) -> List[Tuple[int, str]]:
    """``(offset, concept)`` for every concept present, in text order.

    Case-insensitive, first occurrence only — a concept repeated in one
    sentence is one participant, not two.
    """
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
    """The typed, directed relation between two concepts in one sentence.

    ``left``/``right`` are ``(offset, concept)`` with ``left`` first in the
    text. Returns ``None`` when no rule fires, which is the caller's signal to
    fall back to the sentence-level co-occurrence classification.

    ``adjacent=False`` means another concept sits between the two, and then
    only the particle-marked Korean subject→object rule may fire. Everything
    else reads the span *between* the pair, and with a third concept in there
    that span describes somebody else's relation: ``A는 B가 아니라 C를 쓴다``
    puts ``아니라`` between A and C without A and C being in contrast at all.
    """
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
    return _triple(
        subject, obj, "설명함", "definition", STRONG_EDGE_WEIGHT, sentence
    )


def _part_of(
    between: str, after: str, subject: str, obj: str, sentence: str
) -> Optional[Dict[str, Any]]:
    # English states the relation *between* the two ("A is part of B"); Korean
    # glues it to the second one ("A는 B의 일부이다"), so the tail is read too —
    # anchored at position zero, because the marker belongs to the noun it is
    # stuck to. A match further along the tail is some *other* noun's relation.
    if _EN_PART_FORWARD.match(between) or _KO_PART_FORWARD.match(after):
        return _triple(
            subject, obj, "구성요소", "structure", PATTERN_EDGE_WEIGHT, sentence
        )
    if _EN_PART_REVERSE.match(between) or _KO_PART_REVERSE.match(after):
        # `B consists of A` — the *whole* was named first, so the part-of edge
        # points the other way. Direction is the whole point of this module.
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
    """A verb that sits *between* the pair (SVO) or *after* it (Korean SOV).

    English puts the verb between subject and object, so ``between`` naming a
    verb is enough. Korean puts it last: the direction comes from the particles
    instead — ``A는 … B를 사용한다`` marks A as subject and B as object, and the
    verb in the tail names the relation.
    """
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
    """``A는 … B를 <verb>`` — the particles decide, the tail verb names it."""
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
