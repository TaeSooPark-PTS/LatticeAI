"""Entity-surface normalization and alias merge — deterministic, no model.

Two concepts that are the same thing must become one node, or the graph
answers "무엇이 무엇과 이어져 있나" with a fan of near-duplicates. Before
v12.0.0 the only dedup was ``concept.text.lower()`` inside the Rust writer, so
``"Lattice AI"`` / ``"lattice  ai"`` / ``"Lattice AI의"`` were three nodes.

This module is the *surface* half of that fix, and it is deliberately boring:
Unicode normalization, whitespace collapse, bracket/quote/punctuation trimming,
English possessives, and Korean 조사 (postposition) stripping. No model, no
network, no randomness — the same input always produces the same node id.

## 조사 stripping, and why it is two tiers

Korean marks grammatical role with a suffix glued to the noun, so the *same*
entity appears as ``플랫폼은`` / ``플랫폼을`` / ``플랫폼에서``. Stripping the
suffix is what merges them. But Korean nouns also legitimately *end* in those
syllables — ``고양이`` ends in ``이``, ``전문가`` in ``가``, ``정확도`` in
``도`` — and a blind strip invents ``고양`` / ``전문`` / ``정확``. That is worse
than the duplicate it was trying to fix, because a wrong node id cannot be
undone by a later read.

So:

* **Tier 1 — unconditional.** Multi-syllable particles (``에서``, ``으로``,
  ``에게``, ``부터``, ``까지``, ``보다``, ``처럼`` …) plus ``을``/``를``.
  Korean nouns essentially never end in these *as their own last syllables*
  once a two-character stem is required, so no corroboration is needed.
* **Tier 2 — evidence-gated.** The single-syllable particles that collide with
  real noun endings (``은 는 이 가 의 와 과 도 로 만 나``) are stripped only
  when the source text itself shows the bare stem somewhere else — that is,
  the text contains the stem *not* followed by this particle. With no text to
  corroborate (an LLM-supplied concept, say) nothing is stripped: an
  unmerged duplicate is recoverable, an invented stem is not.

Every stem must keep at least :data:`MIN_STEM_CHARS` characters, which by
itself rejects the whole ``결과 → 결`` / ``회의 → 회`` class.
"""

from __future__ import annotations

import functools
import re
import unicodedata
from typing import Dict, Iterable, List, Sequence, Tuple

#: A stripped stem shorter than this is never accepted — ``결과`` must not
#: become ``결``. Two characters is the shortest real Korean noun stem.
MIN_STEM_CHARS = 2

#: Particles stripped without asking the text. Longest first: ``에서는`` has to
#: be tried before ``에서`` or the leftover ``는`` stays glued on.
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

#: Particles that collide with real noun endings — stripped only when the text
#: shows the bare stem elsewhere (see :func:`strip_particle`).
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

#: Opening → closing delimiters unwrapped when a term is wrapped in a *matched*
#: pair. Matched only: ``<data_dir>/cloud_provider.json`` opens a bracket it
#: never closes, and stripping the lone ``<`` invents a name nobody wrote.
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
#: Trailing punctuation trimmed after the brackets come off.
_TRAILING_PUNCT = ".,;:!?…·~-–—/\\|"

_WHITESPACE = re.compile(r"\s+")
_HANGUL = re.compile(r"[가-힣]")
_POSSESSIVE = re.compile(r"(?:['’]s|['’])$", re.IGNORECASE)


def _is_hangul_tail(text: str) -> bool:
    """True when the last character is a Hangul syllable."""
    return bool(text) and bool(_HANGUL.fullmatch(text[-1]))


