"""Query-class aware retrieval fusion weights (backlog #5, review §7.2 E).

Hybrid retrieval fuses keyword/lexical, vector, and graph channels. A single
static weight set treats "이 함수 왜 죽어?" and "어제 회의에서 뭐 결정했지?"
identically, which is measurably wrong: code questions live on exact
identifiers (lexical), person questions lean on relationship edges (graph),
recency questions need fresher lexical hits over semantic neighbors.

This module is deliberately tiny and deterministic:

* :func:`classify_query` — pure ko/en heuristics → ``fact | code | person |
  recency`` (``fact`` is the fallback class and its weights are identical to
  the historical defaults, so fact-class behavior is byte-compatible).
* :data:`DEFAULT_FUSION_WEIGHTS` — the per-class weight table. ``keyword`` /
  ``vector`` / ``graph`` feed the three-channel service fusion
  (``SearchService.hybrid_search``); ``alpha`` is the vector share for the
  two-channel graph-layer fusion (``KnowledgeGraphStore.hybrid_search``).
* :func:`fusion_profile` — classify + resolve weights, honoring the
  ``LATTICEAI_FUSION_WEIGHTS`` env override (JSON, partial per-class merges,
  e.g. ``{"code": {"alpha": 0.2}}``).

Never uses ``or`` on scores (score 0 is falsy but valid) and never raises:
a malformed override is ignored, an unknown class falls back to ``fact``.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Mapping, Optional

from ..quiet import quiet

QUERY_CLASSES = ("fact", "code", "person", "recency")

FUSION_WEIGHTS_ENV = "LATTICEAI_FUSION_WEIGHTS"

# Per-class fusion weights. "fact" mirrors the pre-fusion-gate defaults
# (SearchService DEFAULT_HYBRID_WEIGHTS + graph-layer alpha=0.6) so the
# fallback class introduces zero behavioral drift.
DEFAULT_FUSION_WEIGHTS: Dict[str, Dict[str, float]] = {
    "fact": {"keyword": 0.35, "vector": 0.40, "graph": 0.25, "alpha": 0.60},
    # Code recall lives on exact identifiers/tokens → lexical dominates.
    "code": {"keyword": 0.55, "vector": 0.25, "graph": 0.20, "alpha": 0.35},
    # Person questions are relationship questions → graph channel leads.
    "person": {"keyword": 0.30, "vector": 0.30, "graph": 0.40, "alpha": 0.45},
    # Recency questions want fresh literal hits over semantic neighbors.
    "recency": {"keyword": 0.45, "vector": 0.35, "graph": 0.20, "alpha": 0.50},
}

# ── query-class heuristics (pure, ko/en) ─────────────────────────────────────

_CODE_FENCE_RE = re.compile(r"```|`[^`\n]+`")
_CODE_IDENT_RE = re.compile(
    # ``\b`` fails between an identifier and an attached Korean particle
    # ("ingest_folder가"), so use explicit ASCII-boundary lookbehinds instead.
    r"[A-Za-z_][A-Za-z0-9_]*\(\)"                      # call syntax foo()
    r"|(?<![A-Za-z0-9_])[a-z0-9]+_[a-z0-9_]+"          # snake_case
    r"|(?<![A-Za-z0-9_])[a-z]+[A-Z][A-Za-z0-9]*"       # camelCase
    r"|(?<![A-Za-z0-9_])[\w-]+\.(?:py|js|jsx|ts|tsx|json|yaml|yml|css|html|sql|sh|go|rs|java|rb)(?![A-Za-z0-9])"
)
_CODE_WORD_RE = re.compile(
    r"\b(?:def|class|import|function|traceback|exception|stack\s*trace|"
    r"bug|error|null|undefined|api|sql|regex)\b"
    r"|코드|함수|버그|에러|오류|스택|컴파일|빌드\s*실패|구현",
    re.IGNORECASE,
)
_PERSON_RE = re.compile(
    r"\b(?:who|whom|whose)\b|누구|어떤\s*사람|담당자|팀원|동료|만난\s*사람"
    r"|[\w.+-]+@[\w-]+\.[\w.]+"        # an email address names a person
    r"|[가-힣]{2,4}\s*(?:님|씨)(?:\s|$|[을를이가은는의??.!,])",
    re.IGNORECASE,
)
_RECENCY_RE = re.compile(
    r"최근|어제|오늘|그저께|방금|아까|지난\s*주|지난주|지난\s*달|지난달"
    r"|이번\s*주|이번주|이번\s*달|이번달"
    r"|\b(?:recent|recently|yesterday|today|latest|last\s+(?:week|month|night|meeting))\b",
    re.IGNORECASE,
)


def classify_query(query: Any) -> str:
    """Classify a retrieval query into fact / code / person / recency.

    Precedence: code (strongest structural signal) → recency (explicit time
    words) → person → fact. Deterministic, never raises, empty → fact.
    """
    text = str(query or "").strip()
    if not text:
        return "fact"
    if _CODE_FENCE_RE.search(text) or _CODE_IDENT_RE.search(text) or _CODE_WORD_RE.search(text):
        return "code"
    if _RECENCY_RE.search(text):
        return "recency"
    if _PERSON_RE.search(text):
        return "person"
    return "fact"


def _env_overrides() -> Dict[str, Dict[str, float]]:
    """Parse LATTICEAI_FUSION_WEIGHTS (JSON) → per-class partial overrides."""
    raw = os.getenv(FUSION_WEIGHTS_ENV, "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    overrides: Dict[str, Dict[str, float]] = {}
    for cls, table in payload.items():
        if cls not in DEFAULT_FUSION_WEIGHTS or not isinstance(table, dict):
            continue
        cleaned: Dict[str, float] = {}
        for key, value in table.items():
            if key not in ("keyword", "vector", "graph", "alpha"):
                continue
            try:
                cleaned[key] = max(0.0, min(1.0, float(value)))
            except (TypeError, ValueError):
                quiet()
                continue
        if cleaned:
            overrides[str(cls)] = cleaned
    return overrides


def fusion_weight_table(
    overrides: Optional[Mapping[str, Mapping[str, float]]] = None,
) -> Dict[str, Dict[str, float]]:
    """Full per-class table: defaults ← env override ← caller override."""
    table = {cls: dict(weights) for cls, weights in DEFAULT_FUSION_WEIGHTS.items()}
    for source in (_env_overrides(), overrides or {}):
        for cls, partial in source.items():
            if cls in table and isinstance(partial, Mapping):
                for key, value in partial.items():
                    if key in table[cls]:
                        try:
                            table[cls][key] = max(0.0, min(1.0, float(value)))
                        except (TypeError, ValueError):
                            quiet()
                            continue
    return table


def fusion_profile(
    query: Any,
    *,
    overrides: Optional[Mapping[str, Mapping[str, float]]] = None,
) -> Dict[str, Any]:
    """Classify ``query`` and return its resolved fusion weights.

    Returns ``{"query_class": str, "weights": {keyword, vector, graph},
    "alpha": float}``. ``weights`` feeds the three-channel service fusion;
    ``alpha`` feeds the two-channel graph-layer fusion.
    """
    query_class = classify_query(query)
    table = fusion_weight_table(overrides)
    resolved = table.get(query_class)
    if resolved is None:  # defensive — classify_query only emits known classes
        query_class = "fact"
        resolved = table["fact"]
    return {
        "query_class": query_class,
        "weights": {
            "keyword": resolved["keyword"],
            "vector": resolved["vector"],
            "graph": resolved["graph"],
        },
        "alpha": resolved["alpha"],
    }


__all__ = [
    "DEFAULT_FUSION_WEIGHTS",
    "FUSION_WEIGHTS_ENV",
    "QUERY_CLASSES",
    "classify_query",
    "fusion_profile",
    "fusion_weight_table",
]
