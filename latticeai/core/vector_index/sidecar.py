"""Load, refresh, and query the HNSW sidecar next to a brain database.

The live graph is cached per ``(db_path, model_id, dim)``. A missing sidecar
is ``index: "none"``. When the store has vectors the cache (or a rebuild from
``vector_embeddings``) can serve, the reply is ``index: "hnsw"``. Freshness is
sidecar size vs ``COUNT(*)`` for that identity. A warm COUNT match never
opens embedding blobs. Growth loads **missing ids only** and appends; a
shrink still rebuilds from the remaining rows.
"""

from __future__ import annotations

import sqlite3
import struct
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from latticeai.core.config import default_data_dir

from .hnsw import HnswIndex, hnswlib_available, sidecar_paths

GRAPH_DB_NAME = "knowledge_graph.sqlite"
VECTOR_QUERY_K_CAP = 200

_CACHE: Dict[Tuple[str, str, int], HnswIndex] = {}
_LOCK = threading.Lock()


def resolve_graph_db(workspace: Optional[str] = None) -> Path:
    """Brain sqlite path: explicit file, a data dir, or ``LATTICEAI_DATA_DIR``."""
    if workspace:
        raw = Path(str(workspace)).expanduser()
        if raw.is_file():
            return raw
        if raw.suffix.lower() == ".sqlite":
            return raw
        return raw / GRAPH_DB_NAME
    return default_data_dir() / GRAPH_DB_NAME


def decode_f32le(blob: bytes, dim: int) -> List[float]:
    """Little-endian f32 payload — the same layout Rust ``encode`` writes."""
    if not blob:
        return []
    count = dim if dim > 0 else len(blob) // 4
    if len(blob) != count * 4:
        count = len(blob) // 4
    if count <= 0:
        return []
    return list(struct.unpack(f"<{count}f", blob[: count * 4]))


def sidecar_fingerprint(model_id: str, dim: int, size: int) -> str:
    """Identity the ``.hnsw.meta.json`` file is keyed on."""
    return f"{model_id}|{int(dim)}|{int(size)}"


def store_vector_count(conn: sqlite3.Connection, model_id: str, dim: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM vector_embeddings "
        "WHERE embedding_model=? AND embedding_dim=?",
        (model_id, int(dim)),
    ).fetchone()
    return int(row[0]) if row else 0


def load_store_items(
    conn: sqlite3.Connection, model_id: str, dim: int
) -> List[Tuple[str, List[float], Dict[str, Any]]]:
    items: List[Tuple[str, List[float], Dict[str, Any]]] = []
    for item_id, blob in conn.execute(
        "SELECT item_id, embedding FROM vector_embeddings "
        "WHERE embedding_model=? AND embedding_dim=? ORDER BY indexed_at ASC, item_id ASC",
        (model_id, int(dim)),
    ):
        vector = decode_f32le(bytes(blob or b""), int(dim))
        if len(vector) != int(dim):
            continue
        items.append((str(item_id), vector, {}))
    return items


def load_store_ids(conn: sqlite3.Connection, model_id: str, dim: int) -> List[str]:
    """Item ids only — the COUNT+delta path must not pull embedding blobs."""
    rows = conn.execute(
        "SELECT item_id FROM vector_embeddings "
        "WHERE embedding_model=? AND embedding_dim=? ORDER BY indexed_at ASC, item_id ASC",
        (model_id, int(dim)),
    )
    return [str(item_id) for (item_id,) in rows]


def load_store_items_for(
    conn: sqlite3.Connection,
    model_id: str,
    dim: int,
    item_ids: Sequence[str],
) -> List[Tuple[str, List[float], Dict[str, Any]]]:
    """Blobs for ``item_ids`` only, preserving the caller's order."""
    if not item_ids:
        return []
    by_id: Dict[str, List[float]] = {}
    wanted = [str(item_id) for item_id in item_ids]
    # SQLite caps bound parameters; 400 leaves room for the identity pair.
    for start in range(0, len(wanted), 400):
        chunk = wanted[start : start + 400]
        qmarks = ",".join("?" * len(chunk))
        query = "SELECT item_id, embedding FROM vector_embeddings WHERE embedding_model=? AND embedding_dim=? AND item_id IN (%s)" % qmarks  # noqa: S608
        for item_id, blob in conn.execute(query, (model_id, int(dim), *chunk)):
            vector = decode_f32le(bytes(blob or b""), int(dim))
            if len(vector) != int(dim):
                continue
            by_id[str(item_id)] = vector
    return [
        (item_id, by_id[item_id], {})
        for item_id in wanted
        if item_id in by_id
    ]