def strip_particle(term: str, text: str = "") -> str:
    """``term`` with one trailing Korean particle removed, when that is safe.

    ``text`` is the passage the term came from; it is the corroboration for
    the Tier-2 particles. Pass ``""`` and only Tier 1 fires.

    >>> strip_particle("플랫폼에서")
    '플랫폼'
    >>> strip_particle("고양이")           # no evidence → left alone
    '고양이'
    >>> strip_particle("플랫폼이", "플랫폼이 있고 플랫폼도 있다")
    '플랫폼'
    """
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
    """Compiled ``stem`` not-followed-by ``particle`` look-ahead."""
    return re.compile(re.escape(stem) + "(?!" + re.escape(particle) + ")")


def _stem_stands_alone(stem: str, particle: str, text: str) -> bool:
    """True when ``text`` contains ``stem`` *not* followed by ``particle``.

    That is the whole evidence test: if the passage only ever writes
    ``정확도``, the trailing ``도`` is part of the word. If it also writes
    ``정확`` on its own (or ``정확을``, ``정확에서`` …), the ``도`` was a
    particle after all.
    """
    return _stem_alone_re(stem, particle).search(text) is not None


def normalize_entity(term: str, text: str = "") -> str:
    """The canonical *surface* form of one extracted entity.

    NFKC (so ``ＡＩ`` and ``AI`` are one word), whitespace collapsed to single
    spaces, brackets/quotes/trailing punctuation trimmed, English possessive
    dropped, and one Korean particle stripped when :func:`strip_particle`
    considers it safe. Returns ``""`` for anything that normalizes away.

    >>> normalize_entity("  “Lattice   AI”  ")
    'Lattice AI'
    >>> normalize_entity("Anthropic's")
    'Anthropic'
    >>> normalize_entity("지식그래프에서")
    '지식그래프'
    """
    cleaned = unicodedata.normalize("NFKC", str(term or ""))
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    # Matched wrappers come off, then trailing sentence punctuation; looped so
    # `("Lattice AI").` unwraps fully rather than one layer at a time.
    for _ in range(3):
        before = cleaned
        if len(cleaned) > 2 and cleaned.endswith(_DELIMITER_PAIRS.get(cleaned[0], "\0")):
            cleaned = cleaned[1:-1].strip()
        cleaned = cleaned.rstrip(_TRAILING_PUNCT).strip()
        if cleaned == before:
            break
    if not cleaned:
        return ""
    cleaned = _POSSESSIVE.sub("", cleaned).strip()
    return strip_particle(cleaned, text)


def entity_key(term: str) -> str:
    """The dedup key two surface forms of one entity share.

    Case-folded and whitespace-collapsed, and with the separators that
    identifier styles disagree about (space, ``-``, ``_``) removed — so
    ``Graph RAG`` / ``graph-rag`` / ``graph_rag`` are one key while
    ``GraphRAG`` (already joined) matches them too.
    """
    folded = unicodedata.normalize("NFKC", str(term or "")).casefold()
    folded = _WHITESPACE.sub(" ", folded).strip()
    return re.sub(r"[\s_\-]+", "", folded)


def _representative(surfaces: Sequence[str], text: str) -> str:
    """Pick the surface form an entity's node should carry.

    Most frequent in the source text wins — the way the author actually writes
    it. Ties go to the form with more capitalized words (``Lattice AI`` over
    ``lattice ai``), then to the longer form, then to the first one seen, so
    the choice never depends on dict ordering.
    """
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
    """Normalize every term, drop the empties, and merge exact-key aliases.

    Order is the order the *first* member of each group appeared in, so the
    caller's own priority (backticked terms first, then proper nouns, …) is
    preserved. The value is the group's representative surface form.

    >>> merge_entity_aliases(["Lattice AI", "lattice  ai", "Graph RAG"])
    ['Lattice AI', 'Graph RAG']
    """
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
    """How many times ``term`` appears in ``text``, case-insensitively.

    The number an edge's ``occurrences`` metadata carries. Counted on the
    normalized surface, so ``플랫폼은`` and ``플랫폼을`` both count toward
    ``플랫폼``. Zero-length terms count zero rather than raising.
    """
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
