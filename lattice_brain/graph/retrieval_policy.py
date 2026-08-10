"""Single retrieval policy consulted by both hybrid fusion layers.

Review 2026-07-25 (UX/harness/loop/KG-RAG), Wave 0.2 — "RetrievalPolicy
단일화" + "query rewrite" + "recency age decay". Before this module the two
hybrid entry points (``KnowledgeGraphStore.hybrid_search`` two-channel
alpha fusion and ``SearchService.hybrid_search`` three-channel weight
fusion) each resolved :func:`lattice_brain.graph.fusion.fusion_profile`
independently and had no shared place to hang retrieval-wide behavior.
This module is that single place:

* :func:`rewrite_query` — deterministic, rule-based ko/en normalization
  (NO LLM). Strips a conservative fixed list of leading/trailing politeness
  fillers ("~좀 알려줘", "궁금해", "뭐였지", "what is", "please", …) and
  collapses repeated whitespace. Code-class queries pass through untouched
  (except whitespace collapse) because exact identifiers/filenames ARE the
  retrieval signal. ``LATTICEAI_QUERY_REWRITE=0`` disables rewriting.
* :func:`resolve_policy` — composes the rewrite with
  :func:`~lattice_brain.graph.fusion.fusion_profile` (the weight table is
  NOT duplicated here) and adds ``recency_half_life_days``, which is
  non-``None`` ONLY for the ``recency`` query class — the honest signal
  that age decay applies exactly where the fusion layers wire it.

Both functions are pure, deterministic, and never raise; the ``fact``
query class resolves to the historical default weights, so fact-class
behavior stays byte-compatible. Scores are never combined with ``or``
(score ``0.0`` is falsy but valid).
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Mapping, Optional

from .fusion import classify_query, fusion_profile

QUERY_REWRITE_ENV = "LATTICEAI_QUERY_REWRITE"

# Half-life (days) for the recency-class age decay. Matches the default of
# lattice_brain.graph._kg_fsutil._recency_score, which the fusion layers use
# to compute the actual multiplier — this module only declares the policy.
RECENCY_HALF_LIFE_DAYS = 14.0

# A filler strip is applied only when the remaining query keeps at least this
# many characters — never rewrite a query down to (near-)nothing.
_MIN_REMAINDER_CHARS = 4

_WS_RE = re.compile(r"\s+")

# Conservative trailing Korean politeness/filler phrases ("… 좀 알려줘",
# "… 궁금해", "… 뭐였지"). Anchored at end-of-string, must start the query or
# follow whitespace so word interiors are never clipped.
_KO_FILLER_TAIL_RE = re.compile(
    r"(?:^|(?<=\s))(?:좀\s+)?"
    r"(?:알려\s*줘요?|알려\s*주세요|알려\s*줄래요?"
    r"|말해\s*줘요?|말해\s*주세요"
    r"|설명해\s*줘요?|설명해\s*주세요"
    r"|궁금해요?|궁금합니다"
    r"|뭐였지|뭐였더라|뭐더라|뭐지|뭐야"
    r")\s*[?!.…~]*\s*$"
)

# Conservative leading English filler ("what is …", "tell me about …",
# optionally prefixed with "please").
_EN_FILLER_LEAD_RE = re.compile(
    r"^(?:please\s+)?(?:tell\s+me\s+about|what\s+is|what\s+was|what\s+are|what's)\s+",
    re.IGNORECASE,
)

# Conservative trailing English politeness ("…, please?").
_EN_FILLER_TAIL_RE = re.compile(r"[,\s]*\bplease\b\s*[?!.]*\s*$", re.IGNORECASE)

_FILLER_RULES = (
    ("strip_filler_ko", _KO_FILLER_TAIL_RE),
    ("strip_filler_en_leading", _EN_FILLER_LEAD_RE),
    ("strip_filler_en_trailing", _EN_FILLER_TAIL_RE),
)


def _rewrite_enabled() -> bool:
    raw = os.getenv(QUERY_REWRITE_ENV, "").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def rewrite_query(query: Any) -> Dict[str, Any]:
    """Deterministic rule-based ko/en query normalization (never raises).

    Returns ``{"original": str, "rewritten": str, "rules": [rule names]}``.
    Only three conservative rules exist: whitespace collapse, trailing
    Korean filler strip, and leading/trailing English filler strip — each
    filler strip applies only when the remainder keeps >= 4 characters.
    Code-class queries (exact identifiers/filenames are the signal) are
    returned unchanged apart from whitespace collapse. The
    ``LATTICEAI_QUERY_REWRITE=0`` kill-switch disables rewriting entirely
    (``rewritten == original``). Empty input yields empty output.
    """
    try:
        text = str(query or "").strip()
    except Exception:  # noqa: BLE001 — rewrite must never raise
        return {"original": "", "rewritten": "", "rules": []}
    if not text:
        return {"original": "", "rewritten": "", "rules": []}
    original = text
    if not _rewrite_enabled():
        return {"original": original, "rewritten": original, "rules": []}

    rules: List[str] = []
    rewritten = _WS_RE.sub(" ", text).strip()
    if rewritten != original:
        rules.append("collapse_whitespace")

    if classify_query(original) == "code":
        # Exact identifiers/filenames are the retrieval signal — never touch
        # them (whitespace collapse is the only permitted normalization).
        return {"original": original, "rewritten": rewritten, "rules": rules}

    for name, pattern in _FILLER_RULES:
        candidate = pattern.sub("", rewritten).strip()
        if candidate != rewritten and len(candidate) >= _MIN_REMAINDER_CHARS:
            rewritten = candidate
            rules.append(name)
    return {"original": original, "rewritten": rewritten, "rules": rules}


def resolve_policy(
    query: Any,
    *,
    overrides: Optional[Mapping[str, Mapping[str, float]]] = None,
) -> Dict[str, Any]:
    """Resolve the full retrieval policy for ``query`` (never raises).

    Composes :func:`rewrite_query` with
    :func:`~lattice_brain.graph.fusion.fusion_profile` (classification runs
    on the ORIGINAL query, so ``query_class`` / ``weights`` / ``alpha`` are
    byte-identical to calling ``fusion_profile`` directly, as both hybrid
    layers did before this module). Returns::

        {
          "query_class": "fact" | "code" | "person" | "recency",
          "weights": {"keyword", "vector", "graph"},   # service fusion
          "alpha": float,                              # graph-layer fusion
          "fusion_strategy": "alpha" | "rrf",          # how they combine
          "original_query": str,
          "search_query": str,       # the rewritten form to search with
          "rewrite_rules": [str],
          "recency_half_life_days": float | None,      # 14.0 only for recency
        }

    ``fusion_strategy`` is ``"alpha"`` for every class unless
    ``LATTICEAI_FUSION_STRATEGY`` says otherwise, so the default policy is
    byte-identical to the pre-11.1.0 one.

    ``recency_half_life_days`` is non-``None`` only for the ``recency``
    class — the honest contract that age decay applies exactly where the
    fusion layers wire it, and nowhere else.
    """
    rewrite = rewrite_query(query)
    profile = fusion_profile(rewrite["original"], overrides=overrides)
    query_class = profile["query_class"]
    search_query = rewrite["rewritten"] if rewrite["rewritten"] else rewrite["original"]
    return {
        "query_class": query_class,
        "weights": dict(profile["weights"]),
        "alpha": float(profile["alpha"]),
        "fusion_strategy": str(profile["strategy"]),
        "original_query": rewrite["original"],
        "search_query": search_query,
        "rewrite_rules": list(rewrite["rules"]),
        "recency_half_life_days": (
            RECENCY_HALF_LIFE_DAYS if query_class == "recency" else None
        ),
    }


__all__ = [
    "QUERY_REWRITE_ENV",
    "RECENCY_HALF_LIFE_DAYS",
    "resolve_policy",
    "rewrite_query",
]