def _open_and_count(path: Path, model_id: str, dim: int) -> int:
    conn = _open_readonly(path)
    if conn is None:
        return 0
    try:
        return store_vector_count(conn, model_id, dim)
    finally:
        conn.close()


def _load_all_items(
    path: Path, model_id: str, dim: int
) -> List[Tuple[str, List[float], Dict[str, Any]]]:
    conn = _open_readonly(path)
    if conn is None:
        return []
    try:
        return load_store_items(conn, model_id, dim)
    finally:
        conn.close()


def _load_missing_items(
    path: Path,
    model_id: str,
    dim: int,
    have: Sequence[str],
) -> List[Tuple[str, List[float], Dict[str, Any]]]:
    conn = _open_readonly(path)
    if conn is None:
        return []
    try:
        have_set = set(have)
        missing = [item_id for item_id in load_store_ids(conn, model_id, dim) if item_id not in have_set]
        return load_store_items_for(conn, model_id, dim, missing)
    finally:
        conn.close()


def sidecar_meta_size(db_path: Path) -> Optional[int]:
    """Label count from the sidecar meta file, or ``None`` when it is absent."""
    _, meta_path = sidecar_paths(db_path)
    try:
        import json

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        labels = meta.get("labels") or []
        return len(labels)
    except Exception:  # noqa: BLE001 — absent/corrupt meta is "no sidecar"
        return None


def _open_readonly(db_path: Path) -> Optional[sqlite3.Connection]:
    if not db_path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        try:
            conn = sqlite3.connect(str(db_path))
        except sqlite3.Error:
            return None
    conn.row_factory = sqlite3.Row
    return conn


def _none_reply(
    *,
    size: int = 0,
    store_size: int = 0,
    stale: bool = False,
    detail: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "ids": [],
        "scores": [],
        "index": "none",
        "size": int(size),
        "store_size": int(store_size),
        "stale": bool(stale),
        "detail": detail,
        "refreshed": None,
    }


def _hnsw_reply(
    ids: Sequence[str],
    scores: Sequence[float],
    *,
    size: int,
    store_size: int,
    stale: bool,
    detail: Optional[str],
    refreshed: Optional[str],
) -> Dict[str, Any]:
    return {
        "ids": [str(item) for item in ids],
        "scores": [float(score) for score in scores],
        "index": "hnsw",
        "size": int(size),
        "store_size": int(store_size),
        "stale": bool(stale),
        "detail": detail,
        "refreshed": refreshed,
    }


def _refresh_index(
    index: HnswIndex,
    items: Sequence[Tuple[str, List[float], Dict[str, Any]]],
    model_id: str,
    dim: int,
) -> str:
    return index.add_items(items, dim=dim, model_id=model_id)


