"""Optional cross-encoder rerank for hybrid retrieval (v9.9.5).

Default path is identity (fused score preserved) — no model download, no
latency tax, no unearned claims. Opt in with::

    LATTICEAI_CROSS_ENCODER_RERANK=1
    # optional model id (sentence-transformers CrossEncoder):
    LATTICEAI_CROSS_ENCODER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2

When the env kill-switch is off, or ``sentence_transformers`` / the model is
unavailable, :func:`rerank_matches` returns the candidates unchanged and
reports ``mode="identity"``. Failures never raise into the search path.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CROSS_ENCODER_RERANK_ENV = "LATTICEAI_CROSS_ENCODER_RERANK"
CROSS_ENCODER_MODEL_ENV = "LATTICEAI_CROSS_ENCODER_MODEL"
DEFAULT_CROSS_ENCODER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

_model_cache: Dict[str, Any] = {}


def _rerank_enabled() -> bool:
    raw = os.getenv(CROSS_ENCODER_RERANK_ENV, "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _model_id() -> str:
    raw = os.getenv(CROSS_ENCODER_MODEL_ENV, "").strip()
    return raw or DEFAULT_CROSS_ENCODER_MODEL


def _candidate_text(match: Dict[str, Any]) -> str:
    parts = [
        str(match.get("title") or ""),
        str(match.get("summary") or ""),
        str((match.get("metadata") or {}).get("snippet") or ""),
    ]
    return " ".join(p for p in parts if p).strip() or str(match.get("node_id") or "")


def _load_cross_encoder(model_id: str) -> Any:
    if model_id in _model_cache:
        return _model_cache[model_id]
    from sentence_transformers import CrossEncoder  # type: ignore

    model = CrossEncoder(model_id)
    _model_cache[model_id] = model
    return model


def identity_rerank(
    query: str,
    candidates: List[Dict[str, Any]],
    *,
    top_k: Optional[int] = None,
) -> Dict[str, Any]:
    """Preserve fused ordering; stamp identity scores for an honest contract."""
    del query  # identity path does not use the query text
    ranked = list(candidates)
    for item in ranked:
        item["rerank_score"] = float(item.get("score") or item.get("fused_score") or 0.0)
    if top_k is not None:
        ranked = ranked[: max(1, int(top_k))]
    return {
        "matches": ranked,
        "mode": "identity",
        "model": None,
        "detail": None,
    }


def cross_encoder_rerank(
    query: str,
    candidates: List[Dict[str, Any]],
    *,
    top_k: Optional[int] = None,
    model_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Score (query, candidate) pairs with a CrossEncoder when available."""
    if not candidates:
        return {
            "matches": [],
            "mode": "cross_encoder",
            "model": model_id or _model_id(),
            "detail": None,
        }
    mid = model_id or _model_id()
    try:
        model = _load_cross_encoder(mid)
    except Exception as exc:  # noqa: BLE001 — never break search
        logger.info("cross-encoder unavailable (%s); falling back to identity", exc)
        result = identity_rerank(query, candidates, top_k=top_k)
        result["detail"] = f"cross_encoder_unavailable: {exc}"
        return result

    pairs = [[str(query or ""), _candidate_text(c)] for c in candidates]
    try:
        scores = model.predict(pairs)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cross-encoder predict failed: %s", exc)
        result = identity_rerank(query, candidates, top_k=top_k)
        result["detail"] = f"cross_encoder_predict_failed: {exc}"
        return result

    ranked = list(candidates)
    for item, score in zip(ranked, scores):
        item["rerank_score"] = float(score)
        # Surface the rerank score as the primary ranking key while keeping
        # the pre-rerank fused score under scores.fused for audit.
        scores_map = item.setdefault("scores", {})
        if isinstance(scores_map, dict):
            scores_map.setdefault("fused", float(item.get("score") or 0.0))
            scores_map["rerank"] = float(score)
        item["score"] = float(score)
    ranked.sort(key=lambda m: (-float(m.get("rerank_score") or 0.0), str(m.get("node_id") or "")))
    if top_k is not None:
        ranked = ranked[: max(1, int(top_k))]
    for rank, match in enumerate(ranked, start=1):
        match["rank"] = rank
    return {
        "matches": ranked,
        "mode": "cross_encoder",
        "model": mid,
        "detail": None,
    }


def rerank_matches(
    query: str,
    candidates: List[Dict[str, Any]],
    *,
    top_k: Optional[int] = None,
    force: Optional[bool] = None,
) -> Dict[str, Any]:
    """Public entry: cross-encoder when enabled, else identity.

    ``force=True/False`` overrides the env kill-switch (tests only).
    """
    enabled = _rerank_enabled() if force is None else bool(force)
    if not enabled:
        return identity_rerank(query, candidates, top_k=top_k)
    return cross_encoder_rerank(query, candidates, top_k=top_k)


__all__ = [
    "CROSS_ENCODER_MODEL_ENV",
    "CROSS_ENCODER_RERANK_ENV",
    "DEFAULT_CROSS_ENCODER_MODEL",
    "cross_encoder_rerank",
    "identity_rerank",
    "rerank_matches",
]
