from __future__ import annotations

import re
from typing import Any, Dict, List

from .patterns import concept_positions, typed_relation
from .relations import COOCCURRENCE_CONCEPT_LIMIT, infer_edge_relation
from .sections import heading_at, heading_spans, sentence_offsets, with_section
from .text import _clean_text

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?\n])\s+|\n{2,}")

_RE_DECISION = re.compile(r"(결정|확정|하기로|decided|decision)")
_RE_TASK = re.compile(r"(todo|해야|하자|진행|구현|수정|확인|next|task|\[ \])")


def _drop_nested(present: List[Any]) -> List[Any]:
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
    adjacent = [(present[i], present[i + 1], True) for i in range(len(present) - 1)]
    distant = [
        (present[i], present[j], False)
        for i in range(len(present))
        for j in range(i + 2, len(present))
    ]
    return adjacent + distant


def _emit(
    triples: List[Dict[str, Any]],
    seen_pairs: set,
    triple: Dict[str, Any],
    section: str,
    limit: int,
) -> bool:
    pair_key = tuple(
        sorted([str(triple["subject"]).lower(), str(triple["object"]).lower()])
    ) + (triple["relation"],)
    if pair_key in seen_pairs:
        return True
    seen_pairs.add(pair_key)
    triple["context"] = with_section(str(triple["context"]), section)[:240]
    triples.append(triple)
    return len(triples) < limit


def _extract_triples_rules(
    text: str,
    concepts: List[str],
    limit: int = 20,
) -> List[Dict[str, Any]]:
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
        enumeration = (
            relation["evidence"] == "cooccurrence"
            and len(present) > COOCCURRENCE_CONCEPT_LIMIT
        )

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
                    "relation": relation["relation"],
                    "object": right[1],
                    "context": sent[:240],
                    "evidence": relation["evidence"],
                    "weight": relation["weight"],
                }
                if not _emit(triples, seen_pairs, coarse, section, limit):
                    return triples[:limit]

    return triples


def _semantic_items(text: str) -> List[Dict[str, Any]]:
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
