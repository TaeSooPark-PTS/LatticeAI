"""Honest context-quality and multimodal signals for a result set.

Plain functions over an already-computed match list — no store, no I/O — so
every mixin in the package (and the chat surfaces outside it) can build the
same signal the same way.
"""

from __future__ import annotations

# ruff: noqa: F403,F405
from .._kg_common import *  # noqa: F403,F401

#: Node types that are a *thing you can look at or listen to*, not prose. A
#: match of one of these means the answer rests on more than text.
MULTIMODAL_NODE_TYPES = ("Image", "ImageText")


def multimodal_signal(matches: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """``{"images": n, "types": [...]}`` when a result set includes pictures.

    ``None`` when it does not: the context-quality contract stays four keys
    wide for the ordinary all-text case, and a caller that sees the key knows
    it means something rather than having to compare a zero.
    """
    images = 0
    seen: List[str] = []
    for match in matches:
        node_type = str(match.get("type") or "")
        if node_type in MULTIMODAL_NODE_TYPES:
            images += 1
            if node_type not in seen:
                seen.append(node_type)
    if not images:
        return None
    return {"images": images, "types": seen}


def context_quality_signal(
    mode: str,
    nodes: int,
    *,
    reason: Optional[str] = None,
    vector: Optional[Dict[str, Any]] = None,
    multimodal: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Honest RAG context-quality signal (v9.8.0, additive contract).

    Shape consumed by the chat metadata channel:
    ``{"mode": "hybrid"|"lexical_only"|"none", "nodes": int, "limited": bool,
    "reason": str|None}``. ``nodes == 0`` always collapses ``mode`` to
    ``"none"``; ``limited`` is true whenever the context is thin (0–1 nodes)
    or the vector side fell back to lexical-only retrieval. ``reason`` is a
    short human-readable Korean phrase, only present when limited.

    ``vector`` (v11.1.0) carries the vector channel's own honesty block —
    which backend scored, whether it was approximate, whether the candidate
    scan was truncated. "hybrid, 6 nodes" describes two different answers
    depending on those bits, and the caller that has to say "I did not find
    it" deserves to know which one it got. The key is present **only when
    there is a caveat to report**: an exact, complete vector scan is the
    contract's baseline assumption, so annotating it would be noise, and the
    four-key shape stays exactly what existing consumers pin.

    ``multimodal`` (v11.1.0) follows the same present-only-when-true rule and
    says that part of this context is a picture. "6 nodes" reads differently
    when two of them are screenshots whose text came out of OCR, and the
    surface that has to explain the answer deserves to know.
    """
    nodes = max(0, int(nodes or 0))
    mode = str(mode or "none")
    if nodes == 0:
        mode = "none"
    if mode not in ("hybrid", "lexical_only", "none"):
        mode = "lexical_only"
    limited = nodes <= 1 or mode != "hybrid"
    if reason is None and limited:
        if nodes == 0:
            reason = "그래프에서 관련 지식을 찾지 못했습니다"
        elif mode == "lexical_only":
            reason = "벡터 검색을 사용할 수 없어 키워드 검색 결과만 사용했습니다"
        else:
            reason = "그래프 기반 컨텍스트가 제한적입니다"
    if not limited:
        reason = None
    signal: Dict[str, Any] = {
        "mode": mode,
        "nodes": nodes,
        "limited": limited,
        "reason": reason,
    }
    if vector is not None:
        signal["vector"] = dict(vector)
    if multimodal is not None:
        signal["multimodal"] = dict(multimodal)
    return signal
