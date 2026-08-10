"""Approximate nearest-neighbour index backed by optional ``hnswlib``.

``hnswlib`` is a compiled extension. It is **not** a core dependency: the
install stays pure-Python-plus-wheels for everyone who never asked for ANN,
and this backend is reachable only through the ``hnsw`` optional extra
(``pip install "ltcai[hnsw]"``) plus an explicit
``LATTICEAI_VECTOR_INDEX=hnsw``. When the module is missing the import is
caught here and reported as a reason string; the selector then falls back to
the exact scan and says so, rather than failing a search.

The graph search index is a **derivative**. SQLite remains the source of
truth, so the ``.hnsw`` sidecar next to the brain database is disposable: if
it is deleted, corrupt, or built for a different embedder/row-set, the
fingerprint check rejects it and the caller rebuilds from
``vector_embeddings``.

Approximation is reported, never hidden: this backend sets ``approx = True``
and ``exhaustive = False``, and ``scripts/bench_vector_index.py`` measures
recall@k against the brute-force backend so the cost of the speed is a
number, not a promise.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .base import IndexItem, IndexStats, ScoredId, score_floor

HNSW_BACKEND = "hnsw"
HNSWLIB_MODULE = "hnswlib"
#: Sidecar suffix for the persisted graph (a derivative of the SQLite rows).
HNSW_SUFFIX = ".hnsw"
#: Companion JSON: fingerprint + label→id mapping. Without it the binary
#: graph is unreadable, because hnswlib only stores integer labels.
HNSW_META_SUFFIX = ".hnsw.meta.json"


def load_hnswlib() -> Tuple[Optional[Any], Optional[str]]:
    """Import ``hnswlib`` or explain why it is unavailable (never raises)."""
    try:
        import hnswlib  # noqa: PLC0415 — guarded, optional, import-on-demand
    except Exception as exc:  # noqa: BLE001 — any import failure is "no ANN"
        return None, (
            f"{HNSWLIB_MODULE} is not available ({exc}); install the optional "
            'extra with `pip install "ltcai[hnsw]"`'
        )
    return hnswlib, None


def hnswlib_available() -> bool:
    """True when the optional ANN engine can actually be imported."""
    module, _ = load_hnswlib()
    return module is not None


def sidecar_paths(db_path: Any) -> Tuple[Path, Path]:
    """``(index file, meta file)`` for a brain database path."""
    base = Path(db_path)
    return (
        base.with_suffix(HNSW_SUFFIX),
        base.with_suffix(HNSW_META_SUFFIX),
    )


class HnswIndex:
    """Small-M HNSW graph over the brain's embeddings."""

    def __init__(
        self,
        *,
        dim: int,
        space: str = "cosine",
        ef_construction: int = 200,
        m: int = 16,
        ef_search: int = 64,
    ) -> None:
        self._dim = int(dim)
        self._space = str(space)
        self._ef_construction = int(ef_construction)
        self._m = int(m)
        self._ef_search = int(ef_search)
        self._module, self._detail = load_hnswlib()
        self._vectors: Dict[str, List[float]] = {}
        self._metadata: Dict[str, Dict[str, Any]] = {}
        self._labels: List[str] = []
        self._ann: Any = None
        self._dirty = True
        self._loaded = False

    # ── identity ─────────────────────────────────────────────────────────────
    @property
    def backend(self) -> str:
        return HNSW_BACKEND

    @property
    def approx(self) -> bool:
        return True

    @property
    def exhaustive(self) -> bool:
        return False

    @property
    def available(self) -> bool:
        return self._module is not None

    @property
    def unavailable_detail(self) -> Optional[str]:
        return self._detail

    @property
    def loaded_from_sidecar(self) -> bool:
        """True when the graph came off disk instead of being rebuilt."""
        return self._loaded

    # ── mutation ─────────────────────────────────────────────────────────────
    def _reject_mutation_after_load(self) -> None:
        if self._loaded:
            raise RuntimeError(
                "this HnswIndex was loaded from a sidecar and holds no source "
                "vectors; rebuild it from vector_embeddings before mutating"
            )

    def add(
        self,
        id: str,
        vector: Sequence[float],
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self._reject_mutation_after_load()
        key = str(id)
        self._vectors[key] = [float(value) for value in vector]
        self._metadata[key] = dict(metadata or {})
        self._dirty = True

    def remove(self, id: str) -> None:
        self._reject_mutation_after_load()
        key = str(id)
        self._vectors.pop(key, None)
        self._metadata.pop(key, None)
        self._dirty = True

    def rebuild(self, items: Iterable[IndexItem]) -> None:
        self._vectors.clear()
        self._metadata.clear()
        self._loaded = False
        for item_id, vector, metadata in items:
            self.add(item_id, vector, metadata)
        self._dirty = True

    def metadata_for(self, id: str) -> Dict[str, Any]:
        return dict(self._metadata.get(str(id), {}))

    # ── graph construction ───────────────────────────────────────────────────
    def _new_graph(self, module: Any, capacity: int) -> Any:
        graph = module.Index(space=self._space, dim=self._dim)
        graph.init_index(
            max_elements=max(1, capacity),
            ef_construction=self._ef_construction,
            M=self._m,
        )
        return graph

    def _ensure_graph(self) -> Any:
        """Build the graph if the held vectors changed (None when disabled)."""
        module = self._module
        if module is None:
            return None
        if self._ann is not None and not self._dirty:
            return self._ann
        labels = list(self._vectors)
        graph = self._new_graph(module, len(labels))
        if labels:
            graph.add_items(
                [self._vectors[key] for key in labels],
                list(range(len(labels))),
            )
        self._ann = graph
        self._labels = labels
        self._dirty = False
        return graph

    # ── search ───────────────────────────────────────────────────────────────
    def search(
        self,
        query: Sequence[float],
        top_k: int,
        filter: Optional[Mapping[str, Any]] = None,
    ) -> List[ScoredId]:
        graph = self._ensure_graph()
        if graph is None or not self._labels:
            return []
        floor = score_floor(filter)
        wanted = max(1, min(int(top_k), len(self._labels)))
        graph.set_ef(max(self._ef_search, wanted))
        labels, distances = graph.knn_query([list(query)], k=wanted)
        scored: List[ScoredId] = []
        for label, distance in zip(labels[0], distances[0], strict=True):
            # cosine/ip space: hnswlib returns 1 - similarity as the distance.
            score = 1.0 - float(distance)
            if score < floor:
                continue
            scored.append((self._labels[int(label)], score))
        return scored

    def stats(self) -> IndexStats:
        return IndexStats(
            backend=self.backend,
            size=len(self._labels) if self._loaded else len(self._vectors),
            dim=self._dim,
            approx=True,
            exhaustive=False,
            available=self.available,
            detail=self._detail,
        )

    # ── sidecar persistence (a derivative — safe to delete) ──────────────────
    def save(self, db_path: Any, *, fingerprint: str) -> bool:
        """Write the graph + label map beside the brain database.

        Returns False (never raises) when ANN is unavailable, the index is
        empty, or the filesystem refuses the write: a missing sidecar only
        costs a rebuild on the next search.
        """
        graph = self._ensure_graph()
        if graph is None or not self._labels:
            return False
        index_path, meta_path = sidecar_paths(db_path)
        try:
            index_path.parent.mkdir(parents=True, exist_ok=True)
            graph.save_index(str(index_path))
            meta_path.write_text(
                json.dumps(
                    {
                        "fingerprint": str(fingerprint),
                        "dim": self._dim,
                        "space": self._space,
                        "labels": self._labels,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001 — persistence is best-effort
            return False
        return True

    def load(self, db_path: Any, *, fingerprint: str) -> bool:
        """Adopt a sidecar graph when it provably matches ``fingerprint``.

        Any mismatch — missing file, unreadable JSON, different embedder or
        row-set, wrong dimension — returns False so the caller rebuilds. The
        sidecar is never trusted over SQLite.
        """
        module = self._module
        if module is None:
            return False
        index_path, meta_path = sidecar_paths(db_path)
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            matches = str(meta["fingerprint"]) == str(fingerprint)
            matches = matches and int(meta["dim"]) == self._dim
            labels = [str(label) for label in meta["labels"]]
        except Exception:  # noqa: BLE001 — absent/corrupt sidecar = rebuild
            return False
        if not (matches and labels):
            return False
        try:
            graph = module.Index(space=self._space, dim=self._dim)
            graph.load_index(str(index_path), max_elements=len(labels))
        except Exception:  # noqa: BLE001 — corrupt binary = rebuild
            return False
        self._ann = graph
        self._labels = labels
        self._vectors.clear()
        self._metadata.clear()
        self._dirty = False
        self._loaded = True
        return True


__all__ = [
    "HNSW_BACKEND",
    "HNSW_META_SUFFIX",
    "HNSW_SUFFIX",
    "HNSWLIB_MODULE",
    "HnswIndex",
    "hnswlib_available",
    "load_hnswlib",
    "sidecar_paths",
]
