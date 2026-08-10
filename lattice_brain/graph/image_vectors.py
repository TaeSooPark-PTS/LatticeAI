"""The image vector space — stored apart, joined by late fusion (v11.1.0).

A CLIP image vector and a BGE text vector are both "1024 floats", and that is
the entire extent of what they have in common. Filing them in one index means
every cosine between them is a number with no meaning, and the ranking that
comes out looks exactly like a working search.

So image vectors get their own table (``image_embeddings``) keyed by the vision
model that produced them, and their own search. Text queries never touch this
index: they reach pictures through OCR text and captions, which are text and
live in the text index like everything else. This index answers one question —
*which stored images look like this vector* — and its scores enter
``hybrid_search`` by late fusion, as a separate channel with its own weight,
rather than by pretending to be comparable up front.

The table is created on demand, is derivable from the images themselves, and is
never the source of truth: dropping it costs a re-embed, not a memory.
"""

from __future__ import annotations

import sqlite3
import struct
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .vector_index import build_index, resolve_vector_index

IMAGE_VECTOR_TABLE = "image_embeddings"
#: Default weight the image channel carries when fused with text scores.
DEFAULT_IMAGE_FUSION_WEIGHT = 0.5


def ensure_image_vector_table(conn: sqlite3.Connection) -> None:
    """Create the image-vector table if this graph has never held one."""
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {IMAGE_VECTOR_TABLE} (
            node_id TEXT PRIMARY KEY,
            model_id TEXT NOT NULL,
            dim INTEGER NOT NULL,
            space TEXT NOT NULL DEFAULT 'image',
            vector BLOB NOT NULL,
            updated_at TEXT
        )
        """
    )


def encode_vector(vector: Sequence[float]) -> bytes:
    values = [float(value) for value in vector]
    return struct.pack(f"<{len(values)}f", *values)


def decode_vector(payload: bytes) -> List[float]:
    count = len(payload) // 4
    return list(struct.unpack(f"<{count}f", payload[: count * 4]))


def record_image_vector(
    store: Any,
    *,
    node_id: str,
    vector: Sequence[float],
    model_id: str,
    space: str = "image",
    updated_at: Optional[str] = None,
) -> bool:
    """Persist one image vector. Returns False when there is nothing to store.

    Never raises: an image whose vector could not be filed is still a memory,
    and :func:`image_index_status` reports it as backlog.
    """
    values = [float(value) for value in (vector or [])]
    if not node_id or not values or not model_id:
        return False
    try:
        with store._connect() as conn:
            ensure_image_vector_table(conn)
            conn.execute(
                f"""
                INSERT INTO {IMAGE_VECTOR_TABLE}
                    (node_id, model_id, dim, space, vector, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    model_id=excluded.model_id,
                    dim=excluded.dim,
                    space=excluded.space,
                    vector=excluded.vector,
                    updated_at=excluded.updated_at
                """,
                (
                    str(node_id),
                    str(model_id),
                    len(values),
                    str(space or "image"),
                    encode_vector(values),
                    updated_at,
                ),
            )
        return True
    except Exception:  # noqa: BLE001 — a vector is derivable; the image is not
        return False


def _rows(
    store: Any, model_id: Optional[str], dim: Optional[int]
) -> List[Tuple[str, List[float]]]:
    with store._connect() as conn:
        ensure_image_vector_table(conn)
        if model_id:
            cursor = conn.execute(
                f"SELECT node_id, vector, dim FROM {IMAGE_VECTOR_TABLE} WHERE model_id=? ORDER BY node_id ASC",
                (str(model_id),),
            )
        else:
            cursor = conn.execute(
                f"SELECT node_id, vector, dim FROM {IMAGE_VECTOR_TABLE} ORDER BY node_id ASC"
            )
        rows = cursor.fetchall()
    out: List[Tuple[str, List[float]]] = []
    for row in rows:
        if dim is not None and int(row["dim"]) != dim:
            # A different width means a different model. Comparing across it is
            # the exact silent wrongness this whole module exists to prevent.
            continue
        out.append((str(row["node_id"]), decode_vector(row["vector"])))
    return out


def image_similarity_search(
    store: Any,
    vector: Sequence[float],
    *,
    top_k: int = 10,
    model_id: Optional[str] = None,
    min_score: float = 0.0,
) -> Dict[str, Any]:
    """Rank stored images against a query *image* vector.

    Scoring is delegated to the pluggable backend layer (Track 1), so this
    index picks up ``LATTICEAI_VECTOR_INDEX`` and reports the same
    ``approx``/``exhaustive`` honesty bits as the text index.
    """
    query = [float(value) for value in (vector or [])]
    result: Dict[str, Any] = {
        "matches": [],
        "count": 0,
        "candidates": 0,
        "model_id": model_id,
        "detail": None,
    }
    if not query:
        result["detail"] = "an image query needs an image vector"
        return result
    try:
        rows = _rows(store, model_id, len(query))
    except Exception as exc:  # noqa: BLE001 — degrade, never fail the search
        result["detail"] = f"image vector index unavailable: {exc}"
        return result
    result["candidates"] = len(rows)
    selection = resolve_vector_index()
    index = build_index(selection, dim=len(query))
    index.rebuild((node_id, values, {}) for node_id, values in rows)
    scored = index.search(query, max(1, int(top_k)), {"min_score": min_score})
    result["matches"] = [
        {"node_id": node_id, "score": round(float(score), 6)} for node_id, score in scored
    ]
    result["count"] = len(result["matches"])
    result["index"] = selection.as_dict()
    return result


def image_index_status(store: Any) -> Dict[str, Any]:
    """How many images carry a vector, and under which model."""
    status: Dict[str, Any] = {"vectors": 0, "models": {}, "detail": None}
    try:
        with store._connect() as conn:
            ensure_image_vector_table(conn)
            rows = conn.execute(
                f"SELECT model_id, COUNT(*) AS total FROM {IMAGE_VECTOR_TABLE} GROUP BY model_id"
            ).fetchall()
    except Exception as exc:  # noqa: BLE001 — status must never raise
        status["detail"] = str(exc)
        return status
    models = {str(row["model_id"]): int(row["total"]) for row in rows}
    status["models"] = models
    status["vectors"] = sum(models.values())
    return status


def fuse_image_scores(
    matches: Iterable[Dict[str, Any]],
    image_scores: Dict[str, float],
    *,
    weight: float = DEFAULT_IMAGE_FUSION_WEIGHT,
) -> int:
    """Late-fuse image-space scores into an already-fused match list.

    Mutates each match in place — ``scores.image`` records the raw image-space
    similarity and ``score`` blends it in — and returns how many matches the
    image channel actually touched. Late fusion is the point: the two spaces
    are ranked separately and combined at the end, so neither is ever asked to
    interpret the other's numbers.
    """
    weight = max(0.0, min(1.0, float(weight)))
    touched = 0
    for match in matches:
        node_id = str(match.get("node_id") or "")
        raw = image_scores.get(node_id)
        if raw is None:
            continue
        touched += 1
        scores = match.setdefault("scores", {})
        scores["image"] = round(float(raw), 6)
        blended = (1.0 - weight) * float(match.get("score") or 0.0) + weight * float(raw)
        match["score"] = round(blended, 6)
    return touched


__all__ = [
    "DEFAULT_IMAGE_FUSION_WEIGHT",
    "IMAGE_VECTOR_TABLE",
    "decode_vector",
    "encode_vector",
    "ensure_image_vector_table",
    "fuse_image_scores",
    "image_index_status",
    "image_similarity_search",
    "record_image_vector",
]
