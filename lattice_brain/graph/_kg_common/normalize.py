from __future__ import annotations

import functools
import re
import unicodedata
from typing import Dict, Iterable, List, Sequence, Tuple

MIN_STEM_CHARS = 2

UNCONDITIONAL_PARTICLES: Tuple[str, ...] = (
    "에서는",
    "으로는",
    "에게는",
    "로부터",
    "이라는",
    "이라고",
    "에서도",
    "으로도",
    "에서의",
    "으로서",
    "으로써",
    "에게서",
    "에게도",
    "라고는",
    "만큼은",
    "에서",
    "에게",
    "한테",
    "께서",
    "으로",
    "부터",
    "까지",
    "보다",
    "처럼",
    "만큼",
    "마다",
    "조차",
    "밖에",
    "라는",
    "라고",
    "라도",
    "이나",
    "을",
    "를",
)

EVIDENCE_PARTICLES: Tuple[str, ...] = (
    "은",
    "는",
    "이",
    "가",
    "의",
    "와",
    "과",
    "도",
    "로",
    "만",
    "나",
)

_DELIMITER_PAIRS = {
    "(": ")",
    "[": "]",
    "{": "}",
    "<": ">",
    "«": "»",
    "“": "”",
    "‘": "’",
    "「": "」",
    "『": "』",
    "《": "》",
    "〈": "〉",
    '"': '"',
    "'": "'",
    "`": "`",
}
_TRAILING_PUNCT = ".,;:!?…·~-–—/\\|"

_WHITESPACE = re.compile(r"\s+")
_HANGUL = re.compile(r"[가-힣]")
_POSSESSIVE = re.compile(r"(?:['’]s|['’])$", re.IGNORECASE)


def _is_hangul_tail(text: str) -> bool:
    return bool(text) and bool(_HANGUL.fullmatch(text[-1]))


def strip_particle(term: str, text: str = "") -> str:
    term = term.strip()
    if not _is_hangul_tail(term):
        return term
    for particle in UNCONDITIONAL_PARTICLES:
        if term.endswith(particle):
            stem = term[: -len(particle)]
            if len(stem) >= MIN_STEM_CHARS and _is_hangul_tail(stem):
                return stem
    if not text:
        return term
    for particle in EVIDENCE_PARTICLES:
        if not term.endswith(particle):
            continue
        stem = term[: -len(particle)]
        if len(stem) < MIN_STEM_CHARS or not _is_hangul_tail(stem):
            continue
        if _stem_stands_alone(stem, particle, text):
            return stem
    return term


@functools.lru_cache(maxsize=8192)
def _stem_alone_re(stem: str, particle: str) -> re.Pattern[str]:
    return re.compile(re.escape(stem) + "(?!" + re.escape(particle) + ")")


def _stem_stands_alone(stem: str, particle: str, text: str) -> bool:
    return _stem_alone_re(stem, particle).search(text) is not None


def normalize_entity(term: str, text: str = "") -> str:
    cleaned = unicodedata.normalize("NFKC", str(term or ""))
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    for _ in range(3):
        before = cleaned
        if len(cleaned) > 2 and cleaned.endswith(
            _DELIMITER_PAIRS.get(cleaned[0], "\0")
        ):
            cleaned = cleaned[1:-1].strip()
        cleaned = cleaned.rstrip(_TRAILING_PUNCT).strip()
        if cleaned == before:
            break
    if not cleaned:
        return ""
    cleaned = _POSSESSIVE.sub("", cleaned).strip()
    return strip_particle(cleaned, text)


def entity_key(term: str) -> str:
    folded = unicodedata.normalize("NFKC", str(term or "")).casefold()
    folded = _WHITESPACE.sub(" ", folded).strip()
    return re.sub(r"[\s_\-]+", "", folded)


def _representative(surfaces: Sequence[str], text: str) -> str:
    best = surfaces[0]
    best_rank = (-1, -1, -1, 0)
    for index, surface in enumerate(surfaces):
        rank = (
            text.count(surface) if text else 0,
            sum(1 for word in surface.split() if word[:1].isupper()),
            len(surface),
            -index,
        )
        if rank > best_rank:
            best_rank = rank
            best = surface
    return best


def merge_entity_aliases(terms: Iterable[str], text: str = "") -> List[str]:
    groups: Dict[str, List[str]] = {}
    order: List[str] = []
    for term in terms:
        surface = normalize_entity(term, text)
        if not surface:
            continue
        key = entity_key(surface)
        if not key:
            continue
        if key not in groups:
            groups[key] = []
            order.append(key)
        if surface not in groups[key]:
            groups[key].append(surface)
    return [_representative(groups[key], text) for key in order]


def occurrence_count(term: str, text: str) -> int:
    if not term or not text:
        return 0
    return len(re.findall(re.escape(term), text, re.IGNORECASE))


__all__ = [
    "EVIDENCE_PARTICLES",
    "MIN_STEM_CHARS",
    "UNCONDITIONAL_PARTICLES",
    "entity_key",
    "merge_entity_aliases",
    "normalize_entity",
    "occurrence_count",
    "strip_particle",
]
