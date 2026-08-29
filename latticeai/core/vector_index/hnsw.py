"""Approximate nearest-neighbour index with incremental append.

``hnswlib`` is optional (``pip install hnswlib``; the published
``ltcai[hnsw]`` extra was retired in 11.6.0 when the write path moved to
Rust). When the compiled module is missing the import is caught here and
reported as a reason string; search then returns no ANN hits rather than
pretending.

Policy, also stated on :meth:`HnswIndex.add_items`:

* **Append** new ids onto the live graph (``resize_index`` + ``add_items``).
* **Full rebuild** on provider/dim/space change, on any deletion when the
  graph was loaded from a sidecar without source vectors, or when deletes
  exceed :data:`DELETE_REBUILD_RATIO` of the current size.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

HNSW_BACKEND = "hnsw"
HNSWLIB_MODULE = "hnswlib"
HNSW_SUFFIX = ".hnsw"
HNSW_META_SUFFIX = ".hnsw.meta.json"
#: Rebuild instead of tombstoning once this fraction of the graph would go.
DELETE_REBUILD_RATIO = 0.10

IndexItem = Tuple[str, Sequence[float], Mapping[str, Any]]
ScoredId = Tuple[str, float]


def load_hnswlib() -> Tuple[Optional[Any], Optional[str]]:
    """Import ``hnswlib`` or explain why it is unavailable (never raises)."""
    try:
        import hnswlib  # noqa: PLC0415 — guarded, optional, import-on-demand
    except Exception as exc:  # noqa: BLE001 — any import failure is "no ANN"
        return None, (
            f"{HNSWLIB_MODULE} is not available ({exc}); install it with "
            "`pip install hnswlib` to use the approximate index"
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
        ef_construction: int = 400,
        m: int = 32,
        ef_search: int = 400,
        model_id: str = "",
    ) -> None:
        self._dim = int(dim)
        self._space = str(space)
        self._ef_construction = int(ef_construction)
        self._m = int(m)
        self._ef_search = int(ef_search)
        self._model_id = str(model_id)
        self._module, self._detail = load_hnswlib()
        self._vectors: Dict[str, List[float]] = {}
        self._metadata: Dict[str, Dict[str, Any]] = {}
        self._labels: List[str] = []
        self._ann: Any = None
        self._dirty = True
        self._loaded = False

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

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def labels(self) -> List[str]:
        """Live label order — the sidecar's identity, loaded or rebuilt."""
        return list(self._labels)

    def _identity_matches(self, dim: int, space: str, model_id: str) -> bool:
        return (
            int(dim) == self._dim
            and str(space) == self._space
            and str(model_id) == self._model_id
        )

    def add(
        self,
        id: str,
        vector: Sequence[float],
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        if self._loaded:
            raise RuntimeError(
                "this HnswIndex was loaded from a sidecar and holds no source "
                "vectors; rebuild it from vector_embeddings before mutating"
            )
        key = str(id)
        self._vectors[key] = [float(value) for value in vector]
        self._metadata[key] = dict(metadata or {})
        self._dirty = True

    def remove(self, id: str) -> None:
        if self._loaded:
            raise RuntimeError(
                "this HnswIndex was loaded from a sidecar and holds no source "
                "vectors; rebuild it from vector_embeddings before mutating"
            )
        key = str(id)
        self._vectors.pop(key, None)
        self._metadata.pop(key, None)
        self._dirty = True

    def rebuild(self, items: Iterable[IndexItem]) -> None:
        """Replace the held set and mark the graph for a full rebuild."""
        self._vectors.clear()
        self._metadata.clear()
        self._labels.clear()
        self._ann = None
        self._loaded = False
        for item_id, vector, metadata in items:
            self.add(item_id, vector, metadata)
        self._dirty = True

    def add_items(
        self,
        items: Iterable[IndexItem],
        *,
        dim: Optional[int] = None,
        space: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> str:
        """Append new vectors, or rebuild when the policy says so.

        Returns ``"append"`` or ``"rebuild"`` so a bench can time the two
        doors without guessing.

        Full rebuild when the embedder identity changed, when any currently
        indexed id is missing from a *full* incoming snapshot (a deletion),
        or when deletes would exceed :data:`DELETE_REBUILD_RATIO`. A sidecar
        that was loaded without source vectors still **appends**: the
        caller passes only the new ids (COUNT+delta) and ``_append_live``
        extends the graph. A disjoint incoming set is that delta, not a
        wipe.
        """
        incoming: Dict[str, IndexItem] = {}
        for item_id, vector, metadata in items:
            incoming[str(item_id)] = (str(item_id), vector, metadata)

        want_dim = self._dim if dim is None else int(dim)
        want_space = self._space if space is None else str(space)
        want_model = self._model_id if model_id is None else str(model_id)
        if not self._identity_matches(want_dim, want_space, want_model):
            self._dim = want_dim
            self._space = want_space
            self._model_id = want_model
            self.rebuild(incoming.values())
            return "rebuild"

        if self._loaded:
            current = set(self._labels)
            incoming_ids = set(incoming)
            overlap = current & incoming_ids
            deleted = current - incoming_ids
            # Delta-only: the caller passed new ids, none of the live labels.
            # A full snapshot that dropped ids is a deletion and must rebuild.
            delta_only = bool(incoming_ids) and not overlap
            if deleted and not delta_only:
                self.rebuild(incoming.values())
                return "rebuild"
            new_ids = [item_id for item_id in incoming if item_id not in current]
            if not new_ids:
                return "append"
            for item_id in new_ids:
                _, vector, metadata = incoming[item_id]
                self._vectors[item_id] = [float(value) for value in vector]
                self._metadata[item_id] = dict(metadata or {})
            if self._ann is not None and self._module is not None:
                self._append_live(new_ids)
                return "append"
            self.rebuild(incoming.values())
            return "rebuild"

        current = set(self._vectors)
        incoming_ids = set(incoming)
        deleted = current - incoming_ids
        overlap = current & incoming_ids
        # Same discriminant as the loaded path: a disjoint incoming set is
        # COUNT+delta, not a snapshot that deleted every live id.
        if deleted and not (bool(incoming_ids) and not overlap):
            ratio = len(deleted) / max(1, len(current))
            if ratio >= DELETE_REBUILD_RATIO or not current:
                self.rebuild(incoming.values())
                return "rebuild"
            # Sparse deletes: drop the gone ids and rebuild so we never
            # leave a tombstoned hole the sidecar cannot describe.
            self.rebuild(incoming.values())
            return "rebuild"

        new_ids = [item_id for item_id in incoming if item_id not in current]
        if not new_ids and not self._dirty and self._ann is not None:
            return "append"
        for item_id in new_ids:
            _, vector, metadata = incoming[item_id]
            self.add(item_id, vector, metadata)
        if self._ann is not None and new_ids and self._module is not None:
            self._append_live(new_ids)
            return "append"
        self._dirty = True
        return "append" if new_ids or self._ann is not None else "rebuild"

    def _new_graph(self, module: Any, capacity: int) -> Any:
        graph = module.Index(space=self._space, dim=self._dim)
        graph.init_index(
            max_elements=max(1, capacity),
            ef_construction=self._ef_construction,
            M=self._m,
        )
        return graph

    def _append_live(self, new_ids: Sequence[str]) -> None:
        """``hnswlib`` ``resize_index`` + ``add_items`` for ``new_ids`` only."""
        module = self._module
        graph = self._ann
        if module is None or graph is None:
            self._dirty = True
            return
        start = len(self._labels)
        needed = start + len(new_ids)
        try:
            current_max = int(graph.get_max_elements())
        except Exception:  # noqa: BLE001 — some builds expose no getter
            current_max = start
        if needed > current_max:
            graph.resize_index(max(needed, current_max * 2 if current_max else needed))
        graph.add_items(
            [self._vectors[key] for key in new_ids],
            list(range(start, start + len(new_ids))),
        )
        self._labels.extend(new_ids)
        self._dirty = False

    def _ensure_graph(self) -> Any:
        """Build the graph if the held vectors changed (None when disabled)."""
        module = self._module
        if module is None:
            return None
        if self._ann is not None and not self._dirty:
            return self._ann
        labels = list(self._vectors)
        graph = self._new_graph(module, max(len(labels), 1))
        if labels:
            graph.add_items(
                [self._vectors[key] for key in labels],
                list(range(len(labels))),
            )
        self._ann = graph
        self._labels = labels
        self._dirty = False
        return graph

    def search(
        self,
        query: Sequence[float],
        top_k: int,
        filter: Optional[Mapping[str, Any]] = None,
    ) -> List[ScoredId]:
        graph = self._ensure_graph()
        if graph is None or not self._labels:
            return []
        floor = float("-inf")
        if filter and filter.get("min_score") is not None:
            try:
                floor = float(filter["min_score"])
            except (TypeError, ValueError):
                floor = float("-inf")
        wanted = max(1, min(int(top_k), len(self._labels)))
        graph.set_ef(max(self._ef_search, wanted))
        labels, distances = graph.knn_query([list(query)], k=wanted)
        scored: List[ScoredId] = []
        for label, distance in zip(labels[0], distances[0], strict=True):
            score = 1.0 - float(distance)
            if score < floor:
                continue
            scored.append((self._labels[int(label)], score))
        return scored

    def stats(self) -> Dict[str, Any]:
        return {
            "backend": self.backend,
            "size": len(self._labels) if self._loaded else len(self._vectors),
            "dim": self._dim,
            "approx": True,
            "exhaustive": False,
            "available": self.available,
            "detail": self._detail,
            "model_id": self._model_id,
        }

    def save(self, db_path: Any, *, fingerprint: str) -> bool:
        """Write the graph + label map beside the brain database."""
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
                        "model_id": self._model_id,
                        "labels": self._labels,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001 — persistence is best-effort
            return False
        return True

    def load(self, db_path: Any, *, fingerprint: str, strict: bool = True) -> bool:
        """Adopt a sidecar graph when it matches this embedder identity.

        ``strict`` (the default) also requires the stored fingerprint — size
        included — so a grown store does not search a stale graph. Relaxed
        load keeps dim/model and leaves COUNT+delta to append the missing
        ids rather than dumping the whole table.
        """
        module = self._module
        if module is None:
            return False
        index_path, meta_path = sidecar_paths(db_path)
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            matches = int(meta["dim"]) == self._dim
            if meta.get("model_id") not in (None, "", self._model_id):
                matches = False
            if strict:
                matches = matches and str(meta["fingerprint"]) == str(fingerprint)
            labels = [str(label) for label in meta["labels"]]
        except Exception:  # noqa: BLE001 — absent/corrupt sidecar = rebuild
            return False
        if not (matches and labels):
            return False
        try:
            graph = module.Index(space=self._space, dim=self._dim)
            # Headroom so an in-process append does not have to rebuild
            # just because load_index pinned max_elements to the old size.
            graph.load_index(str(index_path), max_elements=max(len(labels) + 64, 1))
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
    "DELETE_REBUILD_RATIO",
    "HNSW_BACKEND",
    "HNSW_META_SUFFIX",
    "HNSW_SUFFIX",
    "HNSWLIB_MODULE",
    "HnswIndex",
    "hnswlib_available",
    "load_hnswlib",
    "sidecar_paths",
]