def query_sidecar(
    *,
    workspace: Optional[str],
    embedding_model: str,
    embedding_dim: int,
    vector: Sequence[float],
    k: int,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Serve one ANN query from the sidecar (or say the index is absent)."""
    dim = int(embedding_dim)
    model_id = str(embedding_model or "")
    wanted = max(1, min(int(k), VECTOR_QUERY_K_CAP))
    path = Path(db_path) if db_path is not None else resolve_graph_db(workspace)
    if not hnswlib_available():
        return _none_reply(detail="hnswlib is not available in this worker")
    if dim <= 0 or len(vector) != dim:
        return _none_reply(
            detail=(
                f"query vector width {len(vector)} does not match embedding_dim {dim}"
                if dim > 0
                else "embedding_dim must be a positive integer"
            )
        )
    if not model_id:
        return _none_reply(detail="embedding_model is required")

    # COUNT first. Warm queries that already match never open embedding blobs.
    store_size = _open_and_count(path, model_id, dim)

    key = (str(path), model_id, dim)
    with _LOCK:
        index = _CACHE.get(key)
        refreshed: Optional[str] = None
        if index is None:
            index = HnswIndex(dim=dim, model_id=model_id)
            loaded = index.load(
                path, fingerprint=sidecar_fingerprint(model_id, dim, store_size)
            )
            if not loaded and store_size > 0:
                # Sidecar exists for this identity but the COUNT moved —
                # load the graph and append the missing ids below.
                loaded = index.load(
                    path,
                    fingerprint=sidecar_fingerprint(model_id, dim, store_size),
                    strict=False,
                )
            if not loaded:
                items = _load_all_items(path, model_id, dim)
                store_size = len(items)
                if not items:
                    return _none_reply(
                        store_size=store_size,
                        detail="no HNSW sidecar for this embedding identity",
                    )
                refreshed = _refresh_index(index, items, model_id, dim)
                index.save(path, fingerprint=sidecar_fingerprint(model_id, dim, len(items)))
            _CACHE[key] = index

        cached_size = int(index.stats().get("size") or 0)
        stale = cached_size != store_size
        if stale and store_size == 0:
            _CACHE.pop(key, None)
            return _none_reply(
                store_size=0,
                detail="vector store is empty for this embedding identity",
            )
        if stale and store_size > cached_size:
            delta = _load_missing_items(path, model_id, dim, index.labels)
            if delta:
                refreshed = _refresh_index(index, delta, model_id, dim)
                cached_size = int(index.stats().get("size") or 0)
                index.save(
                    path, fingerprint=sidecar_fingerprint(model_id, dim, cached_size)
                )
                stale = cached_size != store_size
        elif stale:
            items = _load_all_items(path, model_id, dim)
            store_size = len(items)
            if items:
                refreshed = _refresh_index(index, items, model_id, dim)
                cached_size = int(index.stats().get("size") or 0)
                index.save(
                    path, fingerprint=sidecar_fingerprint(model_id, dim, cached_size)
                )
                stale = cached_size != store_size

        if cached_size <= 0:
            return _none_reply(
                store_size=store_size,
                stale=stale,
                detail="HNSW sidecar has no labels for this identity",
            )

        index._ef_search = max(index._ef_search, wanted * 2, 200)
        hits = index.search(list(vector), top_k=min(wanted, cached_size))
        ids = [item_id for item_id, _ in hits]
        scores = [score for _, score in hits]
        detail = None
        if stale:
            detail = (
                f"sidecar size {cached_size} != store count {store_size} "
                "for this embedding identity"
            )
        return _hnsw_reply(
            ids,
            scores,
            size=cached_size,
            store_size=store_size,
            stale=stale,
            detail=detail,
            refreshed=refreshed,
        )


def sidecar_freshness(
    *,
    workspace: Optional[str] = None,
    embedding_model: Optional[str] = None,
    embedding_dim: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Cheap on-disk vs store comparison — no ANN query, no rebuild."""
    path = Path(db_path) if db_path is not None else resolve_graph_db(workspace)
    meta_size = sidecar_meta_size(path)
    store_size = 0
    conn = _open_readonly(path)
    if conn is not None:
        try:
            if embedding_model and embedding_dim:
                store_size = store_vector_count(conn, embedding_model, int(embedding_dim))
            else:
                row = conn.execute("SELECT COUNT(*) FROM vector_embeddings").fetchone()
                store_size = int(row[0]) if row else 0
        except sqlite3.Error:
            store_size = 0
        finally:
            conn.close()
    if meta_size is None:
        return {
            "index": "none",
            "size": 0,
            "store_size": store_size,
            "stale": store_size > 0,
            "detail": "no HNSW sidecar next to the brain database",
        }
    stale = meta_size != store_size
    return {
        "index": "hnsw",
        "size": meta_size,
        "store_size": store_size,
        "stale": stale,
        "detail": (
            f"sidecar size {meta_size} != store count {store_size}"
            if stale
            else "sidecar matches the vector store"
        ),
    }


def reset_sidecar_cache() -> None:
    """Drop the process cache — tests only."""
    with _LOCK:
        _CACHE.clear()


__all__ = [
    "GRAPH_DB_NAME",
    "VECTOR_QUERY_K_CAP",
    "decode_f32le",
    "query_sidecar",
    "reset_sidecar_cache",
    "resolve_graph_db",
    "sidecar_fingerprint",
    "sidecar_freshness",
    "sidecar_meta_size",
    "store_vector_count",
    "load_store_ids",
    "load_store_items",
    "load_store_items_for",
]
